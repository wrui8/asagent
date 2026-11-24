from __future__ import annotations

import sqlite3
from pathlib import Path

from test_runtime import final, make_runtime

from asagent.runtime.checkpoint import CheckpointStore


def test_owner_persisted_and_listed(tmp_path: Path):
    store = CheckpointStore(tmp_path / "state.db")
    alice = store.create_session("rag", "alice 的会话", owner_id="u_alice")
    bob = store.create_session("rag", "bob 的会话", owner_id="u_bob")
    legacy = store.create_session("rag", "CLI 会话")

    assert store.load_state(alice.session_id).owner_id == "u_alice"
    assert store.load_state(legacy.session_id).owner_id is None

    rows = store.list_sessions(owner_id="u_alice")
    assert [r["session_id"] for r in rows] == [alice.session_id]
    assert rows[0]["owner_id"] == "u_alice"

    # 不传 owner_id 表示不做归属过滤（CLI / 管理员视角）
    all_ids = {r["session_id"] for r in store.list_sessions()}
    assert all_ids == {alice.session_id, bob.session_id, legacy.session_id}
    store.close()


def test_legacy_db_gains_owner_column(tmp_path: Path):
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE sessions(
          session_id TEXT PRIMARY KEY,
          profile TEXT NOT NULL,
          title TEXT,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )
    con.execute(
        "INSERT INTO sessions VALUES"
        "('s_old','rag','旧会话','done','2026-01-01','2026-01-01')"
    )
    con.commit()
    con.close()

    store = CheckpointStore(path)
    columns = {row["name"] for row in store._conn.execute("PRAGMA table_info(sessions)")}
    assert "owner_id" in columns

    rows = store.list_sessions(owner_id="u_missing")
    assert rows == []
    assert store.list_sessions()[0]["owner_id"] is None
    store.close()


def test_list_filters_and_pagination(tmp_path: Path):
    store = CheckpointStore(tmp_path / "state.db")
    ids: list[str] = []
    for i in range(6):
        state = store.create_session(
            "rag" if i % 2 else "sql",
            f"会话 {i}",
            owner_id="u1" if i < 4 else "u2",
        )
        ids.append(state.session_id)

    assert {r["session_id"] for r in store.list_sessions(owner_id="u2")} == set(ids[4:])
    assert {r["session_id"] for r in store.list_sessions(profile="sql")} == {
        ids[0], ids[2], ids[4]
    }
    assert len(store.list_sessions(status="running")) == 6
    assert store.list_sessions(status="done") == []

    first = store.list_sessions(limit=4, offset=0)
    second = store.list_sessions(limit=4, offset=4)
    assert len(first) == 4 and len(second) == 2
    merged = {r["session_id"] for r in first + second}
    assert merged == set(ids)
    assert len(first + second) == len(merged)
    store.close()


async def test_runtime_propagates_owner(config, tmp_path):
    runtime, _ = make_runtime(config, tmp_path, [final("完成")])
    result = await runtime.run("测试会话归属", owner_id="u_42")

    state = runtime.store.load_state(result.session_id)
    assert state.owner_id == "u_42"
    rows = runtime.store.list_sessions(owner_id="u_42")
    assert [r["session_id"] for r in rows] == [result.session_id]
    assert runtime.store.list_sessions(owner_id="u_other") == []
    await runtime.aclose()
