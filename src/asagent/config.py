from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from .models.types import Pricing

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)(?::-([^}]*))?\}")


class ConfigError(Exception):
    """配置缺失或非法。"""


class ProviderConfig(BaseModel):
    kind: Literal["openai_compat"] = "openai_compat"
    base_url: str
    api_key_env: str
    timeout_s: float = 120.0


class ModelConfig(BaseModel):
    provider: str
    upstream_name: str
    tier: Literal["local", "fast", "strong"] = "strong"
    context_window: int = 32768
    max_output_tokens: int = 4096
    supports_tools: bool = True
    pricing: Pricing = Field(default_factory=Pricing)


class ModelsFile(BaseModel):
    version: int = 1
    providers: dict[str, ProviderConfig]
    models: dict[str, ModelConfig]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    deepseek_api_key: str | None = None
    dashscope_api_key: str | None = None
    vllm_api_key: str = "EMPTY"
    vllm_base_url: str = "http://127.0.0.1:8000/v1"

    as_db_path: str = "runtime/state.db"
    as_trace_dir: str = "runtime/traces"
    as_artifacts_dir: str = "runtime/artifacts"
    as_workspace_dir: str = "runtime/workspace"
    # 全局默认的单会话成本上限（元）；profile 与 CLI 可覆盖
    as_max_cost_cny: float | None = None
    # 容器部署时 code 沙箱的宿主路径映射：`容器前缀=宿主前缀`，逗号分隔
    as_sandbox_path_map: str = ""

    # ---- 中间件（阶段 0 起逐步启用；默认全部保持 v1 行为）----
    # 权威库：sqlite（v1）| postgres（阶段 2）
    as_storage_backend: Literal["sqlite", "postgres"] = "sqlite"
    as_pg_dsn: str = ""
    as_redis_url: str = ""
    as_rabbitmq_url: str = ""
    # 检索：sqlite（v1 暴力索引）| milvus（阶段 1）
    as_rag_backend: Literal["sqlite", "milvus"] = "sqlite"
    as_milvus_uri: str = ""
    as_tei_url: str = ""
    as_embedding_dim: int = 1024
    # 检查点与编排：sqlite/legacy（v1）| redis/langgraph（阶段 3）
    as_checkpointer: Literal["sqlite", "redis"] = "sqlite"
    as_engine: Literal["legacy", "langgraph"] = "legacy"
    # 记忆子系统（阶段 4）；启用需要 postgres + redis + rabbitmq + milvus/tei
    as_memory_enabled: bool = False
    as_memory_top_k: int = 5
    as_memory_daily_extract_limit: int = 20
    # 抽取使用的模型（结构化 JSON 输出，建议用强模型）
    as_memory_model: str = "deepseek-chat"
    # 多 worker（阶段 2）；1 等于 v1 行为
    as_workers: int = 1
    # 本地库：索引指纹 / 埋点（阶段 2 起）
    as_local_db_path: str = "runtime/local.db"


@dataclass
class AppConfig:
    root: Path
    models_file: ModelsFile
    settings: Settings

    # ---- 模型 ----
    def model(self, model_id: str) -> ModelConfig:
        try:
            return self.models_file.models[model_id]
        except KeyError as exc:
            raise ConfigError(f"未知模型: {model_id}（检查 configs/models.yaml）") from exc

    def provider(self, name: str) -> ProviderConfig:
        try:
            return self.models_file.providers[name]
        except KeyError as exc:
            raise ConfigError(f"未知 provider: {name}") from exc

    def api_key(self, provider_name: str) -> str | None:
        env_name = self.provider(provider_name).api_key_env
        value = os.environ.get(env_name)
        if provider_name == "local":
            return value or "EMPTY"
        return value or None

    def provider_available(self, name: str) -> bool:
        if name == "local":
            # 本地可达性由 doctor 实际探测；配置层面视为可用
            return True
        return bool(self.api_key(name))

    # ---- 路径 ----
    def path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else (self.root / p)

    @property
    def db_path(self) -> Path:
        return self.path(self.settings.as_db_path)

    @property
    def trace_dir(self) -> Path:
        return self.path(self.settings.as_trace_dir)

    @property
    def artifacts_dir(self) -> Path:
        return self.path(self.settings.as_artifacts_dir)

    @property
    def workspace_dir(self) -> Path:
        return self.path(self.settings.as_workspace_dir)


def expand_env(value: str) -> str:
    """展开 ${VAR} / ${VAR:-default}，供 models.yaml 与 profile.yaml 共用。"""
    def repl(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), match.group(2) or "")

    return _ENV_PATTERN.sub(repl, value)


def _expand(obj: Any) -> Any:
    if isinstance(obj, str):
        return expand_env(obj)
    if isinstance(obj, list):
        return [_expand(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    return obj


def load_config(root: Path | None = None) -> AppConfig:
    root = (root or PROJECT_ROOT).resolve()
    env_file = root / ".env"
    load_dotenv(env_file)

    settings = Settings(_env_file=env_file if env_file.exists() else None)

    models_path = root / "configs" / "models.yaml"
    if not models_path.exists():
        raise ConfigError(f"缺少配置文件: {models_path}")
    raw = yaml.safe_load(models_path.read_text(encoding="utf-8")) or {}
    raw = _expand(raw)
    try:
        models_file = ModelsFile.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"models.yaml 校验失败:\n{exc}") from exc

    for model_id, model in models_file.models.items():
        if model.provider not in models_file.providers:
            raise ConfigError(f"模型 {model_id} 引用了不存在的 provider: {model.provider}")

    return AppConfig(root=root, models_file=models_file, settings=settings)
