from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query

from ...config import AppConfig
from ...tools.rag.index import RagIndex
from .. import kb as kb_service
from ..db import WebDB
from ..deps import (
    api_error,
    current_user,
    get_config,
    get_kb_runner,
    get_web,
)
from ..kb import (
    DOC_ID_RE,
    KBIndexBusy,
    KBIndexRunner,
    extract_title,
    is_suspicious,
    latest_trash,
    make_index,
    move_to_trash,
    write_atomic,
)
from ..schemas import (
    KBDocCreate,
    KBDocDetail,
    KBDocOut,
    KBDocUpdate,
    KBReindexRequest,
    KBRollbackRequest,
)
from ..security import sha256_hex

router = APIRouter(prefix="/api/kb", tags=["kb"])


# ---------------------------------------------------------------------- #
# 元数据与磁盘同步
def _disk_docs(paths) -> list[str]:
    if not paths.docs_dir.exists():
        return []
    return sorted(p.stem for p in paths.docs_dir.glob("*.md"))


def _sync_docs(web: WebDB, paths) -> None:
    """把磁盘上已有、但还没进入管理表的文档补录进来（如初始化时的 data/docs）。"""
    known = {row["doc_id"]: row for row in web.kb_list()}
    for doc_id in _disk_docs(paths):
        row = known.get(doc_id)
        if row is not None and row["status"] == "active":
            continue
        content = paths.doc_path(doc_id).read_text(encoding="utf-8")
        web.kb_upsert(
            doc_id=doc_id,
            title=extract_title(doc_id, content),
            sha256=sha256_hex(content),
            size=len(content.encode("utf-8")),
            suspicious=is_suspicious(content),
            actor="system",
        )


def _index_maps(index: RagIndex) -> tuple[dict[str, str], dict[str, int]]:
    return index.doc_hashes(), {d.doc_id: d.chunks for d in index.list_docs()}


def _doc_out(
    row: dict, *, hashes: dict[str, str], chunks: dict[str, int]
) -> KBDocOut:
    indexed = (
        row["status"] == "active"
        and hashes.get(row["doc_id"]) == row["sha256"]
        and chunks.get(row["doc_id"], 0) > 0
    )
    return KBDocOut(**row, indexed=indexed, chunks=chunks.get(row["doc_id"], 0))


def _require_doc_id(doc_id: str) -> None:
    if not DOC_ID_RE.fullmatch(doc_id):
        raise api_error(
            400,
            "文档 ID 只能包含小写字母、数字、下划线和连字符，长度 2-64",
            "invalid_doc_id",
        )


def _read_row(web: WebDB, doc_id: str) -> dict:
    row = web.kb_get(doc_id)
    if row is None:
        raise api_error(404, "文档不存在", "not_found")
    return row


def _content_of(paths, row: dict) -> str:
    path = paths.doc_path(row["doc_id"])
    if row["status"] == "deleted":
        trashed = latest_trash(paths, row["doc_id"])
        if trashed is None:
            raise api_error(404, "文档已删除且回收站无副本", "not_found")
        path = trashed
    if not path.exists():
        raise api_error(404, "文档文件不存在", "not_found")
    return path.read_text(encoding="utf-8")


def _detail(paths, web: WebDB, index: RagIndex, row: dict) -> KBDocDetail:
    hashes, chunks = _index_maps(index)
    base = _doc_out(row, hashes=hashes, chunks=chunks)
    return KBDocDetail(**base.model_dump(), content=_content_of(paths, row))


def _audit(web: WebDB, actor: str, action: str, doc_id: str, detail: str = "") -> None:
    web.kb_audit(actor=actor, action=action, doc_id=doc_id, detail=detail or None)


# ---------------------------------------------------------------------- #
@router.get("/docs", response_model=list[KBDocOut])
async def list_docs(
    include_deleted: bool = True,
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
    config: AppConfig = Depends(get_config),
) -> list[KBDocOut]:
    paths = kb_service.kb_paths(config)
    _sync_docs(web, paths)
    hashes, chunks = _index_maps(make_index(config))
    return [
        _doc_out(row, hashes=hashes, chunks=chunks)
        for row in web.kb_list(include_deleted=include_deleted)
    ]


