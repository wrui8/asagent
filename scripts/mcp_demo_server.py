"""最小 MCP server（stdio），用于演示与集成测试。

启动方式（MCP 客户端会拉起子进程，无需手动运行）：
    python scripts/mcp_demo_server.py
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("asagent-demo")


@mcp.tool()
def add(a: int, b: int) -> int:
    """计算两个整数之和。"""
    return a + b


@mcp.tool()
def echo(text: str) -> str:
    """原样返回传入的文本。"""
    return text


@mcp.tool()
def word_count(text: str) -> int:
    """按空白切分统计词数。"""
    return len(text.split())


if __name__ == "__main__":
    mcp.run()
