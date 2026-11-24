#!/usr/bin/env python3
"""备份核心数据：SQLite 状态库、知识库文档、配置与评测集。

不依赖第三方包，Windows 与 Linux 通用；建议每日运行一次并保留 7 天。

用法：
    uv run python scripts/backup.py
    uv run python scripts/backup.py --keep 7 --dest backups
    uv run python scripts/backup.py --include-env   # 含 .env（注意权限）

备份目录结构：
    backups/20261123-031700/
      state.db          SQLite .backup 一致性快照
      data/docs/        知识库正文
      configs/          models.yaml 等
      evals/            黄金评测集
      env/.env          仅 --include-env
      manifest.json     文件清单、sha256、大小
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

STAMP_RE = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
COPY_DIRS = ("data/docs", "configs", "evals")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_db_path(root: Path, db_path: Path | None) -> Path:
    if db_path is not None:
        return db_path if db_path.is_absolute() else (root / db_path)
    raw = os.environ.get("AS_DB_PATH", "runtime/state.db")
    path = Path(raw)
    return path if path.is_absolute() else (root / path)


def _unique_stamp_dir(dest: Path, now: datetime) -> Path:
    base = now.strftime("%Y%m%d-%H%M%S")
    candidate = dest / base
    suffix = 2
    while candidate.exists():
        candidate = dest / f"{base}-{suffix}"
        suffix += 1
    return candidate


def copy_state_db(src: Path, dst: Path) -> None:
    """用 SQLite backup API 做一致性快照，包含尚未 checkpoint 的 WAL 内容。

    这里用普通连接而不是 `mode=ro`：WAL 数据库在只读模式且缺少 -shm 时
    可能打不开；backup API 本身不会写入源库。
    """
    source = sqlite3.connect(src)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()


def write_manifest(backup_dir: Path, *, db_source: Path, env_included: bool) -> list[dict]:
    items: list[dict] = []
    for path in sorted(backup_dir.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        items.append(
            {
                "path": path.relative_to(backup_dir).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
        )
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "db_source": str(db_source),
        "env_included": env_included,
        "files": items,
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return items


def prune(dest: Path, keep: int) -> list[Path]:
    """只清理符合时间戳命名、且超出保留数量的目录。"""
    if keep <= 0 or not dest.exists():
        return []
    candidates = sorted(
        (p for p in dest.iterdir() if p.is_dir() and STAMP_RE.match(p.name)),
        key=lambda p: p.name,
    )
    removed = candidates[:-keep] if len(candidates) > keep else []
    for path in removed:
        shutil.rmtree(path)
    return removed


def create_backup(
    *,
    root: Path,
    dest: Path,
    keep: int = 7,
    include_env: bool = False,
    db_path: Path | None = None,
    now: datetime | None = None,
) -> Path:
    root = root.resolve()
    dest = dest if dest.is_absolute() else (root / dest)
    dest.mkdir(parents=True, exist_ok=True)

    db_source = resolve_db_path(root, db_path)
    if not db_source.exists():
        raise FileNotFoundError(f"状态库不存在：{db_source}")

    backup_dir = _unique_stamp_dir(dest, now or datetime.now())
    backup_dir.mkdir(parents=True)

    copy_state_db(db_source, backup_dir / "state.db")

    for relative in COPY_DIRS:
        source = root / relative
        if source.is_dir():
            shutil.copytree(source, backup_dir / relative, dirs_exist_ok=True)

    env_included = False
    if include_env:
        env_file = root / ".env"
        if env_file.exists():
            env_dir = backup_dir / "env"
            env_dir.mkdir()
            shutil.copy2(env_file, env_dir / ".env")
            env_included = True

    items = write_manifest(backup_dir, db_source=db_source, env_included=env_included)
    prune(dest, keep)
    total = sum(item["size"] for item in items)
    print(f"备份完成：{backup_dir}（{len(items)} 个文件，{total / 1024:.1f} KiB）")
    return backup_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="备份 asagent 核心数据")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help="项目根目录")
    parser.add_argument("--dest", type=Path, default=Path("backups"), help="备份输出目录")
    parser.add_argument("--keep", type=int, default=7, help="保留最近几份（默认 7）")
    parser.add_argument("--db", type=Path, default=None, help="状态库路径（默认读 AS_DB_PATH）")
    parser.add_argument(
        "--include-env", action="store_true", help="同时备份 .env（含密钥，注意权限）"
    )
    args = parser.parse_args(argv)
    try:
        create_backup(
            root=args.root,
            dest=args.dest,
            keep=args.keep,
            include_env=args.include_env,
            db_path=args.db,
        )
    except FileNotFoundError as exc:
        print(f"备份失败：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
