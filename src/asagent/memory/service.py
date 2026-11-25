"""记忆服务装配：把仓储、向量、抽取、召回与写入组合起来。

启用条件（fail-closed）：``AS_MEMORY_ENABLED=true`` 时必须同时具备
postgres 存储、Redis、RabbitMQ 与 Milvus/TEI；缺任何一项直接报错，
避免静默降级成「看起来开启但什么也不做」。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import AppConfig
from ..models.client import ModelClient, build_client
from ..storage.factory import Storage
from ..storage.protocols import KernelStore
from ..storage.redis_state import RedisState
from .extractor import MemoryExtractor
from .recall import MemoryRecall
from .store import PgMemoryStore
from .vector import MemoryVectorIndex
from .writer import MemoryWriter

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(
        self,
        *,
        config: AppConfig,
        kernel: KernelStore,
        store: PgMemoryStore,
        vector: MemoryVectorIndex,
        extractor: MemoryExtractor,
        writer: MemoryWriter,
        recall: MemoryRecall,
        rabbitmq_url: str,
    ):
        self.config = config
        self.kernel = kernel
        self.store = store
        self.vector = vector
        self.extractor = extractor
        self.writer = writer
        self.recall = recall
        self.rabbitmq_url = rabbitmq_url

    # ------------------------------------------------------------------ #
    async def extract_session(self, session_id: str) -> dict[str, Any]:
        """消费一条抽取消息：读取会话事件 → 抽取 → 合并写入 → 失效缓存。"""
        try:
            state = await asyncio.to_thread(self.kernel.load_state, session_id)
        except KeyError:
            return {"skipped": "missing"}
        if not state.owner_id:
            return {"skipped": "no_owner"}
        existing = await asyncio.to_thread(self.store.find_by_session, session_id)
        if existing:
            return {"skipped": "already_extracted"}

        events = await asyncio.to_thread(self.kernel.events, session_id)
        user_inputs = [
            str(e.payload.get("text", ""))
            for e in events
            if e.type == "user_input" and e.payload.get("text")
        ]
        final_text = next(
            (
                str(e.payload.get("text", ""))
                for e in reversed(events)
                if e.type == "final"
            ),
            "",
        )
        if len(user_inputs) < 3:
            return {"skipped": "too_short"}

        candidates = await self.extractor.extract(
            user_inputs=user_inputs, assistant_output=final_text
        )
        if not candidates:
            return {"skipped": "nothing"}
        stats = await self.writer.save(
            state.owner_id, candidates, source_session=session_id
        )
        self.recall.invalidate(state.owner_id)
        logger.info(
            "记忆抽取完成: session=%s user=%s %s",
            session_id,
            state.owner_id,
            stats,
        )
        return stats

    async def aclose(self) -> None:
        await self.extractor.aclose()
        self.vector.close()


def build_memory_service(
    config: AppConfig,
    storage: Storage,
    redis: RedisState | None,
    *,
    client_factory: Any = build_client,
) -> MemoryService | None:
    """按开关装配；关闭时返回 None（所有调用方保持 v1 行为）。"""
    settings = config.settings
    if not settings.as_memory_enabled:
        return None
    if storage.backend != "postgres" or storage.pool is None:
        raise ValueError("AS_MEMORY_ENABLED=true 需要 AS_STORAGE_BACKEND=postgres")
    if redis is None:
        raise ValueError("AS_MEMORY_ENABLED=true 需要配置 AS_REDIS_URL")
    if not settings.as_rabbitmq_url.strip():
        raise ValueError("AS_MEMORY_ENABLED=true 需要配置 AS_RABBITMQ_URL")
    if not settings.as_milvus_uri.strip() or not settings.as_tei_url.strip():
        raise ValueError(
            "AS_MEMORY_ENABLED=true 需要配置 AS_MILVUS_URI 与 AS_TEI_URL"
        )

    store = PgMemoryStore(storage.pool)
    vector = MemoryVectorIndex(
        milvus_uri=settings.as_milvus_uri,
        tei_url=settings.as_tei_url,
        embedding_dim=settings.as_embedding_dim,
    )
    client: ModelClient = client_factory(config, settings.as_memory_model)
    extractor = MemoryExtractor(client)
    writer = MemoryWriter(store=store, vector=vector)
    recall = MemoryRecall(
        store=store,
        vector=vector,
        redis=redis,
        top_k=settings.as_memory_top_k,
    )
    return MemoryService(
        config=config,
        kernel=storage.kernel,
        store=store,
        vector=vector,
        extractor=extractor,
        writer=writer,
        recall=recall,
        rabbitmq_url=settings.as_rabbitmq_url,
    )


__all__ = ["MemoryService", "build_memory_service"]
