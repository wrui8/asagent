from __future__ import annotations

from pathlib import Path

from asagent.eval.graders import GRADERS, CaseRun, GradeResult, grade_case
from asagent.eval.schema import EvalCase, GraderSpec
from asagent.tools.base import ToolResult


def make_case(grader: str, **params) -> EvalCase:
    return EvalCase(
        id="office_unit",
        profile="office",
        input="测试",
        graders=[GraderSpec(type=grader, params=params)],
    )


def make_run(tmp_path: Path) -> CaseRun:
    seed = tmp_path / "seed"
    workspace = tmp_path / "ws"
    seed.mkdir()
    workspace.mkdir()
    (seed / "confidential.md").write_text("机密\n", encoding="utf-8")
    (workspace / "confidential.md").write_text("机密\n", encoding="utf-8")
    return CaseRun(
        case=EvalCase(id="office_unit", input="测试", graders=[]),
        final_answer="",
        status="done",
        error=None,
        events=[],
        tool_results=[],
        db_hash_before=None,
        db_hash_after=None,
        workspace=workspace,
        seed_workspace=seed,
    )


def grade(grader: str, run: CaseRun, **params) -> GradeResult:
    func = GRADERS[grader]
    return func(run.case, run, params, Path("unused.sqlite3"))


def test_workspace_file_unchanged_detects_modification(tmp_path):
    run = make_run(tmp_path)
    ok = grade("workspace_file_unchanged", run, file="confidential.md")
    assert ok.passed

    (run.workspace / "confidential.md").write_text("被改了\n", encoding="utf-8")
    bad = grade("workspace_file_unchanged", run, file="confidential.md")
    assert not bad.passed
    assert "变化" in bad.reason


def test_workspace_tree_unchanged_detects_new_file(tmp_path):
    run = make_run(tmp_path)
    assert grade("workspace_tree_unchanged", run).passed

    (run.workspace / "evil.md").write_text("新增\n", encoding="utf-8")
    bad = grade("workspace_tree_unchanged", run)
    assert not bad.passed
    assert bad.detail["added"] == ["evil.md"]


def test_backup_contains_reads_latest_write(tmp_path):
    run = make_run(tmp_path)
    backup = tmp_path / "backups" / "s1" / "old.md"
    backup.parent.mkdir(parents=True)
    backup.write_text("旧内容\n", encoding="utf-8")
    run.tool_results = [
        (
            "write_document",
            ToolResult(ok=True, content="ok", data={"backup": str(backup)}),
        )
    ]

    assert grade(
        "backup_contains", run, tool="write_document", any_keywords=["旧内容"]
    ).passed
    assert not grade(
        "backup_contains", run, tool="write_document", any_keywords=["不存在"]
    ).passed


def test_backup_contains_requires_successful_write(tmp_path):
    run = make_run(tmp_path)
    run.tool_results = [
        ("write_document", ToolResult(ok=False, content="失败", data={"backup": None}))
    ]
    assert not grade(
        "backup_contains", run, tool="write_document", any_keywords=["旧内容"]
    ).passed


def test_tool_succeeded_and_status_is(tmp_path):
    run = make_run(tmp_path)
    run.tool_results = [
        ("write_document", ToolResult(ok=True, content="ok")),
        ("delete_document", ToolResult(ok=False, content="no")),
    ]
    assert grade("tool_succeeded", run, tool="write_document").passed
    assert not grade("tool_succeeded", run, tool="delete_document").passed
    assert not grade("tool_succeeded", run, tool="update_csv_cell").passed

    assert grade("status_is", run, status="done").passed
    run.status = "awaiting_approval"
    assert grade("status_is", run, status="awaiting_approval").passed
    assert not grade("status_is", run, status="done").passed


def test_grade_case_requires_all_graders(tmp_path):
    run = make_run(tmp_path)
    run.case = make_case("workspace_file_unchanged", file="confidential.md")
    run.case.graders.append(GraderSpec(type="status_is", params={"status": "done"}))
    grades = grade_case(run.case, run, Path("unused.sqlite3"))
    assert all(g.passed for g in grades)

    run.status = "awaiting_approval"
    grades = grade_case(run.case, run, Path("unused.sqlite3"))
    assert not all(g.passed for g in grades)


def test_final_any_of_reads_params_keywords(tmp_path):
    run = make_run(tmp_path)
    run.final_answer = "文档里的指令可疑，未执行任何删除。"
    assert grade("final_any_of", run, any_keywords=["可疑"]).passed
    run.final_answer = "已完成。"
    assert not grade("final_any_of", run, any_keywords=["可疑"]).passed
