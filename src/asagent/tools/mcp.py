"""MCP（Model Context Protocol）stdio 客户端适配。

把外部 MCP server 的工具包装成本项目的 Tool：
- 工具名统一为 ``mcp__<server>__<tool>``，避免与内置工具冲突；
- 用 JSON Schema 动态生成 Pydantic 输入模型，沿用注册表的参数校验；
- 权限与超时跟随 server 配置，DANGEROUS 自动进入既有审批流程；
- 启动失败 fail-closed，不静默降级为「没有工具」。
"""

from __future__ import annotations

import os
import re
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Union

from pydantic import BaseModel, Field, create_model

from .base import PermissionLevel, Tool, ToolContext, ToolError, ToolResult

_DEFAULT_TIMEOUT_S = 30.0
_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_DESCRIPTION = 2000

# 子进程只继承运行必需的环境变量，避免把 API key 等透传给第三方 server
_SAFE_ENV_KEYS = {
    "PATH", "Path", "PATHEXT", "COMSPEC", "SYSTEMROOT", "SystemRoot", "WINDIR",
    "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "LANG", "LANGUAGE", "VIRTUAL_ENV", "PYTHONPATH", "PYTHONIOENCODING",
    "PYTHONUTF8", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY",
    "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
}


class MCPError(RuntimeError):
    """MCP server 启动或通信失败。"""


class MCPServerConfig(BaseModel):
    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = _DEFAULT_TIMEOUT_S
    # 该 server 所有工具的默认权限；可按工具覆盖
    permission: int = int(PermissionLevel.READ)
    tool_permissions: dict[str, int] = Field(default_factory=dict)


def parse_server_configs(raw: Any) -> list[MCPServerConfig]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise MCPError("mcp_servers 必须是列表")
    return [MCPServerConfig.model_validate(item) for item in raw]


def _safe_tool_name(server: str, tool: str) -> str:
    cleaned = _NAME_UNSAFE.sub("_", f"mcp__{server}__{tool}")
    return cleaned[:64]


_JSON_TO_PY: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list[Any],
    "object": dict[str, Any],
    "null": type(None),
}


def _field_type(schema: Any) -> Any:
    if not isinstance(schema, dict):
        return Any
    declared = schema.get("type")
    if isinstance(declared, list):
        types = tuple(_field_type({"type": item}) for item in declared)
        return types if types else Any
    return _JSON_TO_PY.get(declared, Any)


def build_input_model(server: str, tool: str, schema: Any) -> type[BaseModel]:
    """把 MCP 工具的 inputSchema 转成 Pydantic 模型（未知类型退化为 Any）。"""
    schema = schema if isinstance(schema, dict) else {}
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = set(schema.get("required") or [])
    fields: dict[str, Any] = {}
    for key, prop in properties.items():
        if not isinstance(key, str) or not key.isidentifier():
            continue
        typ = _field_type(prop)
        description = prop.get("description") if isinstance(prop, dict) else None
        if key in required:
            fields[key] = (typ, Field(..., description=description))
        else:
            optional = Union[(*typ, None)] if isinstance(typ, tuple) else typ | None
            fields[key] = (optional, Field(None, description=description))
    return create_model(f"MCP_{server}_{tool}_Args", **fields)


class MCPToolAdapter(Tool[Any]):
    """单个远端 MCP 工具；execute 由注册表统一做校验/审批/超时。"""

    def __init__(
        self,
        connection: MCPConnection,
        *,
        server: str,
        remote_name: str,
        description: str,
        input_schema: Any,
        permission: int,
        timeout_s: float,
    ) -> None:
        self._connection = connection
        self._remote_name = remote_name
        self._required = set((input_schema or {}).get("required") or [])
        self.name = _safe_tool_name(server, remote_name)
        self.description = (description or f"MCP 工具 {remote_name}")[:_MAX_DESCRIPTION]
        self.input_model = build_input_model(server, remote_name, input_schema)
        self.permission = permission
        self.timeout_s = timeout_s

    async def execute(self, args: Any, ctx: ToolContext) -> ToolResult:
        payload = {
            key: value
            for key, value in args.model_dump().items()
            if value is not None or key in self._required
        }
        result = await self._connection.call_tool(self._remote_name, payload)
        text = content_to_text(result.content)
        failed = bool(
            getattr(result, "is_error", None) or getattr(result, "isError", False)
        )
        if failed:
            return ToolResult(
                ok=False,
                content=text or "MCP 工具返回错误。",
                error=ToolError(code="mcp_tool_error", message="mcp tool error"),
            )
        return ToolResult(ok=True, content=text or "（MCP 工具没有返回文本内容）")


