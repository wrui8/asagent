from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from asagent.guard.pipeline import GuardPipeline
from asagent.models.mock import ScriptedClient
from asagent.models.router import ModelPolicy, ModelRouter
from asagent.models.types import ModelResponse, ModelSpec, ToolCall, Usage
from asagent.obs.trace import TraceWriter
from asagent.profiles.loader import ContextConfig, Profile
from asagent.runtime.checkpoint import CheckpointStore
from asagent.runtime.context import ContextBuilder
from asagent.runtime.loop import AgentRuntime
from asagent.runtime.state import SessionStatus
from asagent.tools.base import PermissionLevel, Tool, ToolContext, ToolResult
from asagent.tools.mcp import (
    MCPError,
    MCPManager,
    MCPServerConfig,
    MCPToolAdapter,
    build_input_model,
    content_to_text,
    parse_server_configs,
)
from asagent.tools.registry import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = {
    "type": "object",
    "properties": {
        "a": {"type": "integer", "description": "第一个数"},
        "b": {"type": "integer", "description": "第二个数"},
        "note": {"type": "string"},
    },
    "required": ["a", "b"],
}


def make_ctx() -> ToolContext:
    return ToolContext(
        session_id="s_mcp",
        profile="assistant",
        workspace=ROOT,
        max_permission=int(PermissionLevel.READ),
    )


class FakeConnection:
    def __init__(self, result):
        self._result = result
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, arguments: dict):
        self.calls.append((name, arguments))
        return self._result


def make_adapter(connection, *, schema=SCHEMA, server="demo", remote="add"):
    return MCPToolAdapter(
        connection,
        server=server,
        remote_name=remote,
        description="加法",
        input_schema=schema,
        permission=int(PermissionLevel.READ),
        timeout_s=5,
    )


def test_build_input_model_enforces_required():
    model = build_input_model("demo", "add", SCHEMA)
    valid = model(a=1, b=2)
    assert valid.a == 1 and valid.note is None
    with pytest.raises(ValidationError):
        model(b=2)


def test_build_input_model_skips_invalid_identifiers():
    model = build_input_model(
        "demo", "x", {"properties": {"a-b": {"type": "string"}}, "required": []}
    )
    assert list(model.model_fields) == []


def test_content_to_text_handles_blocks():
    content = [
        SimpleNamespace(type="text", text="第一段"),
        SimpleNamespace(type="image", mimeType="image/png"),
        SimpleNamespace(type="text", text="第二段"),
    ]
    assert content_to_text(content) == "第一段\n[image image/png]\n第二段"


async def test_adapter_calls_remote_and_drops_optional_none():
    connection = FakeConnection(
        SimpleNamespace(content=[SimpleNamespace(type="text", text="5")], is_error=False)
    )
    adapter = make_adapter(connection)
    result = await adapter.execute(adapter.input_model(a=1, b=2), make_ctx())

    assert result.ok
    assert result.content == "5"
    assert connection.calls == [("add", {"a": 1, "b": 2})]
    assert adapter.name == "mcp__demo__add"


async def test_adapter_maps_error_result():
    connection = FakeConnection(
        SimpleNamespace(content=[SimpleNamespace(type="text", text="boom")], is_error=True)
    )
    adapter = make_adapter(connection)
    result = await adapter.execute(adapter.input_model(a=1, b=2), make_ctx())

    assert not result.ok
    assert result.error is not None and result.error.code == "mcp_tool_error"
    assert "boom" in result.content


def test_parse_server_configs_validates_shape():
    assert parse_server_configs(None) == []
    with pytest.raises(MCPError):
        parse_server_configs({"name": "x"})
    configs = parse_server_configs([{"name": "demo", "command": "python"}])
    assert configs[0].permission == int(PermissionLevel.READ)


