#!/usr/bin/env python3
"""为 Playwright E2E 准备固定测试账号（仅测试环境，勿用于生产）。

    AS_DB_PATH=runtime/e2e-state.db uv run python scripts/seed_e2e.py

账号与密码是公开的测试常量，只用于本地/CI 的端到端测试。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from asagent.api.db import WebDB
from asagent.api.security import hash_password
from asagent.config import load_config
from asagent.profiles.loader import load_profile

ADMIN = ("e2e-admin", "e2e-admin-pass-2026", "admin")
USER = ("e2e-user", "e2e-user-pass-2026", "user")


def _require_e2e_path(path: Path) -> Path:
    """只允许清理名字里带 e2e 的路径，避免误删生产数据。"""
    if "e2e" not in path.as_posix().lower():
        raise SystemExit(f"拒绝清理非 e2e 路径：{path}")
    return path


def reset(config) -> None:
    db_path = _require_e2e_path(config.db_path)
    for suffix in ("", "-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)

    profile = load_profile("rag", project_root=config.root)
    docs_dir = _require_e2e_path(config.path(str(profile.tool_config["docs_dir"])))
    index_db = _require_e2e_path(config.path(str(profile.tool_config["index_db"])))
    shutil.rmtree(docs_dir, ignore_errors=True)
    index_db.unlink(missing_ok=True)
    print(f"reset {db_path.name}, {docs_dir.name}, {index_db.name}")


def seed(web: WebDB) -> None:
    for username, password, role in (ADMIN, USER):
        existing = web.get_user_with_hash(username)
        if existing is None:
            web.create_user(
                username=username, password_hash=hash_password(password), role=role
            )
            print(f"created {username} ({role})")
        else:
            web.update_user(
                existing["id"],
                role=role,
                disabled=False,
                password_hash=hash_password(password),
            )
            print(f"updated {username} ({role})")


def main() -> int:
    parser = argparse.ArgumentParser(description="准备 E2E 测试账号")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="清空 e2e 状态库、知识库目录与索引（路径必须含 e2e）",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    config = load_config(root)
    if args.reset:
        reset(config)
    web = WebDB(config.db_path)
    seed(web)
    print(f"database: {config.db_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
