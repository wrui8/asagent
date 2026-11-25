from __future__ import annotations

import pytest
from api_helpers import PASSWORD, auth, create_admin, create_user, login, wait_status
from fastapi.testclient import TestClient

from asagent.models.mock import ScriptedClient
from asagent.models.types import ModelResponse, ModelSpec, ToolCall
from asagent.runtime import factory
from asagent.runtime.state import SessionStatus


def _new_session(client: TestClient, token: str, profile: str = "assistant") -> str:
    resp = client.post(
        "/api/sessions",
        json={"profile": profile, "input": "你好"},
        headers=auth(token),
    )
    assert resp.status_code == 202, resp.text
    session_id = resp.json()["session_id"]
    wait_status(client, token, session_id)
    return session_id


def test_session_lifecycle(api_client: TestClient) -> None:
    create_admin(api_client)
    token = login(api_client, "root")
    session_id = _new_session(api_client, token)

    detail = api_client.get(f"/api/sessions/{session_id}", headers=auth(token)).json()
    assert detail["status"] == "done"
    assert detail["final_answer"] == "完成：你好"

    messages = api_client.get(
        f"/api/sessions/{session_id}/messages", headers=auth(token)
    )
    assert messages.status_code == 200

    events = api_client.get(
        f"/api/sessions/{session_id}/events", headers=auth(token)
    ).json()
    assert any(event["type"] == "final" for event in events)

    listed = api_client.get("/api/sessions", headers=auth(token)).json()
    assert [item["session_id"] for item in listed] == [session_id]

    deleted = api_client.delete(f"/api/sessions/{session_id}", headers=auth(token))
    assert deleted.status_code == 204
    assert api_client.get("/api/sessions", headers=auth(token)).json() == []


def test_ownership_is_enforced(api_client: TestClient) -> None:
    create_admin(api_client)
    create_user(api_client, "alice")
    create_user(api_client, "eve")
    alice_token = login(api_client, "alice")
    eve_token = login(api_client, "eve")
    admin_token = login(api_client, "root")

    session_id = _new_session(api_client, alice_token)

    assert (
        api_client.get(f"/api/sessions/{session_id}", headers=auth(eve_token)).status_code
        == 404
    )
    assert (
        api_client.delete(f"/api/sessions/{session_id}", headers=auth(eve_token)).status_code
        == 404
    )
    assert (
        api_client.get(f"/api/sessions/{session_id}", headers=auth(admin_token)).status_code
        == 200
    )

    assert api_client.get("/api/sessions", headers=auth(eve_token)).json() == []
    everything = api_client.get(
        "/api/sessions?all=true", headers=auth(admin_token)
    ).json()
    assert [item["session_id"] for item in everything] == [session_id]


