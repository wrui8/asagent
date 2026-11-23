from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path


def _safe_name(name: str) -> str:
    cleaned = "".join(
        ch if ch.isalnum() or ch in "._-" else "_" for ch in name
    )
    return cleaned or "file"


def backup_file(
    target: Path, *, backup_root: Path, session_id: str
) -> Path | None:
    """把已存在的文件复制到会话备份目录；新建文件返回 None。

    备份根由 profile 的 tool_config 固定，模型无法指定，避免把文件写到任意位置。
    """
    if not target.is_file():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    dest_dir = backup_root / _safe_name(session_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stamp}_{_safe_name(target.name)}"
    shutil.copy2(target, dest)
    return dest.resolve()
