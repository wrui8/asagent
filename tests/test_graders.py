from __future__ import annotations

import sqlite3

import pytest

from asagent.eval.graders import CaseRun, grade_sql_result_set
from asagent.eval.schema import EvalCase, GraderSpec
from asagent.tools.base import ToolResult


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "g.sqlite3"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE sales (category TEXT, qty INTEGER, price REAL);
        INSERT INTO sales VALUES ('A', 2, 10.0), ('B', 3, 5.5), ('B', 1, 5.5);
        """
    )
    con.commit()
    con.close()
    return path


def make_case(sql: str, **expected) -> EvalCase:
    return EvalCase(
        id="t",
        input="q",
        expected={"gold_sql": sql, **expected},
        graders=[GraderSpec(type="sql_result_set")],
    )


def make_run(columns, rows) -> CaseRun:
    return CaseRun(
        case=None,  # type: ignore[arg-type]
        final_answer=None,
        status="done",
        error=None,
        events=[],
        tool_results=[
            (
                "run_sql",
                ToolResult(ok=True, content="", data={"columns": columns, "rows": rows}),
            )
        ],
        db_hash_before=None,
        db_hash_after=None,
    )


SQL = "SELECT category, SUM(qty*price) AS revenue FROM sales GROUP BY category ORDER BY category"


def test_exact_match_passes(db):
    case = make_case(SQL)
    run = make_run(["category", "revenue"], [["A", 20.0], ["B", 22.0]])
    assert grade_sql_result_set(case, run, {}, db).passed


def test_extra_columns_are_projected_by_name(db):
    case = make_case(SQL)
    run = make_run(
        ["category", "qty_total", "revenue"],
        [["A", 2, 20.0], ["B", 4, 22.0]],
    )
    result = grade_sql_result_set(case, run, {}, db)
    assert result.passed, result.reason


def test_column_order_is_aligned_by_name(db):
    case = make_case(SQL)
    run = make_run(["revenue", "category"], [[20.0, "A"], [22.0, "B"]])
    assert grade_sql_result_set(case, run, {}, db).passed


def test_extra_rows_still_fail(db):
    case = make_case(SQL)
    run = make_run(
        ["category", "revenue", "note"],
        [["A", 20.0, "x"], ["B", 22.0, "y"], ["C", 1.0, "z"]],
    )
    assert not grade_sql_result_set(case, run, {}, db).passed


def test_unmatched_columns_fall_back_to_positional(db):
    # 列名对不上（中文别名）但列数一致：按位置比较，值一致即通过
    case = make_case(SQL)
    run = make_run(["品类", "金额"], [["A", 20.0], ["B", 22.0]])
    assert grade_sql_result_set(case, run, {}, db).passed


def test_alias_columns_matched_by_subset(db):
    # 模型列名是中文别名且多了辅助列：枚举列子集后能对齐
    case = make_case(SQL)
    run = make_run(["品类", "数量", "金额"], [["A", 2, 20.0], ["B", 4, 22.0]])
    assert grade_sql_result_set(case, run, {}, db).passed


def test_wrong_values_with_extra_columns_fail(db):
    case = make_case(SQL)
    run = make_run(["品类", "数量", "金额"], [["A", 2, 21.0], ["B", 4, 22.0]])
    assert not grade_sql_result_set(case, run, {}, db).passed


def test_numeric_tolerance(db):
    case = make_case(SQL, numeric_tolerance=0.5)
    run = make_run(["category", "revenue"], [["A", 20.4], ["B", 22.0]])
    assert grade_sql_result_set(case, run, {}, db).passed
