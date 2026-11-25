"""PostgreSQL 权威库（阶段 2）：连接池、迁移执行器，以及与 SQLite 同签名的仓储实现。

驱动延迟导入：未安装 `state` extra 时，只有显式选择 postgres 才会报错。
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..models.types import ToolCall
from ..runtime.checkpoint import ToolInvocation
from ..runtime.events import AgentEvent
from ..runtime.state import SessionState
from ..tools.base import ToolResult

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


class PgPool:
    """psycopg 连接池；所有仓储共享一个实例。"""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10):
        if not dsn.strip():
            raise ValueError("AS_STORAGE_BACKEND=postgres 需要设置 AS_PG_DSN")
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise RuntimeError(
                "postgres 存储需要 psycopg：请 `uv sync --extra state`"
            ) from exc
        self.dsn = dsn
        self._pool = ConnectionPool(
            dsn,
            min_size=min_size,
            max_size=max_size,
            open=True,
            timeout=10.0,
            kwargs={"row_factory": dict_row},
        )

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            yield conn

    def close(self) -> None:
        self._pool.close()


def run_migrations(pool: PgPool, migrations_dir: Path | None = None) -> list[str]:
    """幂等执行 migrations/*.sql；返回本次新应用的版本文件名。"""
    from psycopg import ClientCursor

    directory = migrations_dir or MIGRATIONS_DIR
    applied: list[str] = []
    with pool.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version("
            "version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        done = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_version").fetchall()
        }
        for path in sorted(directory.glob("*.sql")):
            if path.name in done:
                continue
            sql = path.read_text(encoding="utf-8")
            # ClientCursor 走简单查询协议，可一次提交多条语句
            with ClientCursor(conn) as cur:
                cur.execute(sql)
            conn.execute(
                "INSERT INTO schema_version(version, applied_at) VALUES(%s, %s)",
                (path.name, _now()),
            )
            applied.append(path.name)
    return applied


class PgKernelStore:
    """CheckpointStore 的 PG 实现（方法签名一致）。"""

    def __init__(self, pool: PgPool):
        self.pool = pool

    def close(self) -> None:
        """连接池由 Storage 统一关闭。"""

    # ---- 会话与快照 ----
    def create_session(
        self, profile: str, title: str | None = None, owner_id: str | None = None
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
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO sessions"
                "(session_id, profile, title, status, owner_id, created_at, updated_at)"
                " VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (session_id, profile, title, str(state.status), owner_id, ts, ts),
            )
        self.save_state(state)
        return state

    def save_state(self, state: SessionState) -> None:
        state.updated_at = _now()
        payload = state.model_dump_json()
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO sessions"
                "(session_id, profile, title, status, owner_id, created_at, updated_at)"
                " VALUES(%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (session_id) DO UPDATE SET profile=EXCLUDED.profile,"
                " title=EXCLUDED.title, status=EXCLUDED.status,"
                " owner_id=EXCLUDED.owner_id, created_at=EXCLUDED.created_at,"
                " updated_at=EXCLUDED.updated_at",
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
            conn.execute(
                "INSERT INTO checkpoints(session_id, step, state_json, last_seq, ts)"
                " VALUES(%s,%s,%s,%s,%s)"
                " ON CONFLICT (session_id, step) DO UPDATE SET"
                " state_json=EXCLUDED.state_json, last_seq=EXCLUDED.last_seq,"
                " ts=EXCLUDED.ts",
                (state.session_id, state.step, payload, state.last_seq, state.updated_at),
            )

    def load_state(self, session_id: str) -> SessionState:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT state_json FROM checkpoints WHERE session_id=%s"
                " ORDER BY step DESC LIMIT 1",
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
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("owner_id", owner_id),
            ("profile", profile),
            ("status", status),
        ):
            if value is not None:
                clauses.append(f"{column}=%s")
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT session_id, profile, title, status, owner_id, created_at,"
                f" updated_at FROM sessions{where}"
                " ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    # ---- 事件流 ----
    def append(self, session_id: str, type: str, payload: dict[str, Any]) -> AgentEvent:
        ts = _now()
        with self.pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO events(session_id, ts, type, payload_json)"
                " VALUES(%s,%s,%s,%s) RETURNING seq",
                (session_id, ts, type, json.dumps(payload, ensure_ascii=False)),
            ).fetchone()
        return AgentEvent(
            seq=int(row["seq"]),
            ts=ts,
            session_id=session_id,
            type=type,
            payload=payload,
        )

    def events(self, session_id: str, after_seq: int = 0) -> list[AgentEvent]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT seq, ts, session_id, type, payload_json FROM events"
                " WHERE session_id=%s AND seq>%s ORDER BY seq",
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
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT seq, ts, session_id, type, payload_json FROM events"
                " WHERE type=%s ORDER BY seq DESC LIMIT %s",
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
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO tool_invocations"
                "(session_id, call_id, tool, args_json, permission, status, started_at)"
                " VALUES(%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (session_id, call_id) DO UPDATE SET"
                " tool=EXCLUDED.tool, args_json=EXCLUDED.args_json,"
                " permission=EXCLUDED.permission, status=EXCLUDED.status,"
                " result_json=NULL, started_at=EXCLUDED.started_at, finished_at=NULL",
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

    def finish_tool(self, session_id: str, call_id: str, result: ToolResult) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE tool_invocations SET status=%s, result_json=%s, finished_at=%s"
                " WHERE session_id=%s AND call_id=%s",
                (
                    "finished" if result.ok else "failed",
                    result.model_dump_json(),
                    _now(),
                    session_id,
                    call_id,
                ),
            )

    def pending_invocations(self, session_id: str) -> list[ToolInvocation]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_invocations WHERE session_id=%s AND status='started'",
                (session_id,),
            ).fetchall()
        return [ToolInvocation(**dict(row)) for row in rows]

    def finished_invocations(self, session_id: str) -> list[ToolInvocation]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_invocations"
                " WHERE session_id=%s AND status IN ('finished','failed')"
                " ORDER BY started_at, call_id",
                (session_id,),
            ).fetchall()
        return [ToolInvocation(**dict(row)) for row in rows]

    # ---- 人工审批 ----
    def request_approval(self, session_id: str, call_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "DELETE FROM approvals WHERE session_id=%s AND call_id=%s",
                (session_id, call_id),
            )
            conn.execute(
                "INSERT INTO approvals(id, session_id, call_id, status, decided_at)"
                " VALUES(%s,%s,%s,%s,%s)",
                (f"ap_{uuid.uuid4().hex[:12]}", session_id, call_id, "pending", None),
            )

    def decide_approval(self, session_id: str, call_id: str, approved: bool) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE approvals SET status=%s, decided_at=%s"
                " WHERE session_id=%s AND call_id=%s",
                ("approved" if approved else "denied", _now(), session_id, call_id),
            )

    def approval_status(self, session_id: str, call_id: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT status FROM approvals WHERE session_id=%s AND call_id=%s"
                " ORDER BY decided_at DESC NULLS FIRST, id DESC LIMIT 1",
                (session_id, call_id),
            ).fetchone()
        return row["status"] if row else None

    def list_approvals(self, session_id: str) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, session_id, call_id, status, decided_at FROM approvals"
                " WHERE session_id=%s ORDER BY id",
                (session_id,),
            ).fetchall()
        return [dict(row) for row in rows]

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
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO artifacts(id, session_id, kind, content, meta_json, created_at)"
                " VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    artifact_id,
                    session_id,
                    kind,
                    content,
                    json.dumps(meta or {}, ensure_ascii=False),
                    _now(),
                ),
            )
        return artifact_id

    def get_artifact(self, artifact_id: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT content FROM artifacts WHERE id=%s", (artifact_id,)
            ).fetchone()
        return row["content"] if row else None

    def artifact_session(self, artifact_id: str) -> str | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT session_id FROM artifacts WHERE id=%s", (artifact_id,)
            ).fetchone()
        return row["session_id"] if row else None


class PgWebStore:
    """WebDB 的 PG 实现（方法签名一致）。"""

    def __init__(self, pool: PgPool):
        self.pool = pool

    # ------------------------------------------------------------------ #
    # 用户
    def create_user(
        self,
        *,
        username: str,
        password_hash: str,
        role: str = "user",
    ) -> dict[str, Any]:
        user_id = f"u_{uuid.uuid4().hex[:12]}"
        ts = _now()
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, created_at)"
                " VALUES(%s,%s,%s,%s,FALSE,%s)",
                (user_id, username, password_hash, role, ts),
            )
        user = self.get_user(user_id)
        assert user is not None
        return user

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id=%s", (user_id,)).fetchone()
        return _user(row) if row else None

    def get_user_with_hash(self, username: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username=%s", (username,)
            ).fetchone()
        if row is None:
            return None
        data = _user(row)
        data["password_hash"] = row["password_hash"]
        return data

    def list_users(self) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [_user(row) for row in rows]

    def update_user(
        self,
        user_id: str,
        *,
        role: str | None = None,
        disabled: bool | None = None,
        password_hash: str | None = None,
    ) -> bool:
        fields: list[str] = []
        params: list[Any] = []
        if role is not None:
            fields.append("role=%s")
            params.append(role)
        if disabled is not None:
            fields.append("disabled=%s")
            params.append(bool(disabled))
        if password_hash is not None:
            fields.append("password_hash=%s")
            params.append(password_hash)
        if not fields:
            return False
        params.append(user_id)
        with self.pool.connection() as conn:
            cur = conn.execute(
                f"UPDATE users SET {', '.join(fields)} WHERE id=%s", params
            )
        return cur.rowcount > 0

    def touch_login(self, user_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE users SET last_login_at=%s WHERE id=%s", (_now(), user_id)
            )

    def admin_count(self) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role='admin' AND disabled=FALSE"
            ).fetchone()
        return int(row["n"])

    # ------------------------------------------------------------------ #
    # 邀请码
    def create_invite(
        self,
        *,
        code_hash: str,
        created_by: str,
        role: str = "user",
        expires_at: str | None = None,
        max_uses: int = 1,
    ) -> str:
        invite_id = f"iv_{uuid.uuid4().hex[:12]}"
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO invites(id, code_hash, created_by, role, expires_at,"
                " max_uses, used_count, revoked, created_at)"
                " VALUES(%s,%s,%s,%s,%s,%s,0,FALSE,%s)",
                (invite_id, code_hash, created_by, role, expires_at, max_uses, _now()),
            )
        return invite_id

    def list_invites(self) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, created_by, role, expires_at, max_uses, used_count,"
                " revoked, created_at FROM invites ORDER BY created_at DESC"
            ).fetchall()
        return [{**dict(row), "revoked": bool(row["revoked"])} for row in rows]

    def revoke_invite(self, invite_id: str) -> bool:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE invites SET revoked=TRUE WHERE id=%s", (invite_id,)
            )
        return cur.rowcount > 0

    def register_with_invite(
        self,
        *,
        username: str,
        password_hash: str,
        code_hash: str,
        now: str | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        ts = now or _now()
        user_id = f"u_{uuid.uuid4().hex[:12]}"
        with self.pool.connection() as conn:
            taken = conn.execute(
                "SELECT 1 FROM users WHERE username=%s", (username,)
            ).fetchone()
            if taken:
                return None, "username_taken"
            cur = conn.execute(
                "UPDATE invites SET used_count=used_count+1"
                " WHERE code_hash=%s AND revoked=FALSE AND used_count<max_uses"
                " AND (expires_at IS NULL OR expires_at>%s)",
                (code_hash, ts),
            )
            if cur.rowcount != 1:
                return None, "invalid_invite"
            row = conn.execute(
                "SELECT role FROM invites WHERE code_hash=%s", (code_hash,)
            ).fetchone()
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, created_at)"
                " VALUES(%s,%s,%s,%s,FALSE,%s)",
                (user_id, username, password_hash, row["role"], ts),
            )
        return self.get_user(user_id), ""

    # ------------------------------------------------------------------ #
    # refresh token
    def save_refresh(self, *, user_id: str, token_hash: str, expires_at: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO refresh_tokens(id, user_id, token_hash, expires_at,"
                " revoked_at, created_at) VALUES(%s,%s,%s,%s,NULL,%s)",
                (f"rt_{uuid.uuid4().hex[:12]}", user_id, token_hash, expires_at, _now()),
            )

    def get_refresh(self, token_hash: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM refresh_tokens WHERE token_hash=%s", (token_hash,)
            ).fetchone()
        return dict(row) if row else None

    def revoke_refresh(self, token_hash: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE refresh_tokens SET revoked_at=%s WHERE token_hash=%s",
                (_now(), token_hash),
            )

    def revoke_user_refreshes(self, user_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE refresh_tokens SET revoked_at=%s"
                " WHERE user_id=%s AND revoked_at IS NULL",
                (_now(), user_id),
            )

    # ------------------------------------------------------------------ #
    # 配额与用量
    def get_quota(self, user_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM user_quotas WHERE user_id=%s", (user_id,)
            ).fetchone()
        return dict(row) if row else None

    def set_quota(
        self,
        user_id: str,
        *,
        max_cost_per_session: float | None,
        daily_cost_limit: float | None,
        concurrent_sessions: int,
        eval_daily_limit: int = 0,
    ) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO user_quotas(user_id, max_cost_per_session,"
                " daily_cost_limit, concurrent_sessions, eval_daily_limit)"
                " VALUES(%s,%s,%s,%s,%s)"
                " ON CONFLICT (user_id) DO UPDATE SET"
                " max_cost_per_session=EXCLUDED.max_cost_per_session,"
                " daily_cost_limit=EXCLUDED.daily_cost_limit,"
                " concurrent_sessions=EXCLUDED.concurrent_sessions,"
                " eval_daily_limit=EXCLUDED.eval_daily_limit",
                (
                    user_id,
                    max_cost_per_session,
                    daily_cost_limit,
                    concurrent_sessions,
                    eval_daily_limit,
                ),
            )

    def daily_cost(self, user_id: str) -> float:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT cost FROM user_usage WHERE user_id=%s AND day=%s",
                (user_id, _today()),
            ).fetchone()
        return float(row["cost"]) if row else 0.0

    def add_cost(self, user_id: str, cost: float) -> None:
        if cost <= 0:
            return
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO user_usage(user_id, day, cost) VALUES(%s,%s,%s)"
                " ON CONFLICT (user_id, day) DO UPDATE SET cost=user_usage.cost+EXCLUDED.cost",
                (user_id, _today(), cost),
            )

    # ------------------------------------------------------------------ #
    # 前端可见性
    def hide_session(self, user_id: str, session_id: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO hidden_sessions(user_id, session_id, created_at)"
                " VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (user_id, session_id, _now()),
            )

    def hidden_sessions(self, user_id: str) -> set[str]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT session_id FROM hidden_sessions WHERE user_id=%s", (user_id,)
            ).fetchall()
        return {row["session_id"] for row in rows}

    # ------------------------------------------------------------------ #
    # 知识库元数据
    def kb_upsert(
        self,
        *,
        doc_id: str,
        title: str,
        sha256: str,
        size: int,
        suspicious: bool,
        actor: str,
    ) -> dict[str, Any]:
        ts = _now()
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO kb_docs(doc_id, title, status, sha256, size, suspicious,"
                " created_by, updated_by, created_at, updated_at)"
                " VALUES(%s,%s,'active',%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (doc_id) DO UPDATE SET title=EXCLUDED.title,"
                " status='active', sha256=EXCLUDED.sha256, size=EXCLUDED.size,"
                " suspicious=EXCLUDED.suspicious, updated_by=EXCLUDED.updated_by,"
                " updated_at=EXCLUDED.updated_at",
                (
                    doc_id,
                    title,
                    sha256,
                    size,
                    bool(suspicious),
                    actor,
                    actor,
                    ts,
                    ts,
                ),
            )
        row = self.kb_get(doc_id)
        assert row is not None
        return row

    def kb_list(self, *, include_deleted: bool = True) -> list[dict[str, Any]]:
        clause = "" if include_deleted else " WHERE status='active'"
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM kb_docs" + clause + " ORDER BY updated_at DESC"
            ).fetchall()
        return [{**dict(row), "suspicious": bool(row["suspicious"])} for row in rows]

    def kb_get(self, doc_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM kb_docs WHERE doc_id=%s", (doc_id,)
            ).fetchone()
        return {**dict(row), "suspicious": bool(row["suspicious"])} if row else None

    def kb_set_status(self, doc_id: str, status: str, *, actor: str) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE kb_docs SET status=%s, updated_by=%s, updated_at=%s"
                " WHERE doc_id=%s",
                (status, actor, _now(), doc_id),
            )

    def kb_add_version(
        self, *, doc_id: str, content: str, sha256: str, actor: str
    ) -> str:
        version_id = f"kv_{uuid.uuid4().hex[:12]}"
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO kb_versions(id, doc_id, content, sha256, created_by,"
                " created_at) VALUES(%s,%s,%s,%s,%s,%s)",
                (version_id, doc_id, content, sha256, actor, _now()),
            )
        return version_id

    def kb_versions(self, doc_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT id, doc_id, sha256, created_by, created_at FROM kb_versions"
                " WHERE doc_id=%s ORDER BY created_at DESC, id DESC LIMIT %s",
                (doc_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def kb_get_version(self, version_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM kb_versions WHERE id=%s", (version_id,)
            ).fetchone()
        return dict(row) if row else None

    def kb_audit(
        self,
        *,
        actor: str,
        action: str,
        doc_id: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO kb_audit(ts, actor, action, doc_id, detail)"
                " VALUES(%s,%s,%s,%s,%s)",
                (_now(), actor, action, doc_id, detail),
            )

    def kb_audit_list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM kb_audit ORDER BY id DESC LIMIT %s", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def kb_job_start(
        self, *, kind: str, doc_ids: list[str] | None, actor: str
    ) -> str:
        job_id = f"kj_{uuid.uuid4().hex[:12]}"
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO kb_jobs(id, kind, status, doc_ids, message, created_by,"
                " created_at) VALUES(%s,%s,%s,%s,NULL,%s,%s)",
                (
                    job_id,
                    kind,
                    "running",
                    json.dumps(doc_ids, ensure_ascii=False) if doc_ids else None,
                    actor,
                    _now(),
                ),
            )
        return job_id

    def kb_job_finish(self, job_id: str, *, status: str, message: str | None) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE kb_jobs SET status=%s, message=%s, finished_at=%s WHERE id=%s",
                (status, message, _now(), job_id),
            )

    def kb_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM kb_jobs ORDER BY created_at DESC, id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def kb_job_running(self) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM kb_jobs WHERE status='running'"
                " ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # 评测任务
    def eval_job_start(
        self,
        *,
        actor: str,
        profile: str,
        models: list[str] | None,
        repeat: int,
        limit_cases: int | None,
    ) -> str:
        job_id = f"ev_{uuid.uuid4().hex[:12]}"
        with self.pool.connection() as conn:
            conn.execute(
                "INSERT INTO eval_jobs(id, actor, profile, models, repeat, limit_cases,"
                " status, created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    job_id,
                    actor,
                    profile,
                    json.dumps(models, ensure_ascii=False) if models else None,
                    repeat,
                    limit_cases,
                    "running",
                    _now(),
                ),
            )
        return job_id

    def eval_job_finish(
        self,
        job_id: str,
        *,
        status: str,
        message: str | None = None,
        report_id: str | None = None,
        summary: dict[str, Any] | None = None,
    ) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                "UPDATE eval_jobs SET status=%s, message=%s, report_id=%s,"
                " summary_json=%s, finished_at=%s WHERE id=%s",
                (
                    status,
                    message,
                    report_id,
                    json.dumps(summary, ensure_ascii=False) if summary else None,
                    _now(),
                    job_id,
                ),
            )

    def eval_job(self, job_id: str) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM eval_jobs WHERE id=%s", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def eval_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM eval_jobs ORDER BY created_at DESC, id DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def eval_job_running(self) -> dict[str, Any] | None:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT * FROM eval_jobs WHERE status='running'"
                " ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def eval_count_today(self, actor: str) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM eval_jobs WHERE actor=%s AND created_at LIKE %s",
                (actor, f"{_today()}%"),
            ).fetchone()
        return int(row["n"]) if row else 0

    def eval_jobs_recover(self) -> int:
        with self.pool.connection() as conn:
            cur = conn.execute(
                "UPDATE eval_jobs SET status='failed', message=%s, finished_at=%s"
                " WHERE status='running'",
                ("服务重启，评测中断", _now()),
            )
        return int(cur.rowcount or 0)


def _user(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "disabled": bool(row["disabled"]),
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }
