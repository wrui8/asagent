from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ..config import expand_env
from ..models.router import ModelPolicy

_PROFILES_DIR = Path(__file__).resolve().parent


class ContextConfig(BaseModel):
    max_input_tokens: int = 3200
    max_tool_output_chars: int = 6000
    keep_last_turns: int = 6


class PermissionConfig(BaseModel):
    max_level: int = 0


class BudgetConfig(BaseModel):
    # 单会话成本上限（元）；None 表示不限，由 CLI/设置层兜底
    max_cost_cny: float | None = None


class ProfileConfig(BaseModel):
    name: str
    description: str = ""
    system_prompt_file: str = "prompt.md"
    model_policy: ModelPolicy
    tools: list[str] = Field(default_factory=list)
    permissions: PermissionConfig = Field(default_factory=PermissionConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    context: ContextConfig = Field(default_factory=ContextConfig)
    eval_suite: str | None = None
    # 回答前必须至少成功调用一次工具（RAG 等「先检索再回答」场景）
    require_tool_before_final: bool = False
    # 最终回答前必须成功调用过的工具（code 等「必须落地修改」场景）
    final_requires_tools: list[str] = Field(default_factory=list)
    tool_config: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    system_prompt: str
    model_policy: ModelPolicy
    tool_names: list[str]
    max_permission: int
    context: ContextConfig
    eval_suite: Path | None
    require_tool_before_final: bool = False
    final_requires_tools: list[str] = field(default_factory=list)
    tool_config: dict[str, Any] = field(default_factory=dict)
    max_cost_cny: float | None = None
    directory: Path = field(default_factory=Path)


def load_profile(name: str, *, project_root: Path) -> Profile:
    directory = _PROFILES_DIR / name
    config_path = directory / "profile.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"场景包不存在: {config_path}")
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    raw["tool_config"] = {
        key: expand_env(value) if isinstance(value, str) else value
        for key, value in (raw.get("tool_config") or {}).items()
    }
    cfg = ProfileConfig.model_validate(raw)
    prompt = (directory / cfg.system_prompt_file).read_text(encoding="utf-8")
    eval_suite = project_root / cfg.eval_suite if cfg.eval_suite else None
    return Profile(
        name=cfg.name,
        description=cfg.description,
        system_prompt=prompt,
        model_policy=cfg.model_policy,
        tool_names=cfg.tools,
        max_permission=cfg.permissions.max_level,
        context=cfg.context,
        eval_suite=eval_suite,
        require_tool_before_final=cfg.require_tool_before_final,
        final_requires_tools=cfg.final_requires_tools,
        tool_config=cfg.tool_config,
        max_cost_cny=cfg.budget.max_cost_cny,
        directory=directory,
    )


def available_profiles() -> list[str]:
    return sorted(
        p.name
        for p in _PROFILES_DIR.iterdir()
        if p.is_dir() and (p / "profile.yaml").exists()
    )
