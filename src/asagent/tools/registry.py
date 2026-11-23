from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence
from typing import Any

import json_repair
from pydantic import ValidationError

from .base import PermissionLevel, Tool, ToolContext, ToolError, ToolResult, ToolSpec


class ToolRegistry:
    """工具的唯一执行入口：解析 → 校验 → 权限 → 审批 → 超时 → 截断。"""

    def __init__(self, tools: Sequence[Tool[Any]] | None = None, *, max_output_chars: int = 6000):
        self._tools: dict[str, Tool[Any]] = {t.name: t for t in (tools or [])}
        self.max_output_chars = max_output_chars

    def register(self, tool: Tool[Any]) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any] | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self, only: Sequence[str] | None = None) -> list[ToolSpec]:
        if only is None:
            return [tool.to_spec() for tool in self._tools.values()]
        return [self._tools[n].to_spec() for n in only if n in self._tools]

    def subset(self, names: Sequence[str]) -> ToolRegistry:
        return ToolRegistry([self._tools[n] for n in names if n in self._tools])

    async def invoke(self, name: str, raw_args: Any, ctx: ToolContext) -> ToolResult:
        started = time.perf_counter()

        def elapsed() -> int:
            return int((time.perf_counter() - started) * 1000)

        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                ok=False,
                content=f"未知工具：{name}。可用工具：{', '.join(self._tools)}",
                error=ToolError(code="unknown_tool", message=f"unknown tool {name}"),
                elapsed_ms=elapsed(),
            )

        if tool.permission > ctx.max_permission:
            return ToolResult(
                ok=False,
                content=f"权限不足：工具 {name} 需要 {tool.permission} 级权限，当前 profile 仅允许 {ctx.max_permission} 级。",
                error=ToolError(code="permission_denied", message="permission denied"),
                elapsed_ms=elapsed(),
            )

        parsed = _parse_arguments(raw_args)
        try:
            args = tool.input_model.model_validate(parsed)
        except ValidationError as exc:
            errors = exc.errors(include_url=False, include_input=False)
            return ToolResult(
                ok=False,
                content=f"参数校验失败，请修正参数后重试：\n{json.dumps(errors, ensure_ascii=False)}",
                error=ToolError(code="invalid_arguments", message="invalid arguments"),
                elapsed_ms=elapsed(),
            )

        if tool.permission >= PermissionLevel.DANGEROUS:
            approved = False
            if ctx.approve is not None:
                approved = await ctx.approve(name, args.model_dump())
            if not approved:
                return ToolResult(
                    ok=False,
                    content=f"操作 {name} 需要人工审批，当前未获批准，已拒绝执行。",
                    error=ToolError(code="approval_required", message="approval required"),
                    elapsed_ms=elapsed(),
                )

        try:
            result = await asyncio.wait_for(tool.execute(args, ctx), timeout=tool.timeout_s)
        except TimeoutError:
            return ToolResult(
                ok=False,
                content=f"工具 {name} 执行超时（{tool.timeout_s}s），可缩小查询范围后重试。",
                error=ToolError(code="timeout", message="tool timeout"),
                elapsed_ms=elapsed(),
            )
        except Exception as exc:  # 工具内部异常不允许打断整个循环
            return ToolResult(
                ok=False,
                content=f"工具 {name} 执行出错：{exc}",
                error=ToolError(code="tool_error", message=str(exc)),
                elapsed_ms=elapsed(),
            )

        result.elapsed_ms = elapsed()
        return self._truncate(result, name, ctx)

    def _truncate(self, result: ToolResult, name: str, ctx: ToolContext) -> ToolResult:
        if len(result.content) <= self.max_output_chars:
            return result
        artifact_id = None
        if ctx.artifacts is not None:
            artifact_id = ctx.artifacts.save(
                session_id=ctx.session_id,
                kind=f"tool:{name}",
                content=result.content,
                meta={"tool": name, "ok": result.ok},
            )
        suffix = f"，完整内容见 artifact {artifact_id}" if artifact_id else ""
        result.content = (
            result.content[: self.max_output_chars] + f"\n...[输出已截断{suffix}]"
        )
        result.truncated = True
        result.artifact_id = artifact_id
        return result


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if raw is None or raw == "":
        return {}
    if not isinstance(raw, str):
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        try:
            value = json_repair.loads(raw)
        except Exception:
            return {}
    return value if isinstance(value, dict) else {}
