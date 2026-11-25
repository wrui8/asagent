"""阶段 4 记忆子系统：抽取解析、触发策略、召回、写入、夜间巩固、发布与 worker。

全部使用假实现，不依赖 PG / Milvus / RabbitMQ；真实后端由
`tests/test_memory_pg.py`（需要 AS_TEST_PG_DSN）单独覆盖。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import fakeredis
import pytest
from test_graph_engine import make_engine
from test_runtime import final

from asagent.memory.extractor import (
    MemoryExtractError,
    MemoryExtractor,
    parse_candidates,
)
from asagent.memory.nightly import run_nightly
from asagent.memory.policy import PublishPolicy, user_turns
from asagent.memory.publish import MemoryPublisher
from asagent.memory.recall import (
    FOOTER,
    HEADER,
    MemoryRecall,
    recency_decay,
    render_section,
    score_item,
)
from asagent.memory.service import MemoryService
from asagent.memory.types import MemoryCandidate, MemoryItem
from asagent.memory.vector import VectorHit
from asagent.memory.worker import MemoryWorker
from asagent.memory.writer import MemoryWriter
from asagent.models.types import Message, ModelResponse, Usage
from asagent.runtime.graph import GraphEngine
from asagent.runtime.loop import AgentRuntime
from asagent.runtime.state import SessionState, SessionStatus
from asagent.storage.redis_state import RedisState


# ---------------------------------------------------------------------- #
# 假实现
# ---------------------------------------------------------------------- #
def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def item(
    memory_id: str,
    *,
    user_id: str = "u1",
    type: str = "preference",
    content: str = "用户偏好简洁回答",
    importance: float = 0.5,
    status: str = "active",
    updated_at: str | None = None,
    last_recalled_at: str | None = None,
) -> MemoryItem:
    ts = updated_at or now_iso()
    return MemoryItem(
        id=memory_id,
        user_id=user_id,
        type=type,
        content=content,
        importance=importance,
        confidence=0.8,
        status=status,
        source_session="s0",
        created_at=ts,
        updated_at=ts,
        last_recalled_at=last_recalled_at,
    )


class FakeVector:
    def __init__(self, vectors: dict[str, list[float]] | None = None, hits=None):
        self.vectors = vectors or {}
        self.hits = hits or []
        self.queries: list[tuple[float, str, int]] = []
        self.text_searches: list[tuple[str, str, int]] = []
        self.upserts: list[dict] = []
        self.deleted: list[str] = []

    def embed(self, texts):
        return [self.vectors.get(t, [1.0, 0.0]) for t in texts]

    def query(self, vector, user_id, limit=20):
        self.queries.append((vector, user_id, limit))
        return list(self.hits)

    def search_text(self, query, user_id, limit=20):
        self.text_searches.append((query, user_id, limit))
        return list(self.hits)

    def upsert(self, rows):
        self.upserts.extend(rows)

    def delete(self, ids):
        self.deleted.extend(ids)


class FakeStore:
    def __init__(self, items: dict[str, MemoryItem] | None = None):
        self.items = dict(items or {})
        self.created: list[dict] = []
        self.merged: list[tuple[str, dict]] = []
        self.archived: list[str] = []
        self.touched: list[list[str]] = []
        self.decayed = 0
        self.weak_archived = 0

    def create(self, **kwargs):
        new = item(
            f"m_{len(self.created) + 1}",
            user_id=kwargs["user_id"],
            type=kwargs["type"],
            content=kwargs["content"],
            importance=kwargs.get("importance") or kwargs.get("confidence", 0.5),
        )
        self.items[new.id] = new
        self.created.append(kwargs)
        return new

    def merge(self, memory_id, **kwargs):
        target = self.items.get(memory_id)
        if target is None:
            return None
        target.content = kwargs.get("content", target.content)
        target.importance = kwargs.get("importance", target.importance)
        self.merged.append((memory_id, kwargs))
        return target

    def get(self, memory_id):
        return self.items.get(memory_id)

    def get_many(self, memory_ids):
        return [self.items[mid] for mid in memory_ids if mid in self.items]

    def list_active(self, user_id, limit=50):
        return [i for i in self.items.values() if i.user_id == user_id][:limit]

    def list_active_all(self, limit=5000):
        return list(self.items.values())[:limit]

    def find_by_session(self, session_id):
        return [i for i in self.items.values() if i.source_session == session_id]

    def touch_recall(self, memory_ids):
        self.touched.append(list(memory_ids))

    def archive(self, memory_id):
        target = self.items.get(memory_id)
        if target is None:
            return False
        target.status = "archived"
        self.archived.append(memory_id)
        return True

    def decay_stale(self, *, days=30, factor=0.98):
        return self.decayed

    def archive_weak(self, *, threshold=0.1, days=90):
        return self.weak_archived

    def stats(self):
        return {"total": len(self.items), "by_status": {"active": len(self.items)}}


class FakeRecall:
    def __init__(self, section: str | None = None, *, error: Exception | None = None):
        self.section_text = section
        self.error = error
        self.calls: list[tuple[str, str]] = []
        self.invalidated: list[str] = []

    async def section(self, user_id, query):
        self.calls.append((user_id, query))
        if self.error is not None:
            raise self.error
        return self.section_text

    def invalidate(self, user_id):
        self.invalidated.append(user_id)


@pytest.fixture()
def redis_state() -> RedisState:
    return RedisState("", client=fakeredis.FakeRedis(decode_responses=True))


# ---------------------------------------------------------------------- #
# 抽取解析
# ---------------------------------------------------------------------- #
def test_parse_candidates_filters_and_orders() -> None:
    raw = """```json
    [
      {"type": "preference", "content": "用户喜欢表格形式的答案", "confidence": 0.9},
      {"type": "fact", "content": "团队主要使用 PostgreSQL 16", "tags": ["技术栈"], "confidence": 0.8},
      {"type": "preference", "content": "用户喜欢表格形式的答案", "confidence": 0.95},
      {"type": "unknown", "content": "不属于任何记忆类型但足够长", "confidence": 0.9},
      {"type": "fact", "content": "短", "confidence": 0.9},
      {"type": "decision", "content": "确定用 Milvus 做向量检索", "confidence": 0.3}
    ]
    ```"""
    result = parse_candidates(raw)
    assert [c.type for c in result] == ["preference", "fact"]
    assert result[0].confidence == 0.9
    assert result[1].tags == ["技术栈"]


def test_parse_candidates_accepts_dict_wrapper_and_limits() -> None:
    payload = {
        "memories": [
            {"type": "fact", "content": f"事实条目编号 {i} 的内容", "confidence": 0.9}
            for i in range(8)
        ]
    }
    result = parse_candidates(json.dumps(payload, ensure_ascii=False), max_items=3)
    assert len(result) == 3


def test_parse_candidates_rejects_garbage() -> None:
    with pytest.raises(MemoryExtractError):
        parse_candidates("")
    with pytest.raises(MemoryExtractError):
        parse_candidates("42")
    # dict 形态但没有 memories/items 时按空结果处理
    assert parse_candidates('{"nothing": 1}') == []


class FakeModelClient:
    def __init__(self, text: str = "[]", *, error: Exception | None = None):
        self.text = text
        self.error = error
        self.calls: list[list[Message]] = []

    async def complete(self, messages, *, max_output_tokens=None, tools=None, **kwargs):
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        return ModelResponse(
            text=self.text, finish_reason="stop", usage=Usage(input_tokens=1, output_tokens=1)
        )

    async def aclose(self) -> None:
        return None


async def test_extractor_builds_prompt_and_wraps_errors() -> None:
    client = FakeModelClient('[{"type":"fact","content":"团队使用 PG16","confidence":0.9}]')
    extractor = MemoryExtractor(client)
    candidates = await extractor.extract(
        user_inputs=["问题一", "问题二"], assistant_output="最终答复"
    )
    assert candidates[0].content == "团队使用 PG16"
    prompt = client.calls[0][1].content
    assert "【用户输入】" in prompt and "【助手最终答复】" in prompt

    failing = MemoryExtractor(FakeModelClient(error=RuntimeError("boom")))
    with pytest.raises(MemoryExtractError):
        await failing.extract(user_inputs=["只有一条"], assistant_output="x")


# ---------------------------------------------------------------------- #
# 触发策略
# ---------------------------------------------------------------------- #
def make_state(*, turns: int = 3, session_id: str = "s1", owner: str | None = "u1"):
    messages = [
        Message(role="user", content=f"第 {i} 轮提问") for i in range(1, turns + 1)
    ] + [Message(role="assistant", content="答复")]
    return SessionState(
        session_id=session_id,
        profile="sql",
        owner_id=owner,
        messages=messages,
        status=SessionStatus.DONE,
    )


def test_user_turns() -> None:
    assert user_turns(make_state(turns=2).messages) == 2


def test_policy_requires_turns_and_dedupes(redis_state: RedisState) -> None:
    policy = PublishPolicy(redis_state)
    assert policy.check(make_state(turns=2), "u1") == (False, "too_short")
    assert policy.check(make_state(owner=None), "") == (False, "no_owner")
    assert policy.check(make_state(), "u1") == (True, "")
    # 同一会话 30 秒内不重复抽取
    assert policy.check(make_state(), "u1") == (False, "debounced")


def test_policy_daily_quota(redis_state: RedisState) -> None:
    policy = PublishPolicy(redis_state, daily_limit=1)
    assert policy.check(make_state(session_id="s1"), "u1") == (True, "")
    assert policy.check(make_state(session_id="s2"), "u1") == (False, "quota")


# ---------------------------------------------------------------------- #
# 召回
# ---------------------------------------------------------------------- #
def test_recency_decay_and_score() -> None:
    now = datetime(2026, 1, 1)
    old = (now - timedelta(days=90)).isoformat(timespec="seconds")
    fresh = now.isoformat(timespec="seconds")
    assert recency_decay(fresh, now=now) == pytest.approx(1.0)
    assert recency_decay(old, now=now) == pytest.approx(0.5, abs=1e-6)
    assert recency_decay(None, now=now) == 1.0
    weak = item("m1", importance=0.0)
    strong = item("m2", importance=1.0)
    assert score_item(strong, 1.0, now=datetime.now()) > score_item(weak, 0.5, now=datetime.now())


def test_render_section_truncates_and_marks() -> None:
    text = render_section([item("m1", content="x" * 400)])
    assert text.startswith(HEADER) and text.endswith(FOOTER)
    assert "[preference]" in text
    assert "…" in text


async def test_recall_ranks_filters_and_caches(redis_state: RedisState) -> None:
    store = FakeStore(
        {
            "m1": item("m1", content="第一条", importance=0.2),
            "m2": item("m2", content="第二条", importance=0.9),
            "m3": item("m3", content="已归档", status="archived"),
            "m4": item("m4", content="别人的记忆", user_id="u2"),
        }
    )
    vector = FakeVector(
        hits=[
            VectorHit("m1", 0.9),
            VectorHit("m2", 0.9),
            VectorHit("m3", 0.95),
            VectorHit("m4", 0.99),
        ]
    )
    recall = MemoryRecall(store=store, vector=vector, redis=redis_state, top_k=2)

    section = await recall.section("u1", "用户偏好什么风格")
    assert section is not None
    lines = section.splitlines()
    assert "第二条" in lines[1]  # 重要度更高排前面
    assert "第一条" in lines[2]
    assert "已归档" not in section and "别人的记忆" not in section
    assert store.touched == [["m2", "m1"]]

    # 第二次命中缓存，不再检索
    await recall.section("u1", "换了问法")
    assert len(vector.text_searches) == 1
    # 失效后重新检索
    recall.invalidate("u1")
    await recall.section("u1", "再问一次")
    assert len(vector.text_searches) == 2


async def test_recall_returns_none_without_hits() -> None:
    recall = MemoryRecall(store=FakeStore(), vector=FakeVector())
    assert await recall.section("u1", "任意问题") is None
    assert await recall.section(None, "任意问题") is None
    assert await recall.section("u1", "   ") is None


# ---------------------------------------------------------------------- #
# 写入
# ---------------------------------------------------------------------- #
async def test_writer_creates_or_merges() -> None:
    existing = item("m1", content="用户偏好简短回答", type="preference")
    store = FakeStore({"m1": existing})
    vector = FakeVector(hits=[VectorHit("m1", 0.95)])
    writer = MemoryWriter(store=store, vector=vector)

    stats = await writer.save(
        "u1",
        [MemoryCandidate(type="preference", content="用户偏好简短回答并配表格", confidence=0.9)],
        source_session="s9",
    )
    assert stats == {"created": 0, "merged": 1}
    assert store.merged[0][0] == "m1"
    assert vector.upserts[0]["memory_id"] == "m1"

    # 不同 type 不合并
    low = MemoryWriter(store=store, vector=FakeVector(hits=[VectorHit("m1", 0.99)]))
    stats = await low.save(
        "u1", [MemoryCandidate(type="fact", content="团队使用 PostgreSQL 16", confidence=0.9)]
    )
    assert stats == {"created": 1, "merged": 0}
    assert store.created[0]["source_session"] is None


# ---------------------------------------------------------------------- #
# 夜间巩固
# ---------------------------------------------------------------------- #
async def test_nightly_merges_decays_and_archives() -> None:
    store = FakeStore(
        {
            "a": item("a", content="使用 Milvus 做向量库", importance=0.6),
            "b": item("b", content="向量库确定为 Milvus", importance=0.5),
            "c": item("c", content="用户喜欢中文回答", importance=0.4),
        }
    )
    store.decayed = 2
    store.weak_archived = 1
    vectors = {
        "使用 Milvus 做向量库": [1.0, 0.0],
        "向量库确定为 Milvus": [1.0, 0.0],
        "用户喜欢中文回答": [0.0, 1.0],
    }
    vector = FakeVector(vectors=vectors)
    recall = FakeRecall()

    stats = await run_nightly(store=store, vector=vector, recall=recall)
    assert stats["decayed"] == 2 and stats["archived"] == 1
    assert stats["merged"] == 1
    assert store.archived == ["b"]
    assert vector.deleted == ["b"]
    assert recall.invalidated == ["u1"]


# ---------------------------------------------------------------------- #
# 发布与 worker
# ---------------------------------------------------------------------- #
async def test_publisher_respects_status_and_publishes(redis_state: RedisState) -> None:
    published: list[dict] = []

    async def fake_publish(payload):
        published.append(payload)

    publisher = MemoryPublisher(
        policy=PublishPolicy(redis_state), rabbitmq_url="", publish=fake_publish
    )
    running = make_state()
    running.status = SessionStatus.RUNNING
    assert await publisher.maybe_publish(running, "u1") == (False, "not_done")
    assert published == []

    assert await publisher.maybe_publish(make_state(), "u1") == (True, "")
    assert published[0]["session_id"] == "s1"
    # 去抖：同一会话再次发布被拒
    assert await publisher.maybe_publish(make_state(), "u1") == (False, "debounced")


async def test_publisher_swallows_publish_failure(redis_state: RedisState) -> None:
    async def boom(payload):
        raise RuntimeError("rabbit down")

    publisher = MemoryPublisher(
        policy=PublishPolicy(redis_state), rabbitmq_url="", publish=boom
    )
    assert await publisher.maybe_publish(make_state(), "u1") == (False, "publish_failed")


class FakeService:
    rabbitmq_url = ""

    def __init__(self, failures: int = 0):
        self.failures = failures
        self.calls = 0

    async def extract_session(self, session_id):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("抽取失败")
        return {"created": 1, "merged": 0}


async def test_worker_retries_then_succeeds() -> None:
    service = FakeService(failures=2)
    worker = MemoryWorker(service, retry_base_s=0)
    result = await worker.handle_payload(b'{"session_id": "s1"}')
    assert result["status"] == "ok"
    assert result["attempt"] == 3
    assert service.calls == 3


async def test_worker_dead_letters_after_retries() -> None:
    letters: list[tuple[bytes, str]] = []

    async def dead_letter(raw: bytes, reason: str) -> None:
        letters.append((raw, reason))

    service = FakeService(failures=10)
    worker = MemoryWorker(service, retry_attempts=2, retry_base_s=0, dead_letter=dead_letter)
    result = await worker.handle_payload(b'{"session_id": "s2"}')
    assert result["status"] == "dlq"
    assert letters and letters[0][0] == b'{"session_id": "s2"}'
    assert "抽取失败" in letters[0][1]


async def test_worker_dead_letters_invalid_payload() -> None:
    letters: list[tuple[bytes, str]] = []

    async def dead_letter(raw: bytes, reason: str) -> None:
        letters.append((raw, reason))

    worker = MemoryWorker(FakeService(), dead_letter=dead_letter)
    result = await worker.handle_payload(b"not json")
    assert result["status"] == "dlq"
    assert letters and "invalid payload" in letters[0][1]


# ---------------------------------------------------------------------- #
# 服务装配
# ---------------------------------------------------------------------- #
class FakeKernel:
    def __init__(self, state: SessionState | None, events: list | None = None):
        self.state = state
        self.event_list = events or []

    def load_state(self, session_id):
        if self.state is None or self.state.session_id != session_id:
            raise KeyError(session_id)
        return self.state

    def events(self, session_id):
        return self.event_list


class SimpleEvent:
    def __init__(self, type: str, payload: dict):
        self.type = type
        self.payload = payload


class FakeExtractor:
    def __init__(self, candidates=None):
        self.candidates = candidates or []
        self.calls = 0

    async def extract(self, *, user_inputs, assistant_output):
        self.calls += 1
        return list(self.candidates)


class FakeWriter:
    def __init__(self):
        self.saved: list[tuple] = []

    async def save(self, user_id, candidates, *, source_session=None):
        self.saved.append((user_id, candidates, source_session))
        return {"created": len(candidates), "merged": 0}


def make_service(kernel, store=None, extractor=None, writer=None, recall=None):
    return MemoryService(
        config=None,
        kernel=kernel,
        store=store or FakeStore(),
        vector=None,
        extractor=extractor or FakeExtractor(),
        writer=writer or FakeWriter(),
        recall=recall or FakeRecall(),
        rabbitmq_url="",
    )


def three_turn_events() -> list[SimpleEvent]:
    events = [
        SimpleEvent("user_input", {"text": f"第 {i} 轮"}) for i in range(1, 4)
    ]
    events.append(SimpleEvent("final", {"text": "最终答复"}))
    return events


async def test_service_extracts_and_invalidates() -> None:
    state = make_state()
    extractor = FakeExtractor(
        [MemoryCandidate(type="fact", content="团队使用 PostgreSQL 16", confidence=0.9)]
    )
    writer = FakeWriter()
    recall = FakeRecall()
    service = make_service(
        FakeKernel(state, three_turn_events()),
        extractor=extractor,
        writer=writer,
        recall=recall,
    )

    stats = await service.extract_session("s1")
    assert stats == {"created": 1, "merged": 0}
    assert writer.saved[0][0] == "u1" and writer.saved[0][2] == "s1"
    assert recall.invalidated == ["u1"]


async def test_service_skips_short_missing_and_extracted() -> None:
    short = make_service(
        FakeKernel(make_state(turns=3), three_turn_events()[1:])  # 只有 2 条用户输入
    )
    assert await short.extract_session("s1") == {"skipped": "too_short"}

    missing = make_service(FakeKernel(None))
    assert await missing.extract_session("nope") == {"skipped": "missing"}

    store = FakeStore()
    existing = item("m1")
    existing.source_session = "s1"
    store.items["m1"] = existing
    already = make_service(FakeKernel(make_state(), three_turn_events()), store=store)
    assert await already.extract_session("s1") == {"skipped": "already_extracted"}

    no_owner = make_service(FakeKernel(make_state(owner=None), three_turn_events()))
    assert await no_owner.extract_session("s1") == {"skipped": "no_owner"}


def test_build_memory_service_disabled_and_fail_closed(config) -> None:
    from asagent.memory.service import build_memory_service
    from asagent.storage import build_storage

    storage = build_storage(config)
    try:
        config.settings.as_memory_enabled = False
        assert build_memory_service(config, storage, None) is None
        config.settings.as_memory_enabled = True
        with pytest.raises(ValueError, match="AS_STORAGE_BACKEND=postgres"):
            build_memory_service(config, storage, None)
    finally:
        storage.close()


# ---------------------------------------------------------------------- #
# 运行时注入（legacy 与 graph 两个引擎）
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize("engine_cls", [AgentRuntime, GraphEngine])
async def test_runtime_injects_memory_once(config, tmp_path, engine_cls) -> None:
    recall = FakeRecall("<long_term_memory>用户偏好中文</long_term_memory>")
    engine, client = make_engine(
        engine_cls,
        config,
        tmp_path,
        [final("好的")],
        name=f"mem-{engine_cls.__name__}",
        memory=recall,
    )
    await engine.run("请回答问题", owner_id="u1")

    assert recall.calls == [("u1", "请回答问题")]
    sent = client.calls[0]
    assert sent[0].role == "system"
    assert sent[1] == Message(
        role="system", content="<long_term_memory>用户偏好中文</long_term_memory>"
    )


@pytest.mark.parametrize("engine_cls", [AgentRuntime, GraphEngine])
async def test_runtime_memory_failure_is_fail_open(config, tmp_path, engine_cls) -> None:
    recall = FakeRecall(error=RuntimeError("milvus down"))
    engine, client = make_engine(
        engine_cls,
        config,
        tmp_path,
        [final("好的")],
        name=f"memfail-{engine_cls.__name__}",
        memory=recall,
    )
    state = await engine.run("请回答问题", owner_id="u1")
    assert state.status == SessionStatus.DONE
    # 失败时只有系统提示，没有记忆段
    assert [m.role for m in client.calls[0]] == ["system", "user"]
