"""会话结束后发布抽取消息（旁路，失败不影响会话）。

每次发布使用短连接：抽取频率低（每用户每日 ≤ 配额），换来 API 进程不必
长期持有 RabbitMQ 连接，RabbitMQ 故障也不会影响对话。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..runtime.state import SessionState, SessionStatus
from .policy import PublishPolicy

logger = logging.getLogger(__name__)

EXCHANGE = "asagent.memory"
QUEUE = "memory.extract"
ROUTING_KEY = "extract"
DLQ = "memory.dlq"

PublishFn = Callable[[dict[str, Any]], Awaitable[None]]


def payload_for(state: SessionState) -> dict[str, Any]:
    return {
        "session_id": state.session_id,
        "owner_id": state.owner_id,
        "profile": state.profile,
        "step": state.step,
    }


class MemoryPublisher:
    def __init__(
        self,
        *,
        policy: PublishPolicy,
        rabbitmq_url: str = "",
        exchange: str = EXCHANGE,
        routing_key: str = ROUTING_KEY,
        publish: PublishFn | None = None,
    ):
        self.policy = policy
        self.rabbitmq_url = rabbitmq_url
        self.exchange = exchange
        self.routing_key = routing_key
        self._publish_fn = publish

    async def maybe_publish(
        self, state: SessionState, owner_id: str
    ) -> tuple[bool, str]:
        if state.status != SessionStatus.DONE:
            return False, "not_done"
        allowed, reason = self.policy.check(state, owner_id)
        if not allowed:
            logger.info(
                "跳过记忆抽取: session=%s reason=%s", state.session_id, reason
            )
            return False, reason
        payload = payload_for(state)
        try:
            if self._publish_fn is not None:
                await self._publish_fn(payload)
            else:
                await publish_amqp(
                    self.rabbitmq_url,
                    exchange=self.exchange,
                    routing_key=self.routing_key,
                    payload=payload,
                )
        except Exception:  # noqa: BLE001 - 旁路失败绝不能影响会话结果
            logger.warning("记忆抽取消息发布失败: %s", state.session_id, exc_info=True)
            return False, "publish_failed"
        return True, ""


async def publish_amqp(
    url: str,
    *,
    exchange: str,
    routing_key: str,
    payload: dict[str, Any],
    timeout: float = 5.0,
) -> None:
    if not url.strip():
        raise RuntimeError("记忆子系统需要 AS_RABBITMQ_URL")
    try:
        import aio_pika
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("记忆抽取需要 aio-pika：请 `uv sync --extra state`") from exc

    connection = await aio_pika.connect_robust(url, timeout=timeout)
    try:
        channel = await connection.channel()
        target = await channel.declare_exchange(
            exchange, aio_pika.ExchangeType.DIRECT, durable=True
        )
        await target.publish(
            aio_pika.Message(
                body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                content_type="application/json",
            ),
            routing_key=routing_key,
        )
    finally:
        await connection.close()


__all__ = [
    "DLQ",
    "EXCHANGE",
    "MemoryPublisher",
    "QUEUE",
    "ROUTING_KEY",
    "payload_for",
    "publish_amqp",
]
