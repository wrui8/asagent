from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from asagent.tools.rag.factory import make_index
from asagent.tools.rag.milvus_backend import MilvusIndex
from asagent.tools.rag.tei import TEIClient, TEIError


# ---------------------------------------------------------------------- #
# TEI 客户端
def _tei(handler, *, batch_size: int = 2) -> TEIClient:
    return TEIClient(
        "http://tei", batch_size=batch_size, transport=httpx.MockTransport(handler)
    )


def test_tei_embed_batches_and_sets_flags():
    seen: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/embed"
        body = json.loads(request.content)
        seen.append(body["inputs"])
        assert body["truncate"] is True
        assert body["normalize"] is True
        return httpx.Response(200, json=[[0.1, 0.2] for _ in body["inputs"]])

    vectors = _tei(handler).embed(["甲", "乙", "丙"])

    assert seen == [["甲", "乙"], ["丙"]]
    assert vectors == [[0.1, 0.2]] * 3


def test_tei_blank_text_uses_space():
    captured: list[list[str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content)["inputs"])
        return httpx.Response(200, json=[[0.0]])

    _tei(handler).embed(["   "])
    assert captured == [[" "]]


def test_tei_raises_on_error_status():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="loading")

    with pytest.raises(TEIError, match="503"):
        _tei(handler).embed(["甲"])


def test_tei_raises_on_count_mismatch():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[[0.1], [0.2], [0.3]])

    with pytest.raises(TEIError, match="条数不符"):
        _tei(handler).embed(["甲", "乙"])


def test_tei_accepts_flat_vector_for_single_input():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[0.1, 0.2])

    assert _tei(handler).embed(["甲"]) == [[0.1, 0.2]]


def test_tei_health():
    up = _tei(lambda _r: httpx.Response(200))
    down = _tei(lambda _r: httpx.Response(503))
    assert up.health() is True
    assert down.health() is False

    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    assert _tei(boom).health() is False


# ---------------------------------------------------------------------- #
# 索引工厂
class _Settings:
    def __init__(self, backend: str, milvus: str = "", tei: str = "", dim: int = 1024):
        self.as_rag_backend = backend
        self.as_milvus_uri = milvus
        self.as_tei_url = tei
        self.as_embedding_dim = dim


def _factory_kwargs(tmp_path: Path) -> dict:
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    return {
        "docs_dir": docs,
        "index_db": tmp_path / "rag.sqlite3",
        "embedding_model": "fake-model",
    }


def test_factory_defaults_to_sqlite(tmp_path):
    index = make_index(settings=None, **_factory_kwargs(tmp_path))
    assert type(index).__name__ == "RagIndex"

    index = make_index(settings=_Settings("sqlite"), **_factory_kwargs(tmp_path))
    assert type(index).__name__ == "RagIndex"


def test_factory_requires_milvus_endpoints(tmp_path):
    with pytest.raises(ValueError, match="AS_MILVUS_URI"):
        make_index(settings=_Settings("milvus"), **_factory_kwargs(tmp_path))


def test_factory_rejects_unknown_backend(tmp_path):
    with pytest.raises(ValueError, match="未知的 AS_RAG_BACKEND"):
        make_index(settings=_Settings("faiss"), **_factory_kwargs(tmp_path))


def test_factory_builds_milvus_index(tmp_path):
    index = make_index(
        settings=_Settings("milvus", "http://milvus:19530", "http://tei:80"),
        **_factory_kwargs(tmp_path),
    )
    assert isinstance(index, MilvusIndex)
    assert index.embedding_dim == 1024


# ---------------------------------------------------------------------- #
# Milvus 后端（用内存假客户端替代真实 Milvus）
class _IndexParams:
    def add_index(self, **_kwargs) -> None:
        return None


class _Iterator:
    def __init__(self, rows: list[dict]):
        self._rows = list(rows)
        self._sent = False

    def next(self) -> list[dict]:
        if self._sent:
            return []
        self._sent = True
        return self._rows

    def close(self) -> None:
        return None


