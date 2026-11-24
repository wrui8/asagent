from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class WebSettings(BaseSettings):
    """HTTP 层配置；全部通过 AS_* 环境变量覆盖。

    生产环境必须设置 AS_JWT_SECRET（随机 64 字节），默认值只用于本地开发。
    """

    model_config = SettingsConfigDict(
        env_prefix="AS_",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    jwt_secret: str = "dev-only-change-me"
    access_ttl_minutes: int = 30
    refresh_ttl_days: int = 30
    cookie_name: str = "as_refresh"
    cookie_secure: bool = True
    # 逗号分隔；为空时不校验 Origin（仅本地开发）
    allowed_origins: str = ""
    # 桌面端（Tauri）跨源访问：允许返回 body 刷新令牌并使用系统安全存储
    desktop_clients: bool = True

    default_daily_cost_limit: float = 5.0
    default_session_cost_limit: float = 0.5
    default_concurrent_sessions: int = 1
    global_max_parallel: int = 4
    code_admin_only: bool = True
    max_input_chars: int = 8000
    # 事件流轮询间隔（秒）
    stream_poll_s: float = 0.2
    heartbeat_s: float = 15.0

    # 移动端版本检查（公开接口 /api/app/version）
    # 留空时 latest 取后端 __version__；min_version 留空则不强制更新
    app_latest_version: str = ""
    app_min_version: str = ""
    app_android_url: str = "https://github.com/wrui8/asagent/releases/latest"
    app_release_notes: str = ""

    @property
    def origins(self) -> list[str]:
        return [item.strip() for item in self.allowed_origins.split(",") if item.strip()]


def load_web_settings(root: Path) -> WebSettings:
    env_file = root / ".env"
    return WebSettings(_env_file=env_file if env_file.exists() else None)
