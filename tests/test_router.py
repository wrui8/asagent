from __future__ import annotations

import pytest

from asagent.models.router import FailureSignal, ModelPolicy, ModelRouter, TaskClass


@pytest.fixture()
def router(config, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    policy = ModelPolicy(
        default="deepseek-chat",
        local_first="qwen2.5-7b-awq",
        escalate_to="qwen-plus",
        max_escalations=2,
    )
    return ModelRouter(config, policy)


def test_classify_analytic(router):
    assert router.classify("统计各品类 2024 年的毛利率排名") == TaskClass.SQL_ANALYTIC
    assert router.classify("订单表有多少条记录") == TaskClass.SQL_SIMPLE
    assert router.classify("你好") == TaskClass.LOOKUP


def test_initial_prefers_local_for_simple_task(router):
    state = router.initial("有多少订单？")
    assert state.model_id == "qwen2.5-7b-awq"
    assert state.reason.startswith("local_first")


def test_initial_uses_cloud_for_analytic_task(router):
    state = router.initial("各月的销售额趋势，按品类分组排名")
    assert state.model_id == "deepseek-chat"
    assert state.reason.startswith("default")


def test_initial_without_keys_falls_back_to_local(config, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    router = ModelRouter(config, ModelPolicy(default="deepseek-chat"))
    state = router.initial("复杂分析问题")
    assert state.model_id == "qwen2.5-7b-awq"
    assert state.reason == "fallback_available"


def test_escalation_is_capped(router):
    state = router.initial("有多少订单？")
    first = router.on_failure(state, FailureSignal.TOOL_SCHEMA_INVALID)
    assert first is not None
    assert first.model_id == "qwen-plus"
    assert first.escalations == 1

    second = router.on_failure(first, FailureSignal.LOOP_DETECTED)
    assert second is not None
    assert second.escalations == 2
    assert second.model_id != "qwen-plus"

    assert router.on_failure(second, FailureSignal.EMPTY_FINAL) is None


def test_non_escalatable_signal_returns_none(router):
    state = router.initial("有多少订单？")
    assert router.on_failure(state, FailureSignal.GUARD_BLOCK) is None
