from __future__ import annotations

from api_helpers import (
    FakeRuntime,
    auth,
    create_admin,
    create_user,
    login,
    wait_idle,
    wait_status,
)
from fastapi.testclient import TestClient

from asagent.runtime.state import SessionStatus


def _new_session(client: TestClient, token: str) -> str:
    resp = client.post(
        "/api/sessions",
        json={"profile": "assistant", "input": "你好"},
        headers=auth(token),
    )
    assert resp.status_code == 202, resp.text
    session_id = resp.json()["session_id"]
    wait_status(client, token, session_id)
    wait_idle(client, session_id)
    return session_id


def _capture_runtime(client: TestClient) -> list[FakeRuntime]:
    """替换运行时工厂，记录 continue 提交给内核的参数。"""
    store = client.app.state.store
    calls: list[FakeRuntime] = []

    def factory(_cfg, _profile, **_kw):
        runtime = FakeRuntime(store)
        calls.append(runtime)
        return runtime

    client.app.state.jobs.runtime_factory = factory
    return calls


def test_continue_appends_round_to_same_session(api_client: TestClient) -> None:
    admin = create_admin(api_client)
    token = login(api_client, "root")
    session_id = _new_session(api_client, token)
    calls = _capture_runtime(api_client)

    resp = api_client.post(
        f"/api/sessions/{session_id}/continue",
        json={"input": "继续问"},
        headers=auth(token),
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["session_id"] == session_id
    wait_status(api_client, token, session_id)

    detail = api_client.get(
        f"/api/sessions/{session_id}", headers=auth(token)
    ).json()
    assert detail["status"] == "done"
    assert detail["final_answer"] == "完成：继续问"
    # 第一轮 1 步 + 本轮 1 步；步数为累计值
    assert detail["step"] == 2

    messages = api_client.get(
        f"/api/sessions/{session_id}/messages", headers=auth(token)
    ).json()
    assert [message["role"] for message in messages] == ["user", "user"]

    # 续聊没有新建会话；内核收到累计步数上限（1 + 默认 12）
    assert calls[0].last_call["continuation"] is True
    assert calls[0].last_call["max_steps"] == 13
    listed = api_client.get("/api/sessions", headers=auth(token)).json()
    assert [item["session_id"] for item in listed] == [session_id]

    # 按轮增量记账：两轮各 ¥0.001，日成本不应因累计 state.cost 而重复计费
    assert abs(api_client.app.state.web.daily_cost(admin["id"]) - 0.002) < 1e-9


def test_continue_requires_owner(api_client: TestClient) -> None:
    create_admin(api_client)
    create_user(api_client, "alice")
    create_user(api_client, "eve")
    alice = login(api_client, "alice")
    eve = login(api_client, "eve")
    session_id = _new_session(api_client, alice)

    resp = api_client.post(
        f"/api/sessions/{session_id}/continue",
        json={"input": "hi"},
        headers=auth(eve),
    )
    assert resp.status_code == 404


def test_continue_rejects_busy_or_invalid_states(api_client: TestClient) -> None:
    create_admin(api_client)
    token = login(api_client, "root")
    session_id = _new_session(api_client, token)

    store = api_client.app.state.store
    state = store.load_state(session_id)

    state.status = SessionStatus.AWAITING_APPROVAL
    store.save_state(state)
    resp = api_client.post(
        f"/api/sessions/{session_id}/continue",
        json={"input": "hi"},
        headers=auth(token),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "approval_pending"

    state.status = SessionStatus.RUNNING
    store.save_state(state)
    resp = api_client.post(
        f"/api/sessions/{session_id}/continue",
        json={"input": "hi"},
        headers=auth(token),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "invalid_state"

    state.status = SessionStatus.DONE
    store.save_state(state)
    api_client.app.state.jobs.is_running = lambda _sid: True
    resp = api_client.post(
        f"/api/sessions/{session_id}/continue",
        json={"input": "hi"},
        headers=auth(token),
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "already_running"


def test_continue_respects_daily_and_concurrency_quotas(api_client: TestClient) -> None:
    admin = create_admin(api_client)
    token = login(api_client, "root")
    session_id = _new_session(api_client, token)

    api_client.app.state.web.set_quota(
        admin["id"],
        max_cost_per_session=0.5,
        daily_cost_limit=1.0,
        concurrent_sessions=1,
    )
    api_client.app.state.web.add_cost(admin["id"], 10.0)
    resp = api_client.post(
        f"/api/sessions/{session_id}/continue",
        json={"input": "hi"},
        headers=auth(token),
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "daily_quota_exceeded"

    api_client.app.state.web.set_quota(
        admin["id"],
        max_cost_per_session=0.5,
        daily_cost_limit=100.0,
        concurrent_sessions=1,
    )
    api_client.app.state.jobs.running_for = lambda _owner: 1
    resp = api_client.post(
        f"/api/sessions/{session_id}/continue",
        json={"input": "hi"},
        headers=auth(token),
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "concurrency_exceeded"
