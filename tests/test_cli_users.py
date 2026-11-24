from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from asagent import cli
from asagent.api.db import WebDB
from asagent.api.security import hash_password, verify_password


def _use_db(tmp_path, monkeypatch) -> WebDB:
    db_path = tmp_path / "state.db"
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(db_path=db_path))
    return WebDB(db_path)


def test_reset_password_updates_hash_and_revokes_tokens(tmp_path, monkeypatch):
    db = _use_db(tmp_path, monkeypatch)
    user = db.create_user(
        username="alice", password_hash=hash_password("old-password-123")
    )
    db.save_refresh(
        user_id=user["id"], token_hash="th-1", expires_at="2099-01-01T00:00:00"
    )

    result = CliRunner().invoke(
        cli.app, ["web-reset-password", "-u", "alice", "-p", "new-password-456"]
    )

    assert result.exit_code == 0, result.output
    stored = db.get_user_with_hash("alice")
    assert stored is not None
    assert verify_password("new-password-456", stored["password_hash"])
    assert not verify_password("old-password-123", stored["password_hash"])
    refresh = db.get_refresh("th-1")
    assert refresh is not None and refresh["revoked_at"] is not None


def test_reset_password_rejects_short_password(tmp_path, monkeypatch):
    db = _use_db(tmp_path, monkeypatch)
    db.create_user(username="bob", password_hash=hash_password("old-password-123"))

    result = CliRunner().invoke(
        cli.app, ["web-reset-password", "-u", "bob", "-p", "short"]
    )

    assert result.exit_code == 2
    stored = db.get_user_with_hash("bob")
    assert stored is not None
    assert verify_password("old-password-123", stored["password_hash"])


def test_reset_password_unknown_user(tmp_path, monkeypatch):
    _use_db(tmp_path, monkeypatch)

    result = CliRunner().invoke(
        cli.app, ["web-reset-password", "-u", "ghost", "-p", "new-password-456"]
    )

    assert result.exit_code == 1
