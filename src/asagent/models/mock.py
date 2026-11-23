

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from .types import Message, ModelResponse, ModelSpec, Usage

Responder = Callable[[Sequence[Message]], ModelResponse]


class ScriptedClient:
    """脚本化模型客户端：按顺序返回预设响应，用于测试与离线 eval。"""

    def __init__(
        self,
        spec: ModelSpec,
        responses: Sequence[ModelResponse | Responder],
        *,
        default_text: str = "（脚本已耗尽）",
    ):
        self.spec = spec
        self._responses = list(responses)
        self._default_text = default_text
        self.calls: list[list[Message]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Any] | None = None,
        temperature: float = 0.0,
        max_output_tokens: int | None = None,
    ) -> ModelResponse:
        self.calls.append(list(messages))
        if not self._responses:
            return ModelResponse(
                text=self._default_text,
                finish_reason="stop",
                usage=Usage(input_tokens=10, output_tokens=5),
            )
        item = self._responses.pop(0)
        if callable(item):
            return item(messages)
        return item

    async def aclose(self) -> None:
        return None
