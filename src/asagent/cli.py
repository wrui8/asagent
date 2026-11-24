from __future__ import annotations

import asyncio
import json
import platform
import shutil
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.table import Table

from .config import ConfigError, load_config
from .eval.runner import EvalRunner
from .obs.trace import TraceWriter
from .profiles.loader import available_profiles, load_profile
from .runtime.approval import build_approver
from .runtime.checkpoint import CheckpointStore
from .runtime.events import EventType
from .runtime.factory import build_runtime
from .runtime.state import SessionStatus
from .tools.base import PermissionLevel
from .tools.sql.demo import generate_demo_db

app = typer.Typer(add_completion=False, help="asagent：学习型企业级 Agent 底座")
console = Console()


@app.command()
def doctor() -> None:
    """检查配置、密钥、本地 vLLM 与运行时路径。"""
    table = Table(title="asagent doctor")
    table.add_column("检查项", style="bold")
    table.add_column("状态")
    table.add_column("说明")

    table.add_row("Python", "OK", platform.python_version())

    try:
        config = load_config()
        table.add_row("配置", "OK", str(config.root / "configs" / "models.yaml"))
    except ConfigError as exc:
        table.add_row("配置", "[red]FAIL[/red]", str(exc))
        console.print(table)
        raise typer.Exit(code=2) from exc

    table.add_row("场景包", "OK", ", ".join(available_profiles()) or "（无）")

    for name, provider in config.models_file.providers.items():
        if name == "local":
            url = provider.base_url.rstrip("/") + "/models"
            try:
                resp = httpx.get(url, timeout=2.0)
                ok = resp.status_code == 200
                table.add_row(
                    f"provider:{name}",
                    "[green]OK[/green]" if ok else "[yellow]WARN[/yellow]",
                    f"{url} -> HTTP {resp.status_code}",
                )
            except Exception as exc:
                table.add_row(
                    f"provider:{name}", "[yellow]WARN[/yellow]",
                    f"{url} 不可达（{exc.__class__.__name__}），本地路由将不可用",
                )
        else:
            available = config.provider_available(name)
            table.add_row(
                f"provider:{name}",
                "[green]OK[/green]" if available else "[yellow]WARN[/yellow]",
                "密钥已配置" if available else f"缺少环境变量 {provider.api_key_env}",
            )

    model_lines = ", ".join(
        f"{mid}({cfg.tier}{'' if cfg.supports_tools else ',no-tools'})"
        for mid, cfg in config.models_file.models.items()
    )
    table.add_row("模型", "OK", model_lines)

    try:
        config.db_path.parent.mkdir(parents=True, exist_ok=True)
        table.add_row("运行时目录", "OK", str(config.db_path.parent))
    except OSError as exc:
        table.add_row("运行时目录", "[red]FAIL[/red]", str(exc))

    try:
        from .profiles.loader import load_profile

        rag = load_profile("rag", project_root=config.root)
        docs_dir = config.path(rag.tool_config.get("docs_dir", "data/docs"))
        index_db = config.path(rag.tool_config.get("index_db", "data/rag.sqlite3"))
        docs = len(list(docs_dir.glob("*.md"))) if docs_dir.exists() else 0
        if index_db.exists():
            con = sqlite3.connect(index_db)
            try:
                chunks = con.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]
            finally:
                con.close()
            table.add_row("RAG 索引", "OK", f"{docs} 篇文档 / {chunks} 个片段")
        else:
            table.add_row("RAG 索引", "[yellow]WARN[/yellow]", "未构建，运行 asagent rag-index")
    except Exception as exc:  # noqa: BLE001 - doctor 不应因单个检查崩溃
        table.add_row("RAG 索引", "[yellow]WARN[/yellow]", f"检查跳过：{exc}")

    try:
        if shutil.which("docker") is None:
            table.add_row("Docker 沙箱", "[yellow]WARN[/yellow]", "未找到 docker 命令")
        else:
            from .profiles.loader import load_profile

            image = str(
                load_profile("code", project_root=config.root).tool_config.get(
                    "sandbox_image", "python:3.12-slim"
                )
            )
            probe = subprocess.run(
                ["docker", "image", "inspect", image],
                capture_output=True,
                timeout=10,
                check=False,
            )
            if probe.returncode == 0:
                table.add_row("Docker 沙箱", "[green]OK[/green]", f"镜像 {image} 可用")
            else:
                table.add_row(
                    "Docker 沙箱",
                    "[yellow]WARN[/yellow]",
                    f"镜像 {image} 未拉取：docker pull {image}",
                )
    except Exception as exc:  # noqa: BLE001 - doctor 不应因单个检查崩溃
        table.add_row("Docker 沙箱", "[yellow]WARN[/yellow]", f"检查跳过：{exc}")

    try:
        from .profiles.loader import load_profile

        office = load_profile("office", project_root=config.root)
        backup_dir = config.path(
            office.tool_config.get("backup_dir", "runtime/backups")
        )
        backup_dir.mkdir(parents=True, exist_ok=True)
        table.add_row("办公备份目录", "[green]OK[/green]", str(backup_dir))
    except Exception as exc:  # noqa: BLE001 - doctor 不应因单个检查崩溃
        table.add_row("办公备份目录", "[yellow]WARN[/yellow]", f"检查跳过：{exc}")

    console.print(table)


