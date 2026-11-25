"""按 `AS_STORAGE_BACKEND` 构造存储：默认 sqlite（v1 行为），可选 postgres。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..config import AppConfig
from .protocols import KernelStore, WebStore

logger = logging.getLogger(__name__)


@dataclass
class Storage:
    backend: str
    kernel: KernelStore
    web: WebStore
    close: Callable[[], None]
    # postgres 下是 PgPool，供记忆等额外仓储共用连接池；sqlite 为 None
    pool: Any | None = None


def build_storage(config: AppConfig) -> Storage:
    backend = config.settings.as_storage_backend
    if backend == "postgres":
        from .pg import PgKernelStore, PgPool, PgWebStore, run_migrations

        pool = PgPool(config.settings.as_pg_dsn)
        try:
            applied = run_migrations(pool)
        except Exception:
            pool.close()
            raise
        if applied:
            logger.info("postgres migrations applied: %s", ", ".join(applied))
        return Storage(
            backend="postgres",
            kernel=PgKernelStore(pool),
            web=PgWebStore(pool),
            close=pool.close,
            pool=pool,
        )
    if backend != "sqlite":
        raise ValueError(f"未知的 AS_STORAGE_BACKEND: {backend}（可选 sqlite / postgres）")

    from ..api.db import WebDB
    from ..runtime.checkpoint import CheckpointStore

    store = CheckpointStore(config.db_path)
    web = WebDB(config.db_path)
    return Storage(backend="sqlite", kernel=store, web=web, close=store.close)