@router.post("/docs", response_model=KBDocDetail, status_code=201)
async def create_doc(
    payload: KBDocCreate,
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
    config: AppConfig = Depends(get_config),
) -> KBDocDetail:
    _require_doc_id(payload.doc_id)
    paths = kb_service.kb_paths(config)
    path = paths.doc_path(payload.doc_id)
    if path.exists() or web.kb_get(payload.doc_id) is not None:
        raise api_error(409, "文档 ID 已存在", "doc_exists")

    content = payload.content
    title = (payload.title or extract_title(payload.doc_id, content)).strip()
    await asyncio.to_thread(write_atomic, path, content)
    web.kb_upsert(
        doc_id=payload.doc_id,
        title=title or payload.doc_id,
        sha256=sha256_hex(content),
        size=len(content.encode("utf-8")),
        suspicious=is_suspicious(content),
        actor=user["username"],
    )
    web.kb_add_version(
        doc_id=payload.doc_id,
        content=content,
        sha256=sha256_hex(content),
        actor=user["username"],
    )
    _audit(web, user["username"], "create", payload.doc_id)
    return _detail(paths, web, make_index(config), _read_row(web, payload.doc_id))


@router.get("/docs/{doc_id}", response_model=KBDocDetail)
async def get_doc(
    doc_id: str,
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
    config: AppConfig = Depends(get_config),
) -> KBDocDetail:
    _require_doc_id(doc_id)
    paths = kb_service.kb_paths(config)
    _sync_docs(web, paths)
    row = _read_row(web, doc_id)
    return _detail(paths, web, make_index(config), row)


@router.put("/docs/{doc_id}", response_model=KBDocDetail)
async def update_doc(
    doc_id: str,
    payload: KBDocUpdate,
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
    config: AppConfig = Depends(get_config),
) -> KBDocDetail:
    _require_doc_id(doc_id)
    paths = kb_service.kb_paths(config)
    row = _read_row(web, doc_id)
    if row["status"] == "deleted":
        raise api_error(409, "文档在回收站中，请先恢复", "doc_deleted")
    if payload.content is None and payload.title is None:
        raise api_error(400, "没有需要更新的字段", "nothing_to_update")

    content = payload.content if payload.content is not None else _content_of(paths, row)
    title = (payload.title or extract_title(doc_id, content)).strip() or doc_id
    await asyncio.to_thread(write_atomic, paths.doc_path(doc_id), content)
    web.kb_upsert(
        doc_id=doc_id,
        title=title,
        sha256=sha256_hex(content),
        size=len(content.encode("utf-8")),
        suspicious=is_suspicious(content),
        actor=user["username"],
    )
    web.kb_add_version(
        doc_id=doc_id,
        content=content,
        sha256=sha256_hex(content),
        actor=user["username"],
    )
    _audit(web, user["username"], "update", doc_id)
    return _detail(paths, web, make_index(config), _read_row(web, doc_id))


@router.delete("/docs/{doc_id}", response_model=KBDocOut)
async def delete_doc(
    doc_id: str,
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
    config: AppConfig = Depends(get_config),
) -> KBDocOut:
    _require_doc_id(doc_id)
    paths = kb_service.kb_paths(config)
    row = _read_row(web, doc_id)
    if row["status"] == "deleted":
        raise api_error(409, "文档已在回收站", "doc_deleted")

    await asyncio.to_thread(move_to_trash, paths, doc_id)
    web.kb_set_status(doc_id, "deleted", actor=user["username"])
    # 立即从检索结果移除，避免删掉的文档仍被引用
    await asyncio.to_thread(make_index(config).remove, [doc_id])
    _audit(web, user["username"], "delete", doc_id)

    hashes, chunks = _index_maps(make_index(config))
    return _doc_out(_read_row(web, doc_id), hashes=hashes, chunks=chunks)


