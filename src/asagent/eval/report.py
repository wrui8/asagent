from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .graders import GradeResult


@dataclass
class CaseResult:
    case_id: str
    model: str
    passed: bool
    reason: str
    status: str
    steps: int
    cost: float
    graders: list[GradeResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "model": self.model,
            "passed": self.passed,
            "reason": self.reason,
            "status": self.status,
            "steps": self.steps,
            "cost": round(self.cost, 6),
            "error": self.error,
            "graders": [
                {"passed": g.passed, "reason": g.reason} for g in self.graders
            ],
        }


@dataclass
class EvalReport:
    profile: str
    results: list[CaseResult] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        by_model: dict[str, dict[str, Any]] = {}
        for r in self.results:
            bucket = by_model.setdefault(r.model, {"total": 0, "passed": 0, "cost": 0.0})
            bucket["total"] += 1
            bucket["passed"] += int(r.passed)
            bucket["cost"] += r.cost
        for bucket in by_model.values():
            bucket["pass_rate"] = (
                bucket["passed"] / bucket["total"] if bucket["total"] else 0.0
            )
            bucket["cost"] = round(bucket["cost"], 4)
        return {
            "profile": self.profile,
            "total": total,
            "passed": passed,
            "pass_rate": passed / total if total else 0.0,
            "total_cost": round(sum(r.cost for r in self.results), 4),
            "by_model": by_model,
        }

    def to_dict(self) -> dict[str, Any]:
        return {"summary": self.summary(), "results": [r.to_dict() for r in self.results]}

    def to_markdown(self) -> str:
        summary = self.summary()
        lines = [
            f"# Eval 报告：{self.profile}",
            "",
            f"- 用例数：{summary['total']}",
            f"- 通过：{summary['passed']}（{summary['pass_rate']:.1%}）",
            f"- 总成本：¥{summary['total_cost']:.4f}",
            "",
            "| 模型 | 通过/总数 | 通过率 | 成本(¥) |",
            "| --- | --- | --- | --- |",
        ]
        for model, bucket in summary["by_model"].items():
            lines.append(
                f"| {model} | {bucket['passed']}/{bucket['total']} |"
                f" {bucket['pass_rate']:.1%} | {bucket['cost']:.4f} |"
            )
        lines += ["", "## 明细", "", "| 用例 | 模型 | 结果 | 步数 | 说明 |", "| --- | --- | --- | --- | --- |"]
        for r in self.results:
            mark = "✅" if r.passed else "❌"
            reason = (
                (r.reason or r.error or "")
                .replace("\r", " ")
                .replace("\n", " ")
                .replace("|", "\\|")[:120]
            )
            lines.append(
                f"| {r.case_id} | {r.model} | {mark} | {r.steps} | {reason} |"
            )
        return "\n".join(lines) + "\n"

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
