from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel, Field

ArgsT = TypeVar("ArgsT", bound=BaseModel)


class PermissionLevel(IntEnum):
    READ = 0
    WRITE = 1
    DANGEROUS = 2


class ToolError(BaseModel):
    code: str
    message: str
    hint: str | None = None


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    permission: int = int(PermissionLevel.READ)
    timeout_s: float = 20.0


class ToolResult(BaseModel):
    ok: bool
    content: str
    data: Any | None = None
    error: ToolError | None = None
    artifact_id: str | None = None
    truncated: bool = False
    elapsed_ms: int = 0


class ArtifactSink(Protocol):
    def save(
        self, *, session_id: str, kind: str, content: str, meta: dict[str, Any] | None = None
    ) -> str: ...


Approver = Callable[[str, dict[str, Any]], Awaitable[bool]]


@dataclass
class ToolContext:
    session_id: str
    profile: str
    workspace: Path
    max_permission: int = int(PermissionLevel.READ)
    artifacts: ArtifactSink | None = None
    approve: Approver | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Tool(Generic[ArgsT], ABC):
    name: str
    description: str
    input_model: type[ArgsT]
    permission: int = int(PermissionLevel.READ)
    timeout_s: float = 20.0

    @abstractmethod
    async def execute(self, args: ArgsT, ctx: ToolContext) -> ToolResult: ...

    def to_spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.input_model.model_json_schema(),
            permission=int(self.permission),
            timeout_s=self.timeout_s,
        )
