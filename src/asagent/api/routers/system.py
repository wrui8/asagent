from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ... import __version__
from ...config import AppConfig
from ...profiles.loader import available_profiles, load_profile
from ..config import WebSettings
from ..deps import (
    api_error,
    current_user,
    get_config,
    get_jobs,
    get_settings,
    require_admin,
)
from ..jobs import JobManager
from ..middleware import probe_middleware

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/app/version")
async def app_version(settings: WebSettings = Depends(get_settings)) -> dict:
    """移动端版本检查（公开）：低于 min_supported 时客户端要求强制更新。"""
    latest = settings.app_latest_version.strip() or __version__
    return {
        "latest": latest,
        "min_supported": settings.app_min_version.strip() or latest,
        "android_url": settings.app_android_url.strip(),
        "notes": settings.app_release_notes.strip(),
    }


@router.get("/profiles")
async def list_profiles(
    user: dict = Depends(current_user),
    config: AppConfig = Depends(get_config),
    settings: WebSettings = Depends(get_settings),
) -> list[dict]:
    items: list[dict] = []
    for name in available_profiles():
        try:
            profile = load_profile(name, project_root=config.root)
        except Exception:  # noqa: BLE001 - 单个场景包损坏不影响其余
            continue
        if name == "code" and settings.code_admin_only and user["role"] != "admin":
            continue
        items.append(
            {
                "name": profile.name,
                "description": profile.description,
                "max_permission": profile.max_permission,
                "max_cost_cny": profile.max_cost_cny,
            }
        )
    return items


@router.get("/system/info")
async def system_info(
    _admin: dict = Depends(require_admin),
    config: AppConfig = Depends(get_config),
    settings: WebSettings = Depends(get_settings),
    jobs: JobManager = Depends(get_jobs),
) -> dict:
    providers = {
        name: config.provider_available(name) for name in config.models_file.providers
    }
    # 阶段 0 只做 TCP 可达性；未配置的中间件显示 not_configured，不算故障
    middleware = await asyncio.to_thread(probe_middleware, config.settings)
    return {
        "root": str(config.root),
        "db_path": str(config.db_path),
        "workspace_dir": str(config.workspace_dir),
        "trace_dir": str(config.trace_dir),
        "providers": providers,
        "models": {
            model_id: {
                "provider": model.provider,
                "tier": model.tier,
                "supports_tools": model.supports_tools,
            }
            for model_id, model in config.models_file.models.items()
        },
        "global_max_parallel": settings.global_max_parallel,
        "code_admin_only": settings.code_admin_only,
        "cookie_secure": settings.cookie_secure,
        "jwt_secret_is_default": settings.jwt_secret == "dev-only-change-me",
        "running_jobs": len(jobs.running_sessions()),
        "middleware": middleware,
    }


@router.get("/system/mcp")
async def mcp_servers(
    profile: str,
    _admin: dict = Depends(require_admin),
    config: AppConfig = Depends(get_config),
) -> list[dict]:
    """只返回场景包声明的 MCP server 配置，不启动子进程。"""
    try:
        loaded = load_profile(profile, project_root=config.root)
    except FileNotFoundError:
        raise api_error(404, "场景包不存在", "not_found") from None
    raw = loaded.tool_config.get("mcp_servers") or []
    servers: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        servers.append(
            {
                "name": item.get("name"),
                "command": item.get("command"),
                "args": item.get("args", []),
                "permission": item.get("permission", 0),
                "timeout_s": item.get("timeout_s", 30),
            }
        )
    return servers
