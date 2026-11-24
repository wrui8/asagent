from __future__ import annotations

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
from ..runtime.checkpoint import CheckpointStore
from ..runtime.state import SessionStatus
from .config import WebSettings, load_web_settings
from .db import WebDB
from .evals import EvalJobManager
from .jobs import JobManager
from .kb import KBIndexRunner
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


def _recover_zombies(store: CheckpointStore) -> int:
    """进程重启后，残留的 running 会话不可能再推进，标记为失败。"""
    count = 0
    for row in store.list_sessions(1000, 0, status=str(SessionStatus.RUNNING)):
        try:
            state = store.load_state(row["session_id"])
        except KeyError:
            continue
        if state.status != SessionStatus.RUNNING:
            continue
        state.status = SessionStatus.FAILED
        state.error = "服务重启，任务中断，请重新发起"
        store.save_state(state)
        store.append(row["session_id"], "error", {"message": state.error})
        count += 1
    return count


def create_app(
    config: AppConfig | None = None,
    *,
    web_settings: WebSettings | None = None,
) -> FastAPI:
    config = config or load_config()
    settings = web_settings or load_web_settings(config.root)
    store = CheckpointStore(config.db_path)
    web = WebDB(config.db_path)
    jobs = JobManager(config=config, web=web, store=store, settings=settings)
    kb_runner = KBIndexRunner(config=config, web=web)
    eval_manager = EvalJobManager(config=config, web=web)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        recovered = _recover_zombies(store)
        if recovered:
            logger.warning("recovered %d interrupted session(s)", recovered)
        stale_evals = web.eval_jobs_recover()
        if stale_evals:
            logger.warning("recovered %d interrupted eval job(s)", stale_evals)
        try:
            yield
        finally:
            await eval_manager.shutdown()
            await kb_runner.shutdown()
            await jobs.shutdown()
            store.close()

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
