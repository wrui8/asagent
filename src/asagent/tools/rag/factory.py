from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .index import DocInfo, RagIndex, SearchHit

if TYPE_CHECKING:
    from ...config import Settings


class IndexLike(Protocol):
    """SQLite 与 Milvus 两种索引实现共有的接口。"""

    def build(self, doc_ids: list[str] | None = None) -> int: ...

    def remove(self, doc_ids: list[str]) -> int: ...

    def ensure(self) -> bool: ...

    def status(self) -> dict: ...

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]: ...

    def doc_hashes(self) -> dict[str, str]: ...

    def list_docs(self) -> list[DocInfo]: ...

    def read_doc(self, doc_id: str, max_chars: int = 8000) -> str | None: ...


def rag_backend(settings: Settings | None) -> str:
    """读取索引后端开关；settings 缺失时回退到 v1 的 SQLite。"""
    if settings is None:
        return "sqlite"
    return str(getattr(settings, "as_rag_backend", "sqlite") or "sqlite")


def make_index(
    *,
    settings: Settings | None,
    docs_dir: Path,
    index_db: Path,
    embedding_model: str,
    max_chunk_chars: int = 500,
) -> IndexLike:
    """按 AS_RAG_BACKEND 选择索引实现（默认 sqlite = v1 行为）。"""
    backend = rag_backend(settings)
    if backend == "milvus":
        from .milvus_backend import MilvusIndex

        assert settings is not None  # rag_backend 只在 settings 存在时返回 milvus
        milvus_uri = settings.as_milvus_uri.strip()
        tei_url = settings.as_tei_url.strip()
        if not milvus_uri or not tei_url:
            raise ValueError(
                "AS_RAG_BACKEND=milvus 需要同时配置 AS_MILVUS_URI 与 AS_TEI_URL"
            )
        return MilvusIndex(
            milvus_uri=milvus_uri,
            tei_url=tei_url,
            docs_dir=docs_dir,
            index_db=index_db,
            embedding_dim=settings.as_embedding_dim,
            max_chunk_chars=max_chunk_chars,
        )
    if backend != "sqlite":
        raise ValueError(f"未知的 AS_RAG_BACKEND: {backend}（可选 sqlite / milvus）")
    return RagIndex(
        index_db=index_db,
        docs_dir=docs_dir,
        embedding_model=embedding_model,
        max_chunk_chars=max_chunk_chars,
    )


__all__ = ["IndexLike", "make_index", "rag_backend"]
