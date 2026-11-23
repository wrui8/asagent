from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .chunking import split_markdown

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[一-鿿]+")

# 同进程内复用模型，避免每次构建 runtime 都重新加载（约 20s）
_EMBEDDER_CACHE: dict[str, object] = {}


@dataclass(frozen=True)
class SearchHit:
    doc_id: str
    chunk_index: int
    title: str
    section: str
    text: str
    score: float

    @property
    def citation(self) -> str:
        return f"{self.doc_id}#{self.chunk_index}"


@dataclass(frozen=True)
class DocInfo:
    doc_id: str
    title: str
    chunks: int


def _lexical_score(query: str, text: str) -> float:
    """关键词重叠：中文 bigram + 英文/数字词，覆盖率为命中项占比。"""
    terms: set[str] = set(_TOKEN_RE.findall(query.lower()))
    for block in _CJK_RE.findall(query):
        terms.update(block[i : i + 2] for i in range(max(0, len(block) - 1)))
    if not terms:
        return 0.0
    lowered = text.lower()
    hits = sum(1 for term in terms if term in lowered)
    return hits / len(terms)


class RagIndex:
    """SQLite + numpy 的本地向量索引；语料规模在万级 chunk 内足够。"""

    def __init__(
        self,
        *,
        index_db: Path,
        docs_dir: Path,
        embedding_model: str,
        max_chunk_chars: int = 500,
    ):
        self.index_db = index_db
        self.docs_dir = docs_dir
        self.embedding_model = embedding_model
        self.max_chunk_chars = max_chunk_chars
        self._embedder = None
        self._matrix: np.ndarray | None = None
        self._rows: list[sqlite3.Row] | None = None

    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        self.index_db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.index_db)
        con.row_factory = sqlite3.Row
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS rag_meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS rag_chunks (
                doc_id TEXT, chunk_index INTEGER, title TEXT, section TEXT,
                text TEXT, embedding BLOB,
                PRIMARY KEY (doc_id, chunk_index)
            );
            """
        )
        return con

    def _fingerprint(self) -> str:
        hasher = hashlib.sha256()
        hasher.update(str(self.embedding_model).encode())
        hasher.update(str(self.max_chunk_chars).encode())
        for path in sorted(self.docs_dir.glob("*.md")):
            stat = path.stat()
            hasher.update(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}".encode())
        return hasher.hexdigest()

    def _stored_fingerprint(self, con: sqlite3.Connection) -> str | None:
        try:
            row = con.execute(
                "SELECT value FROM rag_meta WHERE key='fingerprint'"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return row["value"] if row else None

    # ------------------------------------------------------------------ #
    def _embedder_instance(self):
        if self._embedder is None:
            cached = _EMBEDDER_CACHE.get(self.embedding_model)
            if cached is None:
                from sentence_transformers import SentenceTransformer

                cached = SentenceTransformer(self.embedding_model, device="cpu")
                _EMBEDDER_CACHE[self.embedding_model] = cached
            self._embedder = cached
        return self._embedder

    def build(self) -> int:
        if not self.docs_dir.exists():
            raise FileNotFoundError(f"文档目录不存在: {self.docs_dir}")
        chunks = []
        for path in sorted(self.docs_dir.glob("*.md")):
            chunks.extend(
                split_markdown(
                    path.stem,
                    path.read_text(encoding="utf-8"),
                    max_chars=self.max_chunk_chars,
                )
            )
        if not chunks:
            raise ValueError(f"文档目录中没有可索引的 Markdown: {self.docs_dir}")

        vectors = self._embedder_instance().encode(
            [c.embed_text for c in chunks],
            normalize_embeddings=True,
            batch_size=16,
            show_progress_bar=False,
        )
        con = self._connect()
        try:
            with con:
                con.execute("DELETE FROM rag_chunks")
                con.executemany(
                    "INSERT INTO rag_chunks VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        (
                            c.doc_id,
                            c.index,
                            c.title,
                            c.section,
                            c.text,
                            np.asarray(vec, dtype=np.float32).tobytes(),
                        )
                        for c, vec in zip(chunks, vectors, strict=True)
                    ],
                )
                con.execute(
                    "INSERT OR REPLACE INTO rag_meta VALUES ('fingerprint', ?)",
                    (self._fingerprint(),),
                )
        finally:
            con.close()
        self._matrix = None
        self._rows = None
        return len(chunks)

    def ensure(self) -> bool:
        """索引缺失或语料变化时重建；返回是否重建。"""
        if not self.index_db.exists():
            self.build()
            return True
        con = self._connect()
        try:
            stale = self._stored_fingerprint(con) != self._fingerprint()
            has_rows = con.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0] > 0
        finally:
            con.close()
        if stale or not has_rows:
            self.build()
            return True
        return False

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if self._matrix is not None:
            return
        con = self._connect()
        try:
            rows = list(con.execute("SELECT * FROM rag_chunks"))
        finally:
            con.close()
        self._rows = rows
        if rows:
            self._matrix = np.vstack(
                [np.frombuffer(r["embedding"], dtype=np.float32) for r in rows]
            )
        else:
            self._matrix = np.zeros((0, 1), dtype=np.float32)

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        self._load()
        assert self._rows is not None and self._matrix is not None
        if not self._rows:
            return []
        qvec = self._embedder_instance().encode(
            [query], normalize_embeddings=True, show_progress_bar=False
        )[0]
        sims = self._matrix @ np.asarray(qvec, dtype=np.float32)
        hits: list[SearchHit] = []
        for row, sim in zip(self._rows, sims, strict=True):
            lexical = _lexical_score(query, row["text"] + "\n" + row["section"])
            score = 0.7 * float(sim) + 0.3 * lexical
            hits.append(
                SearchHit(
                    doc_id=row["doc_id"],
                    chunk_index=row["chunk_index"],
                    title=row["title"],
                    section=row["section"],
                    text=row["text"],
                    score=score,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[: max(1, top_k)]

    def list_docs(self) -> list[DocInfo]:
        con = self._connect()
        try:
            rows = list(
                con.execute(
                    "SELECT doc_id, MIN(title) AS title, COUNT(*) AS chunks "
                    "FROM rag_chunks GROUP BY doc_id ORDER BY doc_id"
                )
            )
        finally:
            con.close()
        return [DocInfo(r["doc_id"], r["title"], r["chunks"]) for r in rows]

    def read_doc(self, doc_id: str, max_chars: int = 8000) -> str | None:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", doc_id):
            return None
        path = self.docs_dir / f"{doc_id}.md"
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[文档已截断，共 {len(text)} 字符]"
        return text
