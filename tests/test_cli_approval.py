from __future__ import annotations

import asyncio

import pytest

from asagent.runtime.approval import build_approver, interactive_enabled


def test_auto_approver_allows_everything():
    approver = build_approver(auto=True, interactive=False)
    assert approver is not None
    assert asyncio.run(approver("write_document", {"path": "a.md"})) is True


def test_deny_returns_none():
    assert build_approver(deny=True, interactive=True) is None


def test_non_interactive_returns_none():
    assert build_approver(interactive=False) is None


def test_auto_and_deny_conflict():
    with pytest.raises(ValueError):
        build_approver(auto=True, deny=True)


def test_interactive_approver_respects_answer(monkeypatch):
    approver = build_approver(interactive=True)
    assert approver is not None

    monkeypatch.setattr("asagent.runtime.approval.Confirm.ask", lambda *a, **k: True)
    assert asyncio.run(approver("delete_document", {"path": "x.md"})) is True

    monkeypatch.setattr("asagent.runtime.approval.Confirm.ask", lambda *a, **k: False)
    assert asyncio.run(approver("delete_document", {"path": "x.md"})) is False


def test_interactive_approver_eof_denies(monkeypatch):
    approver = build_approver(interactive=True)
    assert approver is not None

    def boom(*args, **kwargs):
        raise EOFError

    monkeypatch.setattr("asagent.runtime.approval.Confirm.ask", boom)
    assert asyncio.run(approver("delete_document", {"path": "x.md"})) is False


def test_interactive_enabled_handles_missing_stdin(monkeypatch):
    class Broken:
        def isatty(self):
            raise ValueError("closed")

    monkeypatch.setattr("sys.stdin", Broken())
    assert interactive_enabled() is False
