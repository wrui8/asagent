from __future__ import annotations

import asyncio
import hashlib
import shutil
import time
from pathlib import Path

from ..config import AppConfig
from ..profiles.loader import load_profile
from ..runtime.factory import build_runtime
from ..tools.base import ToolResult
from .graders import CaseRun, grade_case
from .report import CaseResult, EvalReport
from .schema import EvalCase, load_cases


def _file_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def _auto_approve(name: str, args: dict) -> bool:
    """评测用例的显式人工授权（auto_approve=true）。"""
    return True


class EvalRunner:
    def __init__(self, config: AppConfig):
        self.config = config
        self._workspace_seq = 0

    async def run_suite(
        self,
        profile_name: str,
        *,
        suite_path: Path | None = None,
        models: list[str] | None = None,
        repeat: int = 1,
        limit: int | None = None,
    ) -> EvalReport:
        profile = load_profile(profile_name, project_root=self.config.root)
        suite = suite_path or profile.eval_suite
        if suite is None or not suite.exists():
            raise FileNotFoundError(f"评测集不存在: {suite}")

        cases = load_cases(suite)
        if limit:
            cases = cases[:limit]
        db_path = self.config.path(
            profile.tool_config.get("db_path", "data/demo.sqlite3")
        )
        fixtures_dir = suite.parent / "fixtures"

        report = EvalReport(profile=profile_name)
        model_list: list[str | None] = list(models) if models else [None]
        for case in cases:
            for model in model_list:
                for _ in range(max(1, repeat)):
                    report.results.append(
                        await self._run_case(
                            case, model, profile_name, db_path, fixtures_dir
                        )
                    )
        return report

    def _prepare_fixture(
        self, case: EvalCase, fixtures_dir: Path
    ) -> tuple[Path | None, Path | None, Path | None]:
        """复制用例 fixture 的种子工作区，返回 (workspace, tests_dir, seed)。"""
        fixture = fixtures_dir / case.id
        seed = fixture / "workspace"
        if not seed.is_dir():
            return None, None, None
        self._workspace_seq += 1
        workspace = (
            self.config.root
            / "runtime"
            / "eval-workspaces"
            / f"{case.id}-{self._workspace_seq}"
        )
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(seed, workspace)
        tests_dir = fixture / "tests"
        return workspace, tests_dir.resolve() if tests_dir.is_dir() else None, seed.resolve()

    async def _run_case(
        self,
        case: EvalCase,
        model: str | None,
        profile_name: str,
        db_path: Path,
        fixtures_dir: Path,
    ) -> CaseResult:
        workspace, tests_dir, seed_workspace = self._prepare_fixture(case, fixtures_dir)
        runtime = build_runtime(
            self.config,
            profile_name,
            forced_model=model,
            approver=_auto_approve if case.auto_approve else None,
            workspace=workspace,
            tests_dir=tests_dir,
        )
        before = _file_hash(db_path)
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                runtime.run(case.input, max_steps=case.max_steps),
                timeout=case.timeout_s,
            )
            events = runtime.store.events(result.session_id)
            tool_results = self._collect_tool_results(runtime.store, events, result.session_id)
            case_run = CaseRun(
                case=case,
                final_answer=result.final_answer,
                status=str(result.status),
                error=result.error,
                events=events,
                tool_results=tool_results,
                db_hash_before=before,
                db_hash_after=_file_hash(db_path),
                tool_calls=self._collect_tool_calls(events),
                workspace=workspace,
                tests_dir=tests_dir,
                seed_workspace=seed_workspace,
            )
            grades = grade_case(case, case_run, db_path)
            passed = all(g.passed for g in grades)
            reason = "；".join(g.reason for g in grades)
            final_state = runtime.store.load_state(result.session_id)
            actual_model = (
                final_state.route.model_id
                if final_state.route is not None
                else (model or "auto")
            )
            return CaseResult(
                case_id=case.id,
                model=actual_model,
                passed=passed,
                reason=reason,
                status=str(result.status),
                steps=result.steps,
                cost=result.cost,
                graders=grades,
                error=result.error,
            )
        except TimeoutError:
            return CaseResult(
                case_id=case.id,
                model=model or "auto",
                passed=False,
                reason=f"超时（{case.timeout_s}s）",
                status="timeout",
                steps=0,
                cost=0.0,
                error="timeout",
            )
        except Exception as exc:
            return CaseResult(
                case_id=case.id,
                model=model or "auto",
                passed=False,
                reason=f"执行异常: {exc}",
                status="error",
                steps=0,
                cost=0.0,
                error=str(exc),
            )
        finally:
            elapsed = time.perf_counter() - started
            _ = elapsed
            await runtime.aclose()
            runtime.store.close()

    @staticmethod
    def _collect_tool_calls(events) -> list[tuple[str | None, dict]]:
        return [
            (e.payload.get("tool"), e.payload.get("arguments") or {})
            for e in events
            if e.type == "tool_call"
        ]

    @staticmethod
    def _collect_tool_results(store, events, session_id: str) -> list[tuple[str | None, ToolResult]]:
        call_tools = {
            e.payload.get("call_id"): e.payload.get("tool")
            for e in events
            if e.type == "tool_call"
        }
        pairs: list[tuple[str | None, ToolResult]] = []
        for inv in store.finished_invocations(session_id):
            if inv.result_json:
                pairs.append(
                    (call_tools.get(inv.call_id), ToolResult.model_validate_json(inv.result_json))
                )
        return pairs
