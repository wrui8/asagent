from __future__ import annotations

import csv
import hashlib
import io
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field

from ..base import PermissionLevel, Tool, ToolContext, ToolResult
from ..code.tools import (
    ListFilesTool,
    _error,
    _escape_result,
    _PathEscape,
    _safe_path,
)
from .backup import backup_file

_MAX_WRITE_BYTES = 200_000
_MAX_READ_BYTES = 64 * 1024
_TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".log"}


def _supported_suffixes() -> str:
    return "/".join(sorted(_TEXT_SUFFIXES))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write(target: Path, content: str) -> None:
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=target.parent, prefix=".asagent-", suffix=".tmp"
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        os.replace(tmp_path, target)
        tmp_path = None
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


class OfficeListFilesTool(ListFilesTool):
    description = (
        "列出办公工作区内的文件与目录（带文件大小）。不确定目录结构时先调用。"
    )


class ReadDocumentArgs(BaseModel):
    path: str = Field(..., description="工作区内文件的相对路径")
    max_lines: int = Field(200, ge=1, le=400, description="最多返回的行数")


class ReadDocumentTool(Tool[ReadDocumentArgs]):
    name = "read_document"
    description = (
        "读取工作区内的文档：文本/Markdown 带行号；CSV 按行列预览（第 1 行为表头）。"
        f"支持 {_supported_suffixes()}。"
    )
    input_model = ReadDocumentArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 10.0

    async def execute(self, args: ReadDocumentArgs, ctx: ToolContext) -> ToolResult:
        try:
            target = _safe_path(ctx.workspace, args.path)
        except _PathEscape as exc:
            return _escape_result(exc.rel)
        if not target.exists():
            return _error(
                "path_not_found", "path not found", f"文件不存在：{args.path}。"
            )
        if target.is_dir():
            return _error(
                "is_directory",
                "path is a directory",
                f"{args.path} 是目录，请用 list_files。",
            )
        suffix = target.suffix.lower()
        if suffix not in _TEXT_SUFFIXES:
            return _error(
                "unsupported_file",
                "unsupported file type",
                f"暂不支持 {suffix or '无后缀'} 文件；支持 {_supported_suffixes()}。",
            )
        try:
            size = target.stat().st_size
        except OSError as exc:
            return _error("read_failed", str(exc), f"无法读取 {args.path}：{exc}")
        if size > _MAX_READ_BYTES:
            return _error(
                "file_too_large",
                "file too large",
                f"{args.path} 有 {size} 字节，超过 {_MAX_READ_BYTES} 字节上限。",
            )
        try:
            raw = target.read_bytes()
        except OSError as exc:
            return _error("read_failed", str(exc), f"无法读取 {args.path}：{exc}")
        if b"\x00" in raw[:4096]:
            return _error(
                "binary_file",
                "binary file",
                f"{args.path} 看起来是二进制文件，无法按文本读取。",
            )
        text = raw.decode("utf-8", "replace")
        if suffix == ".csv":
            return self._csv_preview(args.path, text, args.max_lines)

        lines = text.splitlines()
        selected = lines[: args.max_lines]
        body = "\n".join(f"{i:>4} | {line}" for i, line in enumerate(selected, 1))
        if len(lines) > args.max_lines:
            body += f"\n…（共 {len(lines)} 行，已显示前 {args.max_lines} 行）"
        return ToolResult(
            ok=True,
            content=f"{args.path}：\n{body}",
            data={"path": args.path, "lines": len(lines), "shown": len(selected)},
        )

    @staticmethod
    def _csv_preview(rel: str, text: str, max_lines: int) -> ToolResult:
        rows = list(csv.reader(text.splitlines()))
        if not rows:
            return ToolResult(ok=True, content=f"{rel} 是空 CSV。", data={"rows": 0})
        selected = rows[:max_lines]
        body = "\n".join(
            f"{i:>4} | " + " | ".join(row) for i, row in enumerate(selected, 1)
        )
        if len(rows) > max_lines:
            body += f"\n…（共 {len(rows)} 行，已显示前 {max_lines} 行）"
        return ToolResult(
            ok=True,
            content=(
                f"{rel}（CSV，共 {len(rows)} 行；第 1 行为表头）：\n{body}"
            ),
            data={"path": rel, "rows": len(rows), "shown": len(selected)},
        )


