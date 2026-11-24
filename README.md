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

生产运行用 Docker 容器 + Cloudflare 隧道，部署、自启、备份与排障见
[docs/deploy.md](docs/deploy.md)。以下 `uv run` 方式适合本地开发。

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

uv run asagent web --port 8000                              # 多用户 Web API（见下节）

uv run python scripts/backup.py --keep 7                    # 备份 state.db/docs/configs/evals
```

办公场景写操作（`write_document` / `update_csv_cell` / `delete_document`）统一走人工审批：
交互终端默认逐条确认（默认否）；非交互终端（管道/CI）中会话会挂起为 `awaiting_approval`，
用 `asagent approve <session>` 决策后再 `asagent resume <session>` 继续。每次写入前自动备份到
`runtime/backups/<session_id>/`，审计记录见 `asagent audit`。

评测报告写入 `runtime/evals/`（Markdown + JSON）。

## Web API（多用户）

```bash
export AS_JWT_SECRET=$(python -c "import secrets;print(secrets.token_urlsafe(48))")
uv run asagent web-init-admin -u admin          # 交互输入密码（≥12 位）
uv run asagent web --host 0.0.0.0 --port 8000   # API 文档：/api/docs
```

### 生产部署（Docker + Cloudflare 隧道）

```powershell
docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml exec api /app/.venv/bin/asagent web-init-admin -u admin
```

容器监听 `127.0.0.1:8080`，隧道把 `https://agent.woaiwyj.xin` 指向它；宿主
`runtime/ data/ configs/ evals/` 以卷挂载。开机自启、cloudflared 服务、每日备份
（保留 7 天）与恢复步骤见 [docs/deploy.md](docs/deploy.md)。

> 已实测：Windows Docker Desktop 无法把 named pipe 暴露给 Linux 容器
> （容器内只是一个空目录），因此**容器部署下 code 场景不可用**，需在宿主直跑
> `uv run asagent web` 或改用 Linux 宿主；SQL/RAG/Office 等场景不受影响。

- **必须单 worker**：任务调度器在进程内，多 worker 会导致同一会话被并行执行
- 账号通过**邀请码**注册：管理员 `POST /api/invites` 生成，`POST /api/auth/register` 消费
- 鉴权：JWT access（默认 30 分钟，放 `Authorization: Bearer`）+ refresh token
  （默认 30 天，存储的是 SHA-256 哈希，刷新即轮换）。Web 走
  httpOnly/SameSite=Strict Cookie；桌面端/移动端分别带 `X-Client: desktop`/`mobile`
  时改为响应体返回，存入 Windows Credential Manager 或 SecureStore
- 会话：`POST /api/sessions` 入队执行，`GET /api/sessions/{id}/stream` 是 SSE 事件流
  （`agent_event` + `done`，带心跳），移动端改用 `GET /api/sessions/{id}/events?after_seq=`
  轮询；审批走 `GET/POST /api/sessions/{id}/approvals`
- 配额四层：单会话成本、用户日成本、用户并发、全局并发信号量；`code` 场景默认仅管理员可用
- 越权一律返回 404（不泄露会话是否存在）；禁止管理员禁用或降级自己
- `web/dist/index.html` 存在时自动托管前端 SPA，否则只提供 API
- 公网部署必须设置 `AS_ALLOWED_ORIGINS`（同时启用 CORS 与写请求 Origin 校验）与
  `AS_COOKIE_SECURE=true`；完整变量见 `.env.example`

### Web 前端（React + Vite）

```bash
cd web
npm install
npm run dev        # http://127.0.0.1:5173，/api 代理到 127.0.0.1:8000
npm run typecheck
npm run build      # 产出 web/dist，后端启动后自动托管
```

- 页面：会话列表、对话（SSE 实时轨迹 + 网页审批）、知识库、评测中心（admin）、
  审计、MCP 与系统（admin）、用户与邀请码
- access token 存 `sessionStorage`，refresh token 走 httpOnly Cookie；API 401 时单飞刷新并重试一次
- 本地 HTTP 调试需 `AS_COOKIE_SECURE=false`，公网部署必须恢复 `true`
- PWA 为手写 `web/public/manifest.webmanifest` + `web/public/sw.js`
  （导航 network-first、静态资源 cache-first、`/api/` 不缓存），不依赖 vite-plugin-pwa；
  浏览器地址栏/菜单可直接「安装到桌面」
