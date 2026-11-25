#!/usr/bin/env python3
"""备份核心数据：SQLite 状态库、知识库文档、配置与评测集。

不依赖第三方包，Windows 与 Linux 通用；建议每日运行一次并保留 7 天。

用法：
    uv run python scripts/backup.py
    uv run python scripts/backup.py --keep 7 --dest backups
    uv run python scripts/backup.py --include-env   # 含 .env（注意权限）
    uv run python scripts/backup.py --middleware    # 额外 dump PostgreSQL / Redis

备份目录结构：
    backups/20261123-031700/
      state.db          SQLite .backup 一致性快照
      postgres.sql      仅 --middleware 且容器在运行
      redis.rdb         仅 --middleware 且容器在运行
      data/docs/        知识库正文
      configs/          models.yaml 等
      evals/            黄金评测集
      env/.env          仅 --include-env
      manifest.json     文件清单、sha256、大小、中间件状态

中间件备份是「尽力而为」：docker 不存在、容器未运行或 dump 失败只记录状态，
不会让整个备份失败（Milvus 向量属于派生数据，可由 PG + data/docs 重建）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

STAMP_RE = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
COPY_DIRS = ("data/docs", "configs", "evals")

# 阶段 0：中间件容器的固定名字（deploy/docker-compose.yml）
PG_CONTAINER = "asagent-postgres"
REDIS_CONTAINER = "asagent-redis"


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


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _container_running(name: str) -> bool:
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", name],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and proc.stdout.strip().lower() == "true"


def dump_postgres(container: str, dest: Path) -> bool:
    """在容器内执行 pg_dump；认证发生在容器内部，脚本不接触密码。"""
    user = os.environ.get("AS_PG_USER", "asagent")
    db = os.environ.get("AS_PG_DB", "asagent")
    try:
        with dest.open("wb") as handle:
            proc = subprocess.run(
                ["docker", "exec", container, "pg_dump", "-U", user, "-d", db, "--no-owner"],
                stdout=handle,
                stderr=subprocess.PIPE,
                timeout=600,
            )
    except (OSError, subprocess.SubprocessError):
        dest.unlink(missing_ok=True)
        return False
    if proc.returncode != 0 or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        return False
    return True


def _wait_redis_save(container: str, timeout_s: float = 120.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            proc = subprocess.run(
                ["docker", "exec", container, "redis-cli", "INFO", "persistence"],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if proc.returncode == 0 and "rdb_bgsave_in_progress:0" in proc.stdout:
            return True
        time.sleep(1)
    return False


def dump_redis(container: str, dest: Path) -> bool:
    """触发 BGSAVE 并复制 RDB；appendonly 开启时 RDB 仍会生成。"""
    try:
        save = subprocess.run(
            ["docker", "exec", container, "redis-cli", "BGSAVE"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if save.returncode != 0:
            return False
        time.sleep(1)  # 让 BGSAVE 先启动，避免读到上一次的 in_progress:0
        if not _wait_redis_save(container):
            return False
        copy = subprocess.run(
            ["docker", "cp", f"{container}:/data/dump.rdb", str(dest)],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        dest.unlink(missing_ok=True)
        return False
    if copy.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        return False
    return True


def backup_middleware(backup_dir: Path) -> dict[str, str]:
    """尽力而为地备份 PG / Redis；返回 ``{name: ok|skipped|failed}``。"""
    status: dict[str, str] = {}
    if not _docker_available():
        print("跳过中间件备份：未找到 docker 命令", file=sys.stderr)
        return status

    targets = [
        ("postgres", PG_CONTAINER, "postgres.sql", dump_postgres),
        ("redis", REDIS_CONTAINER, "redis.rdb", dump_redis),
    ]
    for name, container, filename, dump in targets:
        if not _container_running(container):
            status[name] = "skipped"
            continue
        if dump(container, backup_dir / filename):
            status[name] = "ok"
        else:
            status[name] = "failed"
            print(f"中间件备份失败：{name}（{container}）", file=sys.stderr)
    return status


def write_manifest(
    backup_dir: Path,
    *,
    db_source: Path,
    env_included: bool,
    middleware: dict[str, str] | None = None,
) -> list[dict]:
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
        "middleware": middleware or {},
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
    middleware: bool = False,
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

    middleware_status: dict[str, str] = {}
    if middleware:
        middleware_status = backup_middleware(backup_dir)

    items = write_manifest(
        backup_dir,
        db_source=db_source,
        env_included=env_included,
        middleware=middleware_status,
    )
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
    parser.add_argument(
        "--middleware",
        action="store_true",
        help="额外备份 PostgreSQL（pg_dump）与 Redis（RDB）；容器不存在时跳过",
    )
    args = parser.parse_args(argv)
    try:
        create_backup(
            root=args.root,
            dest=args.dest,
            keep=args.keep,
            include_env=args.include_env,
            db_path=args.db,
            middleware=args.middleware,
        )
    except FileNotFoundError as exc:
        print(f"备份失败：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
