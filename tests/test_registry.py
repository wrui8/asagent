from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from asagent.tools.base import PermissionLevel, Tool, ToolContext, ToolResult
from asagent.tools.registry import ToolRegistry


class EchoArgs(BaseModel):
    text: str = Field(description="要回显的文本")


class EchoTool(Tool[EchoArgs]):
    name = "echo"
    description = "回显文本"
    input_model = EchoArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 5.0

    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, args: EchoArgs, ctx: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(ok=True, content=args.text)


class SlowTool(EchoTool):
    name = "slow"
    timeout_s = 0.05

    async def execute(self, args: EchoArgs, ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(0.5)
        return ToolResult(ok=True, content="late")


class WriteTool(EchoTool):
    name = "write"
    permission = int(PermissionLevel.WRITE)


class DangerousTool(EchoTool):
    name = "danger"
    permission = int(PermissionLevel.DANGEROUS)


class MemorySink:
    def __init__(self) -> None:
        self.saved: list[tuple[str, str]] = []

    def save(self, *, session_id: str, kind: str, content: str, meta=None) -> str:
        self.saved.append((kind, content))
        return f"a_{len(self.saved)}"


def make_ctx(tmp_path: Path, max_permission: int = int(PermissionLevel.WRITE), **kwargs):
    return ToolContext(
        session_id="s_test",
        profile="test",
        workspace=tmp_path,
        max_permission=max_permission,
        **kwargs,
    )


async def test_unknown_tool(tmp_path):
    registry = ToolRegistry([EchoTool()])
    result = await registry.invoke("nope", {}, make_ctx(tmp_path))
    assert not result.ok
    assert result.error.code == "unknown_tool"


async def test_bad_json_is_repaired(tmp_path):
    tool = EchoTool()
    registry = ToolRegistry([tool])
    result = await registry.invoke("echo", '{"text": "hi"', make_ctx(tmp_path))
    assert result.ok
    assert result.content == "hi"
    assert tool.calls == 1


async def test_invalid_arguments_rejected(tmp_path):
    tool = EchoTool()
    registry = ToolRegistry([tool])
    result = await registry.invoke("echo", {}, make_ctx(tmp_path))
    assert not result.ok
    assert result.error.code == "invalid_arguments"
    assert tool.calls == 0


async def test_permission_denied(tmp_path):
    tool = WriteTool()
    registry = ToolRegistry([tool])
    ctx = make_ctx(tmp_path, max_permission=int(PermissionLevel.READ))
    result = await registry.invoke("write", {"text": "x"}, ctx)
    assert not result.ok
    assert result.error.code == "permission_denied"
    assert tool.calls == 0


async def test_dangerous_requires_approval(tmp_path):
    tool = DangerousTool()
    registry = ToolRegistry([tool])
    danger_ctx = dict(max_permission=int(PermissionLevel.DANGEROUS))

    denied = await registry.invoke("danger", {"text": "x"}, make_ctx(tmp_path, **danger_ctx))
    assert denied.error.code == "approval_required"
    assert tool.calls == 0

    async def approve(name, args):
        return True

    allowed = await registry.invoke(
        "danger", {"text": "x"}, make_ctx(tmp_path, approve=approve, **danger_ctx)
    )
    assert allowed.ok
    assert tool.calls == 1


async def test_timeout(tmp_path):
    registry = ToolRegistry([SlowTool()])
    result = await registry.invoke("slow", {"text": "x"}, make_ctx(tmp_path))
    assert not result.ok
    assert result.error.code == "timeout"


async def test_long_output_truncated_to_artifact(tmp_path):
    sink = MemorySink()
    registry = ToolRegistry([EchoTool()], max_output_chars=10)
    result = await registry.invoke(
        "echo", {"text": "x" * 100}, make_ctx(tmp_path, artifacts=sink)
    )
    assert result.ok
    assert result.truncated
    assert result.artifact_id == "a_1"
    assert "输出已截断" in result.content
    assert sink.saved[0][1] == "x" * 100
