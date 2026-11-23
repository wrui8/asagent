from __future__ import annotations

import pytest

from asagent.tools.base import ToolContext
from asagent.tools.sql.demo import generate_demo_db
from asagent.tools.sql.tools import (
    DescribeTableArgs,
    DescribeTableTool,
    ListTablesTool,
    RunSqlArgs,
    RunSqlTool,
    _NoArgs,
)


@pytest.fixture(scope="module")
def demo_db(tmp_path_factory):
    path = tmp_path_factory.mktemp("sql") / "demo.sqlite3"
    generate_demo_db(path)
    return path


def ctx(demo_db) -> ToolContext:
    return ToolContext(
        session_id="s_test", profile="sql", workspace=demo_db.parent, max_permission=0
    )


async def test_list_tables(demo_db):
    result = await ListTablesTool(demo_db).execute(_NoArgs(), ctx(demo_db))
    assert result.ok
    assert "orders" in result.content
    assert "v_order_revenue" in result.content


async def test_describe_table(demo_db):
    result = await DescribeTableTool(demo_db).execute(
        DescribeTableArgs(table="orders"), ctx(demo_db)
    )
    assert result.ok
    assert "customer_id" in result.content
    assert "PRIMARY KEY" in result.content


async def test_describe_table_rejects_bad_identifier(demo_db):
    result = await DescribeTableTool(demo_db).execute(
        DescribeTableArgs(table="orders; DROP TABLE orders"), ctx(demo_db)
    )
    assert not result.ok
    assert result.error.code == "invalid_table"


async def test_run_sql_select(demo_db):
    result = await RunSqlTool(demo_db).execute(
        RunSqlArgs(sql="SELECT COUNT(*) FROM orders"), ctx(demo_db)
    )
    assert result.ok
    assert result.data["rows"][0][0] == 3000


async def test_run_sql_supports_table_valued_pragma(demo_db):
    result = await RunSqlTool(demo_db).execute(
        RunSqlArgs(sql="SELECT name FROM pragma_table_info('orders') LIMIT 1"),
        ctx(demo_db),
    )
    assert result.ok


async def test_run_sql_rejects_write_statements(demo_db):
    for sql in (
        "DELETE FROM orders",
        "UPDATE orders SET status='x'",
        "DROP TABLE orders",
        "PRAGMA query_only=OFF",
    ):
        result = await RunSqlTool(demo_db).execute(RunSqlArgs(sql=sql), ctx(demo_db))
        assert not result.ok
        assert result.error.code == "not_readonly"


async def test_authorizer_blocks_unlisted_functions(demo_db):
    result = await RunSqlTool(demo_db).execute(
        RunSqlArgs(sql="SELECT load_extension('evil')"), ctx(demo_db)
    )
    assert not result.ok
    assert result.error.code == "sql_error"


async def test_leading_comment_then_select(demo_db):
    result = await RunSqlTool(demo_db).execute(
        RunSqlArgs(sql="-- 统计\nSELECT 1 AS ok"), ctx(demo_db)
    )
    assert result.ok
    assert result.data["rows"][0][0] == 1
