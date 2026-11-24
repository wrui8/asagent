from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

from .chunking import split_markdown

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[一-鿿]+")

# 同进程内复用模型，避免每次构建 runtime 都重新加载（约 20s）
_EMBEDDER_CACHE: dict[str, object] = {}

_DOC_KEY = "doc:"


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


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class RagIndex:
    """SQLite + numpy 的本地向量索引；语料规模在万级 chunk 内足够。

    指纹基于文档内容哈希而非 mtime，增量构建只编码变化的文档。
    """

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
        self._loaded_fingerprint: str | None = None

    # ------------------------------------------------------------------ #
    def _connect(self) -> sqlite3.Connection:
        self.index_db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.index_db)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
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

    def _disk_texts(self) -> dict[str, str]:
        if not self.docs_dir.exists():
            return {}
        texts: dict[str, str] = {}
        for path in sorted(self.docs_dir.glob("*.md")):
            try:
                texts[path.stem] = path.read_text(encoding="utf-8")
            except OSError:  # 读取失败的文档不应拖垮整次构建
                continue
        return texts

    def _compose(self, hashes: dict[str, str]) -> str:
        hasher = hashlib.sha256()
        hasher.update(
            f"{self.embedding_model}\x00{self.max_chunk_chars}\x00".encode()
        )
        for doc_id in sorted(hashes):
            hasher.update(f"{doc_id}\x00{hashes[doc_id]}\n".encode())
        return hasher.hexdigest()

    def _stored_fingerprint(self, con: sqlite3.Connection) -> str | None:
        try:
            row = con.execute(
                "SELECT value FROM rag_meta WHERE key='fingerprint'"
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        return row["value"] if row else None

    @staticmethod
    def _stored_doc_hashes(con: sqlite3.Connection) -> dict[str, str]:
        rows = con.execute(
            "SELECT key, value FROM rag_meta WHERE key LIKE 'doc:%'"
        ).fetchall()
        return {row["key"][len(_DOC_KEY) :]: row["value"] for row in rows}

    @staticmethod
    def _write_meta(con: sqlite3.Connection, key: str, value: str) -> None:
        con.execute(
            "INSERT OR REPLACE INTO rag_meta(key, value) VALUES(?, ?)", (key, value)
        )

    def _invalidate(self) -> None:
        self._matrix = None
        self._rows = None
        self._loaded_fingerprint = None

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

    def build(self, doc_ids: list[str] | None = None) -> int:
        """构建索引；doc_ids 为空表示全量，否则只处理指定文档。

        内容哈希未变的文档直接复用已存向量，因此「全量」也只会编码变化的文档。
        返回本次写入的片段数。
        """
        if not self.docs_dir.exists():
            raise FileNotFoundError(f"文档目录不存在: {self.docs_dir}")
        disk = self._disk_texts()
        if not disk and doc_ids is None:
            raise ValueError(f"文档目录中没有可索引的 Markdown: {self.docs_dir}")

        con = self._connect()
        written = 0
        try:
            # 阶段一：只读快照 + 编码。编码可能持续数十秒，绝不能在
            # 此期间持有写事务，否则并发的删除/重建会撞上 database is locked。
            meta = self._stored_doc_hashes(con)
            indexed = {
                row["doc_id"]
                for row in con.execute("SELECT DISTINCT doc_id FROM rag_chunks")
            }
            if doc_ids is None:
                targets = sorted(disk)
                removed = [doc_id for doc_id in meta if doc_id not in disk]
            else:
                wanted = sorted(set(doc_ids))
                targets = [doc_id for doc_id in wanted if doc_id in disk]
                removed = [doc_id for doc_id in wanted if doc_id not in disk]

            plans: list[tuple[str, str, list[tuple[str, int, str, str, str]], object]] = []
            for doc_id in targets:
                text = disk[doc_id]
                digest = _hash_text(text)
                if doc_id in indexed and meta.get(doc_id) == digest:
                    continue
                chunks = split_markdown(doc_id, text, max_chars=self.max_chunk_chars)
                rows = [
                    (c.doc_id, c.index, c.title, c.section, c.text) for c in chunks
                ]
                vectors = None
                if chunks:
                    vectors = self._embedder_instance().encode(
                        [c.embed_text for c in chunks],
                        normalize_embeddings=True,
                        batch_size=16,
                        show_progress_bar=False,
                    )
                plans.append((doc_id, digest, rows, vectors))

            # 阶段二：短写事务。写前重新核对磁盘，跳过编码期间被删除
            # 或再次修改的文档，避免把过期内容写回索引。
            with con:
                current = self._stored_doc_hashes(con)
                for doc_id in removed:
                    if (self.docs_dir / f"{doc_id}.md").exists():
                        continue
                    con.execute("DELETE FROM rag_chunks WHERE doc_id=?", (doc_id,))
                    con.execute(
                        "DELETE FROM rag_meta WHERE key=?", (f"{_DOC_KEY}{doc_id}",)
                    )
                    current.pop(doc_id, None)

                for doc_id, digest, rows, vectors in plans:
                    path = self.docs_dir / f"{doc_id}.md"
                    try:
                        if _hash_text(path.read_text(encoding="utf-8")) != digest:
                            continue
                    except OSError:
                        continue
                    con.execute("DELETE FROM rag_chunks WHERE doc_id=?", (doc_id,))
                    if rows:
                        con.executemany(
                            "INSERT INTO rag_chunks VALUES (?, ?, ?, ?, ?, ?)",
                            [
                                (
                                    row[0],
                                    row[1],
                                    row[2],
                                    row[3],
                                    row[4],
                                    np.asarray(vec, dtype=np.float32).tobytes(),
                                )
                                for row, vec in zip(rows, vectors, strict=True)
                            ],
                        )
                    con.execute(
                        "INSERT OR REPLACE INTO rag_meta VALUES (?, ?)",
                        (f"{_DOC_KEY}{doc_id}", digest),
                    )
                    current[doc_id] = digest
                    written += len(rows)

                self._write_meta(con, "fingerprint", self._compose(current))
                self._write_meta(
                    con, "built_at", datetime.now().isoformat(timespec="seconds")
                )
        finally:
            con.close()
        self._invalidate()
        return written

    def remove(self, doc_ids: list[str]) -> int:
        """立即从索引移除文档（删除/回收站时使用，无需等待重建）。"""
        if not doc_ids:
            return 0
        con = self._connect()
        removed = 0
        try:
            with con:
                meta = self._stored_doc_hashes(con)
                for doc_id in doc_ids:
                    cur = con.execute(
                        "DELETE FROM rag_chunks WHERE doc_id=?", (doc_id,)
                    )
                    removed += cur.rowcount or 0
                    con.execute(
                        "DELETE FROM rag_meta WHERE key=?", (f"{_DOC_KEY}{doc_id}",)
                    )
                    meta.pop(doc_id, None)
                self._write_meta(con, "fingerprint", self._compose(meta))
                self._write_meta(
                    con, "built_at", datetime.now().isoformat(timespec="seconds")
                )
        finally:
            con.close()
        self._invalidate()
        return removed

    def ensure(self) -> bool:
        """索引缺失或语料变化时重建；返回是否重建。"""
        if not self.index_db.exists():
            self.build()
            return True
        if self.status()["stale"]:
            self.build()
            return True
        return False

    def status(self) -> dict:
        disk = self._disk_texts()
        hashes = {doc_id: _hash_text(text) for doc_id, text in disk.items()}
        con = self._connect()
        try:
            stored = self._stored_fingerprint(con)
            chunks = int(
                con.execute("SELECT COUNT(*) FROM rag_chunks").fetchone()[0]
            )
            index_docs = int(
                con.execute(
                    "SELECT COUNT(DISTINCT doc_id) FROM rag_chunks"
                ).fetchone()[0]
            )
            built = con.execute(
                "SELECT value FROM rag_meta WHERE key='built_at'"
            ).fetchone()
        finally:
            con.close()
        current = self._compose(hashes)
        return {
            "fingerprint": current,
            "stored_fingerprint": stored,
            "stale": stored != current or chunks == 0,
            "chunks": chunks,
            "index_docs": index_docs,
            "disk_docs": len(disk),
            "built_at": built["value"] if built else None,
        }

    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        con = self._connect()
        try:
            stored = self._stored_fingerprint(con)
            if self._matrix is not None and stored == self._loaded_fingerprint:
                return
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
        self._loaded_fingerprint = stored

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

    def doc_hashes(self) -> dict[str, str]:
        """已入库文档的内容哈希，用于判断单篇文档是否需要重建。"""
        con = self._connect()
        try:
            return self._stored_doc_hashes(con)
        finally:
            con.close()

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
