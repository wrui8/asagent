from __future__ import annotations

import json
import re
from typing import Any

from .types import ToolCall

_JSON_BLOCK = re.compile(r"```(?:json|JSON)?\s*(\{.*?\})\s*```", re.S)
_TAG_BLOCK = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
_MAX_RECOVERED = 2
_MAX_REMAINDER_CHARS = 800


def _as_json(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _parse_payload(obj: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    function = obj.get("function")
    name: Any = obj.get("name") or obj.get("tool") or obj.get("tool_name")
    args: Any = obj.get("arguments") or obj.get("args") or obj.get("parameters")
    if isinstance(function, dict):
        name = name or function.get("name")
        args = args if args is not None else function.get("arguments")
    if not isinstance(name, str) or not name:
        return None
    parsed_args = _as_json(args) if args is not None else {}
    return name, parsed_args or {}


def recover_tool_calls(
    text: str, allowed: set[str]
) -> tuple[list[ToolCall], str]:
    """把模型写成文本的 JSON 工具调用还原为 ToolCall。

    部分小模型（如本地 7B）会输出 ```json {"name": "...", "arguments": {...}}```
    而不是走工具调用通道。只接受工具名在白名单内、且正文不是大段解释的情况，
    避免把回答里举例的 JSON 误当成调用。
    """
    if not text.strip() or not allowed:
        return [], text

    blocks = _JSON_BLOCK.findall(text) + _TAG_BLOCK.findall(text)
    if blocks:
        payloads = blocks
        remainder = _TAG_BLOCK.sub("", _JSON_BLOCK.sub("", text)).strip()
    else:
        payloads = [text.strip()]
        remainder = ""

    if len(remainder) > _MAX_REMAINDER_CHARS:
        return [], text

    calls: list[ToolCall] = []
    for raw in payloads:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        nested = obj.get("tool_calls")
        candidates = nested if isinstance(nested, list) else [obj]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            parsed = _parse_payload(candidate)
            if parsed is None:
                continue
            name, args = parsed
            if name not in allowed:
                continue
            calls.append(
                ToolCall(
                    id=f"recovered_{len(calls)}",
                    name=name,
                    arguments=args,
                    raw_arguments=json.dumps(args, ensure_ascii=False),
                )
            )
            if len(calls) >= _MAX_RECOVERED:
                return calls, remainder

    if not calls:
        return [], text
    return calls, remainder
