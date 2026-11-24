from __future__ import annotations

import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    """进程内滑窗限流；单 worker 部署下足够，多实例需换 Redis。"""

    def __init__(self, limit: int, window_s: float = 60.0):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        """清空窗口（测试隔离用）。"""
        self._hits.clear()
