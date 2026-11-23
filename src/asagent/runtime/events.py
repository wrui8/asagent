from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    SESSION_CREATED = "session_created"
    USER_INPUT = "user_input"
    RESUMED = "resumed"
    ROUTE_SELECTED = "route_selected"
    CONTEXT_BUILT = "context_built"
    MODEL_RESPONSE = "model_response"
    PROTOCOL_REPAIR = "protocol_repair"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    GUARD_VERDICT = "guard_verdict"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_DECIDED = "approval_decided"
    AUDIT = "audit"
    BUDGET_EXCEEDED = "budget_exceeded"
    CHECKPOINT = "checkpoint"
    FINAL = "final"
    ERROR = "error"


class AgentEvent(BaseModel):
    seq: int = 0
    ts: str
    session_id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
