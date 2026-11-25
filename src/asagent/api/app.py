from __future__ import annotations

import asyncio
import logging
import mimetypes
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import AppConfig, load_config
from ..memory.policy import PublishPolicy
from ..memory.publish import MemoryPublisher
from ..memory.service import build_memory_service
from ..runtime.state import SessionStatus
from ..storage import build_storage
from ..storage.protocols import KernelStore, WebStore
from ..storage.redis_state import RedisState, RedisWindowLimiter
from .config import WebSettings, load_web_settings
from .evals import EvalJobManager
from .jobs import HEARTBEAT_TTL_S, JobManager
from .kb import KBIndexRunner
from .limiter import SlidingWindowLimiter
from .routers import (
    approvals,
    audit,
    auth,
    evals,
    invites,
    kb,
    sessions,
    system,
    users,
)

logger = logging.getLogger(__name__)

# Tauri 的 WebView 源：Windows 为 http(s)://tauri.localhost，macOS/Linux 为 tauri://localhost
DESKTOP_ORIGINS = (
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
)


def _origin_of(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


def _recover_zombies(store: KernelStore, redis: RedisState | None = None) -> int:
    """进程重启后，残留的 running 会话不可能再推进，标记为失败。

    多 worker 下其他进程可能仍在跑同一会话：Redis 持有 `job:{session}` 锁时跳过，
    避免启动时的恢复扫描误杀活跃任务。Redis 不可达时同样跳过（宁可不恢复）。
    """
    count = 0
    for row in store.list_sessions(1000, 0, status=str(SessionStatus.RUNNING)):
        session_id = row["session_id"]
        if redis is not None:
            try:
                if redis.exists(f"job:{session_id}"):
                    continue
            except Exception:  # noqa: BLE001 - 无法确认是否活跃时不冒险标记
                logger.warning("redis unavailable during recovery, skip %s", session_id)
                continue
        try:
            state = store.load_state(session_id)
        except KeyError:
            continue
        if state.status != SessionStatus.RUNNING:
            continue
        state.status = SessionStatus.FAILED
        state.error = "服务重启，任务中断，请重新发起"
        store.save_state(state)
        store.append(session_id, "error", {"message": state.error})
        count += 1
    return count


def _recover_eval_jobs(web: WebStore, redis: RedisState | None) -> int:
    """启动时清理残留的 running 评测任务。

    多 worker 下 `eval` 锁存在说明另一个进程正在跑评测，不能标记失败；
    Redis 不可达时保守跳过。
    """
    if redis is not None:
        try:
            if redis.exists("eval"):
                return 0
        except Exception:  # noqa: BLE001
            logger.warning("redis unavailable during eval recovery, keep jobs")
            return 0
    return web.eval_jobs_recover()


def _recover_kb_jobs(web: WebStore, redis: RedisState | None) -> int:
    """启动时清理残留的 running 索引任务；Redis 锁存在说明另一个 worker 在跑。"""
    if redis is not None:
        try:
            if redis.exists("kb:index"):
                return 0
        except Exception:  # noqa: BLE001
            logger.warning("redis unavailable during kb recovery, keep jobs")
            return 0
    count = 0
    for job in web.kb_jobs(50):
        if job.get("status") == "running":
            web.kb_job_finish(
                job["id"], status="failed", message="服务重启，索引任务中断"
            )
            count += 1
    return count


async def _delayed_recover(store: KernelStore, redis: RedisState) -> None:
    """启动时上一世残留的会话锁尚未过期；等 TTL 过后再清理一次僵尸会话。"""
    await asyncio.sleep(HEARTBEAT_TTL_S + 5)
    count = await asyncio.to_thread(_recover_zombies, store, redis)
    if count:
        logger.warning("recovered %d zombie session(s) after lock TTL", count)


def create_app(
    config: AppConfig | None = None,
    *,
    web_settings: WebSettings | None = None,
) -> FastAPI:
    config = config or load_config()
    settings = web_settings or load_web_settings(config.root)
    storage = build_storage(config)
    store, web = storage.kernel, storage.web
    redis: RedisState | None = None
    if config.settings.as_redis_url.strip():
        redis = RedisState(config.settings.as_redis_url)
    login_limiter = (
        RedisWindowLimiter(redis, limit=10) if redis else SlidingWindowLimiter(limit=10)
    )
    register_limiter = (
        RedisWindowLimiter(redis, limit=5) if redis else SlidingWindowLimiter(limit=5)
    )
    # 记忆子系统：开关关闭时为 None，行为与 v1 完全一致
    memory_service = build_memory_service(config, storage, redis)
    publisher: MemoryPublisher | None = None
    if memory_service is not None:
        assert redis is not None
        publisher = MemoryPublisher(
            policy=PublishPolicy(
                redis, daily_limit=config.settings.as_memory_daily_extract_limit
            ),
            rabbitmq_url=config.settings.as_rabbitmq_url,
        )
        logger.info("记忆子系统已启用")
    jobs = JobManager(
        config=config,
        web=web,
        store=store,
        settings=settings,
        redis=redis,
        memory=memory_service.recall if memory_service else None,
        publisher=publisher,
    )
    kb_runner = KBIndexRunner(config=config, web=web, redis=redis)
    eval_manager = EvalJobManager(config=config, web=web, redis=redis, store=store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        recovered = _recover_zombies(store, redis)
        if recovered:
            logger.warning("recovered %d interrupted session(s)", recovered)
        stale_evals = _recover_eval_jobs(web, redis)
        if stale_evals:
            logger.warning("recovered %d interrupted eval job(s)", stale_evals)
        stale_kb = _recover_kb_jobs(web, redis)
        if stale_kb:
            logger.warning("recovered %d interrupted kb job(s)", stale_kb)
        delayed = (
            asyncio.create_task(_delayed_recover(store, redis))
            if redis is not None
            else None
        )
        try:
            yield
        finally:
            if delayed is not None:
                delayed.cancel()
                await asyncio.gather(delayed, return_exceptions=True)
            await eval_manager.shutdown()
            await kb_runner.shutdown()
            await jobs.shutdown()
            if memory_service is not None:
                await memory_service.aclose()
            storage.close()
            if redis is not None:
                redis.close()

    app = FastAPI(
        title="AS Agent API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.config = config
    app.state.web_settings = settings
    app.state.store = store
    app.state.web = web
    app.state.jobs = jobs
    app.state.kb_runner = kb_runner
    app.state.eval_manager = eval_manager
    app.state.login_limiter = login_limiter
    app.state.register_limiter = register_limiter
    app.state.redis = redis
    app.state.memory = memory_service

    origins = settings.origins
    desktop_origins = list(DESKTOP_ORIGINS) if settings.desktop_clients else []
    cors_origins = list(dict.fromkeys([*origins, *desktop_origins]))
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    guard_origins = set(cors_origins)

    @app.middleware("http")
    async def origin_guard(request: Request, call_next):  # noqa: ANN001
        # 仅在显式配置白名单时校验 Origin/Referer，挡住跨站写请求。
        # 桌面端源始终放行（CORS 已限制浏览器可读取的范围）。
        if origins and request.method not in ("GET", "HEAD", "OPTIONS"):
            source = request.headers.get("origin") or request.headers.get("referer")
            if source and _origin_of(source) not in guard_origins:
                return JSONResponse(
                    status_code=403,
                    content={"code": "origin_forbidden", "message": "来源不被允许"},
                )
        return await call_next(request)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            content = exc.detail
        else:
            content = {"code": "http_error", "message": str(exc.detail)}
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", []) if part != "body")
        message = first.get("msg", "参数校验失败")
        return JSONResponse(
            status_code=422,
            content={
                "code": "validation_error",
                "message": f"{location}: {message}" if location else message,
            },
        )

    for module in (
        auth,
        users,
        invites,
        sessions,
        approvals,
        audit,
        kb,
        evals,
        system,
    ):
        app.include_router(module.router)

    _mount_frontend(app, config.root)
    return app


def _mount_frontend(app: FastAPI, root: Path) -> None:
    """构建产物存在时托管 SPA；不存在则只提供 API。"""
    dist = root / "web" / "dist"
    index = dist / "index.html"
    if not index.exists():
        return

    # Windows 与部分 Linux 发行版的 MIME 表缺少这些类型，
    # 缺失会导致 PWA 清单/Service Worker 被拒绝。
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    mimetypes.add_type("text/javascript", ".js")

    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    no_cache = {"Cache-Control": "no-cache"}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = dist / full_path
        if full_path and candidate.is_file() and dist in candidate.resolve().parents:
            # Shell 与 Service Worker 必须每次校验，否则升级后仍命中旧缓存
            headers = no_cache if full_path in {"index.html", "sw.js"} else None
            return FileResponse(candidate, headers=headers)
        return FileResponse(index, headers=no_cache)


__all__ = ["create_app"]
