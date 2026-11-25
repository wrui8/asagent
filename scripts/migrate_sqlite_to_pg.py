"""把 v1 的 SQLite（runtime/state.db）一次性搬到 PostgreSQL（v2 阶段 2）。

用法（在项目根目录）：

    uv run python scripts/migrate_sqlite_to_pg.py --dry-run
    uv run python scripts/migrate_sqlite_to_pg.py            # 要求目标为空
    uv run python scripts/migrate_sqlite_to_pg.py --force    # 清空目标后重搬

连接串取 AS_PG_DSN（不打印）。迁移完成后逐表比较行数与内容哈希，
不一致直接非零退出。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from asagent.config import load_config
from asagent.storage.pg import PgPool, run_migrations

# 按外键/依赖顺序搬运；PRIMARY KEY 列保持原值
TABLES = [
    "sessions",
    "events",
    "checkpoints",
    "tool_invocations",
    "artifacts",
    "approvals",
    "users",
    "invites",
    "refresh_tokens",
    "user_quotas",
    "user_usage",
    "hidden_sessions",
    "kb_docs",
    "kb_versions",
    "kb_jobs",
    "kb_audit",
    "eval_jobs",
]

# SQLite 用 0/1 存布尔，PG 用真实 BOOLEAN
BOOL_COLUMNS = {
    "users": {"disabled"},
    "invites": {"revoked"},
    "kb_docs": {"suspicious"},
}

# 显式插入 id 后要推进的自增序列
SERIALS = {
    "events": ("seq", "events_seq_seq"),
    "kb_audit": ("id", "kb_audit_id_seq"),
}


def _sqlite_rows(conn: sqlite3.Connection, table: str) -> tuple[list[str], list[dict[str, Any]]]:
    cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    if not cols:
        return [], []
    rows = [
        dict(zip(cols, row, strict=True))
        for row in conn.execute(f"SELECT * FROM {table}")
    ]
    return cols, rows


def _normalize(table: str, row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for col in BOOL_COLUMNS.get(table, ()):
        if out.get(col) is not None:
            out[col] = bool(int(out[col]))
    return out


def _digest(rows: list[dict[str, Any]]) -> str:
    # 两端 collation 不同，排序顺序不可比；按规范化 JSON 在 Python 侧排序
    lines = sorted(
        json.dumps(row, sort_keys=True, ensure_ascii=False, default=str) for row in rows
    )
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _pg_rows(conn, table: str, cols: list[str]) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT {', '.join(cols)} FROM {table}")
        return [dict(row) for row in cur.fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite", default="", help="源库路径（默认 AS_DB_PATH）")
    parser.add_argument("--dsn", default="", help="目标连接串（默认 AS_PG_DSN）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写入")
    parser.add_argument("--force", action="store_true", help="目标非空时清空后重搬")
    args = parser.parse_args()

    config = load_config()
    sqlite_path = Path(args.sqlite) if args.sqlite else config.db_path
    if not sqlite_path.exists():
        print(f"源库不存在：{sqlite_path}", file=sys.stderr)
        return 2
    dsn = args.dsn or os.environ.get("AS_PG_DSN", "").strip()
    if not dsn:
        print("缺少 AS_PG_DSN（或 --dsn）", file=sys.stderr)
        return 2

    src = sqlite3.connect(str(sqlite_path))
    src.row_factory = sqlite3.Row
    pool = PgPool(dsn)
    try:
        applied = run_migrations(pool)
        if applied:
            print(f"已应用迁移：{', '.join(applied)}")

        plans: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
        for table in TABLES:
            cols, rows = _sqlite_rows(src, table)
            plans[table] = (cols, [_normalize(table, r) for r in rows])
            print(f"{table:18s} 源 {len(rows):6d} 行")

        if args.dry_run:
            print("dry-run：未写入目标库")
            return 0

        with pool.connection() as conn:
            occupied = [
                t for t, (_, rows) in plans.items() if rows and _count(conn, t)
            ]
            if occupied and not args.force:
                print(
                    f"目标表已有数据：{', '.join(occupied)}；确认覆盖请加 --force",
                    file=sys.stderr,
                )
                return 2
            with conn.transaction():
                if args.force:
                    for table in reversed(TABLES):
                        conn.execute(f"DELETE FROM {table}")
                for table in TABLES:
                    cols, rows = plans[table]
                    if rows:
                        _insert(conn, table, cols, rows)
                for table, (col, seq) in SERIALS.items():
                    conn.execute(
                        f"SELECT setval('{seq}', "
                        f"COALESCE((SELECT MAX({col}) FROM {table}), 1), "
                        f"EXISTS(SELECT 1 FROM {table}))"
                    )

            ok = True
            for table in TABLES:
                cols, rows = plans[table]
                got = _pg_rows(conn, table, cols) if cols else []
                if len(got) != len(rows) or _digest(got) != _digest(rows):
                    ok = False
                    print(
                        f"[FAIL] {table}: 源 {len(rows)} 行 / 目标 {len(got)} 行，内容不一致",
                        file=sys.stderr,
                    )
                else:
                    print(f"[OK]   {table}: {len(rows)} 行校验一致")
        return 0 if ok else 1
    finally:
        pool.close()
        src.close()


def _count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return int(cur.fetchone()["n"])


def _insert(conn, table: str, cols: list[str], rows: list[dict[str, Any]]) -> None:
    placeholders = ", ".join(["%s"] * len(cols))
    columns = ", ".join(cols)
    sql = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
    with conn.cursor() as cur:
        cur.executemany(sql, [tuple(row[c] for c in cols) for row in rows])


if __name__ == "__main__":
    raise SystemExit(main())