async def test_real_demo_server_roundtrip():
    pytest.importorskip("mcp")
    config = MCPServerConfig(
        name="demo",
        command=sys.executable,
        args=[str(ROOT / "scripts" / "mcp_demo_server.py")],
    )
    manager = MCPManager([config], root=ROOT)
    try:
        tools = await manager.start()
        names = {tool.name for tool in tools}
        assert {"mcp__demo__add", "mcp__demo__echo", "mcp__demo__word_count"} <= names

        add = next(tool for tool in tools if tool.name == "mcp__demo__add")
        result = await add.execute(add.input_model(a=2, b=3), make_ctx())
        assert result.ok and result.content == "5"

        counter = next(tool for tool in tools if tool.name == "mcp__demo__word_count")
        result = await counter.execute(counter.input_model(text="hello mcp world"), make_ctx())
        assert result.ok and result.content == "3"
    finally:
        await manager.close()


async def test_missing_server_fails_closed():
    pytest.importorskip("mcp")
    manager = MCPManager([MCPServerConfig(name="bad", command="asagent-missing-binary-xyz")])
    with pytest.raises(MCPError):
        await manager.start()
    await manager.close()


class FakeMCPArgs(BaseModel):
    text: str = ""


class FakeMCPTool(Tool[FakeMCPArgs]):
    name = "fake_mcp"
    description = "假 MCP 工具"

    def __init__(self) -> None:
        self.calls = 0

    input_model = FakeMCPArgs
    permission = int(PermissionLevel.READ)
    timeout_s = 5.0

    async def execute(self, args: FakeMCPArgs, context: ToolContext) -> ToolResult:
        self.calls += 1
        return ToolResult(ok=True, content=f"mcp:{args.text}")


class FakeManager:
    def __init__(self, tool: FakeMCPTool) -> None:
        self.tool = tool
        self.started = 0
        self.closed = 0

    async def start(self):
        self.started += 1
        return [self.tool]

    async def close(self):
        self.closed += 1


async def test_runtime_registers_mcp_tools_on_demand(config, tmp_path):
    tool = FakeMCPTool()
    manager = FakeManager(tool)
    client = ScriptedClient(
        ModelSpec(id="deepseek-chat", provider="mock", upstream_name="x", tier="fast"),
        [
            ModelResponse(
                text=None,
                tool_calls=[ToolCall(id="c1", name="fake_mcp", arguments={"text": "hi"})],
                finish_reason="tool_calls",
                usage=Usage(input_tokens=10, output_tokens=5),
            ),
            ModelResponse(text="完成。", finish_reason="stop", usage=Usage()),
        ],
    )
    profile = Profile(
        name="test",
        description="MCP runtime",
        system_prompt="你是测试助手。",
        model_policy=ModelPolicy(default="deepseek-chat", max_escalations=0),
        tool_names=[],
        max_permission=0,
        context=ContextConfig(max_input_tokens=2000, max_tool_output_chars=1000),
        eval_suite=None,
    )
    runtime = AgentRuntime(
        config=config,
        profile=profile,
        registry=ToolRegistry([]),
        store=CheckpointStore(config.db_path),
        tracer=TraceWriter(config.trace_dir),
        router=ModelRouter(config, profile.model_policy),
        guard=GuardPipeline(),
        context_builder=ContextBuilder(max_input_tokens=2000, max_tool_output_chars=1000),
        client_factory=lambda cfg, model_id: client,
        forced_model="deepseek-chat",
        workspace=tmp_path / "ws",
        mcp_manager=manager,
    )
    try:
        result = await runtime.run("调用工具", max_steps=4)
        specs = runtime.registry.specs(["fake_mcp"])
        assert [spec.name for spec in specs] == ["fake_mcp"]
        # 再次准备（相当于 resume 路径）不会重复启动 server
        await runtime._prepare_mcp()
        assert manager.started == 1
    finally:
        await runtime.aclose()

    assert result.status == SessionStatus.DONE
    assert runtime.registry.get("fake_mcp") is not None
    assert tool.calls == 1
    assert manager.started == 1
    assert manager.closed == 1
