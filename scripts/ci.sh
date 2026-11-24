#!/usr/bin/env bash
# 本地等价于 .github/workflows/ci.yml 的门禁命令（Git Bash / Linux / macOS 通用）。
#   bash scripts/ci.sh          # 后端：uv sync + ruff + pytest
#   bash scripts/ci.sh --full   # 额外跑 web / app / e2e（与 CI 四个 job 对齐）
set -euo pipefail

cd "$(dirname "$0")/.."

export PYTHONUTF8=1

echo "==> 安装依赖（含 RAG 场景包）"
uv sync --frozen --extra rag

echo "==> ruff"
uv run ruff check .

echo "==> pytest"
uv run pytest -q

if [[ "${1:-}" == "--full" ]]; then
  echo "==> web typecheck + build"
  (cd web && npm ci && npm run typecheck && npm run build)

  echo "==> app typecheck"
  (cd app && npm ci && npx tsc --noEmit)

  echo "==> e2e"
  (cd e2e && npm ci && npx playwright test)
fi
