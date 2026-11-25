"""记忆抽取 worker：消费 `memory.extract`，重试 3 次后进入 `memory.dlq`。

重试采用进程内指数退避（队列量很低），保证消息处理顺序简单；
worker 崩溃时未 ACK 的消息由 RabbitMQ 重新投递。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .publish import DLQ, EXCHANGE, QUEUE, ROUTING_KEY
from .service import MemoryService

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = 3
RETRY_BASE_S = 2.0

DeadLetterFn = Callable[[bytes, str], Awaitable[None]]


class MemoryWorker:
    def __init__(
        self,
        service: MemoryService,
        *,
        retry_attempts: int = RETRY_ATTEMPTS,
        retry_base_s: float = RETRY_BASE_S,
        dead_letter: DeadLetterFn | None = None,
    ):
        self.service = service
        self.retry_attempts = max(1, retry_attempts)
        self.retry_base_s = max(0.0, retry_base_s)
        self._dead_letter = dead_letter
        self._channel: Any = None

    async def handle_payload(self, raw: bytes) -> dict[str, Any]:
        """处理一条消息；失败重试耗尽后投递 DLQ，返回处理摘要。"""
        try:
            payload = json.loads(raw.decode("utf-8"))
            session_id = str(payload["session_id"])
        except Exception as exc:  # noqa: BLE001 - 坏消息直接进 DLQ
            await self._to_dlq(raw, f"invalid payload: {exc}")
            return {"status": "dlq", "error": str(exc)}

        last_error = ""
        for attempt in range(1, self.retry_attempts + 1):
            try:
                stats = await self.service.extract_session(session_id)
                return {"status": "ok", "attempt": attempt, **stats}
            except Exception as exc:  # noqa: BLE001 - 重试后统一进 DLQ
                last_error = str(exc)
                logger.warning(
                    "记忆抽取失败: session=%s attempt=%s/%s: %s",
                    session_id,
                    attempt,
                    self.retry_attempts,
                    exc,
                )
                if attempt < self.retry_attempts:
                    await asyncio.sleep(self.retry_base_s * 2 ** (attempt - 1))
        await self._to_dlq(raw, last_error)
        return {"status": "dlq", "error": last_error}

    async def _to_dlq(self, raw: bytes, reason: str) -> None:
        try:
            if self._dead_letter is not None:
                await self._dead_letter(raw, reason)
                return
            await self._publish_dlq(raw, reason)
        except Exception:  # noqa: BLE001 - DLQ 失败只记录，不重新抛出
            logger.exception("记忆消息投递 DLQ 失败")

    async def _publish_dlq(self, raw: bytes, reason: str) -> None:
        import aio_pika

        if self._channel is not None:
            await self._channel.default_exchange.publish(
                aio_pika.Message(
                    body=raw,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    headers={"x-error": reason[:500]},
                ),
                routing_key=DLQ,
            )
            return
        connection = await aio_pika.connect_robust(
            self.service.rabbitmq_url, timeout=5.0
        )
        try:
            channel = await connection.channel()
            queue = await channel.declare_queue(DLQ, durable=True)
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=raw,
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    headers={"x-error": reason[:500]},
                ),
                routing_key=queue.name,
            )
        finally:
            await connection.close()

    # ------------------------------------------------------------------ #
    async def run_forever(self) -> None:
        try:
            import aio_pika
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "记忆 worker 需要 aio-pika：请 `uv sync --extra state`"
            ) from exc

        connection = await aio_pika.connect_robust(
            self.service.rabbitmq_url, timeout=10.0
        )
        try:
            channel = await connection.channel()
            # prefetch=1：抽取是低频、成本敏感操作，串行处理便于观察
            await channel.set_qos(prefetch_count=1)
            self._channel = channel
            exchange = await channel.declare_exchange(
                EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True
            )
            await channel.declare_queue(DLQ, durable=True)
            queue = await channel.declare_queue(QUEUE, durable=True)
            await queue.bind(exchange, routing_key=ROUTING_KEY)
            logger.info("记忆 worker 已启动，等待消息: %s", QUEUE)
            async with queue.iterator() as iterator:
                async for message in iterator:
                    async with message.process(requeue=True):
                        result = await self.handle_payload(message.body)
                        logger.info("记忆消息处理结果: %s", result)
        finally:
            self._channel = None
            await connection.close()


async def run_worker(service: MemoryService) -> None:
    await MemoryWorker(service).run_forever()


__all__ = ["MemoryWorker", "RETRY_ATTEMPTS", "run_worker"]
