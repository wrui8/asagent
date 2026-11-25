"""抽取触发条件：轮数过滤、30 秒去抖与每用户每日配额。

配额与去抖都依赖 Redis（多 worker 共享）；未配置 Redis 时记忆子系统不会启用。
"""

from __future__ import annotations

from datetime import date

from ..models.types import Message
from ..runtime.state import SessionState
from ..storage.redis_state import RedisState

MIN_USER_TURNS = 3
DEBOUNCE_S = 30


def user_turns(messages: list[Message]) -> int:
    return sum(1 for m in messages if m.role == "user" and (m.content or "").strip())


class PublishPolicy:
    def __init__(
        self,
        redis: RedisState | None,
        *,
        daily_limit: int = 20,
        debounce_s: int = DEBOUNCE_S,
        min_turns: int = MIN_USER_TURNS,
    ):
        self.redis = redis
        self.daily_limit = max(0, daily_limit)
        self.debounce_s = max(1, debounce_s)
        self.min_turns = max(1, min_turns)

    def check(self, state: SessionState, owner_id: str) -> tuple[bool, str]:
        """返回 (是否发布, 原因)；原因用于日志与测试。"""
        if user_turns(state.messages) < self.min_turns:
            return False, "too_short"
        if not owner_id:
            return False, "no_owner"
        if self.redis is None:
            return False, "redis_unavailable"
        token = self.redis.acquire(
            f"mem:debounce:{state.session_id}", ttl_s=self.debounce_s
        )
        if token is None:
            return False, "debounced"
        if self.daily_limit == 0:
            return False, "quota"
        day = date.today().isoformat()
        used = self.redis.incr(f"mem:quota:{owner_id}:{day}", ttl_s=86400)
        if used > self.daily_limit:
            return False, "quota"
        return True, ""


__all__ = ["DEBOUNCE_S", "MIN_USER_TURNS", "PublishPolicy", "user_turns"]
