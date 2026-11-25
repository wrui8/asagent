"""存储接口协议：SQLite 实现（v1）与 PG 实现（v2）都按这些签名被调用。"""

from __future__ import annotations

from typing import Any, Protocol

from ..models.types import ToolCall
from ..runtime.checkpoint import ToolInvocation
from ..runtime.events import AgentEvent
from ..runtime.state import SessionState
from ..tools.base import ToolResult


class KernelStore(Protocol):
    """内核事件溯源存储（sessions / events / checkpoints / artifacts / approvals）。"""

    def close(self) -> None: ...

    def create_session(
        self, profile: str, title: str | None = None, owner_id: str | None = None
    ) -> SessionState: ...

    def save_state(self, state: SessionState) -> None: ...

    def load_state(self, session_id: str) -> SessionState: ...

    def list_sessions(
        self,
        limit: int = 20,
        offset: int = 0,
        *,
        owner_id: str | None = None,
        profile: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]: ...

    def append(self, session_id: str, type: str, payload: dict[str, Any]) -> AgentEvent: ...

    def events(self, session_id: str, after_seq: int = 0) -> list[AgentEvent]: ...

    def recent_events_of_type(self, type: str, limit: int = 50) -> list[AgentEvent]: ...

    def begin_tool(self, session_id: str, call: ToolCall, permission: int) -> None: ...

    def finish_tool(self, session_id: str, call_id: str, result: ToolResult) -> None: ...

    def pending_invocations(self, session_id: str) -> list[ToolInvocation]: ...

    def finished_invocations(self, session_id: str) -> list[ToolInvocation]: ...

    def request_approval(self, session_id: str, call_id: str) -> None: ...

    def decide_approval(self, session_id: str, call_id: str, approved: bool) -> None: ...

    def approval_status(self, session_id: str, call_id: str) -> str | None: ...

    def list_approvals(self, session_id: str) -> list[dict[str, Any]]: ...

    def save(
        self,
        *,
        session_id: str,
        kind: str,
        content: str,
        meta: dict[str, Any] | None = None,
    ) -> str: ...

    def get_artifact(self, artifact_id: str) -> str | None: ...

    def artifact_session(self, artifact_id: str) -> str | None: ...


class WebStore(Protocol):
    """API 层独立表（用户、邀请码、配额、知识库元数据、评测任务）。"""

    # 用户
    def create_user(
        self, *, username: str, password_hash: str, role: str = "user"
    ) -> dict[str, Any]: ...

    def get_user(self, user_id: str) -> dict[str, Any] | None: ...

    def get_user_with_hash(self, username: str) -> dict[str, Any] | None: ...

    def list_users(self) -> list[dict[str, Any]]: ...

    def update_user(
        self,
        user_id: str,
        *,
        role: str | None = None,
        disabled: bool | None = None,
        password_hash: str | None = None,
    ) -> bool: ...

    def touch_login(self, user_id: str) -> None: ...

    def admin_count(self) -> int: ...

    # 邀请码
    def create_invite(
        self,
        *,
        code_hash: str,
        created_by: str,
        role: str = "user",
        expires_at: str | None = None,
        max_uses: int = 1,
    ) -> str: ...

    def list_invites(self) -> list[dict[str, Any]]: ...

    def revoke_invite(self, invite_id: str) -> bool: ...

    def register_with_invite(
        self,
        *,
        username: str,
        password_hash: str,
        code_hash: str,
        now: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]: ...

    # refresh token
    def save_refresh(self, *, user_id: str, token_hash: str, expires_at: str) -> None: ...

    def get_refresh(self, token_hash: str) -> dict[str, Any] | None: ...

    def revoke_refresh(self, token_hash: str) -> None: ...

    def revoke_user_refreshes(self, user_id: str) -> None: ...

    # 配额与用量
    def get_quota(self, user_id: str) -> dict[str, Any] | None: ...

    def set_quota(
        self,
        user_id: str,
        *,
        max_cost_per_session: float | None,
        daily_cost_limit: float | None,
        concurrent_sessions: int,
        eval_daily_limit: int = 0,
    ) -> None: ...

    def daily_cost(self, user_id: str) -> float: ...

    def add_cost(self, user_id: str, cost: float) -> None: ...

    # 会话可见性
    def hide_session(self, user_id: str, session_id: str) -> None: ...

    def hidden_sessions(self, user_id: str) -> set[str]: ...

    # 知识库元数据
    def kb_upsert(
        self,
        *,
        doc_id: str,
        title: str,
        sha256: str,
        size: int,
        suspicious: bool,
        actor: str,
    ) -> dict[str, Any]: ...

    def kb_list(self, *, include_deleted: bool = True) -> list[dict[str, Any]]: ...

    def kb_get(self, doc_id: str) -> dict[str, Any] | None: ...

    def kb_set_status(self, doc_id: str, status: str, *, actor: str) -> None: ...

    def kb_add_version(
        self, *, doc_id: str, content: str, sha256: str, actor: str
    ) -> str: ...

    def kb_versions(self, doc_id: str, limit: int = 20) -> list[dict[str, Any]]: ...

    def kb_get_version(self, version_id: str) -> dict[str, Any] | None: ...

    def kb_audit(
        self,
        *,
        actor: str,
        action: str,
        doc_id: str | None = None,
        detail: str | None = None,
    ) -> None: ...

    def kb_audit_list(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def kb_job_start(
        self, *, kind: str, doc_ids: list[str] | None, actor: str
    ) -> str: ...

    def kb_job_finish(self, job_id: str, *, status: str, message: str | None) -> None: ...

    def kb_jobs(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def kb_job_running(self) -> dict[str, Any] | None: ...

    # 评测任务
    def eval_job_start(
        self,
        *,
        actor: str,
        profile: str,
        models: list[str] | None,
        repeat: int,
        limit_cases: int | None,
    ) -> str: ...

    def eval_job_finish(
        self,
        job_id: str,
        *,
        status: str,
        message: str | None = None,
        report_id: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None: ...

    def eval_job(self, job_id: str) -> dict[str, Any] | None: ...

    def eval_jobs(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def eval_job_running(self) -> dict[str, Any] | None: ...

    def eval_count_today(self, actor: str) -> int: ...

    def eval_jobs_recover(self) -> int: ...
