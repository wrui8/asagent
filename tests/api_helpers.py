"""API 测试共用：假运行时、登录与轮询工具。"""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from asagent.api.app import create_app
from asagent.api.config import WebSettings
from asagent.api.security import hash_password
from asagent.config import AppConfig
from asagent.models.types import Message
from asagent.runtime.state import SessionState, SessionStatus

PASSWORD = "correct-horse-battery"


class FakeRuntime:
    """不做模型调用：直接把会话推进到 DONE。"""

    def __init__(self, store):
        self.store = store
        self.last_call: dict | None = None

    async def run(
        self,
        user_input: str | None = None,
        *,
        session_id: str,
        resume: bool = False,
        continuation: bool = False,
        max_steps: int = 12,
        owner_id: str | None = None,
        state: SessionState | None = None,
    ):
        self.last_call = {
            "session_id": session_id,
            "resume": resume,
            "continuation": continuation,
            "max_steps": max_steps,
        }
        st = state or self.store.load_state(session_id)
        if user_input:
            st.messages.append(Message(role="user", content=user_input))
        st.status = SessionStatus.DONE
        st.final_answer = f"完成：{user_input or '继续'}"
        st.step += 1
        st.cost += 0.001
        self.store.save_state(st)
        self.store.append(session_id, "final", {"answer": st.final_answer})

    async def aclose(self) -> None:
        return None


def make_client(config: AppConfig, **overrides) -> TestClient:
    settings = WebSettings(
        jwt_secret="test-secret-0123456789-abcdefghijklmnop",
        cookie_secure=False,
        stream_poll_s=0.02,
        heartbeat_s=1,
        **overrides,
    )
    app = create_app(config, web_settings=settings)
    # 限流器挂在 app.state；每个测试客户端从干净的窗口开始
    app.state.login_limiter.reset()
    app.state.register_limiter.reset()
    store = app.state.store
    app.state.jobs.runtime_factory = lambda _cfg, _profile, **_kw: FakeRuntime(store)
    return TestClient(app)


def create_admin(client: TestClient, username: str = "root") -> dict:
    return client.app.state.web.create_user(
        username=username, password_hash=hash_password(PASSWORD), role="admin"
    )


def create_user(client: TestClient, username: str) -> dict:
    return client.app.state.web.create_user(
        username=username, password_hash=hash_password(PASSWORD)
    )


def login(client: TestClient, username: str, password: str = PASSWORD) -> str:
    resp = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def wait_status(
    client: TestClient, token: str, session_id: str, *, timeout: float = 5.0
) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/sessions/{session_id}", headers=auth(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError(f"会话未在 {timeout}s 内结束: {session_id}")


def wait_idle(client: TestClient, session_id: str, *, timeout: float = 5.0) -> None:
    """状态先落 done、JobManager 仍在 finally 收尾时，锁要到稍后才释放。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not client.app.state.jobs.is_running(session_id):
            return
        time.sleep(0.02)
    raise AssertionError(f"会话任务未在 {timeout}s 内释放: {session_id}")
