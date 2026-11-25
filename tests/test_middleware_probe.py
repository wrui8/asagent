from __future__ import annotations

import socket

from asagent.api.middleware import parse_endpoint, probe_middleware, probe_tcp
from asagent.config import Settings


def _blank_settings(**overrides) -> Settings:
    """不读 .env，且显式清空所有中间件地址（init 参数优先于环境变量）。"""
    values = {
        "as_pg_dsn": "",
        "as_redis_url": "",
        "as_rabbitmq_url": "",
        "as_milvus_uri": "",
        "as_tei_url": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _listening_socket() -> socket.socket:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    return server


def _closed_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_parse_endpoint_returns_none_for_empty() -> None:
    assert parse_endpoint("", 5432) is None
    assert parse_endpoint("   ", 5432) is None


def test_parse_endpoint_uses_default_port() -> None:
    assert parse_endpoint("postgresql://u:p@db.internal/asagent", 5432) == (
        "db.internal",
        5432,
    )
    assert parse_endpoint("redis://127.0.0.1/0", 6379) == ("127.0.0.1", 6379)
    assert parse_endpoint("amqp://u:p@mq/", 5672) == ("mq", 5672)
    assert parse_endpoint("http://milvus:19530", 19530) == ("milvus", 19530)


def test_parse_endpoint_keeps_explicit_port_and_bare_host() -> None:
    assert parse_endpoint("postgresql://u:p@db:5433/asagent", 5432) == ("db", 5433)
    assert parse_endpoint("127.0.0.1:8081", 8081) == ("127.0.0.1", 8081)


def test_parse_endpoint_rejects_garbage() -> None:
    assert parse_endpoint("http://", 19530) is None
    assert parse_endpoint("redis://host:not-a-port", 6379) is None


def test_probe_tcp_up_and_down() -> None:
    server = _listening_socket()
    try:
        port = server.getsockname()[1]
        ok, latency_ms = probe_tcp("127.0.0.1", port, timeout=0.5)
        assert ok is True
        assert latency_ms >= 0
    finally:
        server.close()

    ok, latency_ms = probe_tcp("127.0.0.1", _closed_port(), timeout=0.3)
    assert ok is False
    assert latency_ms >= 0


def test_probe_middleware_marks_unconfigured() -> None:
    result = probe_middleware(_blank_settings())
    assert set(result) == {"postgres", "redis", "rabbitmq", "milvus", "tei"}
    for name, item in result.items():
        assert item == {"configured": False, "status": "not_configured"}, name


def test_probe_middleware_reports_up_and_down() -> None:
    server = _listening_socket()
    up_port = server.getsockname()[1]
    down_port = _closed_port()
    try:
        settings = _blank_settings(
            as_pg_dsn=f"postgresql://user:secret@127.0.0.1:{up_port}/asagent",
            as_redis_url=f"redis://127.0.0.1:{down_port}/0",
        )
        result = probe_middleware(settings, timeout=0.3)
    finally:
        server.close()

    assert result["postgres"]["status"] == "up"
    assert result["postgres"]["host"] == "127.0.0.1"
    assert result["postgres"]["port"] == up_port
    assert result["redis"]["status"] == "down"
    assert result["rabbitmq"]["status"] == "not_configured"

    # 连接串中的密码绝不能出现在返回结果里
    assert "secret" not in repr(result)


def test_probe_middleware_empty_dsn_is_not_configured() -> None:
    settings = _blank_settings(as_pg_dsn="")
    assert probe_middleware(settings)["postgres"]["status"] == "not_configured"
