#!/usr/bin/env bash
# 本地等价于 .github/workflows/ci.yml 的门禁命令（Git Bash / Linux / macOS 通用）。
set -euo pipefail

cd "$(dirname "$0")/.."

export PYTHONUTF8=1

echo "==> 安装依赖（含 RAG 场景包）"
uv sync --frozen --extra rag

echo "==> ruff"
uv run ruff check .

echo "==> pytest"
uv run pytest -q
