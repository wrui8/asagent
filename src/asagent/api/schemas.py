from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

_USERNAME = r"^[A-Za-z0-9_.-]+$"


class RegisterRequest(BaseModel):
    username: str = Field(min_length=4, max_length=32, pattern=_USERNAME)
    password: str = Field(min_length=12, max_length=128)
    invite_code: str = Field(min_length=4, max_length=64)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: str
    username: str
    role: str
    disabled: bool
    created_at: str
    last_login_at: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    # 仅桌面端（X-Client: desktop）返回；Web 继续依赖 httpOnly Cookie
    refresh_token: str | None = None


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=4, max_length=32, pattern=_USERNAME)
    password: str = Field(min_length=12, max_length=128)
    role: Literal["admin", "user"] = "user"


class UserPatchRequest(BaseModel):
    role: Literal["admin", "user"] | None = None
    disabled: bool | None = None
    password: str | None = Field(None, min_length=12, max_length=128)
    max_cost_per_session: float | None = Field(None, gt=0, le=100)
    daily_cost_limit: float | None = Field(None, gt=0, le=1000)
    concurrent_sessions: int | None = Field(None, ge=1, le=10)
    # 每日评测次数上限，0 表示不限
    eval_daily_limit: int | None = Field(None, ge=0, le=100)


class InviteCreateRequest(BaseModel):
    role: Literal["admin", "user"] = "user"
    expires_in_days: int = Field(7, ge=1, le=90)
    max_uses: int = Field(1, ge=1, le=100)


class InviteOut(BaseModel):
    id: str
    role: str
    expires_at: str | None
    max_uses: int
    used_count: int
    revoked: bool
    created_at: str


class InviteCreated(InviteOut):
    code: str


class SessionCreateRequest(BaseModel):
    profile: str = Field("assistant", min_length=1, max_length=32)
    input: str = Field(min_length=1, max_length=8000)
    max_steps: int = Field(12, ge=1, le=40)
    max_cost_cny: float | None = Field(None, gt=0, le=100)


class SessionContinueRequest(BaseModel):
    """在已有会话上追加一轮：步数与预算按本轮计算。"""

    input: str = Field(min_length=1, max_length=8000)
    max_steps: int = Field(12, ge=1, le=40)
    max_cost_cny: float | None = Field(None, gt=0, le=100)


class SessionOut(BaseModel):
    session_id: str
    profile: str
    title: str | None = None
    owner_id: str | None = None
    status: str
    cost: float = 0.0
    step: int = 0
    final_answer: str | None = None
    error: str | None = None
    created_at: str = ""
    updated_at: str = ""


class SessionAccepted(BaseModel):
    session_id: str
    status: str = "queued"


class MessageOut(BaseModel):
    role: str
    content: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tool_call_id: str | None = None


class ApprovalOut(BaseModel):
    call_id: str
    tool: str
    permission: int
    args: dict[str, Any] = Field(default_factory=dict)
    status: str


class ApprovalDecision(BaseModel):
    call_id: str | None = None
    approved: bool = True
    resume: bool = True
    approve_all: bool = False


class KBDocCreate(BaseModel):
    doc_id: str = Field(min_length=2, max_length=64)
    title: str | None = Field(None, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)


class KBDocUpdate(BaseModel):
    title: str | None = Field(None, max_length=200)
    content: str | None = Field(None, min_length=1, max_length=100_000)


class KBRollbackRequest(BaseModel):
    version_id: str = Field(min_length=1, max_length=64)


class KBDocOut(BaseModel):
    doc_id: str
    title: str
    status: str
    size: int
    sha256: str
    suspicious: bool
    created_at: str
    updated_at: str
    created_by: str | None = None
    updated_by: str | None = None
    indexed: bool = False
    chunks: int = 0


class KBDocDetail(KBDocOut):
    content: str


class KBReindexRequest(BaseModel):
    doc_ids: list[str] | None = None


class EvalRunRequest(BaseModel):
    profile: str = Field(min_length=1, max_length=32)
    # None 表示按路由策略自动选择模型
    models: list[str] | None = Field(None, max_length=10)
    repeat: int = Field(1, ge=1, le=5)
    limit: int | None = Field(None, ge=1, le=100)
