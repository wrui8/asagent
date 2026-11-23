from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class GraderSpec(BaseModel):
    type: str
    params: dict[str, Any] = Field(default_factory=dict)


class EvalCase(BaseModel):
    id: str
    profile: str = "sql"
    input: str
    expected: dict[str, Any] = Field(default_factory=dict)
    graders: list[GraderSpec]
    tags: list[str] = Field(default_factory=list)
    max_steps: int = 12
    timeout_s: float = 180.0
    # 评测显式授权：该用例的写操作视为已获人工批准（其余用例默认拒绝/挂起）
    auto_approve: bool = False


def load_cases(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(EvalCase.model_validate(json.loads(line)))
    return cases
