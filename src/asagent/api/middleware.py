"""中间件可达性探活（阶段 0）。

只做 TCP 连接探测，不引入 PostgreSQL / Redis / RabbitMQ 客户端依赖：
- 未配置的地址返回 ``not_configured``，不视为故障；
- 已配置但连不上返回 ``down``，并带上耗时，便于系统页排障；
- 已配置项并行探测，最坏耗时由单项超时决定。

返回结果只包含 host / port，绝不回显连接串中的密码。
"""

from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from ..config import Settings

# 服务名 -> (Settings 字段名, 默认端口)
TARGETS: dict[str, tuple[str, int]] = {
    "postgres": ("as_pg_dsn", 5432),
    "redis": ("as_redis_url", 6379),
    "rabbitmq": ("as_rabbitmq_url", 5672),
    "milvus": ("as_milvus_uri", 19530),
    "tei": ("as_tei_url", 8081),
}

DEFAULT_TIMEOUT_S = 1.5


def parse_endpoint(url: str, default_port: int) -> tuple[str, int] | None:
    """从连接串解析 ``(host, port)``；空串或无法解析时返回 ``None``。

    支持 ``postgresql://``、``redis://``、``amqp://``、``http://`` 等形态；
    省略端口时使用该服务的默认端口。
    """
    value = (url or "").strip()
    if not value:
        return None
    # 允许 ``host:port`` 这类不带 scheme 的写法
    if "://" not in value:
        value = f"//{value}"
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not host:
        return None
    return host, port or default_port


def probe_tcp(
    host: str, port: int, timeout: float = DEFAULT_TIMEOUT_S
) -> tuple[bool, float]:
    """尝试建立 TCP 连接，返回 ``(是否可达, 耗时毫秒)``。"""
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
    except OSError:
        return False, round((time.perf_counter() - started) * 1000, 1)
    return True, round((time.perf_counter() - started) * 1000, 1)


def probe_middleware(
    settings: Settings, *, timeout: float = DEFAULT_TIMEOUT_S
) -> dict[str, dict]:
    """探测全部已配置的中间件；未配置项返回 ``not_configured``。"""
    result: dict[str, dict] = {}
    pending: dict[str, tuple[str, int]] = {}

    for name, (field, default_port) in TARGETS.items():
        endpoint = parse_endpoint(getattr(settings, field, ""), default_port)
        if endpoint is None:
            result[name] = {"configured": False, "status": "not_configured"}
        else:
            result[name] = {"configured": True, "status": "down"}
            pending[name] = endpoint

    if pending:
        with ThreadPoolExecutor(max_workers=len(pending)) as pool:
            futures = {
                name: pool.submit(probe_tcp, host, port, timeout)
                for name, (host, port) in pending.items()
            }
            for name, (host, port) in pending.items():
                ok, latency_ms = futures[name].result()
                result[name] = {
                    "configured": True,
                    "status": "up" if ok else "down",
                    "host": host,
                    "port": port,
                    "latency_ms": latency_ms,
                }

    return result


__all__ = ["DEFAULT_TIMEOUT_S", "TARGETS", "parse_endpoint", "probe_middleware", "probe_tcp"]
