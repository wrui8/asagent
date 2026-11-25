from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends

from ...storage.protocols import WebStore
from ..deps import api_error, get_web, require_admin
from ..schemas import InviteCreated, InviteCreateRequest, InviteOut
from ..security import new_invite_code, sha256_hex

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(
    _admin: dict = Depends(require_admin),
    web: WebStore = Depends(get_web),
) -> list[InviteOut]:
    return [InviteOut(**row) for row in web.list_invites()]


@router.post("/invites", response_model=InviteCreated, status_code=201)
async def create_invite(
    payload: InviteCreateRequest,
    admin: dict = Depends(require_admin),
    web: WebStore = Depends(get_web),
) -> InviteCreated:
    code = new_invite_code()
    expires_at = (
        datetime.now() + timedelta(days=payload.expires_in_days)
    ).isoformat(timespec="seconds")
    invite_id = web.create_invite(
        code_hash=sha256_hex(code),
        created_by=admin["id"],
        role=payload.role,
        expires_at=expires_at,
        max_uses=payload.max_uses,
    )
    row = next(i for i in web.list_invites() if i["id"] == invite_id)
    return InviteCreated(**row, code=code)


@router.post("/invites/{invite_id}/revoke", response_model=InviteOut)
async def revoke_invite(
    invite_id: str,
    _admin: dict = Depends(require_admin),
    web: WebStore = Depends(get_web),
) -> InviteOut:
    if not web.revoke_invite(invite_id):
        raise api_error(404, "邀请码不存在", "not_found")
    row = next((i for i in web.list_invites() if i["id"] == invite_id), None)
    assert row is not None
    return InviteOut(**row)
