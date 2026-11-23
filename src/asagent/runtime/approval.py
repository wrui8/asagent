from __future__ import annotations

import asyncio
import json
import sys

from rich.console import Console
from rich.prompt import Confirm

from ..tools.base import Approver


def interactive_enabled() -> bool:
    """只有真实终端才允许交互审批；管道/CI 一律视为非交互。"""
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def build_approver(
    *,
    auto: bool = False,
    deny: bool = False,
    interactive: bool | None = None,
) -> Approver | None:
    """构造 CLI 审批器。

    - auto=True：明确授权（`--yes`），自动批准所有危险操作；
    - deny=True 或非交互终端：返回 None，危险操作不会执行（挂起等待外部审批）；
    - 其余情况：在终端中逐个确认，默认拒绝。
    """
    if auto and deny:
        raise ValueError("--yes 与 --deny 不能同时使用")
    if deny:
        return None
    if auto:
        async def _auto(name: str, args: dict) -> bool:
            return True

        return _auto
    if interactive is None:
        interactive = interactive_enabled()
    if not interactive:
        return None

    console = Console()

    async def _ask(name: str, args: dict) -> bool:
        summary = json.dumps(args, ensure_ascii=False)
        if len(summary) > 500:
            summary = summary[:500] + "…"
        console.print(
            f"\n[bold yellow]需要人工审批[/bold yellow]：工具 [bold]{name}[/bold]"
        )
        console.print(f"参数：{summary}")
        try:
            return await asyncio.to_thread(
                Confirm.ask, "允许执行该操作吗？", default=False
            )
        except EOFError:
            return False

    return _ask
