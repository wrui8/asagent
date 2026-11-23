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
    ):
        self.image = image
        self.workspace = workspace.resolve()
        self.tests_dir = tests_dir.resolve() if tests_dir else None
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
            f"{self.workspace.as_posix()}:/workspace:rw",
        ]
        if self.tests_dir is not None:
            args += ["-v", f"{self.tests_dir.as_posix()}:/tests:ro"]
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
        return SandboxResult(
            exit_code=code,
            output=_tail(raw.decode("utf-8", "replace"), self.max_output_chars),
        )

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
