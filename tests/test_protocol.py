from __future__ import annotations

from asagent.models.protocol import recover_tool_calls

ALLOWED = {"search_docs", "run_sql"}


def test_recovers_json_code_block():
    text = (
        "我先检索一下。\n\n```json\n"
        '{"name": "search_docs", "arguments": {"query": "退款", "top_k": 3}}\n```'
    )
    calls, remainder = recover_tool_calls(text, ALLOWED)
    assert len(calls) == 1
    assert calls[0].name == "search_docs"
    assert calls[0].arguments == {"query": "退款", "top_k": 3}
    assert calls[0].raw_arguments
    assert remainder == "我先检索一下。"


def test_recovers_bare_json():
    calls, _ = recover_tool_calls('{"tool": "run_sql", "parameters": "{\\"sql\\": \\"SELECT 1\\"}"}', ALLOWED)
    assert calls and calls[0].name == "run_sql"
    assert calls[0].arguments == {"sql": "SELECT 1"}


def test_recovers_tool_call_tags():
    text = (
        "我们首先查看表信息。\n<tool_call>\n"
        '{"name": "run_sql", "arguments": {"sql": "SELECT 1"}}\n</tool_call>'
    )
    calls, remainder = recover_tool_calls(text, ALLOWED)
    assert len(calls) == 1
    assert calls[0].name == "run_sql"
    assert calls[0].arguments == {"sql": "SELECT 1"}
    assert remainder == "我们首先查看表信息。"


def test_recovers_tool_call_tags_without_arguments():
    calls, _ = recover_tool_calls("<tool_call>{'name': 'list_tables'}</tool_call>", {"list_tables"})
    # 单引号 JSON 不是合法 JSON，不应被误判
    assert calls == []
    calls, _ = recover_tool_calls('<tool_call>{"name": "list_tables"}</tool_call>', {"list_tables"})
    assert calls and calls[0].arguments == {}


def test_recovers_function_style():
    text = '```json\n{"function": {"name": "search_docs", "arguments": {"query": "年假"}}}\n```'
    calls, _ = recover_tool_calls(text, ALLOWED)
    assert calls and calls[0].name == "search_docs"


def test_ignores_unknown_tool():
    text = '```json\n{"name": "delete_everything", "arguments": {}}\n```'
    calls, remainder = recover_tool_calls(text, ALLOWED)
    assert calls == []
    assert remainder == text


def test_ignores_long_explanatory_text():
    text = "这是一段很长的解释。" * 200 + '\n```json\n{"name": "search_docs", "arguments": {}}\n```'
    calls, _ = recover_tool_calls(text, ALLOWED)
    assert calls == []


def test_plain_answer_untouched():
    calls, remainder = recover_tool_calls("数据库是只读的，无法删除。", ALLOWED)
    assert calls == []
    assert remainder == "数据库是只读的，无法删除。"