@app.command()
def run(
    prompt: str = typer.Argument(..., help="任务描述"),
    profile: str = typer.Option("sql", "--profile", "-p", help="场景包名称"),
    model: str | None = typer.Option(None, "--model", "-m", help="强制指定模型"),
    max_steps: int = typer.Option(12, "--max-steps", help="最大步数"),
    workspace: str | None = typer.Option(
        None, "--workspace", help="代码场景的工作区目录（默认 runtime/workspace/<session>）"
    ),
    tests: str | None = typer.Option(
        None, "--tests", help="代码场景的只读测试目录（隐藏测试）"
    ),
    yes: bool = typer.Option(
        False, "--yes", help="自动批准所有写操作（危险，仅限受控环境）"
    ),
    deny: bool = typer.Option(
        False, "--deny", help="禁用审批：写操作挂起，需 approve 后 resume"
    ),
    max_cost: float | None = typer.Option(
        None, "--max-cost", help="单会话成本上限（元），超出后中止；默认取 profile/全局设置"
    ),
) -> None:
    """执行一次任务。"""
    if yes and deny:
        console.print("[red]--yes 与 --deny 不能同时使用[/red]")
        raise typer.Exit(code=2)
    approver = build_approver(auto=yes, deny=deny)
    if yes:
        console.print("[bold yellow]⚠️ 已启用自动批准：所有写操作将直接执行[/bold yellow]")

    config = load_config()
    runtime = build_runtime(
        config,
        profile,
        forced_model=model,
        approver=approver,
        workspace=Path(workspace).expanduser().resolve() if workspace else None,
        tests_dir=Path(tests).expanduser().resolve() if tests else None,
        max_cost_cny=max_cost,
    )

    async def _go():
        try:
            return await runtime.run(prompt, max_steps=max_steps)
        finally:
            await runtime.aclose()

    result = asyncio.run(_go())
    runtime.store.close()

    if result.status == SessionStatus.AWAITING_APPROVAL:
        console.print(f"\n[bold yellow]等待审批[/bold yellow]：{result.error}")
        console.print(f"  uv run asagent approve {result.session_id}")
        console.print(f"  uv run asagent resume {result.session_id}")
    elif result.ok:
        console.print(f"\n[bold green]完成[/bold green]（{result.steps} 步，¥{result.cost:.4f}）")
        console.print(result.final_answer or "")
    else:
        console.print(f"\n[bold red]失败[/bold red]：{result.error}")
    console.print(f"[dim]session: {result.session_id}[/dim]")
    if not result.ok and result.status != SessionStatus.AWAITING_APPROVAL:
        raise typer.Exit(code=1)


