from __future__ import annotations

from pathlib import Path

from asagent.tools.code.sandbox import (
    DAEMON_HINT,
    HIDDEN_TEST_CMD,
    WORKSPACE_TEST_CMD,
    DockerSandbox,
    _daemon_hint,
    _tail,
    parse_path_map,
)


def _box(tmp_path: Path, tests: bool = True) -> DockerSandbox:
    return DockerSandbox(
        image="python:3.12-slim",
        workspace=tmp_path / "ws",
        tests_dir=(tmp_path / "hidden-tests") if tests else None,
        timeout_s=30,
    )


def _value_after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


def test_build_args_isolates_container(tmp_path):
    box = _box(tmp_path)
    args = box.build_args()
    assert _value_after(args, "--network") == "none"
    assert _value_after(args, "--cpus") == "1"
    assert _value_after(args, "--memory") == "512m"
    assert _value_after(args, "--pids-limit") == "128"
    assert "--read-only" in args
    assert _value_after(args, "--tmpfs") == "/tmp:rw,size=64m"
    assert _value_after(args, "-w") == "/workspace"


def test_build_args_mounts_workspace_rw_and_tests_ro(tmp_path):
    box = _box(tmp_path)
    args = box.build_args()
    mounts = [
        args[i + 1]
        for i, a in enumerate(args)
        if a == "-v" and i + 1 < len(args) and ":/" in args[i + 1]
    ]
    ws_mount = next(m for m in mounts if m.endswith(":/workspace:rw"))
    tests_mount = next(m for m in mounts if m.endswith(":/tests:ro"))
    assert ws_mount.startswith((tmp_path / "ws").resolve().as_posix())
    assert tests_mount.startswith((tmp_path / "hidden-tests").resolve().as_posix())


def test_default_command_prefers_hidden_tests(tmp_path):
    args = _box(tmp_path).build_args()
    assert args[-len(HIDDEN_TEST_CMD) :] == HIDDEN_TEST_CMD


def test_default_command_uses_workspace_tests_without_hidden(tmp_path):
    args = _box(tmp_path, tests=False).build_args()
    assert args[-len(WORKSPACE_TEST_CMD) :] == WORKSPACE_TEST_CMD
    assert not any(m.endswith(":/tests:ro") for m in args if ":/" in m)


def test_custom_command_wins(tmp_path):
    box = DockerSandbox(
        image="img", workspace=tmp_path / "ws", test_command=["python", "-V"]
    )
    assert box.build_args()[-2:] == ["python", "-V"]


def test_parse_path_map_empty():
    assert parse_path_map("") == []
    assert parse_path_map(None) == []


def test_parse_path_map_windows_host_normalizes_slashes():
    pairs = parse_path_map(r"/app/runtime=D:\code\AS\runtime")
    assert pairs == [("/app/runtime", "D:/code/AS/runtime")]


def test_parse_path_map_multiple_pairs():
    pairs = parse_path_map("/app/runtime=D:/as/runtime, /app/data=D:/as/data")
    assert pairs == [
        ("/app/runtime", "D:/as/runtime"),
        ("/app/data", "D:/as/data"),
    ]


def test_parse_path_map_rejects_malformed_pair():
    import pytest

    with pytest.raises(ValueError):
        parse_path_map("/app/runtime")


def test_build_args_maps_workspace_to_host_path(tmp_path):
    ws = (tmp_path / "ws").resolve()
    box = DockerSandbox(
        image="python:3.12-slim",
        workspace=ws,
        tests_dir=tmp_path / "hidden-tests",
        path_map=[(ws.as_posix(), "C:/host/runtime/workspace/s1")],
    )
    args = box.build_args()
    # 注意 unittest 命令末尾也有 `-v`（verbosity），只取挂载形式的参数
    mounts = [
        args[i + 1]
        for i, a in enumerate(args)
        if a == "-v" and i + 1 < len(args) and ":/" in args[i + 1]
    ]
    assert mounts[0] == "C:/host/runtime/workspace/s1:/workspace:rw"


def test_host_path_requires_segment_boundary(tmp_path):
    ws = (tmp_path / "ws").resolve()
    box = DockerSandbox(
        image="img",
        workspace=ws,
        path_map=[(ws.as_posix() + "X", "C:/wrong")],
    )
    assert box._host_path(ws) == ws.as_posix()


def test_daemon_hint_detects_socket_permission_error():
    output = (
        "permission denied while trying to connect to the Docker daemon socket at "
        "unix:///var/run/docker.sock"
    )
    assert _daemon_hint(output) == DAEMON_HINT


def test_daemon_hint_detects_missing_daemon():
    output = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock."
    assert _daemon_hint(output) == DAEMON_HINT


def test_daemon_hint_ignores_normal_test_output():
    assert _daemon_hint("FAILED (failures=1)") is None


def test_tail_keeps_last_characters():
    text = "a" * 100 + "TAIL"
    result = _tail(text, 10)
    assert result.endswith("a" * 6 + "TAIL")
    assert len(result) <= 10 + len("[前面的输出已截断]\n")
