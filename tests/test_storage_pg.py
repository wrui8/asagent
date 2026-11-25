"""PostgreSQL 仓储（阶段 2）：与 SQLite 同签名的往返用例。

未设置 AS_TEST_PG_DSN 时整文件跳过；CI 用 service container 提供。
"""

from __future__ import annotations

import os

import pytest

from asagent.models.types import ToolCall
from asagent.runtime.state import SessionStatus
from asagent.tools.base import ToolResult

DSN = os.environ.get("AS_TEST_PG_DSN", "").strip()

pytestmark = pytest.mark.skipif(not DSN, reason="未设置 AS_TEST_PG_DSN，跳过 PostgreSQL 测试")

TABLES = (
    "sessions",
    "events",
    "checkpoints",
    "tool_invocations",
    "artifacts",
    "approvals",
    "users",
    "invites",
    "refresh_tokens",
    "user_quotas",
    "user_usage",
    "hidden_sessions",
    "kb_docs",
    "kb_versions",
    "kb_jobs",
    "kb_audit",
    "eval_jobs",
)


@pytest.fixture()
def pool():
    from asagent.storage.pg import PgPool, run_migrations

    p = PgPool(DSN)
    run_migrations(p)
    with p.connection() as conn:
        conn.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
    yield p
    p.close()


@pytest.fixture()
def kernel(pool):
    from asagent.storage.pg import PgKernelStore

    return PgKernelStore(pool)


@pytest.fixture()
def web(pool):
    from asagent.storage.pg import PgWebStore

    return PgWebStore(pool)


def test_build_storage_postgres(pool) -> None:
    from types import SimpleNamespace

    from asagent.storage import build_storage

    config = SimpleNamespace(
        db_path=None,
        root=".",
        settings=SimpleNamespace(as_storage_backend="postgres", as_pg_dsn=DSN),
    )
    storage = build_storage(config)
    try:
        assert storage.backend == "postgres"
        assert storage.kernel is not None and storage.web is not None
    finally:
        storage.close()


def test_migrations_are_idempotent(pool) -> None:
    from asagent.storage.pg import run_migrations

    assert run_migrations(pool) == []  # 第二次执行不应重复应用


def test_kernel_roundtrip(kernel) -> None:
    state = kernel.create_session("sql", title="t1", owner_id="u1")
    assert state.session_id
    assert kernel.list_sessions(owner_id="u1")[0]["session_id"] == state.session_id

    e1 = kernel.append(state.session_id, "user_message", {"text": "你好"})
    e2 = kernel.append(state.session_id, "assistant_message", {"text": "在"})
    assert e2.seq > e1.seq
    events = kernel.events(state.session_id)
    assert [e.type for e in events] == ["user_message", "assistant_message"]
    assert kernel.events(state.session_id, after_seq=e1.seq)[0].type == "assistant_message"

    state.status = SessionStatus.DONE
    state.step = 2
    state.last_seq = e2.seq
    state.final_answer = "完成"
    kernel.save_state(state)
    loaded = kernel.load_state(state.session_id)
    assert loaded.status == SessionStatus.DONE
    assert loaded.final_answer == "完成"
    assert loaded.step == 2

    with pytest.raises(KeyError):
        kernel.load_state("missing")


def test_kernel_tool_idempotency(kernel) -> None:
    state = kernel.create_session("sql")
    call = ToolCall(id="c1", name="write_file", arguments={"path": "a.txt"})
    kernel.begin_tool(state.session_id, call, permission=2)
    pending = kernel.pending_invocations(state.session_id)
    assert [inv.call_id for inv in pending] == ["c1"]

    kernel.finish_tool(
        state.session_id, "c1", ToolResult(ok=True, content="done")
    )
    assert kernel.pending_invocations(state.session_id) == []
    finished = kernel.finished_invocations(state.session_id)
    assert finished[0].call_id == "c1"
    assert finished[0].status == "finished"

    # 重试同一 call_id：结果被清空，重新进入 pending
    kernel.begin_tool(state.session_id, call, permission=2)
    assert kernel.pending_invocations(state.session_id)[0].result_json is None