@app.command()
def resume(
    session_id: str = typer.Argument(..., help="会话 ID"),
    max_steps: int = typer.Option(12, "--max-steps"),
    yes: bool = typer.Option(
        False, "--yes", help="自动批准后续写操作（危险，仅限受控环境）"
    ),
    deny: bool = typer.Option(
        False, "--deny", help="禁用审批：新的写操作继续挂起"
    ),
    workspace: str | None = typer.Option(
        None, "--workspace", help="覆盖会话工作区（默认沿用首次运行时的目录）"
    ),
    max_cost: float | None = typer.Option(
        None, "--max-cost", help="单会话成本上限（元），超出后中止"
    ),
) -> None:
    """从检查点恢复会话；已获批准的写操作会继续执行。"""
    if yes and deny:
        console.print("[red]--yes 与 --deny 不能同时使用[/red]")
        raise typer.Exit(code=2)
    approver = build_approver(auto=yes, deny=deny)
    if yes:
        console.print("[bold yellow]⚠️ 已启用自动批准：所有写操作将直接执行[/bold yellow]")

    config = load_config()
    probe = CheckpointStore(config.db_path)
    try:
        state = probe.load_state(session_id)
    finally:
        probe.close()

    runtime = build_runtime(
        config,
        state.profile,
        approver=approver,
        workspace=Path(workspace).expanduser().resolve() if workspace else None,
        max_cost_cny=max_cost,
    )

    async def _go():
        try:
            return await runtime.run(
                session_id=session_id, resume=True, max_steps=max_steps
            )
        finally:
            await runtime.aclose()

    result = asyncio.run(_go())
    runtime.store.close()

    if result.status == SessionStatus.AWAITING_APPROVAL:
        console.print(
            f"[bold yellow]仍在等待审批[/bold yellow]：{result.error}"
        )
        console.print(f"  uv run asagent approve {result.session_id}")
    elif result.ok:
        console.print(f"[bold green]完成[/bold green]（{result.steps} 步）")
        console.print(result.final_answer or "")
    else:
        console.print(f"[bold red]状态[/bold red]：{result.status} — {result.error}")
    console.print(f"[dim]session: {result.session_id}[/dim]")


@app.command()
def approve(
    session_id: str = typer.Argument(..., help="会话 ID"),
    call_id: str | None = typer.Option(None, "--call", help="指定 tool call id"),
    approve_all: bool = typer.Option(False, "--all", help="批准全部待审批调用"),
    deny: bool = typer.Option(False, "--deny", help="拒绝而不是批准"),
) -> None:
    """审批挂起会话中的写操作；之后用 resume 继续执行。"""
    config = load_config()
    store = CheckpointStore(config.db_path)
    try:
        try:
            state = store.load_state(session_id)
        except KeyError:
            console.print(f"[red]会话不存在[/red]：{session_id}")
            raise typer.Exit(code=1) from None

        pending = [
            inv
            for inv in store.pending_invocations(session_id)
            if inv.permission > int(PermissionLevel.READ)
        ]
        if not pending:
            console.print(
                f"会话 {session_id}（{state.profile}）没有待审批的写操作。"
            )
            return

        if call_id:
            targets = [inv for inv in pending if inv.call_id == call_id]
            if not targets:
                console.print(f"[red]找不到待审批调用[/red]：{call_id}")
                raise typer.Exit(code=1)
        elif approve_all:
            targets = pending
        else:
            table = Table(title=f"待审批操作：{session_id}")
            table.add_column("call_id", style="bold")
            table.add_column("tool")
            table.add_column("权限")
            table.add_column("参数", overflow="fold")
            for inv in pending:
                args = inv.args_json
                if len(args) > 160:
                    args = args[:160] + "…"
                table.add_row(inv.call_id, inv.tool, str(inv.permission), args)
            console.print(table)
            console.print(
                "使用 [bold]--call <id>[/bold] 或 [bold]--all[/bold] 批准；"
                "加 [bold]--deny[/bold] 拒绝。"
            )
            return

        for inv in targets:
            store.decide_approval(session_id, inv.call_id, not deny)
            store.append(
                session_id,
                str(EventType.APPROVAL_DECIDED),
                {
                    "tool": inv.tool,
                    "call_id": inv.call_id,
                    "approved": not deny,
                    "source": "external",
                },
            )
            verdict = "已拒绝" if deny else "已批准"
            console.print(f"[green]{verdict}[/green]：{inv.tool}（{inv.call_id}）")
        console.print(f"请运行 [bold]uv run asagent resume {session_id}[/bold]")
    finally:
        store.close()


