from __future__ import annotations

import json
from collections.abc import Sequence

from ..models.types import Message
from ..tools.base import ToolSpec


def estimate_tokens(text: str) -> int:
    """保守的字符级估算：中英混合按约 2 字符/token 计。"""
    if not text:
        return 0
    return max(1, len(text) // 2)


class ContextBuilder:
    def __init__(
        self,
        *,
        max_input_tokens: int = 3200,
        max_tool_output_chars: int = 6000,
        keep_last_turns: int = 6,
    ):
        self.max_input_tokens = max_input_tokens
        self.max_tool_output_chars = max_tool_output_chars
        self.keep_last_turns = keep_last_turns

    def estimate_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    def estimate_messages(
        self, messages: Sequence[Message], tools: Sequence[ToolSpec] = ()
    ) -> int:
        total = 0
        for m in messages:
            total += estimate_tokens(m.content or "")
            if m.tool_calls:
                total += estimate_tokens(
                    json.dumps([c.model_dump() for c in m.tool_calls], ensure_ascii=False)
                )
        for spec in tools:
            total += estimate_tokens(spec.name) + estimate_tokens(spec.description)
            total += estimate_tokens(json.dumps(spec.parameters, ensure_ascii=False))
        return total

    def build(
        self,
        *,
        system_prompt: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        aggressive: bool = False,
    ) -> list[Message]:
        prepared: list[Message] = []
        limit = self.max_tool_output_chars // (2 if aggressive else 1)
        for m in messages:
            # 循环内追加的 system 提示（强制检索、循环检测等）必须保留，
            # 场景系统提示由 build() 单独放在最前面。
            if m.role == "tool" and m.content and len(m.content) > limit:
                m = m.model_copy(
                    update={"content": m.content[:limit] + "\n...[工具输出已截断]"}
                )
            prepared.append(m)

        prepared = self._fix_tool_pairing(prepared)

        budget =self.max_input_tokens - self.estimate_messages([], tools) - estimate_tokens(
            system_prompt
        )
        groups = self._group_turns(prepared)
        total_groups = len(groups)
        keep_count = 2 if aggressive else self.keep_last_turns
        groups = groups[-keep_count:] if len(groups) > keep_count else groups

        kept: list[list[Message]] = []
        used = 0
        for group in reversed(groups):
            group_tokens = self.estimate_messages(group)
            if kept and used + group_tokens > max(budget, 200):
                break
            kept.insert(0, group)
            used += group_tokens

        omitted = total_groups - len(kept)
        result = [Message(role="system", content=system_prompt)]
        if omitted > 0:
            result.append(
                Message(role="system", content=f"（更早的 {omitted} 轮对话已省略）")
            )
        for group in kept:
            result.extend(group)
        return result

    @staticmethod
    def _fix_tool_pairing(messages: Sequence[Message]) -> list[Message]:
        """保证 assistant(tool_calls) 后紧跟全部对应的 tool 消息。

        运行中可能在工具之间追加 system 提示（注入警告、循环检测等），
        OpenAI 兼容接口会因此报 400。这里把这种 system 消息挪到整组工具响应之后。
        """
        result: list[Message] = []
        deferred: list[Message] = []
        pending: set[str] = set()
        for m in messages:
            if pending and m.role == "system":
                deferred.append(m)
                continue
            result.append(m)
            if m.role == "tool" and m.tool_call_id in pending:
                pending.discard(m.tool_call_id)
                if not pending:
                    result.extend(deferred)
                    deferred = []
            elif m.role == "assistant" and m.tool_calls:
                pending = {c.id for c in m.tool_calls}
        result.extend(deferred)
        return result

    @staticmethod
    def _group_turns(messages: Sequence[Message]) -> list[list[Message]]:
        groups: list[list[Message]] = []
        current: list[Message] = []
        for m in messages:
            if m.role == "user" and current:
                groups.append(current)
                current = []
            current.append(m)
        if current:
            groups.append(current)
        return groups