class WriteDocumentArgs(BaseModel):
    path: str = Field(..., description="工作区内文件的相对路径")
    content: str = Field(..., max_length=_MAX_WRITE_BYTES, description="完整的新文件内容")


class WriteDocumentTool(Tool[WriteDocumentArgs]):
    name = "write_document"
    description = (
        "覆盖写入工作区内的文档（自动创建父目录）。覆盖已有文件前会备份到会话备份目录。"
        "本操作需要人工审批。写入前应先 read_document 确认现有内容。"
    )
    input_model = WriteDocumentArgs
    permission = int(PermissionLevel.DANGEROUS)
    timeout_s = 20.0

    def __init__(self, *, backup_root: Path):
        self.backup_root = backup_root

    async def execute(self, args: WriteDocumentArgs, ctx: ToolContext) -> ToolResult:
        try:
            target = _safe_path(ctx.workspace, args.path)
        except _PathEscape as exc:
            return _escape_result(exc.rel)
        suffix = target.suffix.lower()
        if suffix not in _TEXT_SUFFIXES:
            return _error(
                "unsupported_file",
                "unsupported file type",
                f"暂不支持写入 {suffix or '无后缀'} 文件；支持 {_supported_suffixes()}。",
            )
        if target == ctx.workspace.resolve() or (target.exists() and target.is_dir()):
            return _error(
                "is_directory", "path is a directory", f"{args.path} 是目录，不能写入。"
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _error("write_failed", str(exc), f"无法创建目录：{exc}")

        try:
            backup = backup_file(
                target, backup_root=self.backup_root, session_id=ctx.session_id
            )
        except OSError as exc:
            return _error(
                "backup_failed", str(exc), f"备份原文件失败，已取消写入：{exc}"
            )

        try:
            _atomic_write(target, args.content)
        except OSError as exc:
            return _error("write_failed", str(exc), f"写入 {args.path} 失败：{exc}")

        size = len(args.content.encode("utf-8"))
        digest = hashlib.sha256(args.content.encode("utf-8")).hexdigest()
        backup_note = f"，原文件已备份到 {backup}" if backup else ""
        return ToolResult(
            ok=True,
            content=f"已写入 {args.path}（{size} 字节）{backup_note}。",
            data={
                "path": args.path,
                "bytes": size,
                "backup": str(backup) if backup else None,
                "sha256": digest,
            },
        )


class UpdateCsvCellArgs(BaseModel):
    path: str = Field(..., description="工作区内 CSV 文件的相对路径")
    row: int = Field(..., ge=1, description="1-based 行号（第 1 行为表头）")
    column: str = Field(..., description="列名（表头文本）或 1-based 列序号")
    value: str = Field(..., max_length=10_000, description="新的单元格文本")


class UpdateCsvCellTool(Tool[UpdateCsvCellArgs]):
    name = "update_csv_cell"
    description = (
        "修改 CSV 中一个单元格（按行号与列名/列序号），修改前自动备份原文件。"
        "本操作需要人工审批。"
    )
    input_model = UpdateCsvCellArgs
    permission = int(PermissionLevel.DANGEROUS)
    timeout_s = 20.0

    def __init__(self, *, backup_root: Path):
        self.backup_root = backup_root

    async def execute(self, args: UpdateCsvCellArgs, ctx: ToolContext) -> ToolResult:
        try:
            target = _safe_path(ctx.workspace, args.path)
        except _PathEscape as exc:
            return _escape_result(exc.rel)
        if target.suffix.lower() != ".csv":
            return _error(
                "unsupported_file", "not a csv file", f"{args.path} 不是 .csv 文件。"
            )
        if not target.is_file():
            return _error(
                "path_not_found", "file not found", f"文件不存在：{args.path}。"
            )
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return _error("read_failed", str(exc), f"无法读取 {args.path}：{exc}")
        rows = list(csv.reader(io.StringIO(text, newline="")))
        if not rows:
            return _error("empty_csv", "empty csv", f"{args.path} 是空 CSV。")
        header = rows[0]

        if args.column.isdigit():
            index = int(args.column) - 1
            if index < 0 or index >= len(header):
                return _error(
                    "column_out_of_range",
                    "column out of range",
                    f"列序号 {args.column} 超出范围（共 {len(header)} 列）。",
                )
        else:
            wanted = args.column.strip().lower()
            matches = [
                i for i, name in enumerate(header) if name.strip().lower() == wanted
            ]
            if len(matches) != 1:
                return _error(
                    "column_not_found",
                    "column not found",
                    f"找不到唯一列：{args.column}。表头为 {header}。",
                )
            index = matches[0]

        if args.row > len(rows):
            return _error(
                "row_out_of_range",
                "row out of range",
                f"行号 {args.row} 超出范围（共 {len(rows)} 行，含表头）。",
            )
        row_values = list(rows[args.row - 1])
        while len(row_values) <= index:
            row_values.append("")
        old_value = row_values[index]
        row_values[index] = args.value
        rows[args.row - 1] = row_values

        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerows(rows)
        new_text = buffer.getvalue()
        try:
            backup = backup_file(
                target, backup_root=self.backup_root, session_id=ctx.session_id
            )
        except OSError as exc:
            return _error(
                "backup_failed", str(exc), f"备份原文件失败，已取消修改：{exc}"
            )
        try:
            _atomic_write(target, new_text)
        except OSError as exc:
            return _error("write_failed", str(exc), f"写入 {args.path} 失败：{exc}")

        digest = hashlib.sha256(new_text.encode("utf-8")).hexdigest()
        return ToolResult(
            ok=True,
            content=(
                f"已更新 {args.path} 第 {args.row} 行第 {index + 1} 列："
                f"{old_value!r} -> {args.value!r}；原文件已备份到 {backup}。"
            ),
            data={
                "path": args.path,
                "row": args.row,
                "column": index + 1,
                "old": old_value,
                "new": args.value,
                "backup": str(backup) if backup else None,
                "sha256": digest,
            },
        )


class DeleteDocumentArgs(BaseModel):
    path: str = Field(..., description="工作区内要删除的文件相对路径")


class DeleteDocumentTool(Tool[DeleteDocumentArgs]):
    name = "delete_document"
    description = (
        "删除工作区内的单个文件；删除前会备份到会话备份目录（可恢复）。"
        "本操作需要人工审批。"
    )
    input_model = DeleteDocumentArgs
    permission = int(PermissionLevel.DANGEROUS)
    timeout_s = 15.0

    def __init__(self, *, backup_root: Path):
        self.backup_root = backup_root

    async def execute(
        self, args: DeleteDocumentArgs, ctx: ToolContext
    ) -> ToolResult:
        try:
            target = _safe_path(ctx.workspace, args.path)
        except _PathEscape as exc:
            return _escape_result(exc.rel)
        if not target.exists():
            return _error(
                "path_not_found", "file not found", f"文件不存在：{args.path}。"
            )
        if not target.is_file():
            return _error(
                "is_directory", "not a file", f"{args.path} 是目录，不能删除。"
            )
        try:
            before = _file_sha256(target)
            backup = backup_file(
                target, backup_root=self.backup_root, session_id=ctx.session_id
            )
        except OSError as exc:
            return _error(
                "backup_failed", str(exc), f"备份失败，已取消删除：{exc}"
            )
        try:
            target.unlink()
        except OSError as exc:
            return _error("delete_failed", str(exc), f"删除 {args.path} 失败：{exc}")

        return ToolResult(
            ok=True,
            content=f"已删除 {args.path}，原文件备份在 {backup}（可恢复）。",
            data={
                "path": args.path,
                "backup": str(backup) if backup else None,
                "sha256": before,
                "deleted": True,
            },
        )


def build_office_tools(*, backup_root: Path) -> list[Tool]:
    return [
        OfficeListFilesTool(),
        ReadDocumentTool(),
        WriteDocumentTool(backup_root=backup_root),
        UpdateCsvCellTool(backup_root=backup_root),
        DeleteDocumentTool(backup_root=backup_root),
    ]
