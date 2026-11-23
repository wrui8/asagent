from __future__ import annotations

from pathlib import Path

import pytest

from asagent.tools.base import PermissionLevel, ToolContext
from asagent.tools.code.sandbox import SandboxResult
from asagent.tools.code.tools import (
    ListFilesArgs,
    ListFilesTool,
    ReadFileArgs,
    ReadFileTool,
    RunTestsArgs,
    RunTestsTool,
    WriteFileArgs,
    WriteFileTool,
    _PathEscape,
    _safe_path,
)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "a.py").write_text("print('hi')\n", encoding="utf-8")
    return ws


def _ctx(workspace: Path) -> ToolContext:
    return ToolContext(
        session_id="s1",
        profile="code",
        workspace=workspace,
        max_permission=int(PermissionLevel.WRITE),
    )


@pytest.mark.parametrize(
    "bad", ["../secret.txt", "src/../../x", "src/sub/../../../x"]
)
def test_safe_path_rejects_escape(workspace, bad):
    with pytest.raises(_PathEscape):
        _safe_path(workspace, bad)


def test_safe_path_rejects_absolute_path(workspace, tmp_path):
    outside = tmp_path / "outside.txt"
    with pytest.raises(_PathEscape):
        _safe_path(workspace, str(outside))


def test_safe_path_accepts_nested_file(workspace):
    assert _safe_path(workspace, "src/a.py") == (workspace / "src" / "a.py").resolve()
    assert _safe_path(workspace, ".") == workspace.resolve()


async def test_read_file_returns_numbered_lines(workspace):
    result = await ReadFileTool().execute(ReadFileArgs(path="src/a.py"), _ctx(workspace))
    assert result.ok
    assert "print('hi')" in result.content
    assert "   1 |" in result.content


async def test_read_file_rejects_outside_path(workspace):
    result = await ReadFileTool().execute(
        ReadFileArgs(path="../secret.txt"), _ctx(workspace)
    )
    assert not result.ok
    assert result.error is not None and result.error.code == "path_outside_workspace"


async def test_write_file_creates_parents(workspace):
    result = await WriteFileTool().execute(
        WriteFileArgs(path="pkg/new.py", content="x = 1\n"), _ctx(workspace)
    )
    assert result.ok
    assert (workspace / "pkg" / "new.py").read_text(encoding="utf-8") == "x = 1\n"


async def test_write_file_rejects_absolute_path(workspace, tmp_path):
    outside = tmp_path / "evil.txt"
    result = await WriteFileTool().execute(
        WriteFileArgs(path=str(outside), content="x"), _ctx(workspace)
    )
    assert not result.ok
    assert result.error is not None and result.error.code == "path_outside_workspace"


async def test_list_files_shows_tree(workspace):
    result = await ListFilesTool().execute(ListFilesArgs(), _ctx(workspace))
    assert result.ok
    assert "src/" in result.content
    assert "a.py" in result.content


async def test_run_tests_success_passes_tests_dir(workspace, tmp_path):
    tests = tmp_path / "hidden"
    tests.mkdir()
    seen = []

    async def fake_runner(box):
        seen.append(box)
        return SandboxResult(exit_code=0, output="OK")

    ctx = _ctx(workspace)
    ctx.extra["tests_dir"] = str(tests)
    tool = RunTestsTool(image="python:3.12-slim", runner=fake_runner)
    result = await tool.execute(RunTestsArgs(), ctx)

    assert result.ok
    assert tool.permission == int(PermissionLevel.WRITE)
    assert seen[0].tests_dir == tests.resolve()


async def test_run_tests_failure_sets_error_code(workspace):
    async def fake_runner(box):
        return SandboxResult(exit_code=1, output="FAILED")

    result = await RunTestsTool(image="img", runner=fake_runner).execute(
        RunTestsArgs(), _ctx(workspace)
    )
    assert not result.ok
    assert result.error is not None and result.error.code == "tests_failed"


async def test_run_tests_sandbox_unavailable(workspace):
    async def fake_runner(box):
        return SandboxResult(exit_code=-2, output="docker missing")

    result = await RunTestsTool(image="img", runner=fake_runner).execute(
        RunTestsArgs(), _ctx(workspace)
    )
    assert not result.ok
    assert result.error is not None and result.error.code == "sandbox_unavailable"


async def test_run_tests_missing_tests_dir(workspace, tmp_path):
    async def fake_runner(box):
        raise AssertionError("不应启动沙箱")

    ctx = _ctx(workspace)
    ctx.extra["tests_dir"] = str(tmp_path / "nope")
    result = await RunTestsTool(image="img", runner=fake_runner).execute(
        RunTestsArgs(), ctx
    )
    assert not result.ok
    assert result.error is not None and result.error.code == "tests_dir_missing"