- 图标由 `python scripts/gen_app_icons.py` 生成到 `web/public/icons/`
  （同时生成 `desktop/src-tauri/icons/`）

### 评测、审计与系统页

- **评测中心**（仅管理员）：`/api/evals/suites|status|jobs|reports|run`。
  触发评测时可选场景（有 `eval_suite` 的场景包）、模型（不选则按路由自动）、
  重复次数与用例上限；同一时刻只允许一个任务（并发请求返回 409）。
  每次成功评测生成 `runtime/evals/<profile>-<时间戳>.json|.md`，
  前端轮询任务状态并在完成后自动刷新报告列表
- **每日次数**：`user_quotas.eval_daily_limit` 控制每个账号每天可触发的评测数
  （0 表示不限），超限返回 429；管理员在「用户与邀请码」页的编辑弹窗中设置
- **审计**：`GET /api/audit` 读内核 `EventType.AUDIT` 事件；管理员可看全局，
  普通用户必须带 `session_id` 且仅自己的会话。页面展示工具、权限、路径、
  备份、sha256 与结果，可展开完整 JSON
- **MCP 与系统**（仅管理员）：`GET /api/system/info` 返回路径、provider、
  模型与密钥状态（布尔值）；`GET /api/system/mcp?profile=` 只读取场景包
  `tool_config.mcp_servers`，不启动子进程
- 验收记录（2026-11-23）：浏览器以管理员触发 SQL 单用例评测（deepseek-chat）
  并完成报告回看；审计页展示历史写操作；系统页显示 demo MCP 服务与 provider
  可用性；管理页显示「评测/日」列

### 桌面端（Tauri v2）

```bash
cd desktop
npm install
npm run dev        # 启动 web 开发服务器 + Tauri 窗口（API 走 Vite 代理）
npm run build      # 产出 Windows 安装包
```

构建依赖：Rust stable（`x86_64-pc-windows-msvc`）、Visual Studio 2022「使用 C++ 的桌面开发」
或 Build Tools、WebView2 Runtime。安装包位于
`desktop/src-tauri/target/release/bundle/nsis/`。

- 窗口加载 `web/dist`（打包前自动执行 `npm --prefix ../web run build`），CSP 禁止远程脚本
- **服务器地址在构建时写死**：来源为 `desktop/api-base.txt`（当前
  `https://agent.woaiwyj.xin`），由 `src-tauri/build.rs` 编译进二进制；登录页不提供
  输入框。更换地址需修改该文件与 `tauri.conf.json` 的 CSP 后重新打包。
  进程级 `ASAGENT_API_BASE` 可临时覆盖（仅本地联调/运维，不暴露给用户）
- 桌面端 refresh token 存 Windows Credential Manager，不落 `localStorage`；
  access token 仍只存 `sessionStorage`
- 后端 `AS_DESKTOP_CLIENTS=true`（默认）会放行 Tauri 来源并支持 body 刷新令牌；
  生产环境 `AS_ALLOWED_ORIGINS` 仍需填写 Web 域名
- 桌面端不注册 Service Worker

### 移动端（Expo）

```bash
cd app
npm install
npx expo start                     # Expo Go 扫码（真机走公网 API 地址）
npx expo start --web --port 8082   # 浏览器预览
```

- Expo SDK 58 + expo-router（`app/src/app`）+ TypeScript；页面：登录/邀请码注册、
  会话列表、新建任务、详情（轨迹 + 审批 + 最终答案 + 成本）
- **API 地址构建时写死**：`app/src/lib/config.ts` 默认 `https://agent.woaiwyj.xin`，
  UI 不提供输入框；联调可用 `EXPO_PUBLIC_API_BASE` 在打包时覆盖
- 认证：请求头 `X-Client: mobile`，refresh token 走响应体；原生端存 SecureStore，
  Web 预览降级 localStorage。启动时自动恢复登录，401 单飞刷新后重试一次
