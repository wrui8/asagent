from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .chunking import split_markdown
from .index import DocInfo, SearchHit, _hash_text, _lexical_score
from .tei import TEIClient

logger = logging.getLogger(__name__)

_DOC_KEY = "doc:"
_DENSE_FIELD = "dense"

_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_meta (key TEXT PRIMARY KEY, value TEXT);
"""

# Milvus 的 VARCHAR max_length 是字节数（UTF-8），中文按 3 字节计
_FIELDS = {
    "chunk_id": 512,
    "doc_id": 256,
    "title": 1024,
    "section": 1024,
    "text": 8192,
}


class MilvusIndex:
    """Milvus + TEI 的向量索引，对外接口与 :class:`RagIndex` 保持一致。

    - 向量与片段文本存 Milvus，文档哈希/指纹/构建时间存本地 SQLite（meta）。
    - 集合名 ``kb_chunks_<时间戳>``，检索统一走别名 ``kb_chunks``；
      全量重建时先建新集合再切别名，检索不会读到半成品。
    - 检索沿用 v1 的打分：``0.7 * 余弦相似度 + 0.3 * 关键词覆盖率``，
      保证切换后端后评测口径不变。
    """

    def __init__(
        self,
        *,
        milvus_uri: str,
        tei_url: str,
        docs_dir: Path,
        index_db: Path,
        embedding_dim: int = 1024,
        max_chunk_chars: int = 500,
        collection_prefix: str = "kb_chunks",
        meta_db: Path | None = None,
        tei_batch_size: int = 8,
    ):
        if not milvus_uri.strip():
            raise ValueError("Milvus 地址为空：请设置 AS_MILVUS_URI")
        self.milvus_uri = milvus_uri.strip()
        self.docs_dir = docs_dir
        self.index_db = index_db
        self.embedding_dim = int(embedding_dim)
        self.max_chunk_chars = max_chunk_chars
        self.collection_prefix = collection_prefix
        self.alias = collection_prefix
        self.meta_db = meta_db or index_db.with_name(f"{index_db.stem}.meta.sqlite3")
        self.tei = TEIClient(tei_url, batch_size=tei_batch_size)
        self._milvus: Any = None

    # ------------------------------------------------------------------ #
    # 连接与元数据
    # ------------------------------------------------------------------ #
    def _client(self) -> Any:
        if self._milvus is None:
            try:
                from pymilvus import MilvusClient
            except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可执行提示
                raise RuntimeError(
                    "RAG 后端 milvus 需要 pymilvus：请 `uv sync --extra milvus`"
                ) from exc
            self._milvus = MilvusClient(uri=self.milvus_uri)
        return self._milvus

    def _connect_meta(self) -> sqlite3.Connection:
        self.meta_db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.meta_db)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=10000")
        con.executescript(_META_SCHEMA)
        return con

    @staticmethod
    def _stored_meta(con: sqlite3.Connection) -> dict[str, str]:
        return {
            row["key"]: row["value"]
            for row in con.execute("SELECT key, value FROM rag_meta")
        }

    @staticmethod
    def _write_meta(con: sqlite3.Connection, key: str, value: str) -> None:
        con.execute(
            "INSERT OR REPLACE INTO rag_meta(key, value) VALUES(?, ?)", (key, value)
        )

    def _doc_hashes(self) -> dict[str, str]:
        with self._connect_meta() as con:
            rows = con.execute(
                "SELECT key, value FROM rag_meta WHERE key LIKE 'doc:%'"
            ).fetchall()
        return {row["key"][len(_DOC_KEY) :]: row["value"] for row in rows}

    def _disk_texts(self) -> dict[str, str]:
        if not self.docs_dir.exists():
            return {}
        texts: dict[str, str] = {}
        for path in sorted(self.docs_dir.glob("*.md")):
            try:
                texts[path.stem] = path.read_text(encoding="utf-8")
            except OSError:
                continue
        return texts

    def _compose(self, hashes: dict[str, str]) -> str:
        hasher = hashlib.sha256()
        hasher.update(
            f"milvus\x00{self.embedding_dim}\x00{self.max_chunk_chars}\x00".encode()
        )
        for doc_id in sorted(hashes):
            hasher.update(f"{doc_id}\x00{hashes[doc_id]}\n".encode())
        return hasher.hexdigest()

    # ------------------------------------------------------------------ #
    # 集合与别名
    # ------------------------------------------------------------------ #
    def _alias_target(self) -> str | None:
        """别名当前指向的集合；别名不存在时返回 None。

        先用 has_collection 探测（别名存在时返回 True），避免没有别名时
        describe_alias 在 pymilvus 日志里刷 ERROR。
        """
        client = self._client()
        if not client.has_collection(self.alias):
            return None
        try:
            info = client.describe_alias(alias=self.alias)
        except Exception:  # noqa: BLE001 - 别名存在性在两次调用间变化
            return None
        if isinstance(info, dict):
            return info.get("collection_name")
        return getattr(info, "collection_name", None)

    def _collection_dim(self, name: str) -> int | None:
        client = self._client()
        try:
            desc = client.describe_collection(collection_name=name)
        except Exception:  # noqa: BLE001
            return None
        for field in desc.get("fields", []):
            # pymilvus 2.5 返回 "name"，部分版本/接口返回 "field_name"
            name = field.get("name") or field.get("field_name")
            if name != _DENSE_FIELD:
                continue
            params = field.get("params") or {}
            dim = params.get("dim")
            return int(dim) if dim is not None else None
        return None

    def _new_collection_name(self) -> str:
        # 带微秒，避免同一秒内 rebuild 生成同名集合并覆盖
        stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"{self.collection_prefix}_{stamp}"

    def _create_collection(self, name: str) -> None:
        from pymilvus import DataType, MilvusClient

        client = self._client()
        schema = MilvusClient.create_schema(
            auto_id=False, enable_dynamic_field=False
        )
        schema.add_field(
            "chunk_id",
            DataType.VARCHAR,
            is_primary=True,
            max_length=_FIELDS["chunk_id"],
        )
        schema.add_field("doc_id", DataType.VARCHAR, max_length=_FIELDS["doc_id"])
        schema.add_field("chunk_index", DataType.INT64)
        schema.add_field("title", DataType.VARCHAR, max_length=_FIELDS["title"])
        schema.add_field("section", DataType.VARCHAR, max_length=_FIELDS["section"])
        schema.add_field("text", DataType.VARCHAR, max_length=_FIELDS["text"])
        schema.add_field(_DENSE_FIELD, DataType.FLOAT_VECTOR, dim=self.embedding_dim)

        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name=_DENSE_FIELD,
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        client.create_collection(
            collection_name=name, schema=schema, index_params=index_params
        )
        client.load_collection(collection_name=name)

    def _switch_alias(self, target: str) -> str | None:
        """把别名指向 target；返回旧集合名（若有）。"""
        client = self._client()
        previous = self._alias_target()
        if previous is None:
            client.create_alias(collection_name=target, alias=self.alias)
        elif previous != target:
            client.alter_alias(collection_name=target, alias=self.alias)
        return previous

    def _ensure_collection(self) -> str:
        """返回可写入的集合名；别名不存在时创建集合并建立别名。"""
        target = self._alias_target()
        if target is not None:
            dim = self._collection_dim(target)
            if dim is not None and dim != self.embedding_dim:
                raise ValueError(
                    f"Milvus 集合 {target} 维度为 {dim}，与 AS_EMBEDDING_DIM="
                    f"{self.embedding_dim} 不一致，请执行 `asagent rag-index --force` 重建"
                )
            return target
        name = self._new_collection_name()
        self._create_collection(name)
        self._switch_alias(name)
        logger.info("已创建 Milvus 集合并绑定别名: %s -> %s", self.alias, name)
        return name

    # ------------------------------------------------------------------ #
    # 写入
    # ------------------------------------------------------------------ #
    def _entries(self, doc_id: str, text: str) -> list[dict]:
        chunks = split_markdown(doc_id, text, max_chars=self.max_chunk_chars)
        if not chunks:
            return []
        vectors = self.tei.embed([c.embed_text for c in chunks])
        expected = self.embedding_dim
        for vec in vectors:
            if len(vec) != expected:
                raise ValueError(
                    f"TEI 返回向量维度 {len(vec)}，与服务端 AS_EMBEDDING_DIM={expected} 不符"
                )
        return [
            {
                "chunk_id": f"{c.doc_id}#{c.index}",
                "doc_id": c.doc_id,
                "chunk_index": c.index,
                "title": c.title[:512],
                "section": c.section[:512],
                "text": c.text,
                "dense": vec,
            }
            for c, vec in zip(chunks, vectors, strict=True)
        ]

    def _delete_docs(self, collection: str, doc_ids: list[str]) -> int:
        if not doc_ids:
            return 0
        ids = ",".join(json.dumps(doc_id) for doc_id in doc_ids)
        resp = self._client().delete(
            collection_name=collection, filter=f"doc_id in [{ids}]"
        )
        if isinstance(resp, dict):
            return int(resp.get("delete_count", 0))
        return int(getattr(resp, "delete_count", 0) or 0)

    def build(self, doc_ids: list[str] | None = None) -> int:
        """增量构建：只编码内容变化的文档；返回本次写入的片段数。"""
        if not self.docs_dir.exists():
            raise FileNotFoundError(f"文档目录不存在: {self.docs_dir}")
        disk = self._disk_texts()
        if not disk and doc_ids is None:
            raise ValueError(f"文档目录中没有可索引的 Markdown: {self.docs_dir}")

        meta = self._doc_hashes()
        if doc_ids is None:
            targets = sorted(disk)
            removed = [doc_id for doc_id in meta if doc_id not in disk]
        else:
            wanted = sorted(set(doc_ids))
            targets = [doc_id for doc_id in wanted if doc_id in disk]
            removed = [doc_id for doc_id in wanted if doc_id not in disk]

        plans: list[tuple[str, str, list[dict]]] = []
        for doc_id in targets:
            text = disk[doc_id]
            digest = _hash_text(text)
            if meta.get(doc_id) == digest:
                continue
            entries = self._entries(doc_id, text)
            plans.append((doc_id, digest, entries))

        if not plans and not removed:
            return 0

        collection = self._ensure_collection()
        client = self._client()
        written = 0
        current = dict(meta)
        for doc_id, digest, entries in plans:
            # 编码期间文档可能又被改过；以写入时刻的磁盘内容为准
            path = self.docs_dir / f"{doc_id}.md"
            try:
                if _hash_text(path.read_text(encoding="utf-8")) != digest:
                    continue
            except OSError:
                continue
            self._delete_docs(collection, [doc_id])
            if entries:
                client.insert(collection_name=collection, data=entries)
            current[doc_id] = digest
            written += len(entries)

        for doc_id in removed:
            if (self.docs_dir / f"{doc_id}.md").exists():
                continue
            self._delete_docs(collection, [doc_id])
            current.pop(doc_id, None)

        client.flush(collection_name=collection)
        with self._connect_meta() as con:
            con.execute("DELETE FROM rag_meta WHERE key LIKE 'doc:%'")
            for doc_id, digest in current.items():
                self._write_meta(con, f"{_DOC_KEY}{doc_id}", digest)
            self._write_meta(con, "fingerprint", self._compose(current))
            self._write_meta(
                con, "built_at", datetime.now().isoformat(timespec="seconds")
            )
        return written

    def rebuild(self) -> int:
        """全量重建到新集合，切换别名后删除旧集合（蓝绿）。"""
        disk = self._disk_texts()
        if not disk:
            raise ValueError(f"文档目录中没有可索引的 Markdown: {self.docs_dir}")

        client = self._client()
        name = self._new_collection_name()
        self._create_collection(name)
        hashes: dict[str, str] = {}
        written = 0
        try:
            # 逐篇编码并插入，避免一次性构造超大 gRPC 消息
            for doc_id in sorted(disk):
                text = disk[doc_id]
                hashes[doc_id] = _hash_text(text)
                entries = self._entries(doc_id, text)
                if entries:
                    client.insert(collection_name=name, data=entries)
                    written += len(entries)
            client.flush(collection_name=name)
            previous = self._switch_alias(name)
        except Exception:
            try:
                client.drop_collection(collection_name=name)
            except Exception:  # noqa: BLE001 - 清理失败不掩盖原始异常
                logger.warning("清理未完成的新集合失败: %s", name, exc_info=True)
            raise
        if previous and previous != name:
            try:
                client.drop_collection(collection_name=previous)
            except Exception:  # noqa: BLE001 - 旧集合清理失败不影响检索
                logger.warning("删除旧集合并失败: %s", previous, exc_info=True)

        with self._connect_meta() as con:
            con.execute("DELETE FROM rag_meta WHERE key LIKE 'doc:%'")
            for doc_id, digest in hashes.items():
                self._write_meta(con, f"{_DOC_KEY}{doc_id}", digest)
            self._write_meta(con, "fingerprint", self._compose(hashes))
            self._write_meta(
                con, "built_at", datetime.now().isoformat(timespec="seconds")
            )
        return written

    def remove(self, doc_ids: list[str]) -> int:
        """立即从索引移除文档，无需等待重建。"""
        if not doc_ids:
            return 0
        collection = self._alias_target()
        removed = 0
        if collection is not None:
            removed = self._delete_docs(collection, doc_ids)
            self._client().flush(collection_name=collection)

        with self._connect_meta() as con:
            meta = self._doc_hashes()
            for doc_id in doc_ids:
                meta.pop(doc_id, None)
            con.execute("DELETE FROM rag_meta WHERE key LIKE 'doc:%'")
            for doc_id, digest in meta.items():
                self._write_meta(con, f"{_DOC_KEY}{doc_id}", digest)
            self._write_meta(con, "fingerprint", self._compose(meta))
            self._write_meta(
                con, "built_at", datetime.now().isoformat(timespec="seconds")
            )
        return removed

    def ensure(self) -> bool:
        """索引缺失或语料变化时重建；返回是否重建。"""
        if self._alias_target() is None:
            self.build()
            return True
        if self.status()["stale"]:
            self.build()
            return True
        return False

    # ------------------------------------------------------------------ #
    # 读取
    # ------------------------------------------------------------------ #
    def status(self) -> dict:
        disk = self._disk_texts()
        hashes = {doc_id: _hash_text(text) for doc_id, text in disk.items()}
        with self._connect_meta() as con:
            stored = self._stored_meta(con)
        stored_fingerprint = stored.get("fingerprint")
        built_at = stored.get("built_at")
        index_docs = sum(1 for key in stored if key.startswith(_DOC_KEY))

        collection = self._alias_target()
        chunks = 0
        if collection is not None:
            try:
                chunks = int(
                    self._client().get_collection_stats(collection_name=collection).get(
                        "row_count", 0
                    )
                )
            except Exception:  # noqa: BLE001 - 统计失败时仍可判断 stale
                logger.warning("获取 Milvus 集合统计失败", exc_info=True)
        current = self._compose(hashes)
        return {
            "backend": "milvus",
            "fingerprint": current,
            "stored_fingerprint": stored_fingerprint,
            "stale": stored_fingerprint != current or chunks == 0,
            "chunks": chunks,
            "index_docs": index_docs,
            "disk_docs": len(disk),
            "built_at": built_at,
        }

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        collection = self._alias_target()
        if collection is None:
            return []
        qvec = self.tei.embed([query])[0]
        limit = max(top_k * 4, 20)
        client = self._client()
        try:
            client.load_collection(collection_name=collection)
        except Exception:  # noqa: BLE001 - 已加载时会报错，忽略
            pass
        results = client.search(
            collection_name=collection,
            data=[qvec],
            anns_field=_DENSE_FIELD,
            search_params={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=limit,
            output_fields=["doc_id", "chunk_index", "title", "section", "text"],
        )
        rows = results[0] if results else []
        hits: list[SearchHit] = []
        for row in rows:
            entity = row.get("entity") or {}
            text = str(entity.get("text", ""))
            section = str(entity.get("section", ""))
            similarity = float(row.get("distance", 0.0))
            score = 0.7 * similarity + 0.3 * _lexical_score(query, text + "\n" + section)
            hits.append(
                SearchHit(
                    doc_id=str(entity.get("doc_id", "")),
                    chunk_index=int(entity.get("chunk_index", 0)),
                    title=str(entity.get("title", "")),
                    section=section,
                    text=text,
                    score=score,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[: max(1, top_k)]

    def doc_hashes(self) -> dict[str, str]:
        return self._doc_hashes()

    def list_docs(self) -> list[DocInfo]:
        collection = self._alias_target()
        if collection is None:
            return []
        counts: dict[str, int] = {}
        titles: dict[str, str] = {}
        client = self._client()
        try:
            iterator = client.query_iterator(
                collection_name=collection,
                batch_size=1000,
                filter="",
                output_fields=["doc_id", "title"],
            )
            while True:
                rows = iterator.next()
                if not rows:
                    break
                for row in rows:
                    doc_id = str(row.get("doc_id", ""))
                    counts[doc_id] = counts.get(doc_id, 0) + 1
                    titles.setdefault(doc_id, str(row.get("title", "")))
            iterator.close()
        except Exception:  # noqa: BLE001 - 迭代接口不可用时退回方言查询
            logger.warning("Milvus query_iterator 不可用，退回 limit=16384", exc_info=True)
            rows = client.query(
                collection_name=collection,
                filter="",
                output_fields=["doc_id", "title"],
                limit=16384,
            )
            for row in rows:
                doc_id = str(row.get("doc_id", ""))
                counts[doc_id] = counts.get(doc_id, 0) + 1
                titles.setdefault(doc_id, str(row.get("title", "")))
        return [
            DocInfo(doc_id, titles.get(doc_id) or doc_id, chunks)
            for doc_id, chunks in sorted(counts.items())
        ]

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
