from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from ...storage.protocols import WebStore
from ..config import WebSettings
from ..deps import (
    Limiter,
    api_error,
    current_user,
    get_login_limiter,
    get_register_limiter,
    get_settings,
    get_web,
)
from ..schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from ..security import (
    hash_password,
    issue_access_token,
    new_refresh_token,
    refresh_expiry,
    sha256_hex,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RefreshRequest(BaseModel):
    refresh_token: str | None = None


def _client_key(request: Request, suffix: str = "") -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip()
    if not ip:
        ip = request.client.host if request.client else "unknown"
    return f"{ip}:{suffix}" if suffix else ip


def _normalize_code(code: str) -> str:
    return code.strip().upper()


BODY_REFRESH_CLIENTS = frozenset({"desktop", "mobile"})


def _wants_body_refresh(request: Request) -> bool:
    """桌面/移动端通过请求头声明：刷新令牌走响应体，不走 Cookie。"""
    return (
        request.headers.get("x-client", "").strip().lower() in BODY_REFRESH_CLIENTS
    )


def _issue_tokens(
    response: Response,
    user: dict,
    settings: WebSettings,
    web: WebStore,
    *,
    include_refresh: bool = False,
) -> TokenResponse:
    access = issue_access_token(
        user_id=user["id"],
        role=user["role"],
        secret=settings.jwt_secret,
        ttl_minutes=settings.access_ttl_minutes,
    )
    refresh = new_refresh_token()
    web.save_refresh(
        user_id=user["id"],
        token_hash=sha256_hex(refresh),
        expires_at=refresh_expiry(settings.refresh_ttl_days),
    )
    response.set_cookie(
        key=settings.cookie_name,
        value=refresh,
        max_age=settings.refresh_ttl_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api/auth",
    )
    return TokenResponse(
        access_token=access,
        expires_in=settings.access_ttl_minutes * 60,
        user=UserOut(**user),
        refresh_token=refresh if include_refresh and settings.desktop_clients else None,
    )


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    web: WebStore = Depends(get_web),
    settings: WebSettings = Depends(get_settings),
    limiter: Limiter = Depends(get_register_limiter),
) -> TokenResponse:
    if not limiter.allow(_client_key(request)):
        raise api_error(429, "注册过于频繁，请稍后再试", "rate_limited")
    user, error = web.register_with_invite(
        username=payload.username,
        password_hash=hash_password(payload.password),
        code_hash=sha256_hex(_normalize_code(payload.invite_code)),
    )
    if user is None:
        if error == "username_taken":
            raise api_error(409, "用户名已被占用", "username_taken")
        raise api_error(400, "邀请码无效或已过期", "invalid_invite")
    web.touch_login(user["id"])
    return _issue_tokens(
        response, user, settings, web, include_refresh=_wants_body_refresh(request)
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    web: WebStore = Depends(get_web),
    settings: WebSettings = Depends(get_settings),
    limiter: Limiter = Depends(get_login_limiter),
) -> TokenResponse:
    if not limiter.allow(_client_key(request, payload.username)):
        raise api_error(429, "登录尝试过于频繁，请稍后再试", "rate_limited")
    user = web.get_user_with_hash(payload.username)
    if user is None or not verify_password(payload.password, user["password_hash"]):
        raise api_error(401, "用户名或密码错误", "invalid_credentials")
    if user["disabled"]:
        raise api_error(401, "账号已被禁用", "account_disabled")
    user.pop("password_hash", None)
    web.touch_login(user["id"])
    return _issue_tokens(
        response, user, settings, web, include_refresh=_wants_body_refresh(request)
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    web: WebStore = Depends(get_web),
    settings: WebSettings = Depends(get_settings),
) -> TokenResponse:
    token = payload.refresh_token if payload else None
    if not token:
        token = request.cookies.get(settings.cookie_name)
    if not token:
        raise api_error(401, "缺少刷新令牌", "refresh_missing")

    token_hash = sha256_hex(token)
    row = web.get_refresh(token_hash)
    if row is None or row["revoked_at"] is not None:
        raise api_error(401, "刷新令牌无效", "refresh_invalid")
    if row["expires_at"] < datetime.now().isoformat(timespec="seconds"):
        raise api_error(401, "刷新令牌已过期", "refresh_expired")

    user = web.get_user(row["user_id"])
    if user is None or user["disabled"]:
        raise api_error(401, "账号不可用", "account_disabled")

    web.revoke_refresh(token_hash)
    body_flow = _wants_body_refresh(request) or bool(payload and payload.refresh_token)
    return _issue_tokens(
        response, user, settings, web, include_refresh=body_flow
    )


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    payload: RefreshRequest | None = None,
    web: WebStore = Depends(get_web),
    settings: WebSettings = Depends(get_settings),
) -> Response:
    token = payload.refresh_token if payload else None
    if not token:
        token = request.cookies.get(settings.cookie_name)
    if token:
        web.revoke_refresh(sha256_hex(token))
    response.delete_cookie(settings.cookie_name, path="/api/auth")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
async def me(user: dict = Depends(current_user)) -> UserOut:
    return UserOut(**user)
