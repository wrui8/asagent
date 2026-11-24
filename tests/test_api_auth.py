from __future__ import annotations

from api_helpers import (
    PASSWORD,
    auth,
    create_admin,
    create_user,
    login,
)
from fastapi.testclient import TestClient

from asagent.api.security import new_invite_code, sha256_hex


def test_health_is_public(api_client: TestClient) -> None:
    resp = api_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_register_requires_valid_invite(api_client: TestClient) -> None:
    resp = api_client.post(
        "/api/auth/register",
        json={"username": "alice", "password": PASSWORD, "invite_code": "INV-NOPE-NOPE"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_invite"

    admin = create_admin(api_client)
    raw = new_invite_code()
    api_client.app.state.web.create_invite(
        code_hash=sha256_hex(raw), created_by=admin["id"]
    )

    ok = api_client.post(
        "/api/auth/register",
        json={"username": "alice", "password": PASSWORD, "invite_code": raw.lower()},
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["user"]["username"] == "alice"
    assert ok.json()["access_token"]

    # 邀请码单次使用：重复注册被拒
    dup = api_client.post(
        "/api/auth/register",
        json={"username": "bobby", "password": PASSWORD, "invite_code": raw},
    )
    assert dup.status_code == 400


def test_login_me_refresh_logout(api_client: TestClient) -> None:
    create_admin(api_client)
    token = login(api_client, "root")

    me = api_client.get("/api/auth/me", headers=auth(token))
    assert me.status_code == 200
    assert me.json()["role"] == "admin"

    bad = api_client.post(
        "/api/auth/login", json={"username": "root", "password": "wrong-password-123"}
    )
    assert bad.status_code == 401

    refreshed = api_client.post("/api/auth/refresh", json={})
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["access_token"] != token

    out = api_client.post("/api/auth/logout", json={})
    assert out.status_code == 204
    assert api_client.post("/api/auth/refresh", json={}).status_code == 401


def test_desktop_body_refresh_flow(api_client: TestClient) -> None:
    # 使用独立账号，避免与 root 的模块级登录限流窗口互相影响
    create_user(api_client, "desktopflow")
    api_client.cookies.clear()

    login_resp = api_client.post(
        "/api/auth/login",
        json={"username": "desktopflow", "password": PASSWORD},
        headers={"X-Client": "desktop"},
    )
    assert login_resp.status_code == 200, login_resp.text
    body = login_resp.json()
    assert body["refresh_token"]
    assert api_client.get(
        "/api/auth/me", headers=auth(body["access_token"])
    ).status_code == 200

    # 模拟桌面端：不依赖 Cookie，用 body 中的刷新令牌轮换
    api_client.cookies.clear()
    refreshed = api_client.post(
        "/api/auth/refresh",
        json={"refresh_token": body["refresh_token"]},
        headers={"X-Client": "desktop"},
    )
    assert refreshed.status_code == 200, refreshed.text
    new_body = refreshed.json()
    assert new_body["refresh_token"]
    assert new_body["refresh_token"] != body["refresh_token"]
    assert new_body["access_token"] != body["access_token"]

    # 旧刷新令牌已轮换失效
    api_client.cookies.clear()
    reused = api_client.post(
        "/api/auth/refresh",
        json={"refresh_token": body["refresh_token"]},
        headers={"X-Client": "desktop"},
    )
    assert reused.status_code == 401

    # Web 登录不返回 body 刷新令牌
    web_login = api_client.post(
        "/api/auth/login", json={"username": "desktopflow", "password": PASSWORD}
    )
    assert web_login.status_code == 200
    assert web_login.json()["refresh_token"] is None


def test_mobile_body_refresh_flow(api_client: TestClient) -> None:
    create_user(api_client, "mobileflow")
    api_client.cookies.clear()

    login_resp = api_client.post(
        "/api/auth/login",
        json={"username": "mobileflow", "password": PASSWORD},
        headers={"X-Client": "mobile"},
    )
    assert login_resp.status_code == 200, login_resp.text
    body = login_resp.json()
    assert body["refresh_token"]

    api_client.cookies.clear()
    refreshed = api_client.post(
        "/api/auth/refresh",
        json={"refresh_token": body["refresh_token"]},
        headers={"X-Client": "mobile"},
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["refresh_token"] != body["refresh_token"]


def test_desktop_cors_allows_tauri_origin(api_client: TestClient) -> None:
    resp = api_client.options(
        "/api/auth/login",
        headers={
            "Origin": "http://tauri.localhost",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-client",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_access_token_required(api_client: TestClient) -> None:
    assert api_client.get("/api/auth/me").status_code == 401
    assert api_client.get("/api/sessions").status_code == 401
    assert api_client.get("/api/sessions", headers=auth("not-a-token")).status_code == 401


def test_disabled_user_is_rejected(api_client: TestClient) -> None:
    create_admin(api_client)
    user = create_user(api_client, "bob")
    token = login(api_client, "bob")
    api_client.app.state.web.update_user(user["id"], disabled=True)

    resp = api_client.get("/api/auth/me", headers=auth(token))
    assert resp.status_code == 401
    assert resp.json()["code"] == "account_disabled"


def test_login_rate_limit(api_client: TestClient) -> None:
    # 用独立用户名，避免与其他用例共享模块级限流窗口
    create_user(api_client, "ratelimit")
    for _ in range(10):
        api_client.post(
            "/api/auth/login",
            json={"username": "ratelimit", "password": "wrong-password-123"},
        )
    resp = api_client.post(
        "/api/auth/login", json={"username": "ratelimit", "password": PASSWORD}
    )
    assert resp.status_code == 429
    assert resp.json()["code"] == "rate_limited"
