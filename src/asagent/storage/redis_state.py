"""Redis 共享状态（阶段 2）：任务单飞锁、心跳、跨 worker 计数与限流。

未配置 `AS_REDIS_URL` 时上层保持进程内行为；驱动延迟导入。
"""

from __future__ import annotations

import time
import uuid
from typing import Any

PREFIX = "asagent:"


class RedisState:
    def __init__(self, url: str, *, prefix: str = PREFIX, client: Any | None = None):
        self.prefix = prefix
        if client is not None:
            # 测试注入 fakeredis 等兼容客户端
            self._redis = client
            return
        if not url.strip():
            raise ValueError("Redis 地址为空：请设置 AS_REDIS_URL")
        try:
            from redis import Redis
        except ImportError as exc:
            raise RuntimeError("Redis 状态需要 redis：请 `uv sync --extra state`") from exc
        self._redis = Redis.from_url(url, decode_responses=True, socket_timeout=5.0)

    # ---- 探活 ----
    def ping(self) -> bool:
        try:
            return bool(self._redis.ping())
        except Exception:  # noqa: BLE001 - 探活失败按不可用处理
            return False

    def close(self) -> None:
        self._redis.close()

    # ---- 单飞锁 / 心跳 ----
    def acquire(self, name: str, *, ttl_s: int) -> str | None:
        """SET NX EX；成功返回令牌，失败返回 None。"""
        token = uuid.uuid4().hex
        ok = self._redis.set(f"{self.prefix}{name}", token, nx=True, ex=max(1, ttl_s))
        return token if ok else None

    def renew(self, name: str, token: str, *, ttl_s: int) -> bool:
        """仅当持有者仍是 token 时续期（Lua 比较并设置）。"""
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"
        )
        return bool(
            self._redis.eval(script, 1, f"{self.prefix}{name}", token, max(1, ttl_s))
        )

    def release(self, name: str, token: str) -> None:
        script = (
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end"
        )
        self._redis.eval(script, 1, f"{self.prefix}{name}", token)

    def exists(self, name: str) -> bool:
        return bool(self._redis.exists(f"{self.prefix}{name}"))

    # ---- 会话占用计数（按用户）----
    def add_busy(self, owner_id: str, session_id: str) -> None:
        self._redis.sadd(f"{self.prefix}busy:{owner_id}", session_id)
        self._redis.expire(f"{self.prefix}busy:{owner_id}", 24 * 3600)

    def remove_busy(self, owner_id: str, session_id: str) -> None:
        self._redis.srem(f"{self.prefix}busy:{owner_id}", session_id)

    def busy_count(self, owner_id: str) -> int:
        return int(self._redis.scard(f"{self.prefix}busy:{owner_id}"))

    # ---- 通用计数 ----
    def incr(self, name: str, *, ttl_s: int | None = None) -> int:
        value = int(self._redis.incr(f"{self.prefix}{name}"))
        if ttl_s:
            self._redis.expire(f"{self.prefix}{name}", ttl_s)
        return value

    def get(self, name: str) -> Any:
        return self._redis.get(f"{self.prefix}{name}")

    def set(self, name: str, value: str, *, ttl_s: int | None = None) -> None:
        self._redis.set(f"{self.prefix}{name}", value, ex=ttl_s)

    def delete(self, name: str) -> None:
        self._redis.delete(f"{self.prefix}{name}")


class RedisWindowLimiter:
    """固定窗口计数限流（多 worker 共享）。

    与进程内滑窗相比有最多一个窗口的边界误差，换取跨进程一致计数；
    `limit` 个请求以内的正常使用不受影响。
    """

    def __init__(self, state: RedisState, limit: int, window_s: float = 60.0):
        self.state = state
        self.limit = max(1, limit)
        self.window_s = max(1.0, window_s)

    def allow(self, key: str) -> bool:
        bucket = int(time.time() // self.window_s)
        count = self.state.incr(f"limit:{key}:{bucket}", ttl_s=int(self.window_s) + 2)
        return count <= self.limit

    def reset(self) -> None:
        """多 worker 下无法精确清空；保留接口供调用方统一处理。"""
