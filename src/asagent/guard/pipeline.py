from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel


class GuardStage(StrEnum):
    INPUT = "input"
    TOOL_CALL = "tool_call"
    TOOL_OUTPUT = "tool_output"
    FINAL = "final"


class GuardVerdict(BaseModel):
    action: Literal["allow", "redact", "require_approval", "block"] = "allow"
    reason: str = ""
    replacement: str | None = None


_INJECTION_PATTERNS = (
    re.compile(r"忽略(之前|以上|前面|此前).{0,12}(指令|指示|要求)"),
    re.compile(r"(?i)ignore\s+(all\s+)?(previous|above|prior)\s+instructions"),
    re.compile(r"(?i)you\s+are\s+now\s+(a|an)\s+"),
    re.compile(r"(?i)system\s*prompt"),
    re.compile(r"(?i)exec(ute)?\s+(the\s+)?following\s+command"),
    re.compile(r"<\s*\|?\s*im_start\s*\|?\s*>"),
)


def _looks_like_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def looks_like_injection(text: str) -> bool:
    """入库前扫描文档中的可疑指令（只标记，不阻断写入）。"""
    return _looks_like_injection(text)


# 只读场景下，最终回答不应包含可直接执行的写语句
_WRITE_SQL_PATTERN = re.compile(
    r"(?i)\b(delete\s+from|update\s+[\w\"`\[\]]+\s+set|drop\s+(table|index|view|trigger)"
    r"|insert\s+into|alter\s+table|truncate\s+table|replace\s+into|create\s+(table|index|view)"
    r"|attach\s+database|detach\s+database|vacuum)\b"
)

# 引用并警示注入内容时会出现写语句，这是期望行为，不应拦截
_WARNING_WORDS = (
    "可疑", "注入", "忽略", "未执行", "没有执行", "不要执行", "请勿",
    "不应", "拒绝", "恶意", "篡改",
)
_WARNING_WINDOW = 160


def _unsafe_write_sql(text: str) -> str | None:
    """返回未被警示语境覆盖的写语句；没有则返回 None。"""
    for match in _WRITE_SQL_PATTERN.finditer(text):
        window = text[
            max(0, match.start() - _WARNING_WINDOW) : match.end() + _WARNING_WINDOW
        ]
        if not any(word in window for word in _WARNING_WORDS):
            return match.group(0)
    return None


class GuardPipeline:
    """纵深防御的一环；权限门禁才是底线。"""

    def __init__(self, *, max_input_chars: int = 20_000):
        self.max_input_chars = max_input_chars

    async def check(self, stage: GuardStage, payload: dict[str, Any]) -> GuardVerdict:
        text = str(payload.get("text") or "")

        if stage == GuardStage.INPUT:
            if len(text) > self.max_input_chars:
                return GuardVerdict(
                    action="block",
                    reason=f"输入过长（{len(text)} 字符，上限 {self.max_input_chars}）",
                )
            if _looks_like_injection(text):
                return GuardVerdict(action="allow", reason="injection_suspected:input")
            return GuardVerdict()

        if stage == GuardStage.TOOL_OUTPUT:
            if _looks_like_injection(text):
                # 不直接拦截（避免误杀正常文档），打上 taint 由上层记录
                return GuardVerdict(action="allow", reason="injection_suspected:tool_output")
            return GuardVerdict()

        if stage == GuardStage.FINAL:
            if payload.get("readonly"):
                offending = _unsafe_write_sql(payload.get("text") or "")
                if offending:
                    return GuardVerdict(
                        action="block",
                        reason=f"unsafe_write_sql:final（只读场景的回答包含可执行的写语句：{offending}）",
                    )
            if _looks_like_injection(text):
                return GuardVerdict(action="allow", reason="injection_suspected:final")
            return GuardVerdict()

        return GuardVerdict()

    def wrap_tool_output(self, name: str, call_id: str, text: str) -> str:
        """把工具输出包装为「不可信数据」，并转义可能干扰协议的控制串。"""
        safe = (
            text.replace("</tool_result>", "<\\/tool_result>")
            .replace("<|im_start|>", "[im_start]")
            .replace("<|im_end|>", "[im_end]")
            .replace("<tool_result", "[tool_result")
        )
        return (
            f'<tool_result tool="{name}" call_id="{call_id}" trust="untrusted">\n'
            f"{safe}\n"
            f"</tool_result>"
        )
