from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from ..config import AppConfig
from ..runtime.factory import build_runtime
from ..runtime.loop import AgentRuntime
from ..runtime.state import SessionState, SessionStatus
from ..storage.protocols import KernelStore, WebStore
from ..storage.redis_state import RedisState
from .config import WebSettings

logger = logging.getLogger(__name__)

RuntimeFactory = Callable[..., AgentRuntime]

# 会话锁与心跳 TTL：多 worker 下崩溃后最多 60 秒自动释放
HEARTBEAT_TTL_S = 60
HEARTBEAT_EVERY_S = 20


class JobManager:
    """进程内任务调度（uvicorn 必须单 worker）。

    约束：
    - 同一会话同时只允许一个运行中任务；
    - 全局并发由信号量限制，超出排队；
    - 取消时显式落盘 FAILED 状态（CancelledError 不会被内核捕获）。
    """

    def __init__(
        self,
        *,
        config: AppConfig,
        web: WebStore,
        store: KernelStore,
        settings: WebSettings,
        runtime_factory: RuntimeFactory | None = None,
        redis: RedisState | None = None,
        memory: object | None = None,
        publisher: object | None = None,
    ):
        self.config = config
        self.web = web
        self.store = store
        self.settings = settings
        self.runtime_factory = runtime_factory or build_runtime
        self.redis = redis
        # 记忆召回器（实现 `section(user_id, query)`），关闭记忆时为 None
        self.memory = memory
        self.publisher = publisher
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._owners: dict[str, str] = {}
        self._tokens: dict[str, str] = {}
        self._sem = asyncio.Semaphore(max(1, settings.global_max_parallel))

    # ------------------------------------------------------------------ #
    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        if task is not None and not task.done():
            return True
        # 多 worker：其他进程可能正在跑同一会话
        return bool(self.redis and self.redis.exists(f"job:{session_id}"))

    def running_sessions(self) -> list[str]:
        return [session_id for session_id in self._tasks if self.is_running(session_id)]

    def running_for(self, owner_id: str) -> int:
        if self.redis is not None:
            return self.redis.busy_count(owner_id)
        return sum(
            1
            for session_id, uid in self._owners.items()
            if uid == owner_id and self.is_running(session_id)
        )

    def submit(
        self,
        *,
        session_id: str,
        owner_id: str,
        profile: str,
        user_input: str,
        max_steps: int,
        max_cost_cny: float | None,
        state: SessionState,
    ) -> None:
        self._spawn(
            session_id=session_id,
            owner_id=owner_id,
            profile=profile,
            user_input=user_input,
            max_steps=max_steps,
            max_cost_cny=max_cost_cny,
            resume=False,
            state=state,
        )

    def submit_resume(
        self,
        *,
        session_id: str,
        owner_id: str,
        profile: str,
        max_steps: int = 12,
        max_cost_cny: float | None = None,
    ) -> None:
        self._spawn(
            session_id=session_id,
            owner_id=owner_id,
            profile=profile,
            user_input=None,
            max_steps=max_steps,
            max_cost_cny=max_cost_cny,
            resume=True,
            state=None,
        )

    def _spawn(
        self,
        *,
        session_id: str,
        owner_id: str,
        profile: str,
        user_input: str | None,
        max_steps: int,
        max_cost_cny: float | None,
        resume: bool,
        state: SessionState | None,
    ) -> None:
        if self.is_running(session_id):
            raise RuntimeError("该会话已在运行")
        if self.redis is not None:
            token = self.redis.acquire(
                f"job:{session_id}", ttl_s=HEARTBEAT_TTL_S
            )
            if token is None:
                raise RuntimeError("该会话已在运行")
            self._tokens[session_id] = token
            self.redis.add_busy(owner_id, session_id)
        self._owners[session_id] = owner_id
        self._tasks[session_id] = asyncio.create_task(
            self._run(
                session_id=session_id,
                owner_id=owner_id,
                profile=profile,
                user_input=user_input,
                max_steps=max_steps,
                max_cost_cny=max_cost_cny,
                resume=resume,
                state=state,
            ),
            name=f"agent:{session_id}",
        )

    def cancel(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def shutdown(self) -> None:
        tasks = [t for t in self._tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # ------------------------------------------------------------------ #
    async def _run(
        self,
        *,
        session_id: str,
        owner_id: str,
        profile: str,
        user_input: str | None,
        max_steps: int,
        max_cost_cny: float | None,
        resume: bool,
        state: SessionState | None,
    ) -> None:
        heartbeat = asyncio.create_task(
            self._heartbeat(session_id), name=f"heartbeat:{session_id}"
        )
        try:
            async with self._sem:
                runtime = self.runtime_factory(
                    self.config,
                    profile,
                    max_cost_cny=max_cost_cny,
                    store=self.store,
                    redis=self.redis,
                    memory=self.memory,
                )
                try:
                    await runtime.run(
                        user_input,
                        session_id=session_id,
                        resume=resume,
                        max_steps=max_steps,
                        owner_id=owner_id,
                        state=state,
                    )
                finally:
                    await runtime.aclose()
            # 会话完成后异步触发记忆抽取（去抖/配额在 policy 内判断）
            await self._publish_memory(session_id, owner_id)
        except asyncio.CancelledError:
            self._mark_failed(session_id, "用户取消")
            raise
        except Exception as exc:  # noqa: BLE001 - 任务失败必须落盘，不能静默
            logger.exception("agent job failed: %s", session_id)
            self._mark_failed(session_id, f"任务异常：{exc}")
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            self._tasks.pop(session_id, None)
            self._owners.pop(session_id, None)
            self._release(session_id, owner_id)
            self._record_cost(session_id, owner_id)

    async def _heartbeat(self, session_id: str) -> None:
        token = self._tokens.get(session_id)
        if self.redis is None or token is None:
            return
        while True:
            await asyncio.sleep(HEARTBEAT_EVERY_S)
            try:
                self.redis.renew(
                    f"job:{session_id}", token, ttl_s=HEARTBEAT_TTL_S
                )
            except Exception:  # noqa: BLE001 - 续期失败让锁自然过期，不中断任务
                logger.warning("heartbeat renew failed: %s", session_id)

    def _release(self, session_id: str, owner_id: str) -> None:
        if self.redis is None:
            self._tokens.pop(session_id, None)
            return
        token = self._tokens.pop(session_id, None)
        try:
            if token is not None:
                self.redis.release(f"job:{session_id}", token)
            self.redis.remove_busy(owner_id, session_id)
        except Exception:  # noqa: BLE001 - 释放失败依赖 TTL 兜底
            logger.warning("release redis job lock failed: %s", session_id)

    async def _publish_memory(self, session_id: str, owner_id: str) -> None:
        """任务成功结束后投递抽取消息；任何失败都只记录日志。"""
        if self.publisher is None:
            return
        try:
            state = self.store.load_state(session_id)
        except KeyError:
            return
        try:
            published, reason = await self.publisher.maybe_publish(state, owner_id)
            if not published and reason not in ("not_done", ""):
                logger.info("记忆发布跳过: %s %s", session_id, reason)
        except Exception:  # noqa: BLE001 - 记忆是旁路能力，不影响任务结果
            logger.warning("记忆发布失败: %s", session_id, exc_info=True)

    def _mark_failed(self, session_id: str, message: str) -> None:
        try:
            state = self.store.load_state(session_id)
        except KeyError:
            return
        if state.status in (SessionStatus.DONE, SessionStatus.FAILED):
            return
        state.status = SessionStatus.FAILED
        state.error = message
        self.store.save_state(state)
        self.store.append(session_id, "error", {"message": message})

    def _record_cost(self, session_id: str, owner_id: str) -> None:
        try:
            state = self.store.load_state(session_id)
        except KeyError:
            return
        try:
            self.web.add_cost(owner_id, state.cost)
        except Exception:  # noqa: BLE001 - 计费失败不应影响任务结果
            logger.exception("record cost failed: %s", session_id)


__all__ = ["JobManager", "RuntimeFactory"]
