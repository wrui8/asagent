from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from .types import Message, ModelResponse, ModelSpec

if TYPE_CHECKING:
    from asagent.config import AppConfig


class ModelError(Exception):
    """模型调用失败的统一异常。"""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class ContextOverflowError(ModelError):
    """上下文超出模型窗口。"""


class ModelClient(Protocol):
    spec: ModelSpec

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        tools: Sequence[Any] | None = None,
        temperature: float = 0.0,
        max_output_tokens: int | None = None,
    ) -> ModelResponse: ...

    async def aclose(self) -> None: ...


def build_client(config: AppConfig, model_id: str) -> ModelClient:
    """按模型注册表构建客户端（延迟导入，避免配置层与网络层互相依赖）。"""
    from .openai_compat import OpenAICompatClient

    model = config.model(model_id)
    provider = config.provider(model.provider)
    api_key = config.api_key(model.provider) or "EMPTY"
    spec = ModelSpec(id=model_id, **model.model_dump())
    return OpenAICompatClient(spec=spec, base_url=provider.base_url, api_key=api_key,
                              timeout_s=provider.timeout_s)