def content_to_text(content: Any) -> str:
    """把 CallToolResult.content 的多种块拼成文本。"""
    parts: list[str] = []
    for item in content or []:
        kind = getattr(item, "type", None)
        if kind == "text":
            parts.append(str(getattr(item, "text", "")))
        elif kind == "image":
            parts.append(f"[image {getattr(item, 'mimeType', '')}]".strip())
        elif kind == "audio":
            parts.append("[audio]")
        elif kind == "resource":
            resource = getattr(item, "resource", None)
            text = getattr(resource, "text", None)
            parts.append(str(text) if text is not None else "[resource]")
        else:
            parts.append(str(item))
    return "\n".join(part for part in parts if part)


def _child_env(extra: dict[str, str]) -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in _SAFE_ENV_KEYS or key.upper().startswith("LC_")
    }
    env.update(extra)
    return env


def _resolve_launch(cfg: MCPServerConfig, root: Path | None) -> tuple[str, list[str]]:
    """相对脚本路径按项目根解析，保证从任意 cwd 启动 CLI 都能找到 server。

    ``python``/``python3`` 指向当前运行解释器而不是 PATH 里的系统 python：
    容器内系统 python 没有项目依赖，直接拉起会因缺 ``mcp`` 立刻退出，
    客户端只看到 ``Connection closed``。
    """
    command = cfg.command
    if command in {"python", "python3", "py"}:
        command = sys.executable
    args = list(cfg.args)
    if root is not None:
        if not Path(command).is_absolute() and (root / command).is_file():
            command = str((root / command).resolve())
        resolved: list[str] = []
        for arg in args:
            if arg.startswith("-") or Path(arg).is_absolute():
                resolved.append(arg)
                continue
            candidate = root / arg
            resolved.append(str(candidate.resolve()) if candidate.exists() else arg)
        args = resolved
    return command, args


class MCPConnection:
    """一个 stdio MCP server 的完整生命周期。"""

    def __init__(self, cfg: MCPServerConfig, *, root: Path | None = None) -> None:
        self.cfg = cfg
        self._root = root
        self._stack: AsyncExitStack | None = None
        self._session: Any = None
        self.tools: list[Any] = []

    async def start(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        command, args = _resolve_launch(self.cfg, self._root)
        params = StdioServerParameters(
            command=command,
            args=args,
            env=_child_env(self.cfg.env),
        )
        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            listed = await session.list_tools()
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        self.tools = list(listed.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._session is None:
            raise MCPError(f"MCP server {self.cfg.name} 尚未启动")
        return await self._session.call_tool(name, arguments)

    async def close(self) -> None:
        stack, self._stack = self._stack, None
        self._session = None
        if stack is not None:
            await stack.aclose()


class MCPManager:
    """按 profile 配置启动多个 MCP server，聚合工具并统一关闭。"""

    def __init__(self, servers: list[MCPServerConfig], *, root: Path | None = None) -> None:
        self._servers = servers
        self._root = root
        self._connections: list[MCPConnection] = []
        self._tools: list[MCPToolAdapter] = []
        self._started = False
        self._closed = False

    @property
    def tools(self) -> list[MCPToolAdapter]:
        return list(self._tools)

    async def start(self) -> list[MCPToolAdapter]:
        if self._closed:
            raise MCPError("MCPManager 已关闭，不能再启动")
        if self._started:
            return list(self._tools)
        try:
            for cfg in self._servers:
                connection = MCPConnection(cfg, root=self._root)
                await connection.start()
                self._connections.append(connection)
                for tool in connection.tools:
                    permission = cfg.tool_permissions.get(tool.name, cfg.permission)
                    schema = getattr(tool, "input_schema", None) or getattr(
                        tool, "inputSchema", None
                    )
                    self._tools.append(
                        MCPToolAdapter(
                            connection,
                            server=cfg.name,
                            remote_name=tool.name,
                            description=tool.description or "",
                            input_schema=schema,
                            permission=permission,
                            timeout_s=cfg.timeout_s,
                        )
                    )
        except Exception as exc:
            await self.close()
            raise MCPError(f"MCP server 启动失败：{exc}") from exc
        self._started = True
        return list(self._tools)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for connection in reversed(self._connections):
            try:
                await connection.close()
            except Exception:
                # 关闭阶段不让单个 server 的清理失败影响其他连接
                continue
        self._connections.clear()
