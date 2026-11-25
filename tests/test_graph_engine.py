"""LangGraph 引擎（阶段 3）：与 v1 手写循环的语义等价性。

做法：同一份脚本化模型响应分别喂给 AgentRuntime 与 GraphEngine，
比较最终状态、事件类型序列与关键副作用；审批挂起/恢复与 Redis 检查点单独覆盖。
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import fakeredis
from test_runtime import EchoTool, LeakyTool, final, make_profile, tool_call

from asagent.guard.pipeline import GuardPipeline
from asagent.models.mock import ScriptedClient
from asagent.models.router import ModelRouter
from asagent.models.types import Message, ModelResponse, ModelSpec, ToolCall, Usage
from asagent.obs.trace import TraceWriter
from asagent.runtime.checkpoint import CheckpointStore
from asagent.runtime.context import ContextBuilder
from asagent.runtime.factory import build_runtime
from asagent.runtime.graph import GraphEngine
from asagent.runtime.loop import AgentRuntime
from asagent.runtime.state import SessionStatus
from asagent.storage.redis_state import RedisState
from asagent.tools.base import PermissionLevel
from asagent.tools.registry import ToolRegistry


class DangerTool(EchoTool):
    """需要人工审批的危险工具（WRITE 级别不会触发审批）。"""

    name = "danger"
    permission = int(PermissionLevel.DANGEROUS)


def make_engine(
    cls,
    config,
    tmp_path: Path,
    responses: list[ModelResponse],
    *,
    tools=None,
    profile=None,
    redis: RedisState | None = None,
    name: str = "engine",
    client=None,
    max_cost_cny: float | None = None,
    memory=None,
):
    profile = profile or make_profile()
    if client is None:
        client = ScriptedClient(
            ModelSpec(
                id="deepseek-chat", provider="mock", upstream_name="x", tier="fast"
            ),
            responses,
            default_text="完成。",
        )
    kwargs = {}
    if cls is GraphEngine:
        kwargs["redis"] = redis
    engine = cls(
        config=config,
        profile=profile,
        registry=ToolRegistry(tools or [EchoTool()]),
        store=CheckpointStore(tmp_path / f"{name}.db"),
        tracer=TraceWriter(config.trace_dir),
        router=ModelRouter(config, profile.model_policy),
        guard=GuardPipeline(),
        context_builder=ContextBuilder(max_input_tokens=2000, max_tool_output_chars=1000),
        client_factory=lambda cfg, model_id: client,
        forced_model="deepseek-chat",
        max_cost_cny=max_cost_cny,
        memory=memory,
        **kwargs,
    )
    return engine, client


async def run_both(config, tmp_path, responses_factory, *, tools_factory, profile=None):
    legacy, legacy_client = make_engine(
        AgentRuntime,
        config,
        tmp_path,
        responses_factory(),
        tools=tools_factory(),
        profile=profile,
        name="legacy",
    )
    graph, graph_client = make_engine(
        GraphEngine,
        config,
        tmp_path,
        responses_factory(),
        tools=tools_factory(),
        profile=profile,
        name="graph",
    )
    try:
        legacy_result = await legacy.run("测试问题", max_steps=8)
        graph_result = await graph.run("测试问题", max_steps=8)
    finally:
        await legacy.aclose()
        await graph.aclose()
    return (legacy, legacy_client, legacy_result), (graph, graph_client, graph_result)


async def test_tool_loop_event_sequence_matches_legacy(config, tmp_path) -> None:
    (legacy, _, legacy_result), (graph, _, graph_result) = await run_both(
        config,
        tmp_path,
        lambda: [tool_call("c1", "hi"), final("最终答案")],
        tools_factory=lambda: [EchoTool()],
    )

    assert graph_result.status == legacy_result.status == SessionStatus.DONE
    assert graph_result.final_answer == legacy_result.final_answer == "最终答案"
    assert graph_result.steps == legacy_result.steps

    legacy_types = [e.type for e in legacy.store.events(legacy_result.session_id)]
    graph_types = [e.type for e in graph.store.events(graph_result.session_id)]
    assert graph_types == legacy_types
    assert "session_created" in graph_types and "final" in graph_types

    invocations = graph.store.finished_invocations(graph_result.session_id)
    assert [inv.call_id for inv in invocations] == ["c1"]


async def test_guard_final_rewrite_matches_legacy(config, tmp_path) -> None:
    bad = final("你可以执行：\n```sql\nDELETE FROM orders WHERE status='cancelled';\n```")
    good = final("数据库是只读的，我无法删除数据。")
    (legacy, _, legacy_result), (graph, _, graph_result) = await run_both(
        config, tmp_path, lambda: [bad, good], tools_factory=lambda: [EchoTool()]
    )

    assert graph_result.status == SessionStatus.DONE
    assert graph_result.final_answer == legacy_result.final_answer == good.text
    legacy_types = [e.type for e in legacy.store.events(legacy_result.session_id)]
    graph_types = [e.type for e in graph.store.events(graph_result.session_id)]
    assert graph_types == legacy_types


async def test_injection_warning_appended_like_legacy(config, tmp_path) -> None:
    profile = replace(make_profile(), tool_names=["leaky"])
    plain = final("正面：质量不错；负面：包装破损。")
    responses = lambda: [  # noqa: E731 - 两个引擎各消费一份
        tool_call("c1", "x", name="leaky"),
        plain,
        plain,
        plain,
    ]
    (_, legacy_client, legacy_result), (_, graph_client, graph_result) = await run_both(
        config,
        tmp_path,
        responses,
        tools_factory=lambda: [LeakyTool()],
        profile=profile,
    )

    assert graph_result.status == SessionStatus.DONE
    assert graph_result.final_answer == legacy_result.final_answer
    assert (graph_result.final_answer or "").startswith("⚠️ 安全提示")
    assert len(graph_client.calls) == len(legacy_client.calls) == 4


async def test_require_tool_before_final_matches_legacy(config, tmp_path) -> None:
    profile = replace(make_profile(), require_tool_before_final=True)
    (legacy, _, legacy_result), (graph, _, graph_result) = await run_both(
        config,
        tmp_path,
        lambda: [final("我直接回答"), tool_call("c1", "hi"), final("最终答案")],
        tools_factory=lambda: [EchoTool()],
        profile=profile,
    )

    assert graph_result.final_answer == legacy_result.final_answer == "最终答案"
    legacy_types = [e.type for e in legacy.store.events(legacy_result.session_id)]
    graph_types = [e.type for e in graph.store.events(graph_result.session_id)]
    assert graph_types == legacy_types


async def test_max_steps_failure_is_consistent(config, tmp_path) -> None:
    graph, _ = make_engine(
        GraphEngine,
        config,
        tmp_path,
        [tool_call("c1", "same"), tool_call("c2", "same"), tool_call("c3", "same")],
        tools=[EchoTool()],
    )
    try:
        result = await graph.run("一直调用工具", max_steps=3)
    finally:
        await graph.aclose()

    assert result.status == SessionStatus.FAILED
    assert "最大步数" in (result.error or "")
    types = [e.type for e in graph.store.events(result.session_id)]
    assert types[-1] == "error"


async def test_approval_suspend_and_resume(config, tmp_path) -> None:
    danger = DangerTool()
    profile = replace(
        make_profile(max_permission=int(PermissionLevel.DANGEROUS)),
        tool_names=["danger"],
    )
    call = ModelResponse(
        tool_calls=[ToolCall(id="c1", name="danger", arguments={"text": "x"})],
        finish_reason="tool_calls",
        usage=Usage(input_tokens=10, output_tokens=5),
    )
    engine, _ = make_engine(
        GraphEngine,
        config,
        tmp_path,
        [call],
        tools=[danger],
        profile=profile,
    )
    try:
        first = await engine.run("执行危险操作", max_steps=5)
        assert first.status == SessionStatus.AWAITING_APPROVAL
        assert danger.calls == 0

        engine.store.decide_approval(first.session_id, "c1", True)

        resumed, _ = make_engine(
            GraphEngine,
            config,
            tmp_path,
            [final("已写入")],
            tools=[danger],
            profile=profile,
            name="engine",
        )
        try:
            result = await resumed.run(
                session_id=first.session_id, resume=True, max_steps=5
            )
        finally:
            await resumed.aclose()
    finally:
        await engine.aclose()

    assert result.status == SessionStatus.DONE
    assert result.final_answer == "已写入"
    assert danger.calls == 1


async def test_approval_event_sequence_matches_legacy(config, tmp_path) -> None:
    profile = replace(
        make_profile(max_permission=int(PermissionLevel.DANGEROUS)),
        tool_names=["danger"],
    )

    def danger_call() -> ModelResponse:
        return ModelResponse(
            tool_calls=[ToolCall(id="c1", name="danger", arguments={"text": "x"})],
            finish_reason="tool_calls",
            usage=Usage(input_tokens=10, output_tokens=5),
        )

    outcomes = {}
    for cls, name in ((AgentRuntime, "legacy"), (GraphEngine, "graph")):
        tool = DangerTool()
        engine, _ = make_engine(
            cls, config, tmp_path, [danger_call()], tools=[tool], profile=profile, name=name
        )
        try:
            first = await engine.run("执行危险操作", max_steps=4)
            assert first.status == SessionStatus.AWAITING_APPROVAL
            engine.store.decide_approval(first.session_id, "c1", True)

            resumed, _ = make_engine(
                cls,
                config,
                tmp_path,
                [final("已执行")],
                tools=[tool],
                profile=profile,
                name=name,
            )
            try:
                done = await resumed.run(
                    session_id=first.session_id, resume=True, max_steps=4
                )
            finally:
                await resumed.aclose()
            types = [e.type for e in engine.store.events(first.session_id)]
        finally:
            await engine.aclose()
        outcomes[name] = (done, types, tool.calls)

    legacy_done, legacy_types, legacy_calls = outcomes["legacy"]
    graph_done, graph_types, graph_calls = outcomes["graph"]
    assert graph_done.status == legacy_done.status == SessionStatus.DONE
    assert graph_done.final_answer == legacy_done.final_answer == "已执行"
    assert graph_types == legacy_types
    assert graph_calls == legacy_calls == 1


async def test_redis_checkpoint_mirror(config, tmp_path) -> None:
    redis = RedisState("", client=fakeredis.FakeRedis(decode_responses=True))
    engine, _ = make_engine(
        GraphEngine,
        config,
        tmp_path,
        [final("镜像检查")],
        redis=redis,
        name="mirror",
    )
    try:
        result = await engine.run("测试检查点", max_steps=5)
    finally:
        await engine.aclose()

    raw = redis.get(f"ckpt:{result.session_id}")
    assert raw is not None
    data = json.loads(raw)
    assert data["status"] == "done"
    assert data["step"] == 1
    assert data["final_answer"].startswith("镜像检查")


async def test_context_overflow_retry_matches_legacy(config, tmp_path) -> None:
    from test_runtime import OverflowOnceClient

    legacy_client = OverflowOnceClient()
    legacy, _ = make_engine(
        AgentRuntime,
        config,
        tmp_path,
        [],
        tools=[EchoTool()],
        client=legacy_client,
        name="legacy",
    )
    graph_client = OverflowOnceClient()
    graph, _ = make_engine(
        GraphEngine,
        config,
        tmp_path,
        [],
        tools=[EchoTool()],
        client=graph_client,
        name="graph",
    )
    try:
        legacy_result = await legacy.run("测试上下文溢出", max_steps=5)
        graph_result = await graph.run("测试上下文溢出", max_steps=5)
    finally:
        await legacy.aclose()
        await graph.aclose()

    assert graph_result.status == legacy_result.status == SessionStatus.DONE
    assert graph_result.final_answer == legacy_result.final_answer == "溢出后成功"
    assert graph_client.max_outputs[1] == legacy_client.max_outputs[1]
    assert graph_result.steps == legacy_result.steps


async def test_budget_limit_matches_legacy(config, tmp_path) -> None:
    legacy2, _ = make_engine(
        AgentRuntime,
        config,
        tmp_path,
        [tool_call("c2", "hi")],
        tools=[EchoTool()],
        max_cost_cny=1e-9,
        name="legacy-budget",
    )
    graph2, _ = make_engine(
        GraphEngine,
        config,
        tmp_path,
        [tool_call("c2", "hi")],
        tools=[EchoTool()],
        max_cost_cny=1e-9,
        name="graph-budget",
    )
    try:
        legacy_budget = await legacy2.run("触发预算", max_steps=3)
        graph_budget = await graph2.run("触发预算", max_steps=3)
    finally:
        await legacy2.aclose()
        await graph2.aclose()

    assert graph_budget.status == legacy_budget.status == SessionStatus.FAILED
    assert "超出预算上限" in (graph_budget.error or "")
    legacy_types = [e.type for e in legacy2.store.events(legacy_budget.session_id)]
    graph_types = [e.type for e in graph2.store.events(graph_budget.session_id)]
    assert graph_types == legacy_types
    assert "budget_exceeded" in graph_types


async def test_resume_reruns_pending_read_tool(config, tmp_path) -> None:
    probe = CheckpointStore(tmp_path / "engine.db")
    state = probe.create_session("test", "崩溃恢复")
    pending_call = ToolCall(id="c1", name="echo", arguments={"text": "恢复执行"})
    state.messages.append(Message(role="user", content="执行工具"))
    state.messages.append(Message(role="assistant", tool_calls=[pending_call]))
    probe.save_state(state)
    probe.begin_tool(state.session_id, pending_call, int(PermissionLevel.READ))
    session_id = state.session_id
    probe.close()

    echo = EchoTool()
    engine, _ = make_engine(
        GraphEngine,
        config,
        tmp_path,
        [final("恢复完成")],
        tools=[echo],
    )
    try:
        result = await engine.run(session_id=session_id, resume=True, max_steps=5)
    finally:
        await engine.aclose()

    assert result.status == SessionStatus.DONE
    assert result.final_answer == "恢复完成"
    assert echo.calls == 1
    tool_ids = [
        m.tool_call_id
        for m in engine.store.load_state(session_id).messages
        if m.role == "tool"
    ]
    assert tool_ids == ["c1"]


def test_factory_selects_engine(config) -> None:
    import asyncio

    config.settings.as_engine = "legacy"
    legacy = build_runtime(config, "sql")
    try:
        assert type(legacy) is AgentRuntime
    finally:
        asyncio.run(legacy.aclose())

    config.settings.as_engine = "langgraph"
    graph = build_runtime(config, "sql")
    try:
        assert isinstance(graph, GraphEngine)
    finally:
        asyncio.run(graph.aclose())
