from __future__ import annotations

import os
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, Field

from ..base import PermissionLevel, Tool, ToolContext, ToolError, ToolResult
from .sandbox import DockerSandbox, PathMap, SandboxResult

_MAX_WRITE_BYTES = 200_000
_MAX_READ_BYTES = 64 * 1024
_SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
}

SandboxRunner = Callable[[DockerSandbox], Awaitable[SandboxResult]]


class _PathEscape(Exception):
    def __init__(self, rel: str):
        super().__init__(rel)
        self.rel = rel


def _safe_path(root: Path, rel: str) -> Path:
    """把相对路径解析到工作区内；任何越界尝试都抛 _PathEscape。"""
    text = (rel or ".").strip()
    if not text or "\x00" in text:
        raise _PathEscape(rel)
    candidate = Path(text)
    if candidate.is_absolute() or candidate.drive:
        raise _PathEscape(rel)
    root = root.resolve()
    target = (root / candidate).resolve()
    if target != root and root not in target.parents:
        raise _PathEscape(rel)
    return target


def _escape_result(rel: str) -> ToolResult:
    return ToolResult(
        ok=False,
        content=f"拒绝访问工作区外的路径：{rel}。只能使用工作区内的相对路径。",
        error=ToolError(
            code="path_outside_workspace",
            message="path escapes workspace",
            hint="请改用工作区内的相对路径（例如 src/stats.py）",
        ),
    )


def _error(code: str, message: str, content: str) -> ToolResult:
    return ToolResult(ok=False, content=content, error=ToolError(code=code, message=message))


class ListFilesArgs(BaseModel):
    path: str = Field(".", description="工作区内的相对目录（默认工作区根）")
    max_depth: int = Field(3, ge=1, le=6, description="递归深度（1-6）")
    max_entries: int = Field(200, ge=1, le=500, description="最多列出的条目数")


class ListFilesTool(Tool[ListFilesArgs]):
    name = "list_files"
    description = "列出工作区内文件与目录（带文件大小）。不确定项目结构时先调用。"
    input_model = ListFilesArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 10.0

    async def execute(self, args: ListFilesArgs, ctx: ToolContext) -> ToolResult:
        try:
            target = _safe_path(ctx.workspace, args.path)
        except _PathEscape as exc:
            return _escape_result(exc.rel)
        if not target.exists():
            return _error(
                "path_not_found",
                "path not found",
                f"路径不存在：{args.path}。可用 list_files 从工作区根开始查看。",
            )

        lines: list[str] = []
        counter = {"n": 0, "truncated": False}

        def walk(directory: Path, depth: int) -> None:
            if counter["truncated"]:
                return
            try:
                entries = sorted(
                    directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
                )
            except OSError as exc:
                lines.append(f"{'  ' * depth}[无法读取 {directory.name}：{exc}]")
                return
            for entry in entries:
                if counter["n"] >= args.max_entries:
                    counter["truncated"] = True
                    return
                indent = "  " * depth
                try:
                    if entry.is_dir():
                        if entry.name in _SKIP_DIRS:
                            continue
                        lines.append(f"{indent}{entry.name}/")
                        counter["n"] += 1
                        if depth + 1 < args.max_depth:
                            walk(entry, depth + 1)
                    else:
                        size = entry.stat().st_size
                        lines.append(f"{indent}{entry.name} ({size} B)")
                        counter["n"] += 1
                except OSError:
                    continue

        if target.is_file():
            lines.append(f"{target.name} ({target.stat().st_size} B)")
            counter["n"] = 1
        else:
            walk(target, 0)

        heading = f"工作区 {args.path} 下的文件（共 {counter['n']} 项）："
        if counter["truncated"]:
            heading += f"\n[超过 {args.max_entries} 项，已截断]"
        return ToolResult(
            ok=True,
            content=heading + "\n" + "\n".join(lines),
            data={"entries": counter["n"], "truncated": counter["truncated"]},
        )


class ReadFileArgs(BaseModel):
    path: str = Field(..., description="工作区内文件的相对路径")
    max_lines: int = Field(200, ge=1, le=400, description="最多返回的行数")


class ReadFileTool(Tool[ReadFileArgs]):
    name = "read_file"
    description = "读取工作区内的 UTF-8 文本文件，返回带行号的代码，便于定位修改位置。"
    input_model = ReadFileArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 10.0

    async def execute(self, args: ReadFileArgs, ctx: ToolContext) -> ToolResult:
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
                "is_directory", "path is a directory", f"{args.path} 是目录，请用 list_files。"
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
                "binary_file", "binary file", f"{args.path} 看起来是二进制文件，无法按文本读取。"
            )
        text = raw.decode("utf-8", "replace")
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


