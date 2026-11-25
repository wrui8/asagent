from __future__ import annotations

from dataclasses import dataclass, field

# 第一版只做四类，避免噪声（见 docs/platform-v2-plan.md §4.3）
MEMORY_TYPES = ("preference", "fact", "decision", "entity")


@dataclass
class MemoryCandidate:
    """抽取模型输出的一条候选记忆。"""

    type: str
    content: str
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class MemoryItem:
    """PG 中的一条记忆。"""

    id: str
    user_id: str
    type: str
    content: str
    importance: float
    confidence: float
    status: str
    source_session: str | None
    created_at: str
    updated_at: str
    last_recalled_at: str | None = None
    recall_count: int = 0
    tags: list[str] = field(default_factory=list)


def item_from_row(row: dict, tags: list[str] | None = None) -> MemoryItem:
    return MemoryItem(
        id=row["id"],
        user_id=row["user_id"],
        type=row["type"],
        content=row["content"],
        importance=float(row["importance"]),
        confidence=float(row["confidence"]),
        status=row["status"],
        source_session=row.get("source_session"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_recalled_at=row.get("last_recalled_at"),
        recall_count=int(row.get("recall_count") or 0),
        tags=list(tags or []),
    )
