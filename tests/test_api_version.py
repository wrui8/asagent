from __future__ import annotations

from api_helpers import make_client

from asagent import __version__
from asagent.config import AppConfig


def test_app_version_defaults_to_backend_version(config: AppConfig) -> None:
    with make_client(config) as client:
        resp = client.get("/api/app/version")

    assert resp.status_code == 200
    data = resp.json()
    assert data["latest"] == __version__
    assert data["min_supported"] == __version__
    assert data["android_url"].endswith("/releases/latest")
    assert data["notes"] == ""


def test_app_version_honors_settings(config: AppConfig) -> None:
    with make_client(
        config,
        app_latest_version="0.3.0",
        app_min_version="0.2.0",
        app_android_url="https://example.com/app.apk",
        app_release_notes="修复若干问题",
    ) as client:
        resp = client.get("/api/app/version")

    assert resp.status_code == 200
    assert resp.json() == {
        "latest": "0.3.0",
        "min_supported": "0.2.0",
        "android_url": "https://example.com/app.apk",
        "notes": "修复若干问题",
    }
