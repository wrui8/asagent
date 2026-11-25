"""夜间巩固：合并近似条目、时间衰减、归档弱记忆（不物理删除）。

由 ``asagent worker memory --nightly``（计划任务 03:47）触发；只读 PG 中已有
内容，不重新读取会话原文。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .recall import MemoryRecall
from .store import MemoryStore
from .types import MemoryItem
from .vector import MemoryVectorIndex

logger = logging.getLogger(__name__)

MERGE_THRESHOLD = 0.95
DECAY_DAYS = 30
DECAY_FACTOR = 0.98
ARCHIVE_THRESHOLD = 0.1
ARCHIVE_DAYS = 90


def _cosine(a: list[float], b: list[float]) -> float:
    # TEI 输出已归一化，余弦相似度即点积；仍做防御性归一化
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


async def run_nightly(
    *,
    store: MemoryStore,
    vector: MemoryVectorIndex,
    recall: MemoryRecall,
    limit: int = 5000,
) -> dict[str, Any]:
    stats: dict[str, Any] = {"decayed": 0, "archived": 0, "merged": 0, "users": 0}

    stats["decayed"] = await asyncio.to_thread(
        store.decay_stale, days=DECAY_DAYS, factor=DECAY_FACTOR
    )
    stats["archived"] = await asyncio.to_thread(
        store.archive_weak, threshold=ARCHIVE_THRESHOLD, days=ARCHIVE_DAYS
    )

    items = await asyncio.to_thread(store.list_active_all, limit)
    if not items:
        return stats

    grouped: dict[tuple[str, str], list[MemoryItem]] = {}
    for item in items:
        grouped.setdefault((item.user_id, item.type), []).append(item)

    touched_users: set[str] = set()
    for (user_id, _type), group in grouped.items():
        if len(group) < 2:
            continue
        vectors = await asyncio.to_thread(
            vector.embed, [item.content for item in group]
        )
        consumed: set[int] = set()
        for i in range(len(group)):
            if i in consumed:
                continue
            for j in range(i + 1, len(group)):
                if j in consumed:
                    continue
                if _cosine(vectors[i], vectors[j]) < MERGE_THRESHOLD:
                    continue
                winner, loser = (
                    (group[i], group[j])
                    if len(group[i].content) >= len(group[j].content)
                    else (group[j], group[i])
                )
                merged = await asyncio.to_thread(
                    store.merge,
                    winner.id,
                    content=winner.content,
                    reason="nightly-merge",
                    importance=max(winner.importance, loser.importance),
                )
                if merged is None:
                    continue
                await asyncio.to_thread(store.archive, loser.id)
                await asyncio.to_thread(vector.delete, [loser.id])
                consumed.add(j if winner is group[i] else i)
                stats["merged"] += 1
                touched_users.add(user_id)
                if i in consumed:
                    break

    stats["users"] = len(touched_users)
    for user_id in touched_users:
        recall.invalidate(user_id)
    logger.info("记忆夜间巩固完成: %s", stats)
    return stats


__all__ = ["run_nightly"]
