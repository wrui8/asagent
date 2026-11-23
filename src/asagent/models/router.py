from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from .types import ModelSpec

if TYPE_CHECKING:
    from asagent.config import AppConfig


class ModelPolicy(BaseModel):
    default: str
    local_first: str | None = None
    escalate_to: str | None = None
    max_escalations: int = 2


class TaskClass(StrEnum):
    LOOKUP = "lookup"
    SQL_SIMPLE = "sql_simple"
    SQL_ANALYTIC = "sql_analytic"
    CODE = "code"
    GENERAL = "general"


class FailureSignal(StrEnum):
    TOOL_ARG_INVALID = "tool_arg_invalid"
    TOOL_SCHEMA_INVALID = "tool_schema_invalid"
    CONTEXT_OVERFLOW = "context_overflow"
    PROVIDER_TIMEOUT = "provider_timeout"
    EMPTY_FINAL = "empty_final"
    GUARD_BLOCK = "guard_block"
    LOOP_DETECTED = "loop_detected"


# 只有「可归因于模型能力」的失败才升级；业务错误交给模型自查
ESCALATABLE = {
    FailureSignal.TOOL_ARG_INVALID,
    FailureSignal.TOOL_SCHEMA_INVALID,
    FailureSignal.CONTEXT_OVERFLOW,
    FailureSignal.PROVIDER_TIMEOUT,
    FailureSignal.EMPTY_FINAL,
    FailureSignal.LOOP_DETECTED,
}

_ANALYTIC_KEYWORDS = (
    "join", "group by", "分组", "排行", "排名", "top", "趋势", "各月", "每年",
    "每个", "各品类", "毛利率", "同比", "环比", "占比", "分布", "多表", "关联",
    "对比", "增长率",
)
_SIMPLE_KEYWORDS = ("多少", "几个", "总数", "数量", "列出", "查询", "哪些", "是否", "count")


class RouteState(BaseModel):
    model_id: str
    tier: str
    attempt: int = 0
    escalations: int = 0
    reason: str = "default"


class ModelRouter:
    """规则优先的路由器：简单任务走本地，分析类/失败升级走云端。"""

    def __init__(self, config: AppConfig, policy: ModelPolicy):
        self._config = config
        self._policy = policy

    # ---- 可用性 ----
    def available(self, model_id: str, *, require_tools: bool = True) -> bool:
        try:
            model = self._config.model(model_id)
        except Exception:
            return False
        if require_tools and not model.supports_tools:
            return False
        return self._config.provider_available(model.provider)

    def resolve(self, state: RouteState) -> ModelSpec:
        model = self._config.model(state.model_id)
        return ModelSpec(id=state.model_id, **model.model_dump())

    # ---- 分类 ----
    def classify(self, text: str) -> TaskClass:
        lowered = text.lower()
        if any(key in lowered for key in _ANALYTIC_KEYWORDS):
            return TaskClass.SQL_ANALYTIC
        if any(key in lowered for key in _SIMPLE_KEYWORDS):
            return TaskClass.SQL_SIMPLE
        if len(text) > 400:
            return TaskClass.GENERAL
        return TaskClass.LOOKUP

    # ---- 决策 ----
    def initial(self, text: str) -> RouteState:
        task = self.classify(text)
        local = self._policy.local_first
        if (
            local
            and task in (TaskClass.LOOKUP, TaskClass.SQL_SIMPLE)
            and self.available(local)
        ):
            return RouteState(model_id=local, tier="local", reason=f"local_first:{task}")

        default = self._policy.default
        if self.available(default):
            return RouteState(
                model_id=default,
                tier=self._config.model(default).tier,
                reason=f"default:{task}",
            )
        # 兜底：找任何一个支持工具的可用模型
        for model_id in self._config.models_file.models:
            if self.available(model_id):
                return RouteState(
                    model_id=model_id,
                    tier=self._config.model(model_id).tier,
                    reason="fallback_available",
                )
        raise RuntimeError(
            "没有可用模型：请在 .env 配置 DEEPSEEK_API_KEY 或 DASHSCOPE_API_KEY，"
            "或启动本地 vLLM"
        )

    def on_failure(self, state: RouteState, signal: FailureSignal) -> RouteState | None:
        if signal not in ESCALATABLE:
            return None
        if state.escalations >= self._policy.max_escalations:
            return None

        target = self._policy.escalate_to or self._policy.default
        if target == state.model_id:
            # 当前就是升级目标，尝试另一个强模型
            target = next(
                (
                    m
                    for m, cfg in self._config.models_file.models.items()
                    if m != state.model_id and cfg.tier == "strong" and self.available(m)
                ),
                None,
            )
        if not target or not self.available(target):
            return None
        return RouteState(
            model_id=target,
            tier=self._config.model(target).tier,
            attempt=state.attempt,
            escalations=state.escalations + 1,
            reason=f"escalate:{signal}",
        )