@router.post("/docs/{doc_id}/restore", response_model=KBDocDetail)
async def restore_doc(
    doc_id: str,
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
    config: AppConfig = Depends(get_config),
) -> KBDocDetail:
    _require_doc_id(doc_id)
    paths = kb_service.kb_paths(config)
    row = _read_row(web, doc_id)
    if row["status"] != "deleted":
        raise api_error(409, "文档不在回收站", "not_deleted")

    trashed = latest_trash(paths, doc_id)
    if trashed is None:
        raise api_error(404, "回收站中没有该文档的副本", "not_found")
    content = trashed.read_text(encoding="utf-8")
    await asyncio.to_thread(write_atomic, paths.doc_path(doc_id), content)
    trashed.unlink(missing_ok=True)

    web.kb_upsert(
        doc_id=doc_id,
        title=extract_title(doc_id, content),
        sha256=sha256_hex(content),
        size=len(content.encode("utf-8")),
        suspicious=is_suspicious(content),
        actor=user["username"],
    )
    _audit(web, user["username"], "restore", doc_id)
    return _detail(paths, web, make_index(config), _read_row(web, doc_id))


@router.get("/docs/{doc_id}/versions")
async def list_versions(
    doc_id: str,
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
) -> list[dict]:
    _require_doc_id(doc_id)
    _read_row(web, doc_id)
    return web.kb_versions(doc_id, limit=limit)


@router.post("/docs/{doc_id}/rollback", response_model=KBDocDetail)
async def rollback_doc(
    doc_id: str,
    payload: KBRollbackRequest,
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
    config: AppConfig = Depends(get_config),
) -> KBDocDetail:
    _require_doc_id(doc_id)
    paths = kb_service.kb_paths(config)
    row = _read_row(web, doc_id)
    if row["status"] == "deleted":
        raise api_error(409, "文档在回收站中，请先恢复", "doc_deleted")

    version = web.kb_get_version(payload.version_id)
    if version is None or version["doc_id"] != doc_id:
        raise api_error(404, "版本不存在", "not_found")

    content = version["content"]
    await asyncio.to_thread(write_atomic, paths.doc_path(doc_id), content)
    web.kb_upsert(
        doc_id=doc_id,
        title=extract_title(doc_id, content),
        sha256=sha256_hex(content),
        size=len(content.encode("utf-8")),
        suspicious=is_suspicious(content),
        actor=user["username"],
    )
    web.kb_add_version(
        doc_id=doc_id,
        content=content,
        sha256=sha256_hex(content),
        actor=user["username"],
    )
    _audit(web, user["username"], "rollback", doc_id, payload.version_id)
    return _detail(paths, web, make_index(config), _read_row(web, doc_id))


@router.get("/status")
async def index_status(
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
    config: AppConfig = Depends(get_config),
    runner: KBIndexRunner = Depends(get_kb_runner),
) -> dict:
    paths = kb_service.kb_paths(config)
    _sync_docs(web, paths)
    status = make_index(config).status()
    docs = web.kb_list()
    return {
        "docs_total": sum(1 for d in docs if d["status"] == "active"),
        "docs_deleted": sum(1 for d in docs if d["status"] == "deleted"),
        "suspicious_docs": sum(1 for d in docs if d["suspicious"]),
        "chunks": status["chunks"],
        "index_docs": status["index_docs"],
        "stale": status["stale"],
        "built_at": status["built_at"],
        "fingerprint": (
            status["stored_fingerprint"] or status["fingerprint"]
        )[:16],
        "indexing": runner.running,
        "cooldown_s": round(runner.cooldown_remaining(), 1),
        "last_job": (web.kb_jobs(limit=1) or [None])[0],
    }


@router.post("/reindex", status_code=202)
async def reindex(
    payload: KBReindexRequest | None = None,
    user: dict = Depends(current_user),
    runner: KBIndexRunner = Depends(get_kb_runner),
) -> dict:
    doc_ids = payload.doc_ids if payload else None
    if doc_ids is not None:
        if not doc_ids:
            raise api_error(400, "doc_ids 不能为空列表", "invalid_doc_ids")
        for doc_id in doc_ids:
            _require_doc_id(doc_id)
        doc_ids = sorted(set(doc_ids))
    try:
        job_id = runner.start(doc_ids=doc_ids, actor=user["username"])
    except KBIndexBusy as exc:
        raise api_error(409, str(exc), "index_busy") from None
    return {"job_id": job_id, "status": "running"}


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
) -> list[dict]:
    return web.kb_jobs(limit=limit)


@router.get("/audit")
async def list_audit(
    limit: int = Query(50, ge=1, le=200),
    user: dict = Depends(current_user),
    web: WebDB = Depends(get_web),
) -> list[dict]:
    return web.kb_audit_list(limit=limit)
