from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS invites(
  id TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL UNIQUE,
  created_by TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  expires_at TEXT,
  max_uses INTEGER NOT NULL DEFAULT 1,
  used_count INTEGER NOT NULL DEFAULT 0,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refresh_tokens(
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_quotas(
  user_id TEXT PRIMARY KEY,
  max_cost_per_session REAL,
  daily_cost_limit REAL,
  concurrent_sessions INTEGER NOT NULL DEFAULT 1,
  eval_daily_limit INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS user_usage(
  user_id TEXT NOT NULL,
  day TEXT NOT NULL,
  cost REAL NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, day)
);
CREATE TABLE IF NOT EXISTS hidden_sessions(
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, session_id)
);
CREATE TABLE IF NOT EXISTS kb_docs(
  doc_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  suspicious INTEGER NOT NULL DEFAULT 0,
  created_by TEXT,
  updated_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_versions(
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  content TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_jobs(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  doc_ids TEXT,
  message TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS kb_audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  doc_id TEXT,
  detail TEXT
);
CREATE TABLE IF NOT EXISTS eval_jobs(
  id TEXT PRIMARY KEY,
  actor TEXT NOT NULL,
  profile TEXT NOT NULL,
  models TEXT,
  repeat INTEGER NOT NULL DEFAULT 1,
  limit_cases INTEGER,
  status TEXT NOT NULL,
  message TEXT,
  report_id TEXT,
  summary_json TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_invites_code ON invites(code_hash);
CREATE INDEX IF NOT EXISTS idx_kb_versions_doc ON kb_versions(doc_id);
CREATE INDEX IF NOT EXISTS idx_kb_audit_ts ON kb_audit(ts);
CREATE INDEX IF NOT EXISTS idx_eval_jobs_created ON eval_jobs(created_at);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _user(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "disabled": bool(row["disabled"]),
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
    }


class WebDB:
    """API 层独立表：用户、邀请码、refresh token、配额与用量。

    与内核的 CheckpointStore 共用同一个 SQLite 文件，但表彼此独立。
    """

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        con = self._connect()
        try:
            con.executescript(_SCHEMA)
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.path), isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        finally:
            con.close()

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
        with self._tx() as con:
            con.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, created_at)"
                " VALUES(?,?,?,?,0,?)",
                (user_id, username, password_hash, role, ts),
            )
        user = self.get_user(user_id)
        assert user is not None
        return user

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._read() as con:
            row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return _user(row) if row else None

    def get_user_with_hash(self, username: str) -> dict[str, Any] | None:
        with self._read() as con:
            row = con.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        if row is None:
            return None
        data = _user(row)
        data["password_hash"] = row["password_hash"]
        return data

    def list_users(self) -> list[dict[str, Any]]:
        with self._read() as con:
            rows = con.execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [_user(r) for r in rows]

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
            fields.append("role=?")
            params.append(role)
        if disabled is not None:
            fields.append("disabled=?")
            params.append(1 if disabled else 0)
        if password_hash is not None:
            fields.append("password_hash=?")
            params.append(password_hash)
        if not fields:
            return False
        params.append(user_id)
        with self._tx() as con:
            cur = con.execute(f"UPDATE users SET {', '.join(fields)} WHERE id=?", params)
        return cur.rowcount > 0

    def touch_login(self, user_id: str) -> None:
        with self._tx() as con:
            con.execute("UPDATE users SET last_login_at=? WHERE id=?", (_now(), user_id))

    def admin_count(self) -> int:
        with self._read() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM users WHERE role='admin' AND disabled=0"
            ).fetchone()
        return int(row[0])

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
        with self._tx() as con:
            con.execute(
                "INSERT INTO invites(id, code_hash, created_by, role, expires_at,"
                " max_uses, used_count, revoked, created_at)"
                " VALUES(?,?,?,?,?,?,0,0,?)",
                (invite_id, code_hash, created_by, role, expires_at, max_uses, _now()),
            )
        return invite_id

    def list_invites(self) -> list[dict[str, Any]]:
        with self._read() as con:
            rows = con.execute(
                "SELECT id, created_by, role, expires_at, max_uses, used_count,"
                " revoked, created_at FROM invites ORDER BY created_at DESC"
            ).fetchall()
        return [
            {**dict(r), "revoked": bool(r["revoked"])}
            for r in rows
        ]

    def revoke_invite(self, invite_id: str) -> bool:
        with self._tx() as con:
            cur = con.execute(
                "UPDATE invites SET revoked=1 WHERE id=?", (invite_id,)
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
        """事务内校验并消费邀请码；返回 (user, error_code)。"""
        ts = now or _now()
        user_id = f"u_{uuid.uuid4().hex[:12]}"
        with self._tx() as con:
            if con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                return None, "username_taken"
            cur = con.execute(
                "UPDATE invites SET used_count=used_count+1"
                " WHERE code_hash=? AND revoked=0 AND used_count<max_uses"
                " AND (expires_at IS NULL OR expires_at>?)",
                (code_hash, ts),
            )
            if cur.rowcount != 1:
                return None, "invalid_invite"
            row = con.execute(
                "SELECT role FROM invites WHERE code_hash=?", (code_hash,)
            ).fetchone()
            con.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, created_at)"
                " VALUES(?,?,?,?,0,?)",
                (user_id, username, password_hash, row["role"], ts),
            )
        return self.get_user(user_id), ""

    # ------------------------------------------------------------------ #
    # refresh token
    def save_refresh(self, *, user_id: str, token_hash: str, expires_at: str) -> None:
        with self._tx() as con:
            con.execute(
                "INSERT INTO refresh_tokens(id, user_id, token_hash, expires_at, revoked_at,"
                " created_at) VALUES(?,?,?,?,NULL,?)",
                (f"rt_{uuid.uuid4().hex[:12]}", user_id, token_hash, expires_at, _now()),
            )

    def get_refresh(self, token_hash: str) -> dict[str, Any] | None:
        with self._read() as con:
            row = con.execute(
                "SELECT * FROM refresh_tokens WHERE token_hash=?", (token_hash,)
            ).fetchone()
        return dict(row) if row else None

    def revoke_refresh(self, token_hash: str) -> None:
        with self._tx() as con:
            con.execute(
                "UPDATE refresh_tokens SET revoked_at=? WHERE token_hash=?",
                (_now(), token_hash),
            )

    def revoke_user_refreshes(self, user_id: str) -> None:
        with self._tx() as con:
            con.execute(
                "UPDATE refresh_tokens SET revoked_at=?"
                " WHERE user_id=? AND revoked_at IS NULL",
                (_now(), user_id),
            )

    # ------------------------------------------------------------------ #
    # 配额与用量
    def get_quota(self, user_id: str) -> dict[str, Any] | None:
        with self._read() as con:
            row = con.execute(
                "SELECT * FROM user_quotas WHERE user_id=?", (user_id,)
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
        with self._tx() as con:
            con.execute(
                "INSERT OR REPLACE INTO user_quotas(user_id, max_cost_per_session,"
                " daily_cost_limit, concurrent_sessions, eval_daily_limit)"
                " VALUES(?,?,?,?,?)",
                (
                    user_id,
                    max_cost_per_session,
                    daily_cost_limit,
                    concurrent_sessions,
                    eval_daily_limit,
                ),
            )

    def daily_cost(self, user_id: str) -> float:
        with self._read() as con:
            row = con.execute(
                "SELECT cost FROM user_usage WHERE user_id=? AND day=?",
                (user_id, _today()),
            ).fetchone()
        return float(row["cost"]) if row else 0.0

    def add_cost(self, user_id: str, cost: float) -> None:
        if cost <= 0:
            return
        with self._tx() as con:
            con.execute(
                "INSERT INTO user_usage(user_id, day, cost) VALUES(?,?,?)"
                " ON CONFLICT(user_id, day) DO UPDATE SET cost=cost+excluded.cost",
                (user_id, _today(), cost),
            )

    # ------------------------------------------------------------------ #
    # 前端可见性（软删除，不删内核数据）
    def hide_session(self, user_id: str, session_id: str) -> None:
        with self._tx() as con:
            con.execute(
                "INSERT OR IGNORE INTO hidden_sessions(user_id, session_id, created_at)"
                " VALUES(?,?,?)",
                (user_id, session_id, _now()),
            )

    def hidden_sessions(self, user_id: str) -> set[str]:
        with self._read() as con:
            rows = con.execute(
                "SELECT session_id FROM hidden_sessions WHERE user_id=?", (user_id,)
            ).fetchall()
        return {row["session_id"] for row in rows}

    # ------------------------------------------------------------------ #
    # 知识库元数据（正文在 data/docs/*.md，这里只存管理与审计信息）
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
        with self._tx() as con:
            con.execute(
                "INSERT INTO kb_docs(doc_id, title, status, sha256, size, suspicious,"
                " created_by, updated_by, created_at, updated_at)"
                " VALUES(?,?,'active',?,?,?,?,?,?,?)"
                " ON CONFLICT(doc_id) DO UPDATE SET title=excluded.title,"
                " status='active', sha256=excluded.sha256, size=excluded.size,"
                " suspicious=excluded.suspicious, updated_by=excluded.updated_by,"
                " updated_at=excluded.updated_at",
                (
                    doc_id,
                    title,
                    sha256,
                    size,
                    1 if suspicious else 0,
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
        with self._read() as con:
            rows = con.execute(
                "SELECT * FROM kb_docs" + clause + " ORDER BY updated_at DESC"
            ).fetchall()
        return [{**dict(row), "suspicious": bool(row["suspicious"])} for row in rows]

    def kb_get(self, doc_id: str) -> dict[str, Any] | None:
        with self._read() as con:
            row = con.execute(
                "SELECT * FROM kb_docs WHERE doc_id=?", (doc_id,)
            ).fetchone()
        return {**dict(row), "suspicious": bool(row["suspicious"])} if row else None

    def kb_set_status(self, doc_id: str, status: str, *, actor: str) -> None:
        with self._tx() as con:
            con.execute(
                "UPDATE kb_docs SET status=?, updated_by=?, updated_at=? WHERE doc_id=?",
                (status, actor, _now(), doc_id),
            )

    def kb_add_version(
        self, *, doc_id: str, content: str, sha256: str, actor: str
    ) -> str:
        version_id = f"kv_{uuid.uuid4().hex[:12]}"
        with self._tx() as con:
            con.execute(
                "INSERT INTO kb_versions(id, doc_id, content, sha256, created_by,"
                " created_at) VALUES(?,?,?,?,?,?)",
                (version_id, doc_id, content, sha256, actor, _now()),
            )
        return version_id

    def kb_versions(self, doc_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._read() as con:
            rows = con.execute(
                "SELECT id, doc_id, sha256, created_by, created_at FROM kb_versions"
                " WHERE doc_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (doc_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def kb_get_version(self, version_id: str) -> dict[str, Any] | None:
        with self._read() as con:
            row = con.execute(
                "SELECT * FROM kb_versions WHERE id=?", (version_id,)
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
        with self._tx() as con:
            con.execute(
                "INSERT INTO kb_audit(ts, actor, action, doc_id, detail)"
                " VALUES(?,?,?,?,?)",
                (_now(), actor, action, doc_id, detail),
            )

    def kb_audit_list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._read() as con:
            rows = con.execute(
                "SELECT * FROM kb_audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def kb_job_start(
        self, *, kind: str, doc_ids: list[str] | None, actor: str
    ) -> str:
        job_id = f"kj_{uuid.uuid4().hex[:12]}"
        with self._tx() as con:
            con.execute(
                "INSERT INTO kb_jobs(id, kind, status, doc_ids, message, created_by,"
                " created_at) VALUES(?,?,?,?,NULL,?,?)",
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
        with self._tx() as con:
            con.execute(
                "UPDATE kb_jobs SET status=?, message=?, finished_at=? WHERE id=?",
                (status, message, _now(), job_id),
            )

    def kb_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._read() as con:
            rows = con.execute(
                "SELECT * FROM kb_jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def kb_job_running(self) -> dict[str, Any] | None:
        with self._read() as con:
            row = con.execute(
                "SELECT * FROM kb_jobs WHERE status='running' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # 评测任务（报告正文在 runtime/evals/*.json，这里只存任务状态与摘要）
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
        with self._tx() as con:
            con.execute(
                "INSERT INTO eval_jobs(id, actor, profile, models, repeat, limit_cases,"
                " status, created_at) VALUES(?,?,?,?,?,?,?,?)",
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
        with self._tx() as con:
            con.execute(
                "UPDATE eval_jobs SET status=?, message=?, report_id=?, summary_json=?,"
                " finished_at=? WHERE id=?",
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
        with self._read() as con:
            row = con.execute("SELECT * FROM eval_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def eval_jobs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._read() as con:
            rows = con.execute(
                "SELECT * FROM eval_jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def eval_job_running(self) -> dict[str, Any] | None:
        with self._read() as con:
            row = con.execute(
                "SELECT * FROM eval_jobs WHERE status='running' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def eval_count_today(self, actor: str) -> int:
        with self._read() as con:
            row = con.execute(
                "SELECT COUNT(*) AS n FROM eval_jobs WHERE actor=? AND created_at LIKE ?",
                (actor, f"{_today()}%"),
            ).fetchone()
        return int(row["n"]) if row else 0

    def eval_jobs_recover(self) -> int:
        """进程重启后，内存中的评测任务已不可能继续，标记失败。"""
        with self._tx() as con:
            cur = con.execute(
                "UPDATE eval_jobs SET status='failed', message=?, finished_at=?"
                " WHERE status='running'",
                ("服务重启，评测中断", _now(),),
            )
        return int(cur.rowcount or 0)
