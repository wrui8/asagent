from __future__ import annotations

import sqlite3
import time
from pathlib import Path

_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}

_ALLOWED_PRAGMAS = {"table_info", "foreign_key_list", "index_list"}

_ALLOWED_FUNCS = {
    "count", "sum", "avg", "min", "max", "total", "round", "abs",
    "coalesce", "ifnull", "nullif", "lower", "upper", "substr",
    "length", "trim", "ltrim", "rtrim", "replace", "instr", "hex",
    "strftime", "date", "datetime", "julianday", "printf", "typeof",
    "quote", "unicode", "group_concat", "like", "glob",
    "rank", "dense_rank", "row_number", "ntile", "lag", "lead",
    "first_value", "last_value", "nth_value",
    # 表值 pragma：只暴露元数据，用于 describe_table
    "pragma_table_info", "pragma_foreign_key_list", "pragma_index_list",
}


def _authorizer(action: int, arg1: str | None, arg2: str | None, db: str | None, trig: str | None) -> int:
    if action == sqlite3.SQLITE_PRAGMA:
        # 表值 pragma（pragma_table_info 等）只暴露元数据；独立 PRAGMA 语句已被 run_sql 前缀校验拦截
        return (
            sqlite3.SQLITE_OK
            if (arg1 or "").lower() in _ALLOWED_PRAGMAS
            else sqlite3.SQLITE_DENY
        )
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() not in _ALLOWED_FUNCS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def connect_readonly(db_path: Path, *, timeout_s: float = 5.0) -> sqlite3.Connection:
    """四层只读防护中的前三层：文件只读、query_only、authorizer 白名单。"""
    uri = db_path.resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=timeout_s, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA trusted_schema=OFF")
    conn.set_authorizer(_authorizer)
    return conn


def run_select(
    conn: sqlite3.Connection,
    sql: str,
    *,
    max_rows: int,
    timeout_s: float = 5.0,
) -> tuple[list[str], list[tuple], bool]:
    """执行单条查询；超时用 progress handler 中断。返回 (列名, 行, 是否截断)。"""
    deadline = time.monotonic() + timeout_s
    conn.set_progress_handler(
        lambda: 1 if time.monotonic() > deadline else 0, 5_000
    )
    try:
        cursor = conn.execute(sql)
        rows = cursor.fetchall()
        columns = [d[0] for d in cursor.description] if cursor.description else []
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        return columns, [tuple(r) for r in rows], truncated
    finally:
        conn.set_progress_handler(None, 0)