class WriteFileArgs(BaseModel):
    path: str = Field(..., description="工作区内文件的相对路径")
    content: str = Field(..., max_length=_MAX_WRITE_BYTES, description="完整的新文件内容")


class WriteFileTool(Tool[WriteFileArgs]):
    name = "write_file"
    description = "覆盖写入工作区内的文件（自动创建父目录）。写入前应先 read_file 确认现有内容。"
    input_model = WriteFileArgs
    permission = int(PermissionLevel.WRITE)
    timeout_s = 15.0

    async def execute(self, args: WriteFileArgs, ctx: ToolContext) -> ToolResult:
        try:
            target = _safe_path(ctx.workspace, args.path)
        except _PathEscape as exc:
            return _escape_result(exc.rel)
        if target == ctx.workspace.resolve():
            return _error(
                "is_directory", "path is a directory", "不能把工作区目录本身当作文件写入。"
            )
        if target.exists() and target.is_dir():
            return _error(
                "is_directory", "path is a directory", f"{args.path} 是目录，不能写入文件。"
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _error("write_failed", str(exc), f"无法创建目录：{exc}")

        tmp_path: str | None = None
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=target.parent, prefix=".asagent-", suffix=".tmp"
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(args.content)
            os.replace(tmp_path, target)
            tmp_path = None
        except OSError as exc:
            return _error("write_failed", str(exc), f"写入 {args.path} 失败：{exc}")
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

        size = len(args.content.encode("utf-8"))
        return ToolResult(
            ok=True,
            content=f"已写入 {args.path}（{size} 字节）。",
            data={"path": args.path, "bytes": size},
        )


class RunTestsArgs(BaseModel):
    max_output_chars: int = Field(
        6000, ge=1000, le=12000, description="返回的测试输出最多字符数"
    )


class RunTestsTool(Tool[RunTestsArgs]):
    name = "run_tests"
    description = "在隔离的 Docker 沙箱中运行项目单元测试（无网络、只读根文件系统），返回退出码与输出。"
    input_model = RunTestsArgs
    permission = int(PermissionLevel.WRITE)
    timeout_s = 100.0

    def __init__(
        self,
        *,
        image: str,
        sandbox_timeout_s: float = 90.0,
        test_command: list[str] | None = None,
        runner: SandboxRunner | None = None,
        path_map: PathMap | None = None,
    ):
        self.image = image
        self.sandbox_timeout_s = sandbox_timeout_s
        self.test_command = test_command
        self.runner = runner
        self.path_map = list(path_map or [])

    async def execute(self, args: RunTestsArgs, ctx: ToolContext) -> ToolResult:
        tests_dir: Path | None = None
        raw_tests = ctx.extra.get("tests_dir")
        if raw_tests:
            tests_dir = Path(str(raw_tests))
            if not tests_dir.is_dir():
                return _error(
                    "tests_dir_missing",
                    "tests dir missing",
                    f"隐藏测试目录不存在：{tests_dir}。",
                )

        sandbox = DockerSandbox(
            image=self.image,
            workspace=ctx.workspace,
            tests_dir=tests_dir,
            test_command=self.test_command,
            timeout_s=self.sandbox_timeout_s,
            max_output_chars=args.max_output_chars,
            path_map=self.path_map,
        )
        runner: SandboxRunner = self.runner or (lambda box: box.run_tests())
        result = await runner(sandbox)

        heading = "测试通过 ✅" if result.exit_code == 0 else "测试未通过 ❌"
        content = (
            f"{heading}（退出码 {result.exit_code}"
            f"{'，超时' if result.timed_out else ''}）\n\n{result.output}"
        )
        error: ToolError | None = None
        if result.exit_code == -2:
            error = ToolError(code="sandbox_unavailable", message=result.output)
        elif result.exit_code != 0:
            error = ToolError(code="tests_failed", message=f"exit code {result.exit_code}")
        return ToolResult(
            ok=result.exit_code == 0,
            content=content,
            data={
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "tests_dir": str(tests_dir) if tests_dir else None,
            },
            error=error,
        )


def build_code_tools(
    *,
    image: str = "python:3.12-slim",
    sandbox_timeout_s: float = 90.0,
    test_command: list[str] | None = None,
    path_map: PathMap | None = None,
) -> list[Tool]:
    return [
        ListFilesTool(),
        ReadFileTool(),
        WriteFileTool(),
        RunTestsTool(
            image=image,
            sandbox_timeout_s=sandbox_timeout_s,
            test_command=test_command,
            path_map=path_map,
        ),
    ]