def test_kernel_approvals_and_artifacts(kernel) -> None:
    sid = kernel.create_session("sql").session_id
    kernel.request_approval(sid, "c1")
    assert kernel.approval_status(sid, "c1") == "pending"
    kernel.decide_approval(sid, "c1", True)
    assert kernel.approval_status(sid, "c1") == "approved"
    assert len(kernel.list_approvals(sid)) == 1

    # 再次请求同一调用会重置为 pending
    kernel.request_approval(sid, "c1")
    assert kernel.approval_status(sid, "c1") == "pending"

    aid = kernel.save(session_id=sid, kind="file", content="hello", meta={"x": 1})
    assert kernel.get_artifact(aid) == "hello"
    assert kernel.artifact_session(aid) == sid
    assert kernel.get_artifact("missing") is None


def test_web_users_invites_and_tokens(web) -> None:
    admin = web.create_user(username="root", password_hash="h1", role="admin")
    assert admin["disabled"] is False
    assert web.admin_count() == 1
    assert web.get_user_with_hash("root")["password_hash"] == "h1"

    web.update_user(admin["id"], disabled=True)
    assert web.get_user(admin["id"])["disabled"] is True
    assert web.admin_count() == 0
    web.update_user(admin["id"], disabled=False, role="user")
    assert web.list_users()[0]["username"] == "root"

    invite_id = web.create_invite(
        code_hash="code-1", created_by=admin["id"], max_uses=1
    )
    assert web.list_invites()[0]["id"] == invite_id
    user, error = web.register_with_invite(
        username="alice", password_hash="h2", code_hash="code-1"
    )
    assert error == "" and user is not None
    # 名额用尽
    _, error = web.register_with_invite(
        username="bob", password_hash="h3", code_hash="code-1"
    )
    assert error == "invalid_invite"

    web.save_refresh(user_id=user["id"], token_hash="th1", expires_at="2099-01-01")
    assert web.get_refresh("th1")["revoked_at"] is None
    web.revoke_user_refreshes(user["id"])
    assert web.get_refresh("th1")["revoked_at"] is not None

    web.set_quota(
        user["id"],
        max_cost_per_session=1.0,
        daily_cost_limit=2.0,
        concurrent_sessions=2,
        eval_daily_limit=5,
    )
    assert web.get_quota(user["id"])["eval_daily_limit"] == 5
    web.add_cost(user["id"], 0.5)
    assert web.daily_cost(user["id"]) == pytest.approx(0.5)

    web.hide_session(user["id"], "s_hidden")
    assert web.hidden_sessions(user["id"]) == {"s_hidden"}


def test_web_kb_and_eval(web) -> None:
    doc = web.kb_upsert(
        doc_id="d1", title="文档", sha256="sh", size=10, suspicious=True, actor="root"
    )
    assert doc["suspicious"] is True
    assert web.kb_get("d1")["title"] == "文档"
    assert [d["doc_id"] for d in web.kb_list(include_deleted=False)] == ["d1"]

    version_id = web.kb_add_version(
        doc_id="d1", content="正文", sha256="sh", actor="root"
    )
    assert web.kb_get_version(version_id)["content"] == "正文"
    assert web.kb_versions("d1")[0]["id"] == version_id

    web.kb_set_status("d1", "deleted", actor="root")
    assert web.kb_list(include_deleted=False) == []

    web.kb_audit(actor="root", action="upload", doc_id="d1", detail="ok")
    assert web.kb_audit_list()[0]["action"] == "upload"

    job_id = web.kb_job_start(kind="reindex", doc_ids=["d1"], actor="root")
    assert web.kb_job_running()["id"] == job_id
    web.kb_job_finish(job_id, status="done", message=None)
    assert web.kb_job_running() is None
    assert web.kb_jobs()[0]["status"] == "done"

    eval_id = web.eval_job_start(
        actor="root", profile="sql", models=["m1"], repeat=1, limit_cases=None
    )
    assert web.eval_job_running()["id"] == eval_id
    assert web.eval_count_today("root") == 1
    web.eval_job_finish(eval_id, status="done", report_id="r1", summary={"passed": 1})
    assert web.eval_job(eval_id)["report_id"] == "r1"

    # 模拟重启：残留 running 任务被标记失败
    web.eval_job_start(actor="root", profile="sql", models=None, repeat=1, limit_cases=None)
    assert web.eval_jobs_recover() == 1
    assert web.eval_job_running() is None
