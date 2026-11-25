"""记忆仓储（阶段 4）：PostgreSQL 往返用例。

未设置 AS_TEST_PG_DSN 时整文件跳过；CI 用 service container 提供。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta

import pytest

DSN = os.environ.get("AS_TEST_PG_DSN", "").strip()

pytestmark = pytest.mark.skipif(not DSN, reason="未设置 AS_TEST_PG_DSN，跳过 PostgreSQL 测试")

TABLES = ("memory_tags", "memory_versions", "memories")


@pytest.fixture()
def pool():
    from asagent.storage.pg import PgPool, run_migrations

    p = PgPool(DSN)
    run_migrations(p)
    with p.connection() as conn:
        conn.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
    yield p
    p.close()


@pytest.fixture()
def store(pool):
    from asagent.memory.store import PgMemoryStore

    return PgMemoryStore(pool)


def test_create_get_merge_and_versions(store) -> None:
    created = store.create(
        user_id="u1",
        type="preference",
        content="用户偏好表格形式的回答",
        confidence=0.9,
        source_session="s1",
        tags=["风格", "格式"],
    )
    assert created.importance == pytest.approx(0.9)
    assert sorted(created.tags) == ["格式", "风格"]

    loaded = store.get(created.id)
    assert loaded is not None and loaded.content == created.content
    assert [i.id for i in store.get_many([created.id, "missing"])] == [created.id]
    assert store.get_many([]) == []
    assert [i.id for i in store.find_by_session("s1")] == [created.id]
    assert store.list_active("u1")[0].id == created.id
    assert [i.id for i in store.list_active_all()] == [created.id]

    merged = store.merge(
        created.id,
        content="用户偏好表格形式、要点式回答",
        importance=1.0,
        tags=["格式"],
    )
    assert merged is not None
    assert merged.content == "用户偏好表格形式、要点式回答"
    assert merged.importance == pytest.approx(1.0)
    assert sorted(merged.tags) == ["格式", "风格"]
    reasons = [row["reason"] for row in store.versions(created.id)]
    assert reasons == ["merge", "create"]


def test_touch_recall_archive_and_stats(store) -> None:
    created = store.create(
        user_id="u1", type="fact", content="团队使用 PostgreSQL 16", confidence=0.8
    )
    store.touch_recall([created.id])
    reloaded = store.get(created.id)
    assert reloaded is not None
    assert reloaded.recall_count == 1
    assert reloaded.last_recalled_at is not None

    stats = store.stats()
    assert stats["total"] == 1
    assert stats["by_status"] == {"active": 1}
    assert stats["by_type"] == {"fact": 1}

    assert store.archive(created.id) is True
    assert store.archive("missing") is False
    assert store.list_active("u1") == []
    archived = store.get(created.id)
    assert archived is not None and archived.status == "archived"


def test_decay_and_archive_weak(store, pool) -> None:
    stale = store.create(
        user_id="u2",
        type="fact",
        content="很久没有被召回的旧记忆",
        confidence=0.5,
    )
    fresh = store.create(
        user_id="u2", type="fact", content="最近产生的记忆", confidence=0.9
    )
    old_stamp = (datetime.now() - timedelta(days=120)).isoformat(timespec="seconds")
    with pool.connection() as conn:
        conn.execute(
            "UPDATE memories SET importance=0.05, created_at=%s WHERE id=%s",
            (old_stamp, stale.id),
        )

    assert store.decay_stale(days=30, factor=0.98) == 1
    decayed = store.get(stale.id)
    assert decayed is not None
    assert decayed.importance == pytest.approx(0.05 * 0.98)

    assert store.archive_weak(threshold=0.1, days=90) == 1
    assert store.get(stale.id).status == "archived"
    assert store.get(fresh.id).status == "active"
