from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import AppConfig
from ..guard.pipeline import looks_like_injection
from ..profiles.loader import load_profile
from ..tools.rag.index import RagIndex
from .db import WebDB

logger = logging.getLogger(__name__)

DOC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
MAX_DOC_CHARS = 100_000
TRASH_DIRNAME = ".trash"


class KBIndexBusy(Exception):
    """重建正在进行或触发过于频繁。"""


@dataclass(frozen=True)
class KBPaths:
    docs_dir: Path
    index_db: Path
    embedding_model: str
    max_chunk_chars: int

    def doc_path(self, doc_id: str) -> Path:
        return self.docs_dir / f"{doc_id}.md"

    @property
    def trash_dir(self) -> Path:
        return self.docs_dir / TRASH_DIRNAME


def kb_paths(config: AppConfig) -> KBPaths:
    profile = load_profile("rag", project_root=config.root)
    cfg = profile.tool_config
    return KBPaths(
        docs_dir=config.path(str(cfg.get("docs_dir", "data/docs"))),
        index_db=config.path(str(cfg.get("index_db", "data/rag.sqlite3"))),
        embedding_model=str(cfg.get("embedding_model", "")),
        max_chunk_chars=int(cfg.get("max_chunk_chars", 500)),
    )


def make_index(config: AppConfig) -> RagIndex:
    paths = kb_paths(config)
    return RagIndex(
        index_db=paths.index_db,
        docs_dir=paths.docs_dir,
        embedding_model=paths.embedding_model,
        max_chunk_chars=paths.max_chunk_chars,
    )


def extract_title(doc_id: str, content: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            if title:
                return title[:200]
    return doc_id


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def move_to_trash(paths: KBPaths, doc_id: str) -> Path:
    paths.trash_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    target = paths.trash_dir / f"{doc_id}.{stamp}.md"
    return paths.doc_path(doc_id).replace(target)


def latest_trash(paths: KBPaths, doc_id: str) -> Path | None:
    if not paths.trash_dir.exists():
        return None
    matches = sorted(paths.trash_dir.glob(f"{doc_id}.*.md"))
    return matches[-1] if matches else None


class KBIndexRunner:
    """异步重建索引：单飞锁 + 冷却，编码在线程中执行避免阻塞事件循环。"""

    def __init__(self, *, config: AppConfig, web: WebDB, cooldown_s: float = 10.0):
        self.config = config
        self.web = web
        self.cooldown_s = cooldown_s
        self._task: asyncio.Task[None] | None = None
        self._last_start = 0.0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def cooldown_remaining(self) -> float:
        if self._last_start == 0.0:
            return 0.0
        return max(0.0, self.cooldown_s - (time.monotonic() - self._last_start))

    def start(self, *, doc_ids: list[str] | None, actor: str) -> str:
        if self.running:
            raise KBIndexBusy("索引正在重建，请稍候")
        remaining = self.cooldown_remaining()
        if remaining > 0:
            raise KBIndexBusy(f"重建过于频繁，请 {remaining:.0f} 秒后再试")
        self._last_start = time.monotonic()
        job_id = self.web.kb_job_start(kind="reindex", doc_ids=doc_ids, actor=actor)
        self._task = asyncio.create_task(
            self._run(job_id, doc_ids, actor), name=f"kb-reindex:{job_id}"
        )
        return job_id

    async def _run(
        self, job_id: str, doc_ids: list[str] | None, actor: str
    ) -> None:
        try:
            index = make_index(self.config)
            written = await asyncio.to_thread(index.build, doc_ids)
            status = index.status()
            message = f"本次写入 {written} 个片段，索引共 {status['chunks']} 个片段"
            self.web.kb_job_finish(job_id, status="done", message=message)
            self.web.kb_audit(actor=actor, action="reindex", detail=message)
        except asyncio.CancelledError:
            self.web.kb_job_finish(job_id, status="failed", message="服务关闭，重建中断")
            raise
        except Exception as exc:  # noqa: BLE001 - 后台任务必须落盘失败原因
            logger.exception("kb reindex failed: %s", job_id)
            message = f"重建失败：{exc}"
            self.web.kb_job_finish(job_id, status="failed", message=message)
            self.web.kb_audit(actor=actor, action="reindex_failed", detail=message)

    async def shutdown(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def is_suspicious(content: str) -> bool:
    return looks_like_injection(content)


__all__ = [
    "DOC_ID_RE",
    "MAX_DOC_CHARS",
    "KBIndexBusy",
    "KBIndexRunner",
    "KBPaths",
    "extract_title",
    "is_suspicious",
    "kb_paths",
    "latest_trash",
    "make_index",
    "move_to_trash",
    "write_atomic",
]
