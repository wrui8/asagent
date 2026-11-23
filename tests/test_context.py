from __future__ import annotations

from asagent.models.types import Message, ToolCall
from asagent.runtime.context import ContextBuilder, estimate_tokens


def test_estimate_tokens_non_negative():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") >= 1


def test_short_conversation_is_kept():
    builder = ContextBuilder(max_input_tokens=4000, max_tool_output_chars=1000)
    messages = [
        Message(role="user", content="你好"),
        Message(role="assistant", content="你好，有什么可以帮你？"),
    ]
    built = builder.build(system_prompt="system", messages=messages)
    assert built[0].role == "system"
    assert [m.content for m in built[1:]] == ["你好", "你好，有什么可以帮你？"]


def test_old_turns_are_omitted():
    builder = ContextBuilder(max_input_tokens=4000, keep_last_turns=1)
    messages = []
    for i in range(5):
        messages.append(Message(role="user", content=f"问题{i}" + "x" * 20))
        messages.append(Message(role="assistant", content=f"回答{i}" + "y" * 20))
    built = builder.build(system_prompt="system", messages=messages)
    contents = [m.content for m in built if m.role != "system"]
    assert "问题4" in contents[0]
    assert all("问题0" != c for c in contents if c)
    assert any("已省略" in (m.content or "") for m in built)


def test_mid_loop_system_hints_are_kept():
    builder = ContextBuilder(max_input_tokens=4000, keep_last_turns=1)
    messages = [
        Message(role="user", content="退款多久到账？"),
        Message(role="system", content="你还没有调用 search_docs 检索知识库。"),
    ]
    built = builder.build(system_prompt="系统提示", messages=messages)
    hints = [m.content for m in built if m.role == "system"]
    assert hints == ["系统提示", "你还没有调用 search_docs 检索知识库。"]


def test_tool_pairing_not_broken_by_system_hint():
    builder = ContextBuilder(max_input_tokens=4000, keep_last_turns=1)
    messages = [
        Message(role="user", content="查两组数据"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(id="c1", name="echo", arguments={}),
                ToolCall(id="c2", name="echo", arguments={}),
            ],
        ),
        Message(role="tool", content="a", tool_call_id="c1"),
        Message(role="system", content="检测到疑似注入内容"),
        Message(role="tool", content="b", tool_call_id="c2"),
    ]
    built = builder.build(system_prompt="system", messages=messages)
    roles = [(m.role, m.tool_call_id) for m in built]
    assert roles.index(("system", None)) == 0
    # assistant 的两条 tool 响应必须连续，system 提示挪到它们之后
    assert roles[1:5] == [
        ("user", None),
        ("assistant", None),
        ("tool", "c1"),
        ("tool", "c2"),
    ]
    assert roles[5] == ("system", None)


def test_tool_output_is_truncated():
    builder = ContextBuilder(max_input_tokens=4000, max_tool_output_chars=50)
    messages = [
        Message(role="user", content="查一下"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="c1", name="echo", arguments={})],
        ),
        Message(role="tool", content="z" * 500, tool_call_id="c1"),
    ]
    built = builder.build(system_prompt="system", messages=messages)
    tool_msg = next(m for m in built if m.role == "tool")
    assert len(tool_msg.content) < 120
    assert "已截断" in tool_msg.content


def test_tool_calls_stay_with_responses():
    builder = ContextBuilder(max_input_tokens=4000, keep_last_turns=1)
    messages = [
        Message(role="user", content="第一轮"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "a"})],
        ),
        Message(role="tool", content="a", tool_call_id="c1"),
        Message(role="user", content="第二轮"),
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="c2", name="echo", arguments={"text": "b"})],
        ),
        Message(role="tool", content="b", tool_call_id="c2"),
    ]
    built = builder.build(system_prompt="system", messages=messages)
    kept_ids = {m.tool_call_id for m in built if m.role == "tool"}
    kept_call_ids = {c.id for m in built if m.tool_calls for c in m.tool_calls}
    assert kept_ids == kept_call_ids == {"c2"}