- 事件流用 `after_seq` 轮询（1.5s，终态停止）并按 seq 去重；审批提交后自动 resume
- 本地 Web 预览需后端放行源：`AS_ALLOWED_ORIGINS=http://localhost:8082`
- 构建需 Node ≥ 22.13（Expo SDK 58 要求）；iOS 分发需 Apple 账号，暂用 Expo Go
- 验收记录（2026-11-23）：Expo Web（390×844）完成登录、SQL 对话（本地模型）、
  刷新后自动登录、Office 审批→写文件；`tsc --noEmit` 通过；后端 ruff + 183 条
  pytest 全绿

### 知识库接口（`/api/kb`）

知识库是**全局共享**的：任何登录用户都能增删改，所有人共享同一份检索内容。
因此配套了以下约束：删除进回收站可恢复、保留版本历史可回滚、可疑文档（含提示注入
特征）会被标记、所有改动写入审计表、重建有单飞锁与冷却。

- 文档层：`GET/POST /api/kb/docs`、`GET/PUT/DELETE /api/kb/docs/{id}`；
  删除为软删（文件移入 `data/docs/.trash/`），`POST /api/kb/docs/{id}/restore` 恢复
- 版本：`GET /api/kb/docs/{id}/versions`、`POST /api/kb/docs/{id}/rollback`
- 索引层：文档保存后**不会立即**进入检索，需 `POST /api/kb/reindex`（202 异步执行，
  单飞 + 冷却，同一时刻只允许一个任务）；`GET /api/kb/status` 显示 `stale`/片段数与任务进度
- 重建按内容哈希增量执行：内容没变的文档复用已有向量，只有改动过的文档会重新编码；
  删除文档时会立即从索引移除，不必等重建
- `GET /api/kb/jobs`、`GET /api/kb/audit` 分别查看重建任务与操作记录

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
bash scripts/ci.sh        # 本地等价于后端 job（含 uv sync）
bash scripts/ci.sh --full # 追加 web/app/e2e；docker job 用 compose build 本地校验

cd web && npm run typecheck && npm run build
cd app && npx tsc --noEmit
cd e2e && npm ci && npx playwright test    # 独立 8179 端口 + 临时库，不调用模型
```

`.github/workflows/ci.yml` 在 `ubuntu-latest` 上并行跑五个 job：

| job | 内容 |
| --- | --- |
| `test` | `uv sync --frozen --extra rag` → `ruff check .` → `pytest -q` |
| `web` | `npm ci` → `typecheck` → `build` |
| `app` | `npm ci` → `npx tsc --noEmit` |
| `e2e` | 构建 web → 安装 Chromium → Playwright（失败上传报告） |
| `docker` | 构建生产镜像校验 Dockerfile（含 RAG，不推送） |

`.github/workflows/release.yml` 在推送 `v*` tag（或手动触发）时用 `windows-latest`
构建 Tauri NSIS 安装包，作为 artifact 上传并附到 GitHub Release。

### 维护流程（分支 → PR → 更新镜像）

`main` 受保护，不能直接推送。日常维护：

```powershell
git switch main; git pull --ff-only
git switch -c feat/<topic>          # 1. 从 main 拉分支
# 开发、测试、提交
git push -u origin feat/<topic>     # 2. 推分支并开 PR（gh pr create）
# 3. PR 通过 CI 后合并到 main
powershell -ExecutionPolicy Bypass -File deploy\update.ps1   # 4. 更新本机镜像并重启
```

`deploy\update.ps1` 会校验在 `main` 且工作区干净、`git pull --ff-only`、重建镜像、
重启容器并轮询 `/api/health`；`-NoPull` 可跳过 git 更新。

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
web/            React + Vite 控制台（后端托管 dist）
app/            Expo 移动端
desktop/        Tauri v2 桌面壳
deploy/         Dockerfile/compose、Windows/Linux 运维脚本
e2e/            Playwright 端到端测试（独立 Node 包）
configs/        模型注册表
evals/          黄金评测集
data/           demo 数据库与知识库文档
runtime/        状态库、trace、artifacts、backups、eval-workspaces（生成物）
backups/        每日备份产物（生成物，gitignored）
docs/           架构与部署文档
tests/          测试
```
