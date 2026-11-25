from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ...runtime.events import EventType
from ...storage.protocols import KernelStore
from ..deps import api_error, current_user, get_store

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("")
async def list_audit(
    session_id: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(current_user),
    store: KernelStore = Depends(get_store),
) -> list[dict]:
    """写操作审计。

    管理员可查全部或指定会话；普通用户必须带 session_id 且只能查自己的会话。
    """
    if user["role"] != "admin":
        if not session_id:
            raise api_error(403, "普通用户需指定会话", "forbidden")
        try:
            state = store.load_state(session_id)
        except KeyError:
            raise api_error(404, "会话不存在", "not_found") from None
        if state.owner_id != user["id"]:
            raise api_error(404, "会话不存在", "not_found")

    if session_id:
        events = [
            event
            for event in store.events(session_id)
            if event.type == str(EventType.AUDIT)
        ][-limit:]
    else:
        events = store.recent_events_of_type(str(EventType.AUDIT), limit=limit)

    owners: dict[str, str | None] = {}
    result: list[dict] = []
    for event in events:
        if event.session_id not in owners:
            try:
                owners[event.session_id] = store.load_state(event.session_id).owner_id
            except KeyError:
                owners[event.session_id] = None
        result.append(
            {
                "seq": event.seq,
                "ts": event.ts,
                "session_id": event.session_id,
                "owner_id": owners[event.session_id],
                "type": event.type,
                **event.payload,
            }
        )
    return result
