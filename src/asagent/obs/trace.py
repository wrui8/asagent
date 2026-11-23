from __future__ import annotations

import json
import re
from pathlib import Path

from ..runtime.events import AgentEvent

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_\-]")


class TraceWriter:
    """将事件以 JSONL 追加到 runtime/traces/<session>.jsonl。"""

    def __init__(self, trace_dir: Path):
        self.trace_dir = trace_dir
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def write(self, event: AgentEvent) -> None:
        filename = _SAFE_NAME.sub("_", event.session_id) or "session"
        path = self.trace_dir / f"{filename}.jsonl"
        line = json.dumps(event.model_dump(), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read(self, session_id: str) -> list[dict]:
        filename = _SAFE_NAME.sub("_", session_id) or "session"
        path = self.trace_dir / f"{filename}.jsonl"
        if not path.exists():
            return []
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events
