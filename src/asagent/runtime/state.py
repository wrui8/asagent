from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from ..models.router import RouteState
from ..models.types import Message, Usage


class SessionStatus(StrEnum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"


class SessionState(BaseModel):
    session_id: str
    profile: str
    title: str | None = None
    status: SessionStatus = SessionStatus.RUNNING
    messages: list[Message] = Field(default_factory=list)
    step: int = 0
    route: RouteState | None = None
    usage: Usage = Field(default_factory=Usage)
    cost: float = 0.0
    last_seq: int = 0
    final_answer: str | None = None
    error: str | None = None
    # 会话使用的工作区（resume 时据此恢复，避免回落到不存在的新目录）
    workspace: str | None = None
    created_at: str = ""
    updated_at: str = ""