@app.command()
def audit(
    session_id: str | None = typer.Argument(None, help="会话 ID（省略则列出最近记录）"),
) -> None:
    """查看写操作审计：目标文件、备份路径与哈希。"""
    config = load_config()
    store = CheckpointStore(config.db_path)
    try:
        if session_id:
            try:
                store.load_state(session_id)
            except KeyError:
                console.print(f"[red]会话不存在[/red]：{session_id}")
                raise typer.Exit(code=1) from None
            events = [
                e for e in store.events(session_id) if e.type == str(EventType.AUDIT)
            ]
        else:
            events = store.recent_events_of_type(str(EventType.AUDIT), limit=50)
    finally:
        store.close()

    if not events:
        console.print("没有写操作审计记录。")
        return

    table = Table(title="写操作审计")
    table.add_column("时间")
    table.add_column("会话")
    table.add_column("工具")
    table.add_column("目标", overflow="fold")
    table.add_column("备份", overflow="fold")
    table.add_column("结果")
    table.add_column("sha256")
    for event in events:
        p = event.payload
        result = "成功" if p.get("ok") else f"失败（{p.get('error_code') or '-'}）"
        sha = str(p.get("sha256") or "")
        backup_text = "-"
        if p.get("backup"):
            # 会话已在独立列展示，备份列只放文件名，完整路径见 runtime/backups/<session>/
            backup_text = Path(str(p["backup"])).name
        table.add_row(
            event.ts,
            event.session_id,
            str(p.get("tool") or ""),
            str(p.get("path") or "-"),
            backup_text,
            result,
            sha[:12],
        )
    console.print(table)


@app.command("trace")
def trace_cmd(
    session_id: str = typer.Argument(..., help="会话 ID"),
    as_json: bool = typer.Option(False, "--json", help="输出原始 JSON"),
) -> None:
    """查看会话事件流。"""
    config = load_config()
    events = TraceWriter(config.trace_dir).read(session_id)
    if not events:
        store = CheckpointStore(config.db_path)
        try:
            events = [e.model_dump() for e in store.events(session_id)]
        finally:
            store.close()

    if as_json:
        console.print_json(json.dumps(events, ensure_ascii=False))
        return

    table = Table(title=f"trace: {session_id}")
    table.add_column("seq", justify="right")
    table.add_column("type")
    table.add_column("摘要", overflow="fold")
    for event in events:
        payload = json.dumps(event.get("payload", {}), ensure_ascii=False)
        table.add_row(
            str(event.get("seq", "")),
            str(event.get("type", "")),
            payload[:160] + ("…" if len(payload) > 160 else ""),
        )
    console.print(table)


@app.command("eval")
def evaluate(
    profile: str = typer.Option("sql", "--profile", "-p"),
    repeat: int = typer.Option(1, "--repeat", help="每个用例重复次数"),
    models: str | None = typer.Option(None, "--models", help="逗号分隔的模型 ID 列表"),
    limit: int | None = typer.Option(None, "--limit", help="只跑前 N 条"),
) -> None:
    """运行场景包的黄金评测集。"""
    config = load_config()
    model_list = [m.strip() for m in models.split(",")] if models else None
    runner = EvalRunner(config)
    report = asyncio.run(
        runner.run_suite(profile, models=model_list, repeat=repeat, limit=limit)
    )

    console.print(report.to_markdown())

    out_dir = config.root / "runtime" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (out_dir / f"{profile}-{stamp}.json").write_text(report.to_json(), encoding="utf-8")
    (out_dir / f"{profile}-{stamp}.md").write_text(report.to_markdown(), encoding="utf-8")
    console.print(f"[dim]报告已保存到 {out_dir}[/dim]")


