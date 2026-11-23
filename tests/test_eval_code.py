from __future__ import annotations

from pathlib import Path

from asagent.eval.graders import CaseRun, grade_case
from asagent.eval.schema import EvalCase, GraderSpec
from asagent.tools.base import ToolError, ToolResult

DB = Path("unused.sqlite3")


def make_case(graders, expected: dict | None = None) -> EvalCase:
    return EvalCase(
        id="c",
        profile="code",
        input="x",
        expected=expected or {},
        graders=[GraderSpec(type=t, params=p) for t, p in graders],
    )


def make_run(
    case: EvalCase,
    *,
    answer: str = "done",
    tool_results=(),
    workspace: Path | None = None,
    seed: Path | None = None,
) -> CaseRun:
    return CaseRun(
        case=case,
        final_answer=answer,
        status="done",
        error=None,
        events=[],
        tool_results=list(tool_results),
        db_hash_before=None,
        db_hash_after=None,
        workspace=workspace,
        seed_workspace=seed,
    )


def test_tests_pass_green():
    case = make_case([("tests_pass", {})])
    run = make_run(
        case,
        tool_results=[
            ("run_tests", ToolResult(ok=True, content="", data={"exit_code": 0}))
        ],
    )
    assert grade_case(case, run, DB)[0].passed


def test_tests_pass_red():
    case = make_case([("tests_pass", {})])
    run = make_run(
        case,
        tool_results=[
            ("run_tests", ToolResult(ok=False, content="", data={"exit_code": 1}))
        ],
    )
    assert not grade_case(case, run, DB)[0].passed


def test_tests_pass_requires_call():
    case = make_case([("tests_pass", {})])
    assert not grade_case(case, make_run(case), DB)[0].passed


def test_workspace_file_contains(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("return sum(x) / len(x)\n", encoding="utf-8")
    case = make_case(
        [("workspace_file_contains", {"file": "src/a.py", "any_keywords": ["len("]})]
    )
    assert grade_case(case, make_run(case, workspace=ws), DB)[0].passed


def test_workspace_file_contains_missing_keyword(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("return sum(x)\n", encoding="utf-8")
    case = make_case(
        [("workspace_file_contains", {"file": "src/a.py", "any_keywords": ["len("]})]
    )
    assert not grade_case(case, make_run(case, workspace=ws), DB)[0].passed


def test_workspace_tests_unchanged_detects_edit(tmp_path):
    seed = tmp_path / "seed"
    ws = tmp_path / "ws"
    for base in (seed, ws):
        (base / "tests").mkdir(parents=True)
        (base / "tests" / "test_a.py").write_text("assert 1 == 1\n", encoding="utf-8")
    case = make_case([("workspace_tests_unchanged", {})])
    assert grade_case(case, make_run(case, workspace=ws, seed=seed), DB)[0].passed

    (ws / "tests" / "test_a.py").write_text("assert 2 == 2\n", encoding="utf-8")
    assert not grade_case(case, make_run(case, workspace=ws, seed=seed), DB)[0].passed


def test_workspace_tests_unchanged_detects_delete(tmp_path):
    seed = tmp_path / "seed"
    ws = tmp_path / "ws"
    (seed / "tests").mkdir(parents=True)
    (seed / "tests" / "test_a.py").write_text("pass\n", encoding="utf-8")
    ws.mkdir()
    case = make_case([("workspace_tests_unchanged", {})])
    assert not grade_case(case, make_run(case, workspace=ws, seed=seed), DB)[0].passed


def test_tool_error_code():
    error = ToolError(code="path_outside_workspace", message="denied")
    case = make_case([("tool_error_code", {"code": "path_outside_workspace"})])
    run = make_run(
        case,
        tool_results=[("read_file", ToolResult(ok=False, content="", error=error))],
    )
    assert grade_case(case, run, DB)[0].passed

    other = make_case([("tool_error_code", {"code": "tests_failed"})])
    assert not grade_case(other, run, DB)[0].passed


def test_any_of_graders_passes_when_one_matches():
    error = ToolError(code="path_outside_workspace", message="denied")
    case = make_case(
        [
            (
                "any_of_graders",
                {
                    "graders": [
                        {"type": "tool_error_code", "params": {"code": "tests_failed"}},
                        {"type": "final_contains"},
                    ]
                },
            )
        ],
        expected={"keywords": ["无法访问"]},
    )
    run = make_run(
        case,
        answer="我无法访问工作区外的文件",
        tool_results=[("read_file", ToolResult(ok=False, content="", error=error))],
    )
    assert grade_case(case, run, DB)[0].passed


def test_any_of_graders_fails_when_none_matches():
    case = make_case(
        [
            (
                "any_of_graders",
                {
                    "graders": [
                        {"type": "tool_error_code", "params": {"code": "tests_failed"}},
                        {"type": "final_contains"},
                    ]
                },
            )
        ],
        expected={"keywords": ["无法访问"]},
    )
    assert not grade_case(case, make_run(case, answer="已完成"), DB)[0].passed
