from __future__ import annotations

from fastapi import APIRouter, Depends

from ...storage.protocols import WebStore
from ..config import WebSettings
from ..deps import api_error, get_settings, get_web, require_admin
from ..schemas import UserCreateRequest, UserOut, UserPatchRequest
from ..security import hash_password

router = APIRouter(prefix="/api", tags=["admin"])


def _merge_quota(
    web: WebStore, settings: WebSettings, user_id: str, payload: UserPatchRequest
) -> dict:
    current = web.get_quota(user_id)
    base = {
        "max_cost_per_session": (
            current["max_cost_per_session"]
            if current
            else settings.default_session_cost_limit
        ),
        "daily_cost_limit": (
            current["daily_cost_limit"] if current else settings.default_daily_cost_limit
        ),
        "concurrent_sessions": (
            current["concurrent_sessions"]
            if current
            else settings.default_concurrent_sessions
        ),
        "eval_daily_limit": current["eval_daily_limit"] if current else 0,
    }
    for key in base:
        value = getattr(payload, key, None)
        if value is not None:
            base[key] = value
    return base


@router.get("/users", response_model=list[UserOut])
async def list_users(
    _admin: dict = Depends(require_admin),
    web: WebStore = Depends(get_web),
) -> list[UserOut]:
    return [UserOut(**user) for user in web.list_users()]


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    payload: UserCreateRequest,
    _admin: dict = Depends(require_admin),
    web: WebStore = Depends(get_web),
) -> UserOut:
    if web.get_user_with_hash(payload.username) is not None:
        raise api_error(409, "用户名已被占用", "username_taken")
    user = web.create_user(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    return UserOut(**user)


@router.patch("/users/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: str,
    payload: UserPatchRequest,
    admin: dict = Depends(require_admin),
    web: WebStore = Depends(get_web),
    settings: WebSettings = Depends(get_settings),
) -> UserOut:
    target = web.get_user(user_id)
    if target is None:
        raise api_error(404, "用户不存在", "not_found")

    # 防止把自己锁在门外
    if user_id == admin["id"] and (payload.disabled or payload.role not in (None, "admin")):
        raise api_error(400, "不能禁用或降级当前管理员", "self_lockout")
    if (
        payload.role == "user"
        and target["role"] == "admin"
        and web.admin_count() <= 1
    ):
        raise api_error(400, "至少保留一个管理员", "last_admin")

    web.update_user(
        user_id,
        role=payload.role,
        disabled=payload.disabled,
        password_hash=hash_password(payload.password) if payload.password else None,
    )
    if payload.disabled:
        web.revoke_user_refreshes(user_id)

    if any(
        getattr(payload, key) is not None
        for key in (
            "max_cost_per_session",
            "daily_cost_limit",
            "concurrent_sessions",
            "eval_daily_limit",
        )
    ):
        quota = _merge_quota(web, settings, user_id, payload)
        web.set_quota(user_id, **quota)

    updated = web.get_user(user_id)
    assert updated is not None
    return UserOut(**updated)


@router.get("/users/{user_id}/quota")
async def get_user_quota(
    user_id: str,
    _admin: dict = Depends(require_admin),
    web: WebStore = Depends(get_web),
    settings: WebSettings = Depends(get_settings),
) -> dict:
    if web.get_user(user_id) is None:
        raise api_error(404, "用户不存在", "not_found")
    quota = web.get_quota(user_id) or {
        "max_cost_per_session": settings.default_session_cost_limit,
        "daily_cost_limit": settings.default_daily_cost_limit,
        "concurrent_sessions": settings.default_concurrent_sessions,
        "eval_daily_limit": 0,
    }
    quota["used_today"] = web.daily_cost(user_id)
    quota["eval_used_today"] = web.eval_count_today(user_id)
    return quota
