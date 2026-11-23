from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from typing import Any

import json_repair
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)

from .client import ContextOverflowError, ModelError
from .types import Message, ModelResponse, ModelSpec, ToolCall, Usage

_FINISH_MAP = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "tool_calls",
    "function_call": "tool_calls",
    "content_filter": "content_filter",
}


def _message_to_wire(m: Message) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "content": m.content or "", "tool_call_id": m.tool_call_id}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                    },
                }
                for tc in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content or ""}


def _parse_arguments(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        try:
            value = json_repair.loads(raw)
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}


class OpenAICompatClient:
    """OpenAI 兼容客户端：DeepSeek / 阿里云百炼 / vLLM 共用。"""

    def __init__(
        self,
        *,
        spec: ModelSpec,
        base_url: str,
        api_key: str,
        timeout_s: float = 120.0,
    ):
        self.spec = spec
        self._timeout_s = timeout_s
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_s,
            max_retries=0,  # 重试由本项目控制，便于记录与路由
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Any] | None = None,
        temperature: float = 0.0,
        max_output_tokens: int | None = None,
    ) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": self.spec.upstream_name,
            "messages": [_message_to_wire(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_output_tokens or self.spec.max_output_tokens,
        }
        if tools and self.spec.supports_tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": s.name,
                        "description": s.description,
                        "parameters": s.parameters,
                    },
                }
                for s in tools
            ]
            kwargs["tool_choice"] = "auto"

        started = time.perf_counter()
        response = await self._request_with_retry(kwargs)
        latency_ms = int((time.perf_counter() - started) * 1000)

        choice = response.choices[0] if response.choices else None
        if choice is None:
            raise ModelError("模型未返回任何候选结果")

        msg = choice.message
        tool_calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            raw = tc.function.arguments
            tool_calls.append(
                ToolCall(
                    id=tc.id or f"call_{len(tool_calls)}",
                    name=tc.function.name,
                    arguments=_parse_arguments(raw),
                    raw_arguments=raw,
                )
            )

        usage = Usage(
            input_tokens=getattr(response.usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(response.usage, "completion_tokens", 0) or 0,
            cached_input_tokens=getattr(
                getattr(response.usage, "prompt_tokens_details", None), "cached_tokens", 0
            )
            or 0,
        )
        finish = _FINISH_MAP.get(choice.finish_reason or "stop", "stop")
        if tool_calls and finish == "stop":
            finish = "tool_calls"

        return ModelResponse(
            text=msg.content,
            tool_calls=tool_calls,
            finish_reason=finish,  # type: ignore[arg-type]
            usage=usage,
            request_id=getattr(response, "id", None),
            latency_ms=latency_ms,
        )

    async def _request_with_retry(self, kwargs: dict[str, Any]) -> Any:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await self._client.chat.completions.create(**kwargs)
            except (APITimeoutError, APIConnectionError, RateLimitError) as exc:
                last_error = exc
            except APIStatusError as exc:
                if exc.status_code >= 500:
                    last_error = exc
                elif exc.status_code == 400 and _looks_like_context_overflow(str(exc)):
                    raise ContextOverflowError(str(exc), status_code=400) from exc
                else:
                    raise ModelError(str(exc), status_code=exc.status_code) from exc
            if attempt < 2:
                await asyncio.sleep(1.5**attempt)
        raise ModelError(
            f"模型请求重试后仍失败: {last_error}", retryable=True
        ) from last_error


def _looks_like_context_overflow(text: str) -> bool:
    lowered = text.lower()
    return any(
        key in lowered
        for key in ("context length", "maximum context", "too many tokens", "context window")
    )
