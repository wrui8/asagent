"""评测任务：同一时刻只跑一个，报告落盘为 JSON/Markdown，任务状态写入 WebDB。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import AppConfig
from ..eval.report import EvalReport
from ..eval.runner import EvalRunner
from ..eval.schema import load_cases
from ..profiles.loader import available_profiles, load_profile
from ..storage.protocols import KernelStore, WebStore
from ..storage.redis_state import RedisState

logger = logging.getLogger(__name__)

REPORT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,96}$")


class EvalError(Exception):
    def __init__(self, message: str, code: str = "eval_error", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


class EvalJobManager:
    def __init__(
        self,
        *,
        config: AppConfig,
        web: WebStore,
        reports_dir: Path | None = None,
        redis: RedisState | None = None,
        store: KernelStore | None = None,
    ):
        self.config = config
        self.web = web
        self.store = store
        self.reports_dir = reports_dir or (config.root / "runtime" / "evals")
        self.redis = redis
        self._lock_token: str | None = None
        self.runner_factory: Callable[[], EvalRunner] = lambda: EvalRunner(
            config, store=self.store
        )
        self._task: asyncio.Task[None] | None = None
        self._job_id: str | None = None

    # ------------------------------------------------------------------ #
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self, user_id: str) -> dict[str, Any]:
        quota = self.web.get_quota(user_id) or {}
        daily = int(quota.get("eval_daily_limit") or 0)
        used = self.web.eval_count_today(user_id)
        return {
            "running": self.running(),
            "job_id": self._job_id,
            "used_today": used,
            "daily_limit": daily,
            "can_run": daily == 0 or used < daily,
        }

    def suite_info(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for name in available_profiles():
            try:
                profile = load_profile(name, project_root=self.config.root)
            except Exception:  # noqa: BLE001 - 单个场景包损坏不影响列表
                continue
            suite = profile.eval_suite
            cases: int | None = None
            if suite is not None and suite.exists():
                try:
                    cases = len(load_cases(suite))
                except Exception:  # noqa: BLE001
                    cases = None
            items.append(
                {
                    "profile": name,
                    "description": profile.description,
                    "cases": cases,
                    "suite": suite.name if suite is not None else None,
                    "ready": suite is not None and suite.exists(),
                }
            )
        return items

    # ------------------------------------------------------------------ #
    async def start(
        self,
        *,
        actor: str,
        profile: str,
        models: list[str] | None = None,
        repeat: int = 1,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if self.running():
            raise EvalError("已有评测任务在运行，请等待完成", "eval_busy", 409)
        try:
            loaded = load_profile(profile, project_root=self.config.root)
        except FileNotFoundError:
            raise EvalError("场景包不存在", "not_found", 404) from None
        suite = loaded.eval_suite
        if suite is None or not suite.exists():
            raise EvalError("该场景没有可用的评测集", "suite_missing", 400)
        if models:
            known = set(self.config.models_file.models)
            unknown = [model for model in models if model not in known]
            if unknown:
                raise EvalError(
                    f"未知模型：{', '.join(unknown)}", "unknown_model", 400
                )

        quota = self.web.get_quota(actor) or {}
        daily = int(quota.get("eval_daily_limit") or 0)
        used = self.web.eval_count_today(actor)
        if daily > 0 and used >= daily:
            raise EvalError(
                f"今日评测次数已用完（{used}/{daily}）", "eval_quota", 429
            )

        token: str | None = None
        if self.redis is not None:
            token = self.redis.acquire("eval", ttl_s=3600)
            if token is None:
                raise EvalError("已有评测任务在运行，请等待完成", "eval_busy", 409)
        self._lock_token = token
        try:
            job_id = self.web.eval_job_start(
                actor=actor,
                profile=profile,
                models=models,
                repeat=repeat,
                limit_cases=limit,
            )
        except Exception:
            self._release_lock()
            raise
        self._job_id = job_id
        self._task = asyncio.create_task(
            self._run(
                job_id=job_id,
                profile=profile,
                models=models,
                repeat=repeat,
                limit=limit,
            )
        )
        return {"job_id": job_id, "status": "running"}

    async def _run(
        self,
        *,
        job_id: str,
        profile: str,
        models: list[str] | None,
        repeat: int,
        limit: int | None,
    ) -> None:
        try:
            report = await self.runner_factory().run_suite(
                profile, models=models, repeat=repeat, limit=limit
            )
            report_id = self._write_report(report)
            self.web.eval_job_finish(
                job_id,
                status="done",
                report_id=report_id,
                summary=report.summary(),
            )
        except asyncio.CancelledError:
            self.web.eval_job_finish(
                job_id, status="failed", message="评测任务被取消"
            )
            raise
        except Exception as exc:  # noqa: BLE001 - 任务失败要落库并展示原因
            logger.exception("eval job %s failed", job_id)
            self.web.eval_job_finish(
                job_id, status="failed", message=str(exc)[:500]
            )
        finally:
            self._job_id = None
            self._release_lock()

    def _release_lock(self) -> None:
        token, self._lock_token = self._lock_token, None
        if self.redis is not None and token is not None:
            try:
                self.redis.release("eval", token)
            except Exception:  # noqa: BLE001 - 释放失败依赖 TTL 兜底
                logger.warning("release eval lock failed")

    def _write_report(self, report: EvalReport) -> str:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        report_id = f"{report.profile}-{stamp}"
        suffix = 1
        while (self.reports_dir / f"{report_id}.json").exists():
            suffix += 1
            report_id = f"{report.profile}-{stamp}-{suffix}"
        (self.reports_dir / f"{report_id}.json").write_text(
            report.to_json(), encoding="utf-8"
        )
        (self.reports_dir / f"{report_id}.md").write_text(
            report.to_markdown(), encoding="utf-8"
        )
        return report_id

    # ------------------------------------------------------------------ #
    def list_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.reports_dir.exists():
            return []
        paths = sorted(
            self.reports_dir.glob("*.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:limit]
        items: list[dict[str, Any]] = []
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            summary = data.get("summary")
            if not isinstance(summary, dict):
                summary = {}
            items.append(
                {
                    "id": path.stem,
                    "profile": summary.get("profile") or path.stem.split("-")[0],
                    "created_at": datetime.fromtimestamp(
                        path.stat().st_mtime
                    ).isoformat(timespec="seconds"),
                    "summary": summary,
                }
            )
        return items

    def get_report(self, report_id: str) -> dict[str, Any]:
        if not REPORT_ID_RE.fullmatch(report_id):
            raise EvalError("报告 ID 非法", "bad_report_id", 400)
        path = self.reports_dir / f"{report_id}.json"
        if not path.exists():
            raise EvalError("报告不存在", "not_found", 404)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise EvalError("报告文件损坏", "report_broken", 500) from None
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        results = data.get("results") if isinstance(data.get("results"), list) else []
        return {"id": report_id, "summary": summary, "results": results}

    async def shutdown(self) -> None:
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
