from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from ...config import AppConfig
from ...profiles.loader import load_profile
from ...runtime.state import SessionState, SessionStatus
from ...storage.protocols import KernelStore, WebStore
from ...tools.base import PermissionLevel
from ..config import WebSettings
from ..deps import (
    api_error,
    current_user,
    get_config,
    get_jobs,
    get_settings,
    get_store,
    get_web,
    load_owned_session,
)
from ..jobs import JobManager
from ..schemas import (
    MessageOut,
    SessionAccepted,
    SessionCreateRequest,
    SessionOut,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

_TERMINAL = (SessionStatus.DONE, SessionStatus.FAILED, SessionStatus.AWAITING_APPROVAL)


def _session_out(state: SessionState, *, truncate: bool = False) -> SessionOut:
    final = state.final_answer
    if truncate and final and len(final) > 240:
        final = final[:240] + "…"
    return SessionOut(
        session_id=state.session_id,
        profile=state.profile,
        title=state.title,
        owner_id=state.owner_id,
        status=str(state.status),
        cost=state.cost,
        step=state.step,
        final_answer=final,
        error=state.error,
        created_at=state.created_at,
        updated_at=state.updated_at,
    )


def _limits(
    quota: dict | None, settings: WebSettings
) -> tuple[float | None, float | None, int]:
    if not quota:
        return (
            settings.default_session_cost_limit,
            settings.default_daily_cost_limit,
            settings.default_concurrent_sessions,
        )
    return (
        quota["max_cost_per_session"],
        quota["daily_cost_limit"],
        quota["concurrent_sessions"],
    )


@router.get("", response_model=list[SessionOut])
async def list_sessions(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    profile: str | None = None,
    status: str | None = None,
    all: bool = False,
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
    web: WebStore = Depends(get_web),
) -> list[SessionOut]:
    owner_id = None if (user["role"] == "admin" and all) else user["id"]
    hidden = web.hidden_sessions(user["id"])
    rows = store.list_sessions(
        limit + offset + len(hidden),
        0,
        owner_id=owner_id,
        profile=profile,
        status=status,
    )
    rows = [row for row in rows if row["session_id"] not in hidden][offset : offset + limit]
    result: list[SessionOut] = []
    for row in rows:
        try:
            state = store.load_state(row["session_id"])
        except KeyError:
            continue
        result.append(_session_out(state, truncate=True))
    return result


@router.post("", response_model=SessionAccepted, status_code=202)
async def create_session(
    payload: SessionCreateRequest,
    user: dict = Depends(current_user),
    web: WebStore = Depends(get_web),
    store: KernelStore = Depends(get_store),
    jobs: JobManager = Depends(get_jobs),
    settings: WebSettings = Depends(get_settings),
    config: AppConfig = Depends(get_config),
) -> SessionAccepted:
    try:
        load_profile(payload.profile, project_root=config.root)
    except FileNotFoundError:
        raise api_error(400, f"场景包不存在：{payload.profile}", "unknown_profile") from None

    if (
        payload.profile == "code"
        and settings.code_admin_only
        and user["role"] != "admin"
    ):
        raise api_error(403, "code 场景仅管理员可用", "profile_forbidden")

    session_limit, daily_limit, concurrency = _limits(web.get_quota(user["id"]), settings)
    if daily_limit is not None and web.daily_cost(user["id"]) >= daily_limit:
        raise api_error(429, f"今日成本已达上限 ¥{daily_limit:.2f}", "daily_quota_exceeded")
    if jobs.running_for(user["id"]) >= concurrency:
        raise api_error(429, f"并发任务已达上限 {concurrency}", "concurrency_exceeded")

    max_cost = payload.max_cost_cny
    if session_limit is not None:
        max_cost = session_limit if max_cost is None else min(max_cost, session_limit)

    title = payload.input.strip().splitlines()[0][:50]
    state = store.create_session(payload.profile, title=title, owner_id=user["id"])
    state.workspace = str(config.workspace_dir / user["id"] / state.session_id)
    store.save_state(state)

    try:
        jobs.submit(
            session_id=state.session_id,
            owner_id=user["id"],
            profile=payload.profile,
            user_input=payload.input,
            max_steps=payload.max_steps,
            max_cost_cny=max_cost,
            state=state,
        )
    except RuntimeError:
        raise api_error(409, "会话已在运行", "already_running") from None
    return SessionAccepted(session_id=state.session_id)


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: str,
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
) -> SessionOut:
    state = load_owned_session(session_id, store, user)
    return _session_out(state)


