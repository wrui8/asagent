from __future__ import annotations

from pathlib import Path

from asagent.guard.pipeline import GuardPipeline
from asagent.models.mock import ScriptedClient
from asagent.models.router import ModelPolicy, ModelRouter
from asagent.models.types import ModelResponse, ModelSpec, Pricing, Usage
from asagent.obs.trace import TraceWriter
from asagent.profiles.loader import ContextConfig, Profile
from asagent.runtime.checkpoint import CheckpointStore
from asagent.runtime.context import ContextBuilder
from asagent.runtime.loop import AgentRuntime
from asagent.runtime.state import SessionStatus
from asagent.tools.registry import ToolRegistry

PRICING = Pricing(input_per_mtok=2, output_per_mtok=8)


def make_runtime(config, tmp_path: Path, responses, *, max_cost_cny: float | None):
    spec = ModelSpec(
        id="deepseek-chat",
        provider="mock",
        upstream_name="x",
        tier="fast",
        pricing=PRICING,
    )
    client = ScriptedClient(spec, responses, default_text="完成。")
    profile = Profile(
        name="test",
        description="预算测试",
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
        max_cost_cny=max_cost_cny,
    )
    return runtime, client


def model_response(text: str = "完成。") -> ModelResponse:
    # 定价 ¥2/¥8 每百万 token，各 1000 token → 单次 ¥0.01
    return ModelResponse(
        text=text,
        finish_reason="stop",
        usage=Usage(input_tokens=1000, output_tokens=1000),
    )


async def test_over_budget_stops_after_first_model_call(config, tmp_path):
    runtime, client = make_runtime(
        config, tmp_path, [model_response(), model_response()], max_cost_cny=0.001
    )
    result = await runtime.run("你好", max_steps=4)

    assert result.status == SessionStatus.FAILED
    assert result.error is not None and "预算" in result.error
    assert result.final_answer is None
    assert len(client.calls) == 1
    events = runtime.store.events(result.session_id)
    exceeded = [e for e in events if e.type == "budget_exceeded"]
    assert exceeded and exceeded[0].payload["limit"] == 0.001
    await runtime.aclose()


async def test_within_budget_completes(config, tmp_path):
    runtime, client = make_runtime(
        config, tmp_path, [model_response("好的。")], max_cost_cny=1.0
    )
    result = await runtime.run("你好", max_steps=4)

    assert result.status == SessionStatus.DONE
    assert result.final_answer == "好的。"
    assert result.cost > 0
    assert len(client.calls) == 1
    await runtime.aclose()


async def test_no_budget_limit_by_default(config, tmp_path):
    runtime, _ = make_runtime(config, tmp_path, [model_response("完成。")], max_cost_cny=None)
    result = await runtime.run("你好", max_steps=4)
    assert result.status == SessionStatus.DONE
    await runtime.aclose()
