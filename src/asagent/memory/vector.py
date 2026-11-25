"""记忆向量索引：Milvus collection `memories` + TEI 编码。

只存 ``memory_id / user_id / type / dense``；原文与状态以 PG 为准，
向量丢失时可由 ``asagent memory reindex``（或重建脚本）重新编码恢复。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from ..tools.rag.tei import TEIClient

logger = logging.getLogger(__name__)

DENSE_FIELD = "dense"


@dataclass
class VectorHit:
    memory_id: str
    similarity: float


class MemoryVectorIndex:
    def __init__(
        self,
        *,
        milvus_uri: str,
        tei_url: str,
        embedding_dim: int = 1024,
        collection: str = "memories",
    ):
        if not milvus_uri.strip():
            raise ValueError("记忆子系统需要 AS_MILVUS_URI")
        if not tei_url.strip():
            raise ValueError("记忆子系统需要 AS_TEI_URL")
        self.milvus_uri = milvus_uri.strip()
        self.collection = collection
        self.embedding_dim = int(embedding_dim)
        self.tei = TEIClient(tei_url)
        self._milvus: Any = None

    def _client(self) -> Any:
        if self._milvus is None:
            try:
                from pymilvus import MilvusClient
            except ImportError as exc:  # pragma: no cover - 依赖缺失提示
                raise RuntimeError(
                    "记忆向量需要 pymilvus：请 `uv sync --extra milvus`"
                ) from exc
            self._milvus = MilvusClient(uri=self.milvus_uri)
        return self._milvus

    def close(self) -> None:
        self.tei.close()

    # ------------------------------------------------------------------ #
    def ensure(self) -> None:
        client = self._client()
        if client.has_collection(self.collection):
            return
        from pymilvus import DataType, MilvusClient

        schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(
            "memory_id", DataType.VARCHAR, is_primary=True, max_length=64
        )
        schema.add_field("user_id", DataType.VARCHAR, max_length=64)
        schema.add_field("type", DataType.VARCHAR, max_length=32)
        schema.add_field(DENSE_FIELD, DataType.FLOAT_VECTOR, dim=self.embedding_dim)
        index_params = client.prepare_index_params()
        index_params.add_index(
            field_name=DENSE_FIELD,
            index_type="HNSW",
            metric_type="COSINE",
            params={"M": 16, "efConstruction": 200},
        )
        try:
            client.create_collection(
                collection_name=self.collection,
                schema=schema,
                index_params=index_params,
            )
        except Exception:  # noqa: BLE001 - 并发下另一方可能已创建
            if not client.has_collection(self.collection):
                raise
        client.load_collection(collection_name=self.collection)
        logger.info("已确保 Milvus 记忆集合存在: %s", self.collection)

    def _load(self) -> None:
        try:
            self._client().load_collection(collection_name=self.collection)
        except Exception:  # noqa: BLE001 - 已加载时会报错，忽略
            pass

    # ------------------------------------------------------------------ #
    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self.tei.embed(texts)
        for vec in vectors:
            if len(vec) != self.embedding_dim:
                raise ValueError(
                    f"TEI 返回向量维度 {len(vec)}，与 AS_EMBEDDING_DIM="
                    f"{self.embedding_dim} 不一致"
                )
        return vectors

    def upsert(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self.ensure()
        self._client().upsert(collection_name=self.collection, data=rows)

    def delete(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        if not self._client().has_collection(self.collection):
            return
        ids = ",".join(json.dumps(mid) for mid in memory_ids)
        self._client().delete(
            collection_name=self.collection, filter=f"memory_id in [{ids}]"
        )

    def query(
        self, vector: list[float], user_id: str, *, limit: int = 20
    ) -> list[VectorHit]:
        self.ensure()
        self._load()
        results = self._client().search(
            collection_name=self.collection,
            data=[vector],
            anns_field=DENSE_FIELD,
            search_params={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=max(1, limit),
            filter=f"user_id == {json.dumps(user_id)}",
            output_fields=["memory_id", "type"],
        )
        rows = results[0] if results else []
        hits: list[VectorHit] = []
        for row in rows:
            entity = row.get("entity") or {}
            memory_id = str(entity.get("memory_id") or row.get("id") or "")
            if not memory_id:
                continue
            hits.append(
                VectorHit(
                    memory_id=memory_id,
                    similarity=float(row.get("distance", 0.0)),
                )
            )
        return hits

    def search_text(
        self, query: str, user_id: str, *, limit: int = 20
    ) -> list[VectorHit]:
        vector = self.embed([query])[0]
        return self.query(vector, user_id, limit=limit)


__all__ = ["MemoryVectorIndex", "VectorHit"]
