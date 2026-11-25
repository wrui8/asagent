"""记忆写入：向量相似度合并（> 0.92）后落 PG 并 upsert Milvus。"""

from __future__ import annotations

import asyncio
import logging

from .store import MemoryStore
from .types import MemoryCandidate
from .vector import MemoryVectorIndex

logger = logging.getLogger(__name__)

MERGE_THRESHOLD = 0.92
MAX_NEIGHBORS = 5


class MemoryWriter:
    def __init__(
        self,
        *,
        store: MemoryStore,
        vector: MemoryVectorIndex,
        merge_threshold: float = MERGE_THRESHOLD,
    ):
        self.store = store
        self.vector = vector
        self.merge_threshold = merge_threshold

    async def save(
        self,
        user_id: str,
        candidates: list[MemoryCandidate],
        *,
        source_session: str | None = None,
    ) -> dict[str, int]:
        created = merged = 0
        for cand in candidates:
            vector = (await asyncio.to_thread(self.vector.embed, [cand.content]))[0]
            hits = await asyncio.to_thread(
                self.vector.query, vector, user_id, limit=MAX_NEIGHBORS
            )
            items = {
                item.id: item
                for item in await asyncio.to_thread(
                    self.store.get_many, [hit.memory_id for hit in hits]
                )
            }
            neighbors = sorted(hits, key=lambda h: h.similarity, reverse=True)
            target = next(
                (
                    items[hit.memory_id]
                    for hit in neighbors
                    if hit.similarity >= self.merge_threshold
                    and hit.memory_id in items
                    and items[hit.memory_id].type == cand.type
                    and items[hit.memory_id].status == "active"
                ),
                None,
            )
            if target is not None:
                content = (
                    cand.content
                    if len(cand.content) >= len(target.content)
                    else target.content
                )
                memory_id = target.id
                await asyncio.to_thread(
                    self.store.merge,
                    memory_id,
                    content=content,
                    reason="merge",
                    importance=max(target.importance, cand.confidence),
                    confidence=max(target.confidence, cand.confidence),
                    tags=cand.tags,
                )
                merged += 1
            else:
                item = await asyncio.to_thread(
                    self.store.create,
                    user_id=user_id,
                    type=cand.type,
                    content=cand.content,
                    confidence=cand.confidence,
                    source_session=source_session,
                    tags=cand.tags,
                )
                memory_id = item.id
                created += 1
            await asyncio.to_thread(
                self.vector.upsert,
                [
                    {
                        "memory_id": memory_id,
                        "user_id": user_id,
                        "type": cand.type,
                        "dense": vector,
                    }
                ],
            )
        return {"created": created, "merged": merged}


__all__ = ["MERGE_THRESHOLD", "MemoryWriter"]
