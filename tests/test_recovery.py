"""启动恢复：多 worker 下不能误杀仍在运行（Redis 持锁）的会话/任务。"""

from __future__ import annotations

import fakeredis
import pytest

from asagent.api import app as app_module
from asagent.api.db import WebDB
from asagent.runtime.checkpoint import CheckpointStore
from asagent.runtime.state import SessionStatus
from asagent.storage.redis_state import RedisState


@pytest.fixture()
def redis_state() -> RedisState:
    return RedisState("", client=fakeredis.FakeRedis(decode_responses=True))


def test_recover_zombies_skips_locked_session(tmp_path, redis_state) -> None:
    store = CheckpointStore(tmp_path / "state.db")
    try:
        state = store.create_session("sql")
        state.status = SessionStatus.RUNNING
        store.save_state(state)

        token = redis_state.acquire(f"job:{state.session_id}", ttl_s=60)
        assert token is not None
        assert app_module._recover_zombies(store, redis_state) == 0
        assert store.load_state(state.session_id).status == SessionStatus.RUNNING

        # 锁释放后（进程已退出），下一次恢复才标记失败
        redis_state.release(f"job:{state.session_id}", token)
        assert app_module._recover_zombies(store, redis_state) == 1
        assert store.load_state(state.session_id).status == SessionStatus.FAILED
    finally:
        store.close()


def test_recover_zombies_without_redis(tmp_path) -> None:
    store = CheckpointStore(tmp_path / "state.db")
    try:
        state = store.create_session("sql")
        state.status = SessionStatus.RUNNING
        store.save_state(state)
        assert app_module._recover_zombies(store) == 1
        assert store.load_state(state.session_id).status == SessionStatus.FAILED
    finally:
        store.close()


def test_recover_jobs_skip_when_locked(tmp_path, redis_state) -> None:
    web = WebDB(tmp_path / "state.db")
    web.kb_job_start(kind="reindex", doc_ids=None, actor="root")
    eval_id = web.eval_job_start(
        actor="root", profile="sql", models=None, repeat=1, limit_cases=None
    )

    kb_token = redis_state.acquire("kb:index", ttl_s=900)
    eval_token = redis_state.acquire("eval", ttl_s=3600)
    assert app_module._recover_kb_jobs(web, redis_state) == 0
    assert app_module._recover_eval_jobs(web, redis_state) == 0

    assert kb_token is not None and eval_token is not None
    redis_state.release("kb:index", kb_token)
    redis_state.release("eval", eval_token)
    assert app_module._recover_kb_jobs(web, redis_state) == 1
    assert app_module._recover_eval_jobs(web, redis_state) == 1
    assert web.kb_jobs()[0]["status"] == "failed"
    assert web.eval_job(eval_id)["status"] == "failed"
    assert web.kb_job_running() is None
    assert web.eval_job_running() is None
