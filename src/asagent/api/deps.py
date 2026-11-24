from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import AppConfig
from ..runtime.checkpoint import CheckpointStore
from ..runtime.state import SessionState
from .config import WebSettings
from .db import WebDB
from .evals import EvalJobManager
from .jobs import JobManager
from .kb import KBIndexRunner
from .security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


def api_error(status_code: int, message: str, code: str) -> HTTPException:
    return HTTPException(
        status_code=status_code, detail={"code": code, "message": message}
    )


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_settings(request: Request) -> WebSettings:
    return request.app.state.web_settings


def get_web(request: Request) -> WebDB:
    return request.app.state.web


def get_store(request: Request) -> CheckpointStore:
    return request.app.state.store


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs


def get_kb_runner(request: Request) -> KBIndexRunner:
    return request.app.state.kb_runner


def get_eval_manager(request: Request) -> EvalJobManager:
    return request.app.state.eval_manager


async def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    web: WebDB = Depends(get_web),
    settings: WebSettings = Depends(get_settings),
) -> dict:
    if credentials is None:
        raise api_error(401, "需要登录", "unauthorized")
    payload = decode_access_token(credentials.credentials, settings.jwt_secret)
    if payload is None:
        raise api_error(401, "登录已过期，请重新登录", "token_invalid")
    user = web.get_user(str(payload.get("sub") or ""))
    if user is None or user["disabled"]:
        raise api_error(401, "账号不可用", "account_disabled")
    return user


async def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise api_error(403, "需要管理员权限", "forbidden")
    return user


def load_owned_session(
    session_id: str, store: CheckpointStore, user: dict
) -> SessionState:
    """越权与不存在统一返回 404，避免泄露会话是否存在。"""
    try:
        state = store.load_state(session_id)
    except KeyError:
        raise api_error(404, "会话不存在", "not_found") from None
    if user["role"] != "admin" and state.owner_id != user["id"]:
        raise api_error(404, "会话不存在", "not_found")
    return state
