from __future__ import annotations

import asyncio
import time
from pathlib import Path

from api_helpers import auth, create_admin, create_user, login
from fastapi.testclient import TestClient

from asagent.eval.report import CaseResult, EvalReport
from asagent.runtime.events import EventType


class FakeEvalRunner:
    """不调模型：返回固定两条结果，可选延迟/失败。"""

    def __init__(self, *, delay: float = 0.0, fail: bool = False):
        self.delay = delay
        self.fail = fail
        self.calls: list[dict] = []

    async def run_suite(self, profile, *, models=None, repeat=1, limit=None, **_kw):
        self.calls.append(
            {"profile": profile, "models": models, "repeat": repeat, "limit": limit}
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("模型服务不可用")
        model = models[0] if models else "auto"
        return EvalReport(
            profile=profile,
            results=[
                CaseResult(
                    case_id="q1",
                    model=model,
                    passed=True,
                    reason="ok",
                    status="done",
                    steps=2,
                    cost=0.01,
                ),
                CaseResult(
                    case_id="q2",
                    model=model,
                    passed=False,
                    reason="答案不符",
                    status="done",
                    steps=3,
                    cost=0.02,
                ),
            ],
        )


def _install_runner(client: TestClient, runner: FakeEvalRunner, tmp_path: Path) -> None:
    manager = client.app.state.eval_manager
    manager.reports_dir = tmp_path / "evals"
    manager.runner_factory = lambda: runner


def _wait_job(client: TestClient, token: str, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get(f"/api/evals/jobs/{job_id}", headers=auth(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] != "running":
            return body
        time.sleep(0.05)
    raise AssertionError(f"评测任务未在 {timeout}s 内结束: {job_id}")


def test_evals_require_admin(api_client: TestClient) -> None:
    create_admin(api_client)
    create_user(api_client, "alice")
    token = login(api_client, "alice")

    assert api_client.get("/api/evals/suites", headers=auth(token)).status_code == 403
    assert api_client.get("/api/evals/reports", headers=auth(token)).status_code == 403
    assert (
        api_client.post(
            "/api/evals/run", json={"profile": "sql"}, headers=auth(token)
        ).status_code
        == 403
    )


def test_suites_and_run_report_flow(
    api_client: TestClient, tmp_path: Path
) -> None:
    create_admin(api_client)
    token = login(api_client, "root")
    _install_runner(api_client, FakeEvalRunner(), tmp_path)

    suites = api_client.get("/api/evals/suites", headers=auth(token))
    assert suites.status_code == 200
    sql = next(item for item in suites.json() if item["profile"] == "sql")
    assert sql["ready"] is True
    assert sql["cases"] and sql["cases"] > 0

    status = api_client.get("/api/evals/status", headers=auth(token)).json()
    assert status["running"] is False
    assert status["can_run"] is True
    assert status["daily_limit"] == 0

    started = api_client.post(
        "/api/evals/run",
        json={"profile": "sql", "models": ["deepseek-chat"], "limit": 2},
        headers=auth(token),
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]

    job = _wait_job(api_client, token, job_id)
    assert job["status"] == "done"
    assert job["report_id"]
    assert job["summary"]["passed"] == 1
    assert job["summary"]["total"] == 2

    report_id = job["report_id"]
    assert (tmp_path / "evals" / f"{report_id}.json").exists()
    assert (tmp_path / "evals" / f"{report_id}.md").exists()

    reports = api_client.get("/api/evals/reports", headers=auth(token)).json()
    assert [item["id"] for item in reports] == [report_id]
    assert reports[0]["summary"]["profile"] == "sql"

    detail = api_client.get(
        f"/api/evals/reports/{report_id}", headers=auth(token)
    ).json()
    assert len(detail["results"]) == 2
    assert detail["results"][1]["case_id"] == "q2"

    jobs = api_client.get("/api/evals/jobs", headers=auth(token)).json()
    assert jobs[0]["id"] == job_id
    assert jobs[0]["models"] == ["deepseek-chat"]

    last = api_client.get("/api/evals/status", headers=auth(token)).json()
    assert last["used_today"] == 1
    assert last["last_job"]["id"] == job_id


def test_run_rejects_bad_input(api_client: TestClient, tmp_path: Path) -> None:
    create_admin(api_client)
    token = login(api_client, "root")
    _install_runner(api_client, FakeEvalRunner(), tmp_path)

    missing = api_client.post(
        "/api/evals/run", json={"profile": "not-a-profile"}, headers=auth(token)
    )
    assert missing.status_code == 404

    bad_model = api_client.post(
        "/api/evals/run",
        json={"profile": "sql", "models": ["gpt-none"]},
        headers=auth(token),
    )
    assert bad_model.status_code == 400
    assert bad_model.json()["code"] == "unknown_model"


def test_single_flight_and_failure(api_client: TestClient, tmp_path: Path) -> None:
    create_admin(api_client)
    token = login(api_client, "root")
    _install_runner(api_client, FakeEvalRunner(delay=0.4), tmp_path)

    first = api_client.post(
        "/api/evals/run", json={"profile": "sql"}, headers=auth(token)
    )
    assert first.status_code == 202
    second = api_client.post(
        "/api/evals/run", json={"profile": "sql"}, headers=auth(token)
    )
    assert second.status_code == 409
    assert second.json()["code"] == "eval_busy"
    _wait_job(api_client, token, first.json()["job_id"])

    _install_runner(api_client, FakeEvalRunner(fail=True), tmp_path)
    failed = api_client.post(
        "/api/evals/run", json={"profile": "sql"}, headers=auth(token)
    )
    assert failed.status_code == 202
    job = _wait_job(api_client, token, failed.json()["job_id"])
    assert job["status"] == "failed"
    assert "模型服务不可用" in job["message"]
    assert job["report_id"] is None


def test_daily_quota(api_client: TestClient, tmp_path: Path) -> None:
    admin = create_admin(api_client)
    token = login(api_client, "root")
    _install_runner(api_client, FakeEvalRunner(), tmp_path)
    api_client.app.state.web.set_quota(
        admin["id"],
        max_cost_per_session=1.0,
        daily_cost_limit=100.0,
        concurrent_sessions=2,
        eval_daily_limit=1,
    )

    first = api_client.post(
        "/api/evals/run", json={"profile": "sql", "limit": 1}, headers=auth(token)
    )
    assert first.status_code == 202
    _wait_job(api_client, token, first.json()["job_id"])

    blocked = api_client.post(
        "/api/evals/run", json={"profile": "sql", "limit": 1}, headers=auth(token)
    )
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "eval_quota"

    quota = api_client.get(
        f"/api/users/{admin['id']}/quota", headers=auth(token)
    ).json()
    assert quota["eval_daily_limit"] == 1
    assert quota["eval_used_today"] == 1


def test_report_id_guards(api_client: TestClient, tmp_path: Path) -> None:
    create_admin(api_client)
    token = login(api_client, "root")
    _install_runner(api_client, FakeEvalRunner(), tmp_path)

    assert (
        api_client.get(
            "/api/evals/reports/does-not-exist", headers=auth(token)
        ).status_code
        == 404
    )
    assert (
        api_client.get(
            "/api/evals/reports/bad%21id", headers=auth(token)
        ).status_code
        == 400
    )


def test_audit_and_mcp_system(api_client: TestClient) -> None:
    create_admin(api_client)
    create_user(api_client, "alice")
    admin_token = login(api_client, "root")
    user_token = login(api_client, "alice")
    store = api_client.app.state.store

    resp = api_client.post(
        "/api/sessions",
        json={"profile": "assistant", "input": "你好"},
        headers=auth(user_token),
    )
    assert resp.status_code == 202
    session_id = resp.json()["session_id"]
    store.append(
        session_id,
        str(EventType.AUDIT),
        {"tool": "write_file", "ok": True, "path": "notes.txt"},
    )

    admin_audit = api_client.get("/api/audit", headers=auth(admin_token)).json()
    assert len(admin_audit) == 1
    assert admin_audit[0]["tool"] == "write_file"
    assert admin_audit[0]["owner_id"] is not None

    own = api_client.get(
        f"/api/audit?session_id={session_id}", headers=auth(user_token)
    )
    assert own.status_code == 200
    assert len(own.json()) == 1
    assert api_client.get("/api/audit", headers=auth(user_token)).status_code == 403

    mcp = api_client.get(
        "/api/system/mcp?profile=assistant", headers=auth(admin_token)
    ).json()
    assert mcp[0]["name"] == "demo"
    assert mcp[0]["permission"] == 0
    assert (
        api_client.get(
            "/api/system/mcp?profile=assistant", headers=auth(user_token)
        ).status_code
        == 403
    )
