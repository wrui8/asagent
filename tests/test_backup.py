from __future__ import annotations

import importlib.util
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_backup_module():
    spec = importlib.util.spec_from_file_location(
        "asagent_backup_script", ROOT / "scripts" / "backup.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = _load_backup_module()


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "runtime").mkdir(parents=True)
    (root / "data" / "docs").mkdir(parents=True)
    (root / "configs").mkdir()
    (root / "evals").mkdir()

    conn = sqlite3.connect(root / "runtime" / "state.db")
    conn.execute("CREATE TABLE demo(id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO demo(name) VALUES (?)", [("a",), ("b",), ("c",)])
    conn.commit()
    conn.close()

    (root / "data" / "docs" / "policy.md").write_text("# 制度\n", encoding="utf-8")
    (root / "configs" / "models.yaml").write_text("version: 1\n", encoding="utf-8")
    (root / "evals" / "sql.yaml").write_text("cases: []\n", encoding="utf-8")
    (root / ".env").write_text("AS_JWT_SECRET=test-only\n", encoding="utf-8")
    return root


def test_create_backup_copies_db_and_dirs(tmp_path):
    root = _make_project(tmp_path)
    out = backup.create_backup(
        root=root,
        dest=root / "backups",
        db_path=root / "runtime" / "state.db",
    )

    assert out.is_dir()
    copied = sqlite3.connect(out / "state.db")
    try:
        assert copied.execute("SELECT COUNT(*) FROM demo").fetchone()[0] == 3
    finally:
        copied.close()

    assert (out / "data" / "docs" / "policy.md").is_file()
    assert (out / "configs" / "models.yaml").is_file()
    assert (out / "evals" / "sql.yaml").is_file()
    assert not (out / "env").exists()

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    paths = {item["path"] for item in manifest["files"]}
    assert {
        "state.db",
        "data/docs/policy.md",
        "configs/models.yaml",
        "evals/sql.yaml",
    } <= paths
    assert manifest["env_included"] is False
    assert all(len(item["sha256"]) == 64 for item in manifest["files"])


def test_include_env_copies_env_file(tmp_path):
    root = _make_project(tmp_path)
    out = backup.create_backup(
        root=root,
        dest=root / "backups",
        include_env=True,
        db_path=root / "runtime" / "state.db",
    )
    assert (out / "env" / ".env").is_file()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["env_included"] is True
    assert "env/.env" in {item["path"] for item in manifest["files"]}


def test_missing_db_raises(tmp_path):
    root = _make_project(tmp_path)
    (root / "runtime" / "state.db").unlink()
    with pytest.raises(FileNotFoundError):
        backup.create_backup(root=root, dest=root / "backups")


def test_prune_keeps_newest_and_ignores_other_dirs(tmp_path):
    dest = tmp_path / "backups"
    dest.mkdir()
    names = [f"2026112{day}-030000" for day in range(1, 9)]
    for name in names:
        (dest / name).mkdir()
    (dest / "not-a-backup").mkdir()

    removed = backup.prune(dest, keep=7)

    assert [p.name for p in removed] == [names[0]]
    assert (dest / "not-a-backup").is_dir()
    assert len([p for p in dest.iterdir() if backup.STAMP_RE.match(p.name)]) == 7


def test_same_timestamp_does_not_overwrite(tmp_path):
    root = _make_project(tmp_path)
    now = datetime(2026, 11, 23, 3, 17, 0)
    first = backup.create_backup(
        root=root, dest=root / "backups", db_path=root / "runtime" / "state.db", now=now
    )
    second = backup.create_backup(
        root=root, dest=root / "backups", db_path=root / "runtime" / "state.db", now=now
    )
    assert first != second
    assert second.name.endswith("-2")


def test_unique_stamp_dir_uses_suffix(tmp_path):
    dest = tmp_path
    (dest / "20261123-031700").mkdir()
    result = backup._unique_stamp_dir(dest, datetime(2026, 11, 23, 3, 17, 0))
    assert result.name == "20261123-031700-2"
