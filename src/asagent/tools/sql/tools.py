from __future__ import annotations

import csv
import io
import re
import sqlite3
from pathlib import Path

from pydantic import BaseModel, Field

from ..base import PermissionLevel, Tool, ToolContext, ToolError, ToolResult
from .demo import TABLE_DOCS, generate_demo_db
from .readonly import connect_readonly, run_select

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SELECT_PREFIX = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def _strip_leading_comments(sql: str) -> str:
    text = sql
    while True:
        stripped = text.lstrip()
        if stripped.startswith("--"):
            idx = stripped.find("\n")
            if idx == -1:
                return ""
            text = stripped[idx + 1 :]
            continue
        if stripped.startswith("/*"):
            idx = stripped.find("*/")
            if idx == -1:
                return ""
            text = stripped[idx + 2 :]
            continue
        return stripped


def _format_cell(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    text = str(value)
    return text[:500] + "…" if len(text) > 500 else text


def _jsonable(value: object) -> object:
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, (str, int, float)) or value is None:
        return value
    return str(value)


class _NoArgs(BaseModel):
    pass


class ListTablesTool(Tool[_NoArgs]):
    name = "list_tables"
    description = "列出数据库中的表与视图，包含大致行数和业务说明。"
    input_model = _NoArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 10.0

    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def execute(self, args: _NoArgs, ctx: ToolContext) -> ToolResult:
        conn = connect_readonly(self.db_path)
        try:
            rows = conn.execute(
                "SELECT name, type FROM sqlite_master"
                " WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
                " ORDER BY type, name"
            ).fetchall()
            lines = []
            for row in rows:
                name, kind = row["name"], row["type"]
                try:
                    count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
                except sqlite3.Error:
                    count = "?"
                doc = TABLE_DOCS.get(name, "")
                lines.append(f"- {name}（{kind}，行数≈{count}）{doc}")
            return ToolResult(ok=True, content="数据库对象：\n" + "\n".join(lines))
        finally:
            conn.close()


class DescribeTableArgs(BaseModel):
    table: str = Field(..., description="表或视图名，必须来自 list_tables 的结果")


class DescribeTableTool(Tool[DescribeTableArgs]):
    name = "describe_table"
    description = "查看指定表的列定义、主键、外键与业务口径说明。"
    input_model = DescribeTableArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 10.0

    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def execute(self, args: DescribeTableArgs, ctx: ToolContext) -> ToolResult:
        if not _IDENT.match(args.table):
            return ToolResult(
                ok=False,
                content="表名非法：只接受字母、数字、下划线组成的标识符。",
                error=ToolError(code="invalid_table", message="invalid identifier"),
            )
        conn = connect_readonly(self.db_path)
        try:
            exists = conn.execute(
                "SELECT type FROM sqlite_master WHERE name=? AND type IN ('table','view')",
                (args.table,),
            ).fetchone()
            if exists is None:
                return ToolResult(
                    ok=False,
                    content=f"表不存在：{args.table}。请先调用 list_tables 查看可用表。",
                    error=ToolError(code="table_not_found", message="table not found"),
                )
            columns = conn.execute(
                'SELECT name, type, "notnull", dflt_value, pk FROM pragma_table_info(?)',
                (args.table,),
            ).fetchall()
            foreign_keys = conn.execute(
                'SELECT "table", "from", "to" FROM pragma_foreign_key_list(?)',
                (args.table,),
            ).fetchall()

            lines = [f"表 {args.table}：{TABLE_DOCS.get(args.table, '')}", "列："]
            for col in columns:
                parts = [f"- {col['name']} {col['type']}"]
                if col["pk"]:
                    parts.append("PRIMARY KEY")
                if col["notnull"]:
                    parts.append("NOT NULL")
                if col["dflt_value"] is not None:
                    parts.append(f"DEFAULT {col['dflt_value']}")
                lines.append(" ".join(parts))
            if foreign_keys:
                lines.append("外键：")
                for fk in foreign_keys:
                    lines.append(f"- {fk['from']} → {fk['table']}.{fk['to']}")
            return ToolResult(ok=True, content="\n".join(lines))
        finally:
            conn.close()


class RunSqlArgs(BaseModel):
    sql: str = Field(..., description="单条 SQLite SELECT 或 WITH 查询，不能包含多条语句")
    max_rows: int = Field(100, ge=1, le=200, description="最多返回行数（1-200）")


class RunSqlTool(Tool[RunSqlArgs]):
    name = "run_sql"
    description = "执行单条只读 SELECT/WITH 查询，返回 CSV 文本与结构化结果。"
    input_model = RunSqlArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 15.0

    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def execute(self, args: RunSqlArgs, ctx: ToolContext) -> ToolResult:
        sql = _strip_leading_comments(args.sql)
        if not sql or not _SELECT_PREFIX.match(sql):
            return ToolResult(
                ok=False,
                content="仅支持只读查询：SQL 必须以 SELECT 或 WITH 开头。"
                "若用户要求修改数据，请拒绝并说明数据库为只读。",
                error=ToolError(code="not_readonly", message="only select/with allowed"),
            )

        conn = connect_readonly(self.db_path)
        try:
            columns, rows, truncated = run_select(
                conn, sql, max_rows=args.max_rows, timeout_s=5.0
            )
        except sqlite3.Error as exc:
            return ToolResult(
                ok=False,
                content=f"SQL 执行失败：{exc}。请检查表名、列名或语法后重试。",
                error=ToolError(code="sql_error", message=str(exc)),
            )
        finally:
            conn.close()

        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_format_cell(v) for v in row])

        content = f"查询成功，返回 {len(rows)} 行：\n{buffer.getvalue()}"
        if truncated:
            content += f"\n[结果超过 {args.max_rows} 行，已截断]"
        return ToolResult(
            ok=True,
            content=content,
            data={
                "columns": columns,
                "rows": [[_jsonable(v) for v in row] for row in rows],
                "row_count": len(rows),
                "truncated": truncated,
            },
        )


def build_sql_tools(db_path: Path) -> list[Tool]:
    generate_demo_db(db_path)
    return [
        ListTablesTool(db_path),
        DescribeTableTool(db_path),
        RunSqlTool(db_path),
    ]
