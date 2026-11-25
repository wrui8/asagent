from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..guard.pipeline import GuardPipeline
from ..models.client import build_client
from ..models.router import ModelRouter
from ..obs.trace import TraceWriter
from ..profiles.loader import load_profile
from ..storage.protocols import KernelStore
from ..tools.build import build_registry
from .checkpoint import CheckpointStore
from .context import ContextBuilder
from .loop import AgentRuntime


def build_runtime(
    config: AppConfig,
    profile_name: str,
    *,
    forced_model: str | None = None,
    use_mock: bool = False,
    approver: Any | None = None,
    workspace: Path | None = None,
    tests_dir: Path | None = None,
    max_cost_cny: float | None = None,
    store: KernelStore | None = None,
    redis: Any | None = None,
    memory: Any | None = None,
) -> AgentRuntime:
    profile = load_profile(profile_name, project_root=config.root)
    registry = build_registry(profile, config)

    effective_max_cost = (
        max_cost_cny
        if max_cost_cny is not None
        else profile.max_cost_cny
        if profile.max_cost_cny is not None
        else config.settings.as_max_cost_cny
    )

    mcp_manager = None
    raw_mcp = profile.tool_config.get("mcp_servers")
    if raw_mcp:
        from ..tools.mcp import MCPManager, parse_server_configs

        mcp_manager = MCPManager(parse_server_configs(raw_mcp), root=config.root)
    kernel_store = store if store is not None else CheckpointStore(config.db_path)
    tracer = TraceWriter(config.trace_dir)
    router = ModelRouter(config, profile.model_policy)
    guard = GuardPipeline()
    context_builder = ContextBuilder(
        max_input_tokens=profile.context.max_input_tokens,
        max_tool_output_chars=profile.context.max_tool_output_chars,
        keep_last_turns=profile.context.keep_last_turns,
    )

    client_factory = build_client
    if use_mock:
        from ..models.mock import ScriptedClient
        from ..models.types import ModelSpec

        def _mock_factory(cfg, model_id):  # noqa: ANN001
            return ScriptedClient(
                ModelSpec(id=model_id, provider="mock", upstream_name=model_id, tier="fast"),
                [],
                default_text="（mock 客户端未配置脚本）",
            )

        client_factory = _mock_factory

    engine_kwargs: dict[str, Any] = {}
    engine_cls: type[AgentRuntime] = AgentRuntime
    if config.settings.as_engine == "langgraph":
        from .graph import GraphEngine

        engine_cls = GraphEngine
        if redis is not None and config.settings.as_checkpointer == "redis":
            engine_kwargs["redis"] = redis

    return engine_cls(
        config=config,
        profile=profile,
        registry=registry,
        store=kernel_store,
        tracer=tracer,
        router=router,
        guard=guard,
        context_builder=context_builder,
        client_factory=client_factory,
        forced_model=forced_model,
        approver=approver,
        workspace=workspace,
        tests_dir=tests_dir,
        mcp_manager=mcp_manager,
        max_cost_cny=effective_max_cost,
        memory=memory,
        **engine_kwargs,
    )
