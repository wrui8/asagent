from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from asagent.tools.base import PermissionLevel, ToolContext
from asagent.tools.office.tools import (
    DeleteDocumentArgs,
    DeleteDocumentTool,
    ReadDocumentArgs,
    ReadDocumentTool,
    UpdateCsvCellArgs,
    UpdateCsvCellTool,
    WriteDocumentArgs,
    WriteDocumentTool,
    build_office_tools,
)


def make_ctx(workspace: Path) -> ToolContext:
    return ToolContext(
        session_id="s_office",
        profile="office",
        workspace=workspace,
        max_permission=int(PermissionLevel.DANGEROUS),
    )


def test_write_document_creates_file(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tool = WriteDocumentTool(backup_root=tmp_path / "backups")
    result = asyncio.run(
        tool.execute(
            WriteDocumentArgs(path="summary.md", content="# 总结\n\n完成。"),
            make_ctx(workspace),
        )
    )

    assert result.ok
    assert (workspace / "summary.md").read_text(encoding="utf-8") == "# 总结\n\n完成。"
    assert result.data["backup"] is None
    assert result.data["sha256"] == hashlib.sha256(
        "# 总结\n\n完成。".encode()
    ).hexdigest()


def test_write_document_backs_up_old_content(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "notes.md"
    target.write_text("旧内容\n", encoding="utf-8")
    tool = WriteDocumentTool(backup_root=tmp_path / "backups")
    result = asyncio.run(
        tool.execute(
            WriteDocumentArgs(path="notes.md", content="新内容\n"),
            make_ctx(workspace),
        )
    )

    assert result.ok
    assert target.read_text(encoding="utf-8") == "新内容\n"
    backup = Path(result.data["backup"])
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == "旧内容\n"
    assert backup.parent.name == "s_office"


def test_write_document_rejects_path_escape(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tool = WriteDocumentTool(backup_root=tmp_path / "backups")
    result = asyncio.run(
        tool.execute(
            WriteDocumentArgs(path="../outside.md", content="x"),
            make_ctx(workspace),
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "path_outside_workspace"
    assert not (tmp_path / "outside.md").exists()


def test_write_document_rejects_unsupported_suffix(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tool = WriteDocumentTool(backup_root=tmp_path / "backups")
    result = asyncio.run(
        tool.execute(
            WriteDocumentArgs(path="a.pdf", content="x"), make_ctx(workspace)
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "unsupported_file"


def test_update_csv_cell(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "budget.csv"
    target.write_text("部门,预算\n研发,1000\n市场,800\n", encoding="utf-8")
    tool = UpdateCsvCellTool(backup_root=tmp_path / "backups")
    result = asyncio.run(
        tool.execute(
            UpdateCsvCellArgs(path="budget.csv", row=2, column="预算", value="1500"),
            make_ctx(workspace),
        )
    )

    assert result.ok
    assert result.data["old"] == "1000"
    assert result.data["new"] == "1500"
    assert "研发,1500" in target.read_text(encoding="utf-8")
    backup = Path(result.data["backup"])
    assert "研发,1000" in backup.read_text(encoding="utf-8")


def test_update_csv_cell_unknown_column(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "budget.csv"
    target.write_text("部门,预算\n研发,1000\n", encoding="utf-8")
    tool = UpdateCsvCellTool(backup_root=tmp_path / "backups")
    result = asyncio.run(
        tool.execute(
            UpdateCsvCellArgs(path="budget.csv", row=2, column="不存在", value="1"),
            make_ctx(workspace),
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "column_not_found"
    assert "研发,1000" in target.read_text(encoding="utf-8")


def test_update_csv_cell_row_out_of_range(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "budget.csv"
    target.write_text("部门,预算\n研发,1000\n", encoding="utf-8")
    tool = UpdateCsvCellTool(backup_root=tmp_path / "backups")
    result = asyncio.run(
        tool.execute(
            UpdateCsvCellArgs(path="budget.csv", row=9, column="预算", value="1"),
            make_ctx(workspace),
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "row_out_of_range"


def test_delete_document_moves_to_backup(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = workspace / "confidential.md"
    target.write_text("机密内容\n", encoding="utf-8")
    tool = DeleteDocumentTool(backup_root=tmp_path / "backups")
    result = asyncio.run(
        tool.execute(DeleteDocumentArgs(path="confidential.md"), make_ctx(workspace))
    )

    assert result.ok
    assert not target.exists()
    backup = Path(result.data["backup"])
    assert backup.read_text(encoding="utf-8") == "机密内容\n"
    assert result.data["deleted"] is True


def test_delete_document_rejects_directory(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "folder").mkdir()
    tool = DeleteDocumentTool(backup_root=tmp_path / "backups")
    result = asyncio.run(
        tool.execute(DeleteDocumentArgs(path="folder"), make_ctx(workspace))
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.code == "is_directory"


def test_read_document_text_and_csv(tmp_path: Path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "notes.md").write_text("第一行\n第二行\n", encoding="utf-8")
    (workspace / "budget.csv").write_text(
        "部门,预算\n研发,1000\n", encoding="utf-8"
    )
    tool = ReadDocumentTool()

    text_result = asyncio.run(
        tool.execute(ReadDocumentArgs(path="notes.md"), make_ctx(workspace))
    )
    assert text_result.ok
    assert "1 | 第一行" in text_result.content
    assert "2 | 第二行" in text_result.content

    csv_result = asyncio.run(
        tool.execute(ReadDocumentArgs(path="budget.csv"), make_ctx(workspace))
    )
    assert csv_result.ok
    assert "部门 | 预算" in csv_result.content
    assert "研发 | 1000" in csv_result.content


def test_write_tools_require_dangerous_permission(tmp_path: Path):
    tools = build_office_tools(backup_root=tmp_path / "backups")
    by_name = {tool.name: tool for tool in tools}
    for name in ("write_document", "update_csv_cell", "delete_document"):
        assert int(by_name[name].permission) == int(PermissionLevel.DANGEROUS)
    for name in ("list_files", "read_document"):
        assert int(by_name[name].permission) == int(PermissionLevel.READ)
