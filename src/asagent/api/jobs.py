from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from ..config import AppConfig
from ..runtime.checkpoint import CheckpointStore
from ..runtime.factory import build_runtime
from ..runtime.loop import AgentRuntime
from ..runtime.state import SessionState, SessionStatus
from .config import WebSettings
from .db import WebDB

logger = logging.getLogger(__name__)

RuntimeFactory = Callable[..., AgentRuntime]


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
        web: WebDB,
        store: CheckpointStore,
        settings: WebSettings,
        runtime_factory: RuntimeFactory | None = None,
    ):
        self.config = config
        self.web = web
        self.store = store
        self.settings = settings
        self.runtime_factory = runtime_factory or build_runtime
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._owners: dict[str, str] = {}
        self._sem = asyncio.Semaphore(max(1, settings.global_max_parallel))

    # ------------------------------------------------------------------ #
    def is_running(self, session_id: str) -> bool:
        task = self._tasks.get(session_id)
        return task is not None and not task.done()

    def running_sessions(self) -> list[str]:
        return [session_id for session_id in self._tasks if self.is_running(session_id)]

    def running_for(self, owner_id: str) -> int:
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
        try:
            async with self._sem:
                runtime = self.runtime_factory(
                    self.config, profile, max_cost_cny=max_cost_cny
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
        except asyncio.CancelledError:
            self._mark_failed(session_id, "用户取消")
            raise
        except Exception as exc:  # noqa: BLE001 - 任务失败必须落盘，不能静默
            logger.exception("agent job failed: %s", session_id)
            self._mark_failed(session_id, f"任务异常：{exc}")
        finally:
            self._tasks.pop(session_id, None)
            self._owners.pop(session_id, None)
            self._record_cost(session_id, owner_id)

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
