# asagent

学习型企业级 Agent 底座：手写运行时 + 场景包（Profile）+ 评测闭环。

架构设计见 [docs/architecture.md](docs/architecture.md)。

## 环境准备

```bash
# 1. Python 3.12 + 依赖
uv python pin 3.12
uv sync
# 国内网络如遇 hatchling 下载超时：
# UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple uv sync

# 2. 密钥
cp .env.example .env    # 填写 DEEPSEEK_API_KEY（当前只用 DeepSeek）

# 3. RAG 场景依赖（CPU 版 torch + bge，可选）
uv sync --extra rag

# 4. Code 场景依赖（Docker + 沙箱镜像）
docker pull python:3.12-slim

# 5. 自检
uv run asagent doctor
```

## 本地 vLLM（可选，分流的本地 provider）

```powershell
# PowerShell 中执行（避开 Git Bash 的路径改写）
docker run -d --name vllm-local --gpus all -p 8000:8000 `
  -v D:/fishmatrix-llm/models:/models `
  vllm/vllm-openai:v0.10.2 `
  --model /models/Qwen2.5-7B-Instruct-AWQ `
  --served-model-name qwen2.5-7b-awq `
  --max-model-len 4096 `
  --gpu-memory-utilization 0.82 `
  --max-num-seqs 4 `
  --enable-auto-tool-choice `
  --tool-call-parser hermes
```

验证：`curl http://127.0.0.1:8000/v1/models` 应返回 `qwen2.5-7b-awq`。

8GB 显存 OOM 时按顺序降级：`--max-model-len 3072` → `--max-num-seqs 1` → `--enforce-eager` → `--max-model-len 2048`。

实测（RTX 4060 Laptop 8GB，2026-11）：

| 项 | 值 |
| --- | --- |
| 镜像 | `vllm/vllm-openai:v0.10.2` |
| digest | `sha256:607442e407b0fea97f8a132a78b787c121a996dd4de181fa08e8da06e71ec2db` |
| 权重加载 | ≈69s（WSL2 直挂 D 盘） |
| 服务就绪 | ≈3min（含 torch.compile） |
| 工具调用 | `--tool-call-parser hermes` 返回合法 `tool_calls`，已验证 |

## 常用命令

```bash
uv run asagent doctor
uv run asagent demo                         # 生成 data/demo.sqlite3
uv run asagent run "统计2024年各月销售额" --profile sql
uv run asagent run "..." --profile sql --model qwen2.5-7b-awq   # 强制本地模型
uv run asagent resume <session_id>
uv run asagent trace <session_id> [--json]
uv run asagent eval --profile sql [--repeat 3] [--models a,b] [--limit N]

uv run asagent rag-index                      # 构建/刷新 RAG 向量索引（文件名变化会自动重建）
uv run asagent run "P1 故障多久响应？" --profile rag
uv run asagent eval --profile rag --models deepseek-chat

uv run asagent run "修复 src/stats.py 的 average" --profile code \
  --workspace D:\path\to\repo --tests D:\path\to\hidden-tests   # 沙箱内读改代码 + 跑 unittest
uv run asagent eval --profile code --models deepseek-chat

uv run asagent run "把 meeting.md 整理成 summary.md" --profile office
uv run asagent run "..." --profile office --yes     # 显式自动批准危险写操作（慎用）
uv run asagent approve <session_id> [--call ID | --all | --deny]   # 审批挂起的危险操作
uv run asagent audit [session_id]                                  # 查看写操作审计
uv run asagent eval --profile office --models deepseek-chat

uv run asagent mcp-list --profile assistant                 # 启动 MCP server 并列出工具
uv run asagent run "用 add 计算 2+3" --profile assistant    # 工具全部来自 MCP
uv run asagent run "统计2024年各月销售额" --profile sql --max-cost 0.5   # 成本上限（元）
```

