from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

HIDDEN_TEST_CMD = [
    "python",
    "-m",
    "unittest",
    "discover",
    "-s",
    "/tests",
    "-t",
    "/tests",
    "-v",
]

WORKSPACE_TEST_CMD = [
    "python",
    "-m",
    "unittest",
    "discover",
    "-s",
    "/workspace/tests",
    "-t",
    "/workspace",
    "-v",
]


@dataclass
class SandboxResult:
    exit_code: int
    output: str
    timed_out: bool = False


PathMap = list[tuple[str, str]]

# Docker Desktop（Windows）把宿主 named pipe 挂进 Linux 容器后是一个空目录，
# 容器内 docker CLI 一定连不上宿主 daemon。命中时给出可操作的提示。
_DAEMON_UNREACHABLE_MARKERS = (
    "permission denied while trying to connect to the Docker daemon socket",
    "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
)

DAEMON_HINT = (
    "\n[提示] 当前部署环境无法访问 Docker daemon。Windows 的 Docker Desktop 不能把"
    "宿主 named pipe 暴露为 Linux 容器内的 unix socket：请在宿主直跑 asagent，"
    "或改用 Linux 宿主部署（真实 /var/run/docker.sock 可挂载）。"
)


def _daemon_hint(output: str) -> str | None:
    """输出里出现连不上 daemon 的特征时返回运维提示，否则 None。"""
    if any(marker in output for marker in _DAEMON_UNREACHABLE_MARKERS):
        return DAEMON_HINT
    return None


def parse_path_map(raw: str | None) -> PathMap:
    """解析 AS_SANDBOX_PATH_MAP：`容器前缀=宿主前缀`，逗号分隔多组。

    容器部署时才需要：宿主 Docker daemon 解析的是宿主路径，而容器内
    workspace 是容器路径，必须做前缀替换。宿主前缀统一转成正斜杠。
    """
    pairs: PathMap = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        container, sep, host = item.partition("=")
        container = container.strip().rstrip("/")
        host = host.strip().replace("\\", "/").rstrip("/")
        if not sep or not container or not host:
            raise ValueError(
                f"AS_SANDBOX_PATH_MAP 格式错误：{item!r}（应为 容器前缀=宿主前缀）"
            )
        pairs.append((container, host))
    return pairs


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return "[前面的输出已截断]\n" + text[-limit:]


class DockerSandbox:
    """在隔离容器中运行工作区的单元测试。

    - 无网络、只读根文件系统、限制 CPU/内存/进程数；
    - 工作区读写挂载到 /workspace，隐藏测试只读挂载到 /tests；
    - 只执行固定的 unittest 命令，不经过 shell。
    """

    def __init__(
        self,
        *,
        image: str,
        workspace: Path,
        tests_dir: Path | None = None,
        test_command: list[str] | None = None,
        timeout_s: float = 90.0,
        max_output_chars: int = 8000,
        path_map: PathMap | None = None,
    ):
        self.image = image
        self.workspace = workspace.resolve()
        self.tests_dir = tests_dir.resolve() if tests_dir else None
        self.path_map = list(path_map or [])
        self._default_command = test_command
        self.timeout_s = timeout_s
        self.max_output_chars = max_output_chars
        # 固定容器名，超时后可显式 kill（杀掉 docker CLI 不会自动停容器）
        self.container_name = (
            f"asagent-sandbox-{os.getpid()}-{os.urandom(4).hex()}"
        )

    @staticmethod
    def available() -> bool:
        return shutil.which("docker") is not None

    def _host_path(self, path: Path) -> str:
        """把容器内路径映射成宿主路径；未命中映射时原样返回 posix 路径。"""
        text = path.as_posix()
        for container, host in self.path_map:
            if text == container or text.startswith(container + "/"):
                return host + text[len(container) :]
        return text

    def test_command(self) -> list[str]:
        if self._default_command:
            return list(self._default_command)
        return HIDDEN_TEST_CMD if self.tests_dir else WORKSPACE_TEST_CMD

    def build_args(self, command: list[str] | None = None) -> list[str]:
        args = [
            "docker",
            "run",
            "--rm",
            "--name",
            self.container_name,
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "512m",
            "--pids-limit",
            "128",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=64m",
            "-e",
            "PYTHONDONTWRITEBYTECODE=1",
            "-e",
            "PYTHONPATH=/workspace",
            "-v",
            f"{self._host_path(self.workspace)}:/workspace:rw",
        ]
        if self.tests_dir is not None:
            args += ["-v", f"{self._host_path(self.tests_dir)}:/tests:ro"]
        args += ["-w", "/workspace", self.image]
        args += list(command or self.test_command())
        return args

    async def run_tests(self) -> SandboxResult:
        if not self.available():
            return SandboxResult(
                exit_code=-2,
                output="Docker 不可用：未找到 docker 命令。"
                "请启动 Docker Desktop 并拉取沙箱镜像后重试。",
            )
        self.workspace.mkdir(parents=True, exist_ok=True)
        try:
            proc = await asyncio.create_subprocess_exec(
                *self.build_args(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except OSError as exc:
            return SandboxResult(exit_code=-2, output=f"无法启动 Docker 沙箱：{exc}")

        try:
            raw, _ = await asyncio.wait_for(proc.communicate(), timeout=self.timeout_s)
        except TimeoutError:
            await self._stop_container()
            proc.kill()
            raw, _ = await proc.communicate()
            return SandboxResult(
                exit_code=-1,
                output=_tail(raw.decode("utf-8", "replace"), self.max_output_chars)
                + "\n[测试执行超时，已终止容器]",
                timed_out=True,
            )

        code = proc.returncode if proc.returncode is not None else -1
        output = _tail(raw.decode("utf-8", "replace"), self.max_output_chars)
        hint = _daemon_hint(output)
        if hint:
            output += hint
        return SandboxResult(exit_code=code, output=output)

    async def _stop_container(self) -> None:
        """尽力停止容器；失败（已退出/被 --rm 删除）时忽略。"""
        try:
            kill = await asyncio.create_subprocess_exec(
                "docker",
                "kill",
                self.container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(kill.wait(), timeout=10)
        except (OSError, TimeoutError):
            return
