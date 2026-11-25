"""记忆子系统（阶段 4）：异步抽取 + 结构化存储 + 向量召回。

旁路设计：会话主链路只在 `build_context` 时读取摘要（失败即跳过），
抽取走 RabbitMQ 异步完成；`AS_MEMORY_ENABLED=false` 时所有入口保持关闭。
"""

from __future__ import annotations

from .types import MEMORY_TYPES, MemoryCandidate, MemoryItem

__all__ = ["MEMORY_TYPES", "MemoryCandidate", "MemoryItem"]