@router.get("/{session_id}/messages", response_model=list[MessageOut])
async def get_messages(
    session_id: str,
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
) -> list[MessageOut]:
    state = load_owned_session(session_id, store, user)
    return [MessageOut(**message.model_dump()) for message in state.messages]


@router.get("/{session_id}/events")
async def get_events(
    session_id: str,
    after_seq: int = Query(0, ge=0),
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
) -> list[dict]:
    load_owned_session(session_id, store, user)
    return [event.model_dump() for event in store.events(session_id, after_seq)]


@router.get("/{session_id}/stream")
async def stream_events(
    session_id: str,
    after_seq: int = Query(0, ge=0),
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
    settings: WebSettings = Depends(get_settings),
) -> StreamingResponse:
    load_owned_session(session_id, store, user)
    poll_s = max(0.05, settings.stream_poll_s)
    heartbeat_every = max(1, int(settings.heartbeat_s / poll_s))

    async def generate():
        cursor = after_seq
        idle = 0
        while True:
            events = store.events(session_id, cursor)
            for event in events:
                cursor = event.seq
                yield (
                    f"event: agent_event\nid: {event.seq}\n"
                    f"data: {event.model_dump_json()}\n\n"
                )
            if events:
                idle = 0
            else:
                idle += 1
                try:
                    state = store.load_state(session_id)
                except KeyError:
                    break
                if state.status in _TERMINAL:
                    yield "event: done\ndata: {}\n\n"
                    break
                if idle % heartbeat_every == 0:
                    yield ": heartbeat\n\n"
            await asyncio.sleep(poll_s)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{session_id}/resume", response_model=SessionAccepted, status_code=202)
async def resume_session(
    session_id: str,
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
    jobs: JobManager = Depends(get_jobs),
) -> SessionAccepted:
    state = load_owned_session(session_id, store, user)
    if jobs.is_running(session_id):
        raise api_error(409, "会话已在运行", "already_running")
    if state.status == SessionStatus.DONE:
        raise api_error(409, "会话已完成，无需恢复", "already_done")

    pending = [
        inv
        for inv in store.pending_invocations(session_id)
        if inv.permission > int(PermissionLevel.READ)
    ]
    undecided = [
        inv
        for inv in pending
        if store.approval_status(session_id, inv.call_id) not in ("approved", "denied")
    ]
    if undecided:
        raise api_error(409, "仍有未决审批，请先处理", "approval_pending")

    owner_id = state.owner_id or user["id"]
    jobs.submit_resume(
        session_id=session_id,
        owner_id=owner_id,
        profile=state.profile,
    )
    return SessionAccepted(session_id=session_id)


@router.post("/{session_id}/cancel")
async def cancel_session(
    session_id: str,
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
    jobs: JobManager = Depends(get_jobs),
) -> dict:
    load_owned_session(session_id, store, user)
    if not jobs.cancel(session_id):
        raise api_error(409, "会话当前没有运行中的任务", "not_running")
    return {"session_id": session_id, "cancelled": True}


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
    web: WebStore = Depends(get_web),
) -> None:
    state = load_owned_session(session_id, store, user)
    if state.status not in (SessionStatus.DONE, SessionStatus.FAILED):
        raise api_error(409, "只能删除已结束的会话", "not_finished")
    web.hide_session(user["id"], session_id)


@router.get("/{session_id}/artifacts/{artifact_id}")
async def get_artifact(
    session_id: str,
    artifact_id: str,
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
) -> dict:
    load_owned_session(session_id, store, user)
    owner = store.artifact_session(artifact_id)
    if owner != session_id:
        raise api_error(404, "artifact 不存在", "not_found")
    content = store.get_artifact(artifact_id)
    if content is None:
        raise api_error(404, "artifact 不存在", "not_found")
    return {"id": artifact_id, "session_id": session_id, "content": content}
