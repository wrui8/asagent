from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

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
from asagent.tools.registry import ToolRegistry


class DangerArgs(BaseModel):
    text: str = Field(description="要写入的文本")


class DangerTool(Tool[DangerArgs]):
    name = "danger"
    description = "危险写操作"
    input_model = DangerArgs
    permission = int(PermissionLevel.DANGEROUS)
    timeout_s = 5.0

    def __init__(self) -> None:
        self.calls = 0
        self.last_workspace: Path | None = None

    async def execute(self, args: DangerArgs, ctx: ToolContext) -> ToolResult:
        self.calls += 1
        self.last_workspace = ctx.workspace
        return ToolResult(ok=True, content=f"已执行：{args.text}")


def make_profile() -> Profile:
    return Profile(
        name="test",
        description="审批测试",
        system_prompt="你是测试助手。",
        model_policy=ModelPolicy(default="deepseek-chat", max_escalations=0),
        tool_names=["danger"],
        max_permission=int(PermissionLevel.DANGEROUS),
        context=ContextConfig(max_input_tokens=2000, max_tool_output_chars=1000),
        eval_suite=None,
    )


def make_runtime(config, tmp_path: Path, responses, *, tool: DangerTool, approver=None):
    client = ScriptedClient(
        ModelSpec(id="deepseek-chat", provider="mock", upstream_name="x", tier="fast"),
        responses,
        default_text="完成。",
    )
    runtime = AgentRuntime(
        config=config,
        profile=make_profile(),
        registry=ToolRegistry([tool]),
        store=CheckpointStore(config.db_path),
        tracer=TraceWriter(config.trace_dir),
        router=ModelRouter(config, make_profile().model_policy),
        guard=GuardPipeline(),
        context_builder=ContextBuilder(max_input_tokens=2000, max_tool_output_chars=1000),
        client_factory=lambda cfg, model_id: client,
        forced_model="deepseek-chat",
        approver=approver,
        workspace=tmp_path / "ws",
    )
    return runtime, client


def danger_call(call_id: str = "c1", text: str = "写入文件") -> ModelResponse:
    return ModelResponse(
        text=None,
        tool_calls=[ToolCall(id=call_id, name="danger", arguments={"text": text})],
        finish_reason="tool_calls",
        usage=Usage(input_tokens=10, output_tokens=5),
    )


def final(text: str) -> ModelResponse:
    return ModelResponse(
        text=text, finish_reason="stop", usage=Usage(input_tokens=10, output_tokens=5)
    )


async def test_missing_approver_suspends_session(config, tmp_path):
    tool = DangerTool()
    runtime, client = make_runtime(
        config, tmp_path, [danger_call(), final("不应到达")], tool=tool
    )
    result = await runtime.run("执行危险操作", max_steps=4)

    assert result.status == SessionStatus.AWAITING_APPROVAL
    assert tool.calls == 0
    assert len(client.calls) == 1
    pending = runtime.store.pending_invocations(result.session_id)
    assert [p.tool for p in pending] == ["danger"]
    assert runtime.store.approval_status(result.session_id, "c1") == "pending"
    state = runtime.store.load_state(result.session_id)
    assert not any(m.role == "tool" for m in state.messages)
    events = runtime.store.events(result.session_id)
    required = [e for e in events if e.type == "approval_required"]
    assert required and required[0].payload["tool"] == "danger"
    assert required[0].payload["interactive"] is False
    await runtime.aclose()


async def test_denied_approval_reports_to_model(config, tmp_path):
    tool = DangerTool()

    async def deny(name: str, args: dict) -> bool:
        return False

    runtime, client = make_runtime(
        config,
        tmp_path,
        [danger_call(), final("未获批准，未执行写入。")],
        tool=tool,
        approver=deny,
    )
    result = await runtime.run("执行危险操作", max_steps=4)

    assert result.status == SessionStatus.DONE
    assert tool.calls == 0
    assert runtime.store.approval_status(result.session_id, "c1") == "denied"
    state = runtime.store.load_state(result.session_id)
    tool_messages = [m for m in state.messages if m.role == "tool"]
    assert tool_messages and "已拒绝执行" in (tool_messages[0].content or "")
    events = runtime.store.events(result.session_id)
    decided = [e for e in events if e.type == "approval_decided"]
    assert decided and decided[0].payload["approved"] is False
    audits = [e for e in events if e.type == "audit"]
    assert audits and audits[0].payload["ok"] is False
    assert audits[0].payload["error_code"] == "approval_required"
    await runtime.aclose()


