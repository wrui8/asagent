from __future__ import annotations

from typing import TYPE_CHECKING

from .registry import ToolRegistry

if TYPE_CHECKING:
    from asagent.config import AppConfig
    from asagent.profiles.loader import Profile


def build_registry(profile: Profile, config: AppConfig) -> ToolRegistry:
    """按场景包装配工具；新增场景时在此注册工具构建器。"""
    if profile.name == "sql":
        from .sql.tools import build_sql_tools

        db_path = config.path(profile.tool_config.get("db_path", "data/demo.sqlite3"))
        tools = build_sql_tools(db_path=db_path)
    elif profile.name == "rag":
        from .rag.tools import build_rag_tools

        cfg = profile.tool_config
        model = str(cfg.get("embedding_model") or "")
        if not model:
            raise ValueError(
                "RAG 场景缺少 embedding 模型路径：请在 .env 设置 AS_EMBEDDING_MODEL"
            )
        tools = build_rag_tools(
            docs_dir=config.path(cfg.get("docs_dir", "data/docs")),
            index_db=config.path(cfg.get("index_db", "data/rag.sqlite3")),
            embedding_model=model,
            top_k=int(cfg.get("top_k", 5)),
            settings=config.settings,
        )
    elif profile.name == "office":
        from .office.tools import build_office_tools

        backup_root = config.path(
            profile.tool_config.get("backup_dir", "runtime/backups")
        )
        tools = build_office_tools(backup_root=backup_root)
    elif profile.name == "assistant":
        # 静态工具为空；工具全部来自运行时发现的 MCP server
        tools = []
    elif profile.name == "code":
        from .code.sandbox import parse_path_map
        from .code.tools import build_code_tools

        cfg = profile.tool_config
        test_command = cfg.get("test_command")
        tools = build_code_tools(
            image=str(cfg.get("sandbox_image", "python:3.12-slim")),
            sandbox_timeout_s=float(cfg.get("sandbox_timeout_s", 90)),
            test_command=list(test_command) if test_command else None,
            path_map=parse_path_map(config.settings.as_sandbox_path_map),
        )
    else:
        raise ValueError(f"场景包 {profile.name} 尚未实现工具构建器")

    return ToolRegistry(tools, max_output_chars=profile.context.max_tool_output_chars)