def test_create_session_validation(api_client: TestClient) -> None:
    create_admin(api_client)
    create_user(api_client, "alice")
    user_token = login(api_client, "alice")

    unknown = api_client.post(
        "/api/sessions",
        json={"profile": "not-a-profile", "input": "hi"},
        headers=auth(user_token),
    )
    assert unknown.status_code == 400
    assert unknown.json()["code"] == "unknown_profile"

    forbidden = api_client.post(
        "/api/sessions",
        json={"profile": "code", "input": "hi"},
        headers=auth(user_token),
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "profile_forbidden"


def test_daily_quota_blocks_new_sessions(api_client: TestClient) -> None:
    create_admin(api_client)
    user = create_user(api_client, "alice")
    token = login(api_client, "alice")

    api_client.app.state.web.add_cost(user["id"], 10.0)
    resp = api_client.post(
        "/api/sessions",
        json={"profile": "assistant", "input": "hi"},
        headers=auth(token),
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "daily_quota_exceeded"

    api_client.app.state.web.set_quota(
        user["id"],
        max_cost_per_session=0.5,
        daily_cost_limit=100.0,
        concurrent_sessions=1,
    )
    api_client.app.state.jobs.running_for = lambda owner_id: 1
    resp = api_client.post(
        "/api/sessions",
        json={"profile": "assistant", "input": "hi"},
        headers=auth(token),
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "concurrency_exceeded"


def test_approval_flow(api_client: TestClient) -> None:
    create_admin(api_client)
    token = login(api_client, "root")
    session_id = _new_session(api_client, token)

    store = api_client.app.state.store
    state = store.load_state(session_id)
    state.status = SessionStatus.AWAITING_APPROVAL
    store.save_state(state)
    call = ToolCall(id="call_write_1", name="write_file", arguments={"path": "a.txt"})
    store.begin_tool(session_id, call, permission=1)
    store.request_approval(session_id, call.id)

    pending = api_client.get(
        f"/api/sessions/{session_id}/approvals", headers=auth(token)
    ).json()
    assert len(pending) == 1
    assert pending[0]["call_id"] == call.id
    assert pending[0]["args"] == {"path": "a.txt"}

    # 未决审批存在时不允许 resume
    resume = api_client.post(
        f"/api/sessions/{session_id}/resume", headers=auth(token)
    )
    assert resume.status_code == 409
    assert resume.json()["code"] == "approval_pending"

    decided = api_client.post(
        f"/api/sessions/{session_id}/approvals",
        json={"call_id": call.id, "approved": False, "resume": False},
        headers=auth(token),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json() == []
    assert store.approval_status(session_id, call.id) == "denied"


@pytest.mark.parametrize("approved", [True, False], ids=["approve", "deny"])
def test_office_decision_resumes_real_file_operation(api_client, config, monkeypatch, approved):
    args = {"path": "decision.md", "content": "iOS approval check"}
    call = ToolCall(id="write-1", name="write_document", arguments=args)
    model = ScriptedClient(
        ModelSpec(id="offline", provider="mock", upstream_name="offline"),
        [ModelResponse(tool_calls=[call], finish_reason="tool_calls"), ModelResponse(text="完成")],
    )
    monkeypatch.setattr(factory, "build_client", lambda cfg, model_id: model)
    api_client.app.state.jobs.runtime_factory = factory.build_runtime
    user = create_user(api_client, "mobileoffice")
    token = login(api_client, user["username"])
    headers = {**auth(token), "X-Client": "mobile"}
    opened = api_client.post(
        "/api/sessions", headers=headers, json={"profile": "office", "input": "写入测试文件"}
    )
    assert opened.status_code == 202, opened.text
    session_id = opened.json()["session_id"]
    url = f"/api/sessions/{session_id}"
    output = config.workspace_dir / user["id"] / session_id / args["path"]

    wait_status(api_client, token, session_id, expected="awaiting_approval")
    (pending,) = api_client.get(f"{url}/approvals", headers=headers).json()
    assert (pending["tool"], pending["call_id"], pending["args"]) == (call.name, call.id, args)
    assert not output.exists()

    decision = {"call_id": pending["call_id"], "approved": approved, "resume": True}
    decided = api_client.post(f"{url}/approvals", headers=headers, json=decision)
    assert decided.status_code == 200, decided.text
    wait_status(api_client, token, session_id, expected="done")
    assert api_client.get(f"{url}/approvals", headers=headers).json() == []
    repeated = api_client.post(f"{url}/approvals", headers=headers, json=decision)
    assert repeated.status_code == 409
    events = api_client.get(f"{url}/events", headers=headers).json()
    decisions = [e["payload"] for e in events if e["type"] == "approval_decided"]
    assert [(d["call_id"], d["approved"]) for d in decisions] == [(call.id, approved)]
    if approved:
        assert output.read_text(encoding="utf-8") == args["content"]
    else:
        assert not output.exists()


def test_cancel_without_job_conflicts(api_client: TestClient) -> None:
    create_admin(api_client)
    token = login(api_client, "root")
    session_id = _new_session(api_client, token)

    resp = api_client.post(f"/api/sessions/{session_id}/cancel", headers=auth(token))
    assert resp.status_code == 409
    assert resp.json()["code"] == "not_running"


def test_stream_returns_events_and_done(api_client: TestClient) -> None:
    create_admin(api_client)
    token = login(api_client, "root")
    session_id = _new_session(api_client, token)

    with api_client.stream(
        "GET", f"/api/sessions/{session_id}/stream", headers=auth(token)
    ) as response:
        assert response.status_code == 200
        text = "".join(response.iter_text())

    assert "event: agent_event" in text
    assert '"type":"final"' in text
    assert "event: done" in text


def test_artifact_access_is_scoped(api_client: TestClient) -> None:
    create_admin(api_client)
    create_user(api_client, "alice")
    token = login(api_client, "root")
    session_id = _new_session(api_client, token)

    store = api_client.app.state.store
    artifact_id = store.save(session_id=session_id, kind="text", content="hello")

    ok = api_client.get(
        f"/api/sessions/{session_id}/artifacts/{artifact_id}", headers=auth(token)
    )
    assert ok.status_code == 200
    assert ok.json()["content"] == "hello"

    alice_token = login(api_client, "alice")
    denied = api_client.get(
        f"/api/sessions/{session_id}/artifacts/{artifact_id}", headers=auth(alice_token)
    )
    assert denied.status_code == 404


def test_admin_views_api(api_client: TestClient) -> None:
    admin = create_admin(api_client)
    create_user(api_client, "alice")
    admin_token = login(api_client, "root")
    user_token = login(api_client, "alice", PASSWORD)

    assert api_client.get("/api/users", headers=auth(user_token)).status_code == 403
    users = api_client.get("/api/users", headers=auth(admin_token)).json()
    assert {item["username"] for item in users} == {"root", "alice"}

    quota = api_client.get(
        f"/api/users/{admin['id']}/quota", headers=auth(admin_token)
    ).json()
    assert quota["used_today"] == 0.0

    invite = api_client.post(
        "/api/invites", json={"role": "user"}, headers=auth(admin_token)
    )
    assert invite.status_code == 201
    assert invite.json()["code"].startswith("INV-")
    invites = api_client.get("/api/invites", headers=auth(admin_token)).json()
    assert len(invites) == 1

    profiles = api_client.get("/api/profiles", headers=auth(user_token)).json()
    assert "code" not in {item["name"] for item in profiles}

    info = api_client.get("/api/system/info", headers=auth(admin_token)).json()
    assert info["jwt_secret_is_default"] is False
    assert api_client.get("/api/system/info", headers=auth(user_token)).status_code == 403

    assert api_client.get("/api/audit", headers=auth(user_token)).status_code == 403
    assert api_client.get("/api/audit", headers=auth(admin_token)).json() == []


def test_patch_user_guards(api_client: TestClient) -> None:
    admin = create_admin(api_client)
    token = login(api_client, "root")

    self_lock = api_client.patch(
        f"/api/users/{admin['id']}", json={"disabled": True}, headers=auth(token)
    )
    assert self_lock.status_code == 400
    assert self_lock.json()["code"] == "self_lockout"

    demote = api_client.patch(
        f"/api/users/{admin['id']}", json={"role": "user"}, headers=auth(token)
    )
    assert demote.status_code == 400
    assert demote.json()["code"] == "self_lockout"
