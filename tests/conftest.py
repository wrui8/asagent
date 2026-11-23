from __future__ import annotations

from pathlib import Path

import pytest

from asagent.config import AppConfig, load_config


@pytest.fixture(scope="session")
def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def config(tmp_path: Path, project_root: Path) -> AppConfig:
    cfg = load_config(project_root)
    cfg.settings.as_db_path = str(tmp_path / "state.db")
    cfg.settings.as_trace_dir = str(tmp_path / "traces")
    cfg.settings.as_artifacts_dir = str(tmp_path / "artifacts")
    cfg.settings.as_workspace_dir = str(tmp_path / "workspace")
    return cfg
