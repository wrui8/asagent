from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends

from ...runtime.state import SessionStatus
from ...storage.protocols import KernelStore
from ...tools.base import PermissionLevel
from ..deps import api_error, current_user, get_jobs, get_store, load_owned_session
from ..jobs import JobManager
from ..schemas import ApprovalDecision, ApprovalOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["approvals"])


def _pending(store: KernelStore, session_id: str) -> list:
    """需要人工决策的工具调用：权限高于 READ 且审批状态未定。"""
    result = []
    for inv in store.pending_invocations(session_id):
        if inv.permission <= int(PermissionLevel.READ):
            continue
        if store.approval_status(session_id, inv.call_id) in ("approved", "denied"):
            continue
        result.append(inv)
    return result


def _parse_args(raw: str) -> dict:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


@router.get("/{session_id}/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    session_id: str,
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
) -> list[ApprovalOut]:
    load_owned_session(session_id, store, user)
    return [
        ApprovalOut(
            call_id=inv.call_id,
            tool=inv.tool,
            permission=inv.permission,
            args=_parse_args(inv.args_json),
            status=store.approval_status(session_id, inv.call_id) or "pending",
        )
        for inv in store.pending_invocations(session_id)
        if inv.permission > int(PermissionLevel.READ)
    ]


@router.post("/{session_id}/approvals", response_model=list[ApprovalOut])
async def decide_approvals(
    session_id: str,
    payload: ApprovalDecision,
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
    jobs: JobManager = Depends(get_jobs),
) -> list[ApprovalOut]:
    state = load_owned_session(session_id, store, user)
    if jobs.is_running(session_id):
        raise api_error(409, "任务正在运行，无法审批", "already_running")

    pending = _pending(store, session_id)
    if not pending:
        raise api_error(409, "没有待审批的工具调用", "no_pending_approval")

    if payload.call_id:
        selected = [inv for inv in pending if inv.call_id == payload.call_id]
        if not selected:
            raise api_error(404, "审批项不存在或已处理", "not_found")
    elif payload.approve_all or len(pending) == 1:
        selected = pending
    else:
        raise api_error(400, "存在多个待审批项，请指定 call_id 或 approve_all", "ambiguous")

    for inv in selected:
        store.decide_approval(session_id, inv.call_id, payload.approved)
        store.append(
            session_id,
            "approval_decided",
            {
                "call_id": inv.call_id,
                "tool": inv.tool,
                "approved": payload.approved,
                "source": "api",
            },
        )
        logger.info(
            "approval %s for %s/%s by %s",
            "approved" if payload.approved else "denied",
            session_id,
            inv.call_id,
            user["id"],
        )

    if payload.resume and not _pending(store, session_id):
        if state.status == SessionStatus.DONE:
            raise api_error(409, "会话已完成，无需恢复", "already_done")
        jobs.submit_resume(
            session_id=session_id,
            owner_id=state.owner_id or user["id"],
            profile=state.profile,
        )

    return [
        ApprovalOut(
            call_id=inv.call_id,
            tool=inv.tool,
            permission=inv.permission,
            args=_parse_args(inv.args_json),
            status=store.approval_status(session_id, inv.call_id) or "pending",
        )
        for inv in _pending(store, session_id)
    ]