@app.command("rag-index")
def rag_index(
    profile: str = typer.Option("rag", "--profile", "-p"),
    force: bool = typer.Option(False, "--force", help="无条件重建"),
) -> None:
    """构建或刷新 RAG 场景的向量索引（本地 bge，CPU）。"""
    from .profiles.loader import load_profile
    from .tools.rag.index import RagIndex

    config = load_config()
    cfg = load_profile(profile, project_root=config.root).tool_config
    model = str(cfg.get("embedding_model") or "")
    if not model:
        console.print("[red]缺少 embedding 模型路径[/red]：请设置 AS_EMBEDDING_MODEL")
        raise typer.Exit(code=2)

    index = RagIndex(
        index_db=config.path(cfg.get("index_db", "data/rag.sqlite3")),
        docs_dir=config.path(cfg.get("docs_dir", "data/docs")),
        embedding_model=model,
    )
    if force:
        count = index.build()
        console.print(f"[green]索引已重建[/green]：{count} 个片段 -> {index.index_db}")
    else:
        rebuilt = index.ensure()
        state = "已重建" if rebuilt else "已是最新"
        console.print(f"[green]索引{state}[/green]：{index.index_db}")


@app.command("mcp-list")
def mcp_list(
    profile: str = typer.Option("assistant", "--profile", "-p", help="场景包名称"),
) -> None:
    """启动 profile 配置的 MCP server 并列出可用工具。"""
    config = load_config()
    try:
        prof = load_profile(profile, project_root=config.root)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    raw = prof.tool_config.get("mcp_servers")
    if not raw:
        console.print(f"场景包 {profile} 未配置 mcp_servers。")
        return

    from .tools.mcp import MCPManager, parse_server_configs

    manager = MCPManager(parse_server_configs(raw), root=config.root)

    async def _go():
        try:
            return await manager.start()
        finally:
            await manager.close()

    try:
        tools = asyncio.run(_go())
    except Exception as exc:
        console.print(f"[red]MCP 启动失败[/red]：{exc}")
        raise typer.Exit(code=1) from None

    table = Table(title=f"MCP 工具（{profile}）")
    table.add_column("工具", style="bold")
    table.add_column("权限")
    table.add_column("描述", overflow="fold")
    for tool in tools:
        table.add_row(tool.name, str(int(tool.permission)), tool.description)
    console.print(table)


@app.command()
def demo(
    force: bool = typer.Option(False, "--force", help="重建演示数据库"),
) -> None:
    """生成 SQL 场景的演示数据库。"""
    config = load_config()
    path = generate_demo_db(config.path("data/demo.sqlite3"), force=force)
    console.print(f"[green]演示数据库就绪[/green]：{path}")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址"),
    port: int = typer.Option(8000, "--port", help="监听端口"),
) -> None:
    """启动 Web API（单 worker：任务调度在进程内）。"""
    import uvicorn

    from .api.app import create_app
    from .api.config import load_web_settings

    config = load_config()
    settings = load_web_settings(config.root)
    if settings.jwt_secret == "dev-only-change-me":
        console.print(
            "[bold yellow]⚠️ AS_JWT_SECRET 仍是默认值，公网部署前必须更换[/bold yellow]"
        )
    if not settings.origins:
        console.print(
            "[yellow]未配置 AS_ALLOWED_ORIGINS：不对浏览器做 Origin 校验（仅适合本地）[/yellow]"
        )
    console.print(f"[green]API[/green] http://{host}:{port}/api/docs")
    uvicorn.run(
        create_app(config, web_settings=settings),
        host=host,
        port=port,
        workers=1,
        log_level="info",
    )


@app.command("web-init-admin")
def web_init_admin(
    username: str = typer.Option(..., "--username", "-u", help="管理员用户名"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="密码（省略则交互输入）"
    ),
) -> None:
    """创建首个管理员账号。"""
    from .api.db import WebDB
    from .api.security import hash_password

    config = load_config()
    pwd = password or typer.prompt("密码", hide_input=True, confirmation_prompt=True)
    if len(pwd) < 12:
        console.print("[red]密码至少 12 位[/red]")
        raise typer.Exit(code=2)

    web_db = WebDB(config.db_path)
    if web_db.get_user_with_hash(username) is not None:
        console.print(f"[red]用户名已存在[/red]：{username}")
        raise typer.Exit(code=1)
    user = web_db.create_user(
        username=username, password_hash=hash_password(pwd), role="admin"
    )
    console.print(
        f"[green]管理员已创建[/green]：{user['username']}（{user['id']}）"
    )


if __name__ == "__main__":
    app()
