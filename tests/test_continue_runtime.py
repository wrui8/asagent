"""续聊内核：同一会话追加一轮，历史与事件连续、本轮指标可区分。"""

from __future__ import annotations

from test_graph_engine import make_engine
from test_runtime import final, make_runtime

from asagent.runtime.graph import GraphEngine
from asagent.runtime.state import SessionStatus


async def _assert_continuation(engine) -> None:
    first = await engine.run("你好", owner_id="u1")
    assert first.status == SessionStatus.DONE
    session_id = first.session_id

    second = await engine.run(
        "继续", session_id=session_id, continuation=True, max_steps=4, owner_id="u1"
    )
    assert second.status == SessionStatus.DONE
    assert second.final_answer == "第二轮回答"
    assert second.steps == 2

    state = engine.store.load_state(session_id)
    # 上一轮最终回答被固化进消息，模型能看到自己的上下文
    assert [message.role for message in state.messages] == [
        "user",
        "assistant",
        "user",
    ]
    assert state.messages[1].content == "第一轮回答"
    assert state.final_answer == "第二轮回答"

    events = engine.store.events(session_id, 0)
    assert len([event for event in events if event.type == "session_created"]) == 1
    finals = [event for event in events if event.type == "final"]
    assert [event.payload["round_steps"] for event in finals] == [1, 1]
    users = [event for event in events if event.type == "user_input"]
    assert users[0].payload.get("continuation") is None
    assert users[1].payload.get("continuation") is True


async def test_legacy_continuation_keeps_history(config, tmp_path) -> None:
    engine, _ = make_runtime(
        config, tmp_path, [final("第一轮回答"), final("第二轮回答")]
    )
    try:
        await _assert_continuation(engine)
    finally:
        await engine.aclose()


async def test_graph_continuation_keeps_history(config, tmp_path) -> None:
    engine, _ = make_engine(
        GraphEngine,
        config,
        tmp_path,
        [final("第一轮回答"), final("第二轮回答")],
    )
    try:
        await _assert_continuation(engine)
    finally:
        await engine.aclose()
