from __future__ import annotations

import subprocess

import pytest

from asagent.tools.code.sandbox import DockerSandbox

IMAGE = "python:3.12-slim"


def _docker_ready() -> bool:
    if not DockerSandbox.available():
        return False
    probe = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, check=False
    )
    return probe.returncode == 0


pytestmark = pytest.mark.skipif(
    not _docker_ready(), reason="需要 Docker 与 python:3.12-slim 镜像"
)


@pytest.fixture
def project(tmp_path):
    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "mod.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text(
        "import unittest\n\n"
        "from src.mod import add\n\n\n"
        "class AddTest(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(2, 3), 5)\n",
        encoding="utf-8",
    )
    return ws, tests


async def test_hidden_tests_fail_then_pass(project):
    ws, tests = project
    box = DockerSandbox(image=IMAGE, workspace=ws, tests_dir=tests, timeout_s=60)

    first = await box.run_tests()
    assert first.exit_code != 0
    assert "FAILED" in first.output.upper()

    (ws / "src" / "mod.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    second = await box.run_tests()
    assert second.exit_code == 0
    assert "OK" in second.output


async def test_timeout_kills_container(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    box = DockerSandbox(
        image=IMAGE,
        workspace=ws,
        test_command=["python", "-c", "import time; time.sleep(30)"],
        timeout_s=2,
    )
    result = await box.run_tests()
    assert result.timed_out
    assert result.exit_code == -1
