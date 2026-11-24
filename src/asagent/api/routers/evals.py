from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query

from ..db import WebDB
from ..deps import api_error, get_eval_manager, get_web, require_admin
from ..evals import EvalError, EvalJobManager
from ..schemas import EvalRunRequest

router = APIRouter(prefix="/api/evals", tags=["evals"])


def _job_out(row: dict) -> dict:
    data = dict(row)
    raw_summary = data.pop("summary_json", None)
    raw_models = data.get("models")
    try:
        data["summary"] = json.loads(raw_summary) if raw_summary else None
    except json.JSONDecodeError:
        data["summary"] = None
    try:
        data["models"] = json.loads(raw_models) if raw_models else None
    except json.JSONDecodeError:
        data["models"] = None
    return data


@router.get("/suites")
async def list_suites(
    _admin: dict = Depends(require_admin),
    manager: EvalJobManager = Depends(get_eval_manager),
) -> list[dict]:
    return manager.suite_info()


@router.get("/status")
async def eval_status(
    admin: dict = Depends(require_admin),
    manager: EvalJobManager = Depends(get_eval_manager),
    web: WebDB = Depends(get_web),
) -> dict:
    body = manager.status(admin["id"])
    rows = web.eval_jobs(limit=1)
    body["last_job"] = _job_out(rows[0]) if rows else None
    return body


@router.get("/jobs")
async def list_jobs(
    limit: int = Query(20, ge=1, le=100),
    _admin: dict = Depends(require_admin),
    web: WebDB = Depends(get_web),
) -> list[dict]:
    return [_job_out(row) for row in web.eval_jobs(limit=limit)]


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    _admin: dict = Depends(require_admin),
    web: WebDB = Depends(get_web),
) -> dict:
    row = web.eval_job(job_id)
    if row is None:
        raise api_error(404, "评测任务不存在", "not_found")
    return _job_out(row)


@router.get("/reports")
async def list_reports(
    limit: int = Query(30, ge=1, le=200),
    _admin: dict = Depends(require_admin),
    manager: EvalJobManager = Depends(get_eval_manager),
) -> list[dict]:
    return manager.list_reports(limit=limit)


@router.get("/reports/{report_id}")
async def get_report(
    report_id: str,
    _admin: dict = Depends(require_admin),
    manager: EvalJobManager = Depends(get_eval_manager),
) -> dict:
    try:
        return manager.get_report(report_id)
    except EvalError as exc:
        raise api_error(exc.status, str(exc), exc.code) from None


@router.post("/run", status_code=202)
async def run_eval(
    payload: EvalRunRequest,
    admin: dict = Depends(require_admin),
    manager: EvalJobManager = Depends(get_eval_manager),
) -> dict:
    try:
        return await manager.start(
            actor=admin["id"],
            profile=payload.profile,
            models=payload.models,
            repeat=payload.repeat,
            limit=payload.limit,
        )
    except EvalError as exc:
        raise api_error(exc.status, str(exc), exc.code) from None
