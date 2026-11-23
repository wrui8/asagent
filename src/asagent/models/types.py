from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

JSON = dict[str, Any]


class Pricing(BaseModel):
    """模型价格，单位：元 / 百万 token（相对成本参考，以厂商官网为准）。"""

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    currency: str = "CNY"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: JSON = Field(default_factory=dict)
    # 原始参数文本；JSON 解析失败时保留，便于修复重试与审计
    raw_arguments: str | None = None


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_call_id: str | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
        )


class ModelResponse(BaseModel):
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: Literal["stop", "length", "tool_calls", "content_filter", "error"] = "stop"
    usage: Usage = Field(default_factory=Usage)
    request_id: str | None = None
    latency_ms: int = 0


class ModelSpec(BaseModel):
    id: str
    provider: str
    upstream_name: str
    tier: Literal["local", "fast", "strong"] = "strong"
    context_window: int = 32768
    max_output_tokens: int = 4096
    supports_tools: bool = True
    pricing: Pricing = Field(default_factory=Pricing)
