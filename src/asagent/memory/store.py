"""记忆仓储：PostgreSQL 结构化存储（阶段 4）。

方法均为同步调用（与 ``PgKernelStore`` 一致），异步调用方用 ``asyncio.to_thread``
包一层，避免阻塞事件循环。向量由 :mod:`asagent.memory.vector` 单独维护。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, Protocol

from .types import MemoryItem, item_from_row


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cutoff(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


class MemoryStore(Protocol):
    def create(
        self,
        *,
        user_id: str,
        type: str,
        content: str,
        confidence: float = 0.5,
        importance: float | None = None,
        source_session: str | None = None,
        source_seq: int | None = None,
        tags: list[str] | None = None,
    ) -> MemoryItem: ...

    def merge(
        self,
        memory_id: str,
        *,
        content: str,
        reason: str = "merge",
        importance: float | None = None,
        confidence: float | None = None,
        tags: list[str] | None = None,
    ) -> MemoryItem | None: ...

    def get(self, memory_id: str) -> MemoryItem | None: ...

    def get_many(self, memory_ids: list[str]) -> list[MemoryItem]: ...

    def list_active(self, user_id: str, limit: int = 50) -> list[MemoryItem]: ...

    def list_active_all(self, limit: int = 5000) -> list[MemoryItem]: ...

    def find_by_session(self, session_id: str) -> list[MemoryItem]: ...

    def touch_recall(self, memory_ids: list[str]) -> None: ...

    def archive(self, memory_id: str) -> bool: ...

    def decay_stale(self, *, days: int = 30, factor: float = 0.98) -> int: ...

    def archive_weak(self, *, threshold: float = 0.1, days: int = 90) -> int: ...

    def stats(self) -> dict[str, Any]: ...


class PgMemoryStore:
    def __init__(self, pool: Any):
        self.pool = pool

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def create(
        self,
        *,
        user_id: str,
        type: str,
        content: str,
        confidence: float = 0.5,
        importance: float | None = None,
        source_session: str | None = None,
        source_seq: int | None = None,
        tags: list[str] | None = None,
    ) -> MemoryItem:
        memory_id = f"m_{uuid.uuid4().hex[:12]}"
        ts = _now()
        level = importance if importance is not None else max(0.1, min(1.0, confidence))
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO memories(id, user_id, type, content, importance,"
                " confidence, status, source_session, source_seq, created_at,"
                " updated_at) VALUES(%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s)",
                (
                    memory_id,
                    user_id,
                    type,
                    content,
                    float(level),
                    float(confidence),
                    source_session,
                    source_seq,
                    ts,
                    ts,
                ),
            )
            conn.execute(
                "INSERT INTO memory_versions(memory_id, content, reason, created_at)"
                " VALUES(%s,%s,'create',%s)",
                (memory_id, content, ts),
            )
            self._insert_tags(conn, memory_id, tags or [])
        item = self.get(memory_id)
        assert item is not None
        return item

    def merge(
        self,
        memory_id: str,
        *,
        content: str,
        reason: str = "merge",
        importance: float | None = None,
        confidence: float | None = None,
        tags: list[str] | None = None,
    ) -> MemoryItem | None:
        ts = _now()
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id=%s", (memory_id,)
            ).fetchone()
            if row is None:
                return None
            new_importance = (
                float(importance)
                if importance is not None
                else max(float(row["importance"]), 0.5)
            )
            new_confidence = (
                float(confidence)
                if confidence is not None
                else float(row["confidence"])
            )
            conn.execute(
                "UPDATE memories SET content=%s, importance=%s, confidence=%s,"
                " updated_at=%s WHERE id=%s",
                (content, new_importance, new_confidence, ts, memory_id),
            )
            conn.execute(
                "INSERT INTO memory_versions(memory_id, content, reason, created_at)"
                " VALUES(%s,%s,%s,%s)",
                (memory_id, content, reason, ts),
            )
            self._insert_tags(conn, memory_id, tags or [])
        return self.get(memory_id)

    def archive(self, memory_id: str) -> bool:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE memories SET status='archived', updated_at=%s WHERE id=%s",
                (_now(), memory_id),
            )
        return cur.rowcount > 0

    def touch_recall(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE memories SET last_recalled_at=%s,"
                " recall_count=recall_count+1 WHERE id = ANY(%s)",
                (_now(), list(memory_ids)),
            )

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def get(self, memory_id: str) -> MemoryItem | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id=%s", (memory_id,)
            ).fetchone()
            if row is None:
                return None
            tags = [
                r["tag"]
                for r in conn.execute(
                    "SELECT tag FROM memory_tags WHERE memory_id=%s ORDER BY tag",
                    (memory_id,),
                ).fetchall()
            ]
        return item_from_row(dict(row), tags)

    def get_many(self, memory_ids: list[str]) -> list[MemoryItem]:
        if not memory_ids:
            return []
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE id = ANY(%s)",
                (list(memory_ids),),
            ).fetchall()
            tags: dict[str, list[str]] = {}
            for r in conn.execute(
                "SELECT memory_id, tag FROM memory_tags WHERE memory_id = ANY(%s)",
                (list(memory_ids),),
            ).fetchall():
                tags.setdefault(r["memory_id"], []).append(r["tag"])
        by_id = {
            row["id"]: item_from_row(dict(row), tags.get(row["id"], []))
            for row in rows
        }
        # 保持入参顺序，方便调用方与向量结果对齐
        return [by_id[mid] for mid in memory_ids if mid in by_id]

    def list_active(self, user_id: str, limit: int = 50) -> list[MemoryItem]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE user_id=%s AND status='active'"
                " ORDER BY importance DESC, updated_at DESC LIMIT %s",
                (user_id, max(1, limit)),
            ).fetchall()
        return [item_from_row(dict(row)) for row in rows]

    def list_active_all(self, limit: int = 5000) -> list[MemoryItem]:
        """夜间巩固用：跨用户读取活跃记忆（按用户分组在调用方完成）。"""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE status='active'"
                " ORDER BY user_id, type, importance DESC LIMIT %s",
                (max(1, limit),),
            ).fetchall()
        return [item_from_row(dict(row)) for row in rows]

    def find_by_session(self, session_id: str) -> list[MemoryItem]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE source_session=%s ORDER BY created_at",
                (session_id,),
            ).fetchall()
        return [item_from_row(dict(row)) for row in rows]

    def versions(self, memory_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, memory_id, content, reason, created_at FROM memory_versions"
                " WHERE memory_id=%s ORDER BY id DESC LIMIT %s",
                (memory_id, max(1, limit)),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ #
    # 夜间巩固
    # ------------------------------------------------------------------ #
    def decay_stale(self, *, days: int = 30, factor: float = 0.98) -> int:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE memories SET importance=GREATEST(0.0, importance*%s)"
                " WHERE status='active' AND COALESCE(last_recalled_at, created_at) < %s",
                (float(factor), _cutoff(days)),
            )
        return int(cur.rowcount or 0)

    def archive_weak(self, *, threshold: float = 0.1, days: int = 90) -> int:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE memories SET status='archived', updated_at=%s"
                " WHERE status='active' AND importance < %s"
                " AND COALESCE(last_recalled_at, created_at) < %s",
                (_now(), float(threshold), _cutoff(days)),
            )
        return int(cur.rowcount or 0)

    def stats(self) -> dict[str, Any]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT status, type, COUNT(*) AS n FROM memories"
                " GROUP BY status, type"
            ).fetchall()
        total = sum(int(r["n"]) for r in rows)
        by_status: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for row in rows:
            by_status[row["status"]] = by_status.get(row["status"], 0) + int(row["n"])
            by_type[row["type"]] = by_type.get(row["type"], 0) + int(row["n"])
        return {"total": total, "by_status": by_status, "by_type": by_type}

    # ------------------------------------------------------------------ #
    @staticmethod
    def _insert_tags(conn: Any, memory_id: str, tags: list[str]) -> None:
        for tag in dict.fromkeys(t.strip()[:20] for t in tags if t and t.strip()):
            conn.execute(
                "INSERT INTO memory_tags(memory_id, tag) VALUES(%s,%s)"
                " ON CONFLICT DO NOTHING",
                (memory_id, tag),
            )


__all__ = ["MemoryStore", "PgMemoryStore"]
