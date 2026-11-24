from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from ..models.types import ToolCall
from ..tools.base import ToolResult
from .events import AgentEvent
from .state import SessionState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY,
  profile TEXT NOT NULL,
  title TEXT,
  status TEXT NOT NULL,
  owner_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);
CREATE TABLE IF NOT EXISTS checkpoints(
  session_id TEXT NOT NULL,
  step INTEGER NOT NULL,
  state_json TEXT NOT NULL,
  last_seq INTEGER NOT NULL,
  ts TEXT NOT NULL,
  PRIMARY KEY(session_id, step)
);
CREATE TABLE IF NOT EXISTS tool_invocations(
  session_id TEXT NOT NULL,
  call_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args_json TEXT NOT NULL,
  permission INTEGER NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  PRIMARY KEY(session_id, call_id)
);
CREATE TABLE IF NOT EXISTS artifacts(
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  meta_json TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS approvals(
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  call_id TEXT NOT NULL,
  status TEXT NOT NULL,
  decided_at TEXT
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ToolInvocation(BaseModel):
    session_id: str
    call_id: str
    tool: str
    args_json: str
    permission: int
    status: str
    result_json: str | None = None
    started_at: str = ""
    finished_at: str | None = None


class CheckpointStore:
    """SQLite 事件溯源：事件流 + 步级快照 + 工具幂等记录。"""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        # 轻量迁移：已有库补 owner_id 列（多用户 API 层使用）
        self._ensure_column("sessions", "owner_id", "TEXT")
        self._conn.commit()

    def _ensure_column(self, table: str, column: str, decl: str) -> None:
        columns = {
            row["name"] for row in self._conn.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def close(self) -> None:
        self._conn.close()

    # ---- 会话与快照 ----
    def create_session(
        self,
        profile: str,
        title: str | None = None,
        owner_id: str | None = None,
    ) -> SessionState:
        session_id = f"s_{uuid.uuid4().hex[:12]}"
        ts = _now()
        state = SessionState(
            session_id=session_id,
            profile=profile,
            title=title,
            owner_id=owner_id,
            created_at=ts,
            updated_at=ts,
        )
        self._conn.execute(
            "INSERT INTO sessions"
            "(session_id, profile, title, status, owner_id, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (session_id, profile, title, str(state.status), owner_id, ts, ts),
        )
        self._conn.commit()
        self.save_state(state)
        return state

    def save_state(self, state: SessionState) -> None:
        state.updated_at = _now()
        payload = state.model_dump_json()
        self._conn.execute(
            "INSERT OR REPLACE INTO sessions"
            "(session_id, profile, title, status, owner_id, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                state.session_id,
                state.profile,
                state.title,
                str(state.status),
                state.owner_id,
                state.created_at,
                state.updated_at,
            ),
        )
        self._conn.execute(
            "INSERT OR REPLACE INTO checkpoints(session_id, step, state_json, last_seq, ts)"
            " VALUES(?,?,?,?,?)",
            (state.session_id, state.step, payload, state.last_seq, state.updated_at),
        )
        self._conn.commit()

    def load_state(self, session_id: str) -> SessionState:
        row = self._conn.execute(
            "SELECT state_json FROM checkpoints WHERE session_id=? ORDER BY step DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"会话不存在: {session_id}")
        return SessionState.model_validate_json(row["state_json"])

    def list_sessions(
        self,
        limit: int = 20,
        offset: int = 0,
        *,
        owner_id: str | None = None,
        profile: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """按归属/场景/状态筛选会话（API 层分页使用）。"""
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("owner_id", owner_id),
            ("profile", profile),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{column}=?")
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        rows = self._conn.execute(
            "SELECT session_id, profile, title, status, owner_id, created_at, updated_at"
            f" FROM sessions{where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- 事件流 ----
    def append(self, session_id: str, type: str, payload: dict[str, Any]) -> AgentEvent:
        ts = _now()
        cur = self._conn.execute(
            "INSERT INTO events(session_id, ts, type, payload_json) VALUES(?,?,?,?)",
            (session_id, ts, type, json.dumps(payload, ensure_ascii=False)),
        )
        self._conn.commit()
        return AgentEvent(
            seq=cur.lastrowid or 0, ts=ts, session_id=session_id, type=type, payload=payload
        )

    def events(self, session_id: str, after_seq: int = 0) -> list[AgentEvent]:
        rows = self._conn.execute(
            "SELECT seq, ts, session_id, type, payload_json FROM events"
            " WHERE session_id=? AND seq>? ORDER BY seq",
            (session_id, after_seq),
        ).fetchall()
        return [
            AgentEvent(
                seq=r["seq"],
                ts=r["ts"],
                session_id=r["session_id"],
                type=r["type"],
                payload=json.loads(r["payload_json"]),
            )
            for r in rows
        ]

    def recent_events_of_type(self, type: str, limit: int = 50) -> list[AgentEvent]:
        """最近的指定类型事件（时间升序，便于表格展示）。"""
        rows = self._conn.execute(
            "SELECT seq, ts, session_id, type, payload_json FROM events"
            " WHERE type=? ORDER BY seq DESC LIMIT ?",
            (type, limit),
        ).fetchall()
        events = [
            AgentEvent(
                seq=r["seq"],
                ts=r["ts"],
                session_id=r["session_id"],
                type=r["type"],
                payload=json.loads(r["payload_json"]),
            )
            for r in rows
        ]
        return list(reversed(events))

    # ---- 工具幂等 ----
    def begin_tool(self, session_id: str, call: ToolCall, permission: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO tool_invocations"
            "(session_id, call_id, tool, args_json, permission, status, started_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (
                session_id,
                call.id,
                call.name,
                json.dumps(call.arguments, ensure_ascii=False),
                permission,
                "started",
                _now(),
            ),
        )
        self._conn.commit()

    def finish_tool(self, session_id: str, call_id: str, result: ToolResult) -> None:
        self._conn.execute(
            "UPDATE tool_invocations SET status=?, result_json=?, finished_at=?"
            " WHERE session_id=? AND call_id=?",
            (
                "finished" if result.ok else "failed",
                result.model_dump_json(),
                _now(),
                session_id,
                call_id,
            ),
        )
        self._conn.commit()

    def pending_invocations(self, session_id: str) -> list[ToolInvocation]:
        rows = self._conn.execute(
            "SELECT * FROM tool_invocations WHERE session_id=? AND status='started'",
            (session_id,),
        ).fetchall()
        return [ToolInvocation(**dict(r)) for r in rows]

    def finished_invocations(self, session_id: str) -> list[ToolInvocation]:
        rows = self._conn.execute(
            "SELECT * FROM tool_invocations"
            " WHERE session_id=? AND status IN ('finished','failed') ORDER BY rowid",
            (session_id,),
        ).fetchall()
        return [ToolInvocation(**dict(r)) for r in rows]

    # ---- 人工审批 ----
    def request_approval(self, session_id: str, call_id: str) -> None:
        """登记一次待审批的工具调用；重复请求保持一条记录并重置为 pending。"""
        self._conn.execute(
            "DELETE FROM approvals WHERE session_id=? AND call_id=?",
            (session_id, call_id),
        )
        self._conn.execute(
            "INSERT INTO approvals(id, session_id, call_id, status, decided_at)"
            " VALUES(?,?,?,?,?)",
            (f"ap_{uuid.uuid4().hex[:12]}", session_id, call_id, "pending", None),
        )
        self._conn.commit()

    def decide_approval(self, session_id: str, call_id: str, approved: bool) -> None:
        self._conn.execute(
            "UPDATE approvals SET status=?, decided_at=?"
            " WHERE session_id=? AND call_id=?",
            ("approved" if approved else "denied", _now(), session_id, call_id),
        )
        self._conn.commit()

    def approval_status(self, session_id: str, call_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM approvals WHERE session_id=? AND call_id=?"
            " ORDER BY rowid DESC LIMIT 1",
            (session_id, call_id),
        ).fetchone()
        return row["status"] if row else None

    def list_approvals(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT id, session_id, call_id, status, decided_at FROM approvals"
            " WHERE session_id=? ORDER BY rowid",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- artifacts ----
    def save(
        self,
        *,
        session_id: str,
        kind: str,
        content: str,
        meta: dict[str, Any] | None = None,
    ) -> str:
        artifact_id = f"a_{uuid.uuid4().hex[:12]}"
        self._conn.execute(
            "INSERT INTO artifacts(id, session_id, kind, content, meta_json, created_at)"
            " VALUES(?,?,?,?,?,?)",
            (
                artifact_id,
                session_id,
                kind,
                content,
                json.dumps(meta or {}, ensure_ascii=False),
                _now(),
            ),
        )
        self._conn.commit()
        return artifact_id

    def get_artifact(self, artifact_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT content FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        return row["content"] if row else None

    def artifact_session(self, artifact_id: str) -> str | None:
        """artifact 所属会话；API 层据此做归属校验。"""
        row = self._conn.execute(
            "SELECT session_id FROM artifacts WHERE id=?", (artifact_id,)
        ).fetchone()
        return row["session_id"] if row else None