async def test_approved_write_executes_and_audits(config, tmp_path):
    tool = DangerTool()

    async def allow(name: str, args: dict) -> bool:
        return True

    runtime, client = make_runtime(
        config,
        tmp_path,
        [danger_call(), final("写入完成。")],
        tool=tool,
        approver=allow,
    )
    result = await runtime.run("执行危险操作", max_steps=4)

    assert result.status == SessionStatus.DONE
    assert tool.calls == 1
    assert runtime.store.approval_status(result.session_id, "c1") == "approved"
    state = runtime.store.load_state(result.session_id)
    tool_messages = [m for m in state.messages if m.role == "tool"]
    assert tool_messages and "已执行" in (tool_messages[0].content or "")
    events = runtime.store.events(result.session_id)
    decided = [e for e in events if e.type == "approval_decided"]
    assert decided and decided[0].payload["approved"] is True
    assert decided[0].payload["source"] == "approver"
    audits = [e for e in events if e.type == "audit"]
    assert len(audits) == 1
    assert audits[0].payload["tool"] == "danger"
    assert audits[0].payload["ok"] is True
    await runtime.aclose()


async def test_external_approval_then_resume_executes(config, tmp_path):
    tool = DangerTool()
    runtime, _ = make_runtime(
        config, tmp_path, [danger_call(), final("不应到达")], tool=tool
    )
    suspended = await runtime.run("执行危险操作", max_steps=4)
    session_id = suspended.session_id
    assert suspended.status == SessionStatus.AWAITING_APPROVAL
    await runtime.aclose()

    store = CheckpointStore(config.db_path)
    store.decide_approval(session_id, "c1", True)
    store.close()

    resumed, client = make_runtime(
        config, tmp_path, [final("恢复后继续完成。")], tool=tool
    )
    result = await resumed.run("", session_id=session_id, resume=True, max_steps=4)

    assert result.status == SessionStatus.DONE
    assert tool.calls == 1
    assert resumed.store.approval_status(session_id, "c1") == "approved"
    assert not resumed.store.pending_invocations(session_id)
    state = resumed.store.load_state(session_id)
    tool_messages = [m for m in state.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    assert "已执行" in (tool_messages[0].content or "")
    events = resumed.store.events(session_id)
    assert [e for e in events if e.type == "audit"]
    assert len(client.calls) == 1
    await resumed.aclose()


async def test_external_denial_then_resume_reports_error(config, tmp_path):
    tool = DangerTool()
    runtime, _ = make_runtime(
        config, tmp_path, [danger_call(), final("不应到达")], tool=tool
    )
    suspended = await runtime.run("执行危险操作", max_steps=4)
    session_id = suspended.session_id
    assert suspended.status == SessionStatus.AWAITING_APPROVAL
    await runtime.aclose()

    store = CheckpointStore(config.db_path)
    store.decide_approval(session_id, "c1", False)
    store.close()

    resumed, _ = make_runtime(
        config, tmp_path, [final("未获批准，未执行。")], tool=tool
    )
    result = await resumed.run("", session_id=session_id, resume=True, max_steps=4)

    assert result.status == SessionStatus.DONE
    assert tool.calls == 0
    assert resumed.store.approval_status(session_id, "c1") == "denied"
    assert not resumed.store.pending_invocations(session_id)
    state = resumed.store.load_state(session_id)
    tool_messages = [m for m in state.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    assert "已拒绝执行" in (tool_messages[0].content or "")
    events = resumed.store.events(session_id)
    audits = [e for e in events if e.type == "audit"]
    assert audits and audits[0].payload["ok"] is False
    assert audits[0].payload["error_code"] == "approval_required"
    await resumed.aclose()


async def test_pending_approval_still_suspends_on_resume(config, tmp_path):
    tool = DangerTool()
    runtime, _ = make_runtime(
        config, tmp_path, [danger_call(), final("不应到达")], tool=tool
    )
    suspended = await runtime.run("执行危险操作", max_steps=4)
    session_id = suspended.session_id
    await runtime.aclose()

    resumed, client = make_runtime(
        config, tmp_path, [final("不应到达")], tool=tool
    )
    result = await resumed.run("", session_id=session_id, resume=True, max_steps=4)

    assert result.status == SessionStatus.AWAITING_APPROVAL
    assert tool.calls == 0
    assert len(client.calls) == 0
    assert resumed.store.approval_status(session_id, "c1") == "pending"
    assert resumed.store.pending_invocations(session_id)
    await resumed.aclose()


async def test_resume_restores_session_workspace(config, tmp_path):
    tool = DangerTool()
    runtime, _ = make_runtime(
        config, tmp_path, [danger_call(), final("不应到达")], tool=tool
    )
    suspended = await runtime.run("执行危险操作", max_steps=4)
    assert suspended.status == SessionStatus.AWAITING_APPROVAL
    await runtime.aclose()

    store = CheckpointStore(config.db_path)
    store.decide_approval(suspended.session_id, "c1", True)
    store.close()

    # resume 不显式传 workspace，应沿用首次运行时持久化的目录
    resumed, _ = make_runtime(config, tmp_path, [final("完成。")], tool=tool)
    result = await resumed.run("", session_id=suspended.session_id, resume=True, max_steps=4)

    assert result.status == SessionStatus.DONE
    assert tool.last_workspace == (tmp_path / "ws")
    await resumed.aclose()
