"""记忆召回：向量检索 → 重排 → 摘要 → Redis 缓存。

摘要作为「参考信息」注入系统上下文，不覆盖用户当前输入；任何异常由调用方
（运行时）吞掉并降级为无记忆，绝不影响对话主链路。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from ..storage.redis_state import RedisState
from .store import MemoryStore
from .types import MemoryItem
from .vector import MemoryVectorIndex

logger = logging.getLogger(__name__)

CACHE_TTL_S = 900
HALF_LIFE_DAYS = 90.0
HEADER = '<long_term_memory note="以下为历史记忆，可能过时，冲突时以当前输入为准">'
FOOTER = "</long_term_memory>"
MAX_CONTENT_CHARS = 160


def recency_decay(value: str | None, *, now: datetime | None = None) -> float:
    """90 天半衰期的时间衰减，下限 0.1；时间缺失按 1.0 处理。"""
    if not value:
        return 1.0
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return 1.0
    current = now or datetime.now()
    age_days = max(0.0, (current - stamp).total_seconds() / 86400.0)
    return max(0.1, 0.5 ** (age_days / HALF_LIFE_DAYS))


def score_item(item: MemoryItem, similarity: float, *, now: datetime | None = None) -> float:
    """相似度 × 重要度 × 时间衰减。"""
    sim = min(1.0, max(0.0, float(similarity)))
    importance = min(1.0, max(0.0, item.importance))
    return sim * (0.5 + 0.5 * importance) * recency_decay(
        item.last_recalled_at or item.updated_at, now=now
    )


def render_section(items: list[MemoryItem]) -> str:
    lines = [HEADER]
    for item in items:
        content = item.content.replace("\n", " ").strip()
        if len(content) > MAX_CONTENT_CHARS:
            content = content[:MAX_CONTENT_CHARS] + "…"
        lines.append(f"- [{item.type}] {content}")
    lines.append(FOOTER)
    return "\n".join(lines)


def _cache_key(user_id: str) -> str:
    return f"mem:{user_id}:summary"


class MemoryRecall:
    def __init__(
        self,
        *,
        store: MemoryStore,
        vector: MemoryVectorIndex,
        redis: RedisState | None = None,
        top_k: int = 5,
        fetch_limit: int = 20,
        cache_ttl_s: int = CACHE_TTL_S,
    ):
        self.store = store
        self.vector = vector
        self.redis = redis
        self.top_k = max(1, top_k)
        self.fetch_limit = max(self.top_k, fetch_limit)
        self.cache_ttl_s = cache_ttl_s

    # ------------------------------------------------------------------ #
    async def section(self, user_id: str | None, query: str) -> str | None:
        if not user_id or not query.strip():
            return None
        return await asyncio.to_thread(self._section_sync, user_id, query)

    def _section_sync(self, user_id: str, query: str) -> str | None:
        cached = self._cache_get(user_id)
        if cached is not None:
            return cached or None

        hits = self.vector.search_text(query, user_id, limit=self.fetch_limit)
        if not hits:
            return None
        items = {item.id: item for item in self.store.get_many([h.memory_id for h in hits])}
        ranked: list[tuple[float, MemoryItem]] = []
        for hit in hits:
            item = items.get(hit.memory_id)
            if item is None or item.status != "active" or item.user_id != user_id:
                continue
            ranked.append((score_item(item, hit.similarity), item))
        if not ranked:
            return None
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        chosen = [item for _, item in ranked[: self.top_k]]
        text = render_section(chosen)
        self._cache_set(user_id, text)
        try:
            self.store.touch_recall([item.id for item in chosen])
        except Exception:  # noqa: BLE001 - 召回计数失败不影响注入
            logger.warning("记忆召回计数失败: %s", user_id, exc_info=True)
        return text

    # ------------------------------------------------------------------ #
    def invalidate(self, user_id: str) -> None:
        """记忆变更后主动失效摘要缓存；Redis 不可用时忽略。"""
        if self.redis is None:
            return
        try:
            self.redis.delete(_cache_key(user_id))
        except Exception:  # noqa: BLE001
            logger.warning("记忆摘要缓存失效失败: %s", user_id, exc_info=True)

    def _cache_get(self, user_id: str) -> str | None:
        if self.redis is None:
            return None
        try:
            value: Any = self.redis.get(_cache_key(user_id))
        except Exception:  # noqa: BLE001 - 缓存故障降级为直接检索
            logger.warning("记忆摘要缓存读取失败: %s", user_id, exc_info=True)
            return None
        return value if isinstance(value, str) else None

    def _cache_set(self, user_id: str, text: str) -> None:
        if self.redis is None:
            return
        try:
            self.redis.set(_cache_key(user_id), text, ttl_s=self.cache_ttl_s)
        except Exception:  # noqa: BLE001
            logger.warning("记忆摘要缓存写入失败: %s", user_id, exc_info=True)


__all__ = [
    "CACHE_TTL_S",
    "FOOTER",
    "HEADER",
    "MemoryRecall",
    "recency_decay",
    "render_section",
    "score_item",
]