class FakeMilvus:
    def __init__(self):
        self.collections: dict[str, dict] = {}
        self.aliases: dict[str, str] = {}
        self.dropped: list[str] = []

    def has_collection(self, name):
        return name in self.collections or name in self.aliases

    def describe_alias(self, alias):
        if alias not in self.aliases:
            raise RuntimeError("alias not found")
        return {"alias": alias, "collection_name": self.aliases[alias]}

    def create_alias(self, collection_name, alias):
        self.aliases[alias] = collection_name

    def alter_alias(self, collection_name, alias):
        self.aliases[alias] = collection_name

    def prepare_index_params(self):
        return _IndexParams()

    def create_collection(self, collection_name, schema=None, index_params=None):
        dim = 1024
        for field in getattr(schema, "fields", []):
            if field.name == "dense":
                dim = int(field.params.get("dim", 1024))
        assert index_params is not None
        self.collections[collection_name] = {"dim": dim, "rows": []}

    def describe_collection(self, collection_name):
        return {
            "fields": [
                {"name": "dense", "params": {"dim": self.collections[collection_name]["dim"]}}
            ]
        }

    def load_collection(self, collection_name):
        return None

    def insert(self, collection_name, data):
        self.collections[collection_name]["rows"].extend(data)
        return {"insert_count": len(data)}

    def delete(self, collection_name, filter):  # noqa: A002 - 与 pymilvus 参数名一致
        doc_ids = set(json.loads(filter.split("in", 1)[1].strip()))
        rows = self.collections[collection_name]["rows"]
        before = len(rows)
        self.collections[collection_name]["rows"] = [
            row for row in rows if row["doc_id"] not in doc_ids
        ]
        return {"delete_count": before - len(self.collections[collection_name]["rows"])}

    def flush(self, collection_name):
        return None

    def get_collection_stats(self, collection_name):
        return {"row_count": len(self.collections[collection_name]["rows"])}

    def search(self, collection_name, data, anns_field=None, search_params=None, limit=10, output_fields=None):
        rows = self.collections[collection_name]["rows"][:limit]
        return [
            [
                {
                    "id": row["chunk_id"],
                    "distance": 0.9,
                    "entity": {name: row[name] for name in output_fields or []},
                }
                for row in rows
            ]
        ]

    def query_iterator(self, collection_name, batch_size=1000, filter="", output_fields=None):  # noqa: A002
        return _Iterator(
            [
                {name: row[name] for name in output_fields or []}
                for row in self.collections[collection_name]["rows"]
            ]
        )

    def drop_collection(self, collection_name):
        self.collections.pop(collection_name, None)
        self.dropped.append(collection_name)
        for alias, target in list(self.aliases.items()):
            if target == collection_name:
                self.aliases.pop(alias)


class FakeTEI:
    def __init__(self, dim: int = 1024):
        self.dim = dim
        self.calls: list[int] = []

    def embed(self, texts):
        self.calls.append(len(texts))
        return [[0.01 * (i % 7) for i in range(self.dim)] for _ in texts]


@pytest.fixture
def milvus_index(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text("# 甲\n\n## 小节\n\n退款 7 天到账。", encoding="utf-8")
    (docs / "b.md").write_text("# 乙\n\n## 小节\n\n住宿 600 元。", encoding="utf-8")
    index = MilvusIndex(
        milvus_uri="http://milvus:19530",
        tei_url="http://tei:80",
        docs_dir=docs,
        index_db=tmp_path / "rag.sqlite3",
        embedding_dim=1024,
    )
    fake = FakeMilvus()
    index._milvus = fake
    index.tei = FakeTEI()
    return index, fake, docs


def test_milvus_build_creates_collection_and_alias(milvus_index):
    index, fake, _docs = milvus_index

    written = index.build()

    assert written == 2
    assert index.alias in fake.aliases
    target = fake.aliases[index.alias]
    assert len(fake.collections[target]["rows"]) == 2
    status = index.status()
    assert status["backend"] == "milvus"
    assert status["chunks"] == 2
    assert status["index_docs"] == 2
    assert status["stale"] is False
    assert index.doc_hashes().keys() == {"a", "b"}


def test_milvus_build_is_incremental(milvus_index):
    index, fake, docs = milvus_index
    index.build()
    target = fake.aliases[index.alias]

    assert index.build() == 0

    (docs / "a.md").write_text("# 甲\n\n## 小节\n\n退款 15 天到账。", encoding="utf-8")
    assert index.build() == 1
    assert len(fake.collections[target]["rows"]) == 2
    assert index.status()["stale"] is False


def test_milvus_build_removes_deleted_docs(milvus_index):
    index, fake, docs = milvus_index
    index.build()
    target = fake.aliases[index.alias]

    (docs / "b.md").unlink()
    index.build()

    rows = fake.collections[target]["rows"]
    assert {row["doc_id"] for row in rows} == {"a"}
    assert index.doc_hashes().keys() == {"a"}


def test_milvus_search_and_list_docs(milvus_index):
    index, _fake, _docs = milvus_index
    index.build()

    hits = index.search("退款多久到账", top_k=1)
    assert len(hits) == 1
    assert hits[0].doc_id in {"a", "b"}
    assert hits[0].citation.endswith("#0")

    docs = {d.doc_id: d for d in index.list_docs()}
    assert docs["a"].chunks == 1
    assert docs["a"].title == "甲"


def test_milvus_remove_and_rebuild_switch_alias(milvus_index):
    index, fake, _docs = milvus_index
    index.build()
    old = fake.aliases[index.alias]

    assert index.remove(["a"]) == 1
    assert len(fake.collections[old]["rows"]) == 1

    written = index.rebuild()
    assert written == 2
    new = fake.aliases[index.alias]
    assert new != old
    assert old in fake.dropped
    assert len(fake.collections[new]["rows"]) == 2


def test_milvus_status_stale_without_alias(milvus_index):
    index, _fake, _docs = milvus_index

    status = index.status()
    assert status["stale"] is True
    assert status["chunks"] == 0
    assert index.search("任意") == []


def test_milvus_build_rejects_dimension_mismatch(milvus_index):
    index, fake, docs = milvus_index
    index.build()
    target = fake.aliases[index.alias]
    fake.collections[target]["dim"] = 768
    # 制造变化，确保 build 进入写入路径并触发维度校验
    (docs / "a.md").write_text("# 甲\n\n## 小节\n\n退款 30 天到账。", encoding="utf-8")

    with pytest.raises(ValueError, match="维度"):
        index.build()
