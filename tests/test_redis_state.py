"""Redis 共享状态：锁、心跳续期、占用计数、跨 worker 限流（fakeredis）。"""

from __future__ import annotations

import fakeredis
import pytest

from asagent.storage.redis_state import RedisState, RedisWindowLimiter


@pytest.fixture()
def state() -> RedisState:
    client = fakeredis.FakeRedis(decode_responses=True)
    return RedisState("", client=client)


def test_acquire_is_exclusive_and_release_needs_token(state: RedisState) -> None:
    token = state.acquire("job:s1", ttl_s=60)
    assert token is not None
    assert state.exists("job:s1")
    assert state.acquire("job:s1", ttl_s=60) is None

    # 错误令牌不能释放别人的锁
    state.release("job:s1", "not-holder")
    assert state.exists("job:s1")

    state.release("job:s1", token)
    assert not state.exists("job:s1")
    # 释放后可重新获取
    assert state.acquire("job:s1", ttl_s=60) is not None


def test_renew_only_for_holder(state: RedisState) -> None:
    token = state.acquire("kb:index", ttl_s=30)
    assert token is not None
    assert state.renew("kb:index", token, ttl_s=30) is True
    assert state.renew("kb:index", "other", ttl_s=30) is False


def test_busy_counter_tracks_sessions(state: RedisState) -> None:
    assert state.busy_count("u1") == 0
    state.add_busy("u1", "s1")
    state.add_busy("u1", "s2")
    state.add_busy("u1", "s1")  # 集合去重
    assert state.busy_count("u1") == 2
    state.remove_busy("u1", "s1")
    assert state.busy_count("u1") == 1


def test_window_limiter_shared_count(state: RedisState) -> None:
    limiter = RedisWindowLimiter(state, limit=3, window_s=60.0)
    assert [limiter.allow("login:1.2.3.4") for _ in range(3)] == [True] * 3
    assert limiter.allow("login:1.2.3.4") is False
    # 其他来源互不影响
    assert limiter.allow("login:5.6.7.8") is True
    limiter.reset()  # 多 worker 下无操作，但不应抛错


def test_namespaced_keys(state: RedisState) -> None:
    state.set("k", "v", ttl_s=30)
    assert state.get("k") == "v"
    assert state.incr("n") == 1
    assert state.incr("n") == 2
