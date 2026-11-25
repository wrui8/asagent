from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from asagent.config import AppConfig, load_config


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def config(tmp_path: Path, project_root: Path) -> AppConfig:
    cfg = load_config(project_root)
    # 本地根 .env 可能已切到 PostgreSQL；单元测试一律用临时 SQLite，避免依赖中间件。
    cfg.settings.as_storage_backend = "sqlite"
    cfg.settings.as_pg_dsn = ""
    cfg.settings.as_db_path = str(tmp_path / "state.db")
    cfg.settings.as_trace_dir = str(tmp_path / "traces")
    cfg.settings.as_artifacts_dir = str(tmp_path / "artifacts")
    cfg.settings.as_workspace_dir = str(tmp_path / "workspace")
    return cfg


@pytest.fixture()
def api_client(config: AppConfig) -> Iterator[TestClient]:
    """带假 Agent 运行时的 FastAPI 测试客户端（会触发 lifespan）。"""
    from api_helpers import make_client

    with make_client(config) as client:
        yield client