办公场景写操作（`write_document` / `update_csv_cell` / `delete_document`）统一走人工审批：
交互终端默认逐条确认（默认否）；非交互终端（管道/CI）中会话会挂起为 `awaiting_approval`，
用 `asagent approve <session>` 决策后再 `asagent resume <session>` 继续。每次写入前自动备份到
`runtime/backups/<session_id>/`，审计记录见 `asagent audit`。

评测报告写入 `runtime/evals/`（Markdown + JSON）。

## MCP 工具接入

`assistant` profile 的静态工具为空，工具全部由外部 MCP server（stdio）提供：

```yaml
tool_config:
  mcp_servers:
    - name: demo
      command: python
      args: [scripts/mcp_demo_server.py]
      timeout_s: 30
      permission: 0          # 0=READ 1=WRITE 2=DANGEROUS，可按工具覆盖
```

- 工具名统一为 `mcp__<server>__<tool>`，远端 JSON Schema 动态转成 Pydantic 输入模型
- server 启动失败直接报错（fail-closed），不会静默退化成「没有工具」
- 子进程只继承白名单环境变量，API key 不会透传给第三方 server
- MCP 工具沿用注册表的权限、审批与超时机制；DANGEROUS 工具同样进入 HITL 流程

## 预算上限

优先级：`--max-cost` > profile 的 `budget.max_cost_cny` > 环境变量 `AS_MAX_COST_CNY` > 不限。
每次模型调用后检查累计成本，超限立即结束为 `failed` 并写入 `budget_exceeded` 事件，不再调用工具。

## 测试与 CI

```bash
uv run pytest -q
uv run ruff check .
bash scripts/ci.sh        # 本地等价于 GitHub Actions 门禁（含 uv sync）
```

`.github/workflows/ci.yml` 在 `ubuntu-latest` 上执行 `uv sync --frozen --extra rag`
→ `ruff check .` → `pytest -q`。仓库尚未初始化 git，推送后 workflow 才会实际触发。

## 评测基线

| 场景 | 模型 | 通过率 | 说明 |
| --- | --- | ---: | --- |
| SQL（6 条） | `deepseek-chat`（云端） | 6/6（100%） | ≈¥0.07 / 轮；安全用例 2/2，数据库哈希不变 |
| SQL（6 条） | `qwen2.5-7b-awq`（本地） | 5/6（83.3%） | ¥0；唯一失败为毛利率用例返回 6 个品类而非前 3，属 7B 指令遵循限制 |
| RAG（10 条） | `deepseek-chat`（云端） | 10/10（100%） | ≈¥0.08 / 轮；引用与注入用例全部通过 |
| RAG（10 条） | `qwen2.5-7b-awq`（本地） | 10/10（100%） | ¥0；依赖 `require_tool_before_final` 强制先检索 |
| Code（5 条） | `deepseek-chat`（云端） | 5/5（100%） | ≈¥0.10 / 轮；Docker 沙箱内测试-修复闭环 |
| Code（5 条） | `qwen2.5-7b-awq`（本地） | 2/5（40%） | ¥0；多次运行 2–3/5，失败以「改错逻辑」和 768 输出上限为主 |
| Office（5 条） | `deepseek-chat`（云端） | 5/5（100%） | ≈¥0.06 / 轮；非交互拒绝、注入抵御均通过，写前备份可回查 |
| Office（5 条） | `qwen2.5-7b-awq`（本地） | 3/5（60%） | ¥0；2 条因 7B 提前结束、未真正调用写工具；3 条安全用例全过 |

当前云端只接入 DeepSeek；百炼（qwen 系列）配置保留但未启用，路由升级目标为 `deepseek-chat`。迭代过程与根因分析见 [docs/eval-baseline.md](docs/eval-baseline.md)。

## 目录

```text
src/asagent/    内核源码（models / runtime / tools / guard / eval / obs / profiles）
configs/        模型注册表
evals/          黄金评测集
data/           demo 数据库（生成物）
runtime/        状态库、trace、artifacts、backups、eval-workspaces（生成物）
docs/           架构文档
tests/          测试
```
