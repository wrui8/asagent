# 部署与运维（Docker + Cloudflare 隧道）

生产形态：本机 Windows 上用 Docker 跑 API 容器，Cloudflare named tunnel 把
`https://agent.woaiwyj.xin` 指向 `127.0.0.1:8080`。Web/桌面/移动端共用这一个后端。

```text
Internet
  │  HTTPS
  ▼
Cloudflare 边缘  ──  named tunnel（QUIC）──▶  cloudflared（Windows 服务）
                                                  │ http://127.0.0.1:8080
                                                  ▼
                                        asagent-api 容器 :8000
                                          ├─ /app/runtime  ← ../runtime
                                          ├─ /app/data     ← ../data
                                          ├─ /app/configs  ← ../configs (ro)
                                          ├─ /app/evals    ← ../evals (ro)
                                          ├─ /models       ← BGE 模型目录 (ro)
                                          └─ /var/run/docker.sock ← 宿主 Docker（仅 Linux 有效）
```

code 沙箱在容器内调用**宿主** Docker：`AS_SANDBOX_PATH_MAP` 把容器里的
`/app/runtime` 前缀换成宿主真实路径（默认 `D:/code/AS/runtime`），否则宿主
daemon 找不到要挂载的目录。

> **Windows 限制（已实测）**：Docker Desktop 把宿主 named pipe
> `//./pipe/docker_engine` 挂载进 Linux 容器后只是一个空目录（`0o40755`），
> 不是 unix socket，容器内 `docker` 必然报
> `permission denied ... /var/run/docker.sock`。因此**本机 Windows 容器部署下
> code 场景不可用**（错误信息会附带提示），其余场景不受影响。code 场景请：
> 1) 停容器后在宿主直跑 `uv run asagent web`（默认 8080，隧道可直接使用）；
> 2) 或改用 Linux 宿主部署，`DOCKER_SOCKET=/var/run/docker.sock` 可正常工作；
> 3) 如果坚持容器化，可自行开启 Docker Desktop 的 TCP daemon（2375）并用
>    `DOCKER_HOST=tcp://host.docker.internal:2375`，但这等于向本机网络暴露
>    无 TLS 的 Docker API，需自行评估风险，本仓库默认不启用。

## 1. 前置条件

- Docker Desktop（Linux 容器模式）已启动，`docker info` 正常
- uv、Node.js 22（前端构建与 E2E 用）
- 本机已有 Cloudflare named tunnel 配置 `runtime/cloudflared-asagent.yml`
  （含 tunnel 凭据引用，**不要提交或外发**）
- bge embedding 模型目录（默认 `D:/fishmatrix-llm/models/bge-base-zh-v1.5`）

## 2. 首次部署

```powershell
# 1) 应用配置（.env 已被 gitignore）
Copy-Item .env.example .env
#    必填：DEEPSEEK_API_KEY、AS_JWT_SECRET（随机 64 字节）
#    python -c "import secrets;print(secrets.token_urlsafe(48))"
#    公网运行：AS_COOKIE_SECURE=true、AS_ALLOWED_ORIGINS=https://agent.woaiwyj.xin
#    AS_DESKTOP_CLIENTS=true（桌面端走 body 刷新令牌）

# 2) 可选：compose 插值变量写入 deploy/.env（或当前 shell 环境变量）
#    AS_SANDBOX_PATH_MAP=/app/runtime=D:/code/AS/runtime
#    BGE_MODEL_DIR=D:/fishmatrix-llm/models
#    DOCKER_SOCKET=//./pipe/docker_engine

# 3) 启动：优先拉 CI 发布的镜像
docker compose -f deploy/docker-compose.yml pull
docker compose -f deploy/docker-compose.yml up -d
#    拉取失败（包私有或尚无镜像）时改源码构建（首次含 torch，耗时较长）：
#    powershell -ExecutionPolicy Bypass -File deploy\update.ps1 -NoPull

# 4) 健康检查与日志
curl.exe http://127.0.0.1:8080/api/health
docker compose -f deploy/docker-compose.yml ps
docker logs -f asagent-api
```

首次建管理员（账号入库后即可用邀请码注册其他用户）：

```powershell
docker compose -f deploy/docker-compose.yml exec api `
  /app/.venv/bin/asagent web-init-admin -u admin
```

> 切换前先停掉手工运行的 `uv run asagent web`（默认也占用 8080）。
> `netstat -ano | findstr :8080` 可查占用进程。

镜像组件可用构建参数裁剪：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `WITH_RAG` | `1` | `0` 构建不含 torch 的轻量镜像，RAG 场景不可用 |
| `WITH_DOCKER_CLI` | `1` | `0` 省略容器内 docker CLI，code 沙箱不可用；Windows 下即使为 `1` 也受上述 named pipe 限制 |
| `PIP_INDEX_URL` | 清华 PyPI 镜像 | 海外部署可设为 `https://pypi.org/simple` |
| `PYPI_FILES_MIRROR` | 清华 PyPI 镜像 | 替换 `uv.lock` 中 `files.pythonhosted.org` 的下载前缀；海外部署可置空 |
| `NPM_REGISTRY` | `https://registry.npmmirror.com` | 海外部署可设为 npmjs 官方源 |
| `APT_MIRROR` | 清华 Debian 镜像 | 空字符串表示使用官方源 |
| `TORCH_WHEEL_MIRROR` | 阿里云 PyTorch wheel 镜像 | 官方源 `download.pytorch.org` 国内常超时；置空改用官方 |

构建参数通过 compose 插值（shell 环境变量或 `deploy/.env`）覆盖，例如
`$env:PIP_INDEX_URL="https://pypi.org/simple"` 后再执行 `compose build`。
Dockerfile 不再依赖 `docker/dockerfile` 外部前端镜像，基础镜像为
`node:20-bookworm-slim` 与 `python:3.12-slim`。

## 3. 隧道与开机自启

隧道进程由 Cloudflare named tunnel 配置驱动，ingress 已把
`agent.woaiwyj.xin` 指向 `http://127.0.0.1:8080`。

```powershell
# 注册计划任务：登录启动栈 + 每日 03:17 备份（需管理员）
powershell -ExecutionPolicy Bypass -File deploy\windows\install-tasks.ps1

# 把 cloudflared 装成 Windows 服务（需管理员；凭据只在脚本内引用，不回显）
powershell -ExecutionPolicy Bypass -File deploy\windows\install-cloudflared-service.ps1

Get-Service cloudflared
Get-ScheduledTask -TaskName "ASAgent*"
```

- 容器 `restart: unless-stopped`，Docker Desktop 自启后会自动拉起
- `start-stack.ps1` 会等待 Docker daemon 就绪（最多 3 分钟）再 `compose up -d`
- 隧道日志：`runtime/cloudflared-asagent.log`；配置使用 QUIC（见文件头注释）
- 手工重启栈：`docker compose -f deploy/docker-compose.yml up -d`

## 4. 中间件（v2 分阶段启用）

`deploy/docker-compose.yml` 从 v2 起支持按 profile 启用中间件，**默认不启动**，
`api` 服务保持 v1 的 SQLite 单机行为。三组可独立启用，互不强制。

| profile | 服务 | 端口（仅绑 127.0.0.1） | 用途 |
| --- | --- | --- | --- |
| `state` | postgres / redis / rabbitmq | 5432 / 6379 / 5672、15672 | 权威库、缓存与锁、记忆队列 |
| `rag` | tei / milvus | 8081 / 19530、9091 | bge-m3 编码、向量检索 |

> 8 GB 显存实测仅剩约 285 MiB，TEI 必须使用 CPU 镜像；不要给 TEI 传 GPU 参数。
>
> Milvus 使用**内置 etcd + 本地磁盘存储**，不再单独部署 etcd 与 MinIO：
> MinIO 已停止在 Docker Hub 发布社区镜像（quay 仓库也需登录），而 Milvus 2.5
> 原生支持 `ETCD_USE_EMBED` 与 `common.storageType=local`，单容器即可运行，
> 内存占用更低。配置见 `deploy/milvus/embedEtcd.yaml` 与 `deploy/milvus/user.yaml`。

### 4.1 准备密码与目录

compose 插值变量写在 **`deploy/.env`**（与 compose 文件同目录，已被 gitignore）：

```dotenv
AS_PG_DB=asagent
AS_PG_USER=asagent
AS_PG_PASSWORD=<随机强密码>
AS_RABBITMQ_USER=asagent
AS_RABBITMQ_PASSWORD=<随机强密码>
BGE_M3_DIR=D:/fishmatrix-llm/models
```

应用连接串写在**项目根 `.env`**（容器内主机名用服务名；宿主直跑改 `127.0.0.1`，
其中 TEI 宿主端口为 `8081`，其余端口与容器一致）：

```dotenv
AS_PG_DSN=postgresql://asagent:<与上面相同>@postgres:5432/asagent
AS_REDIS_URL=redis://redis:6379/0
AS_RABBITMQ_URL=amqp://asagent:<与上面相同>@rabbitmq:5672/
AS_MILVUS_URI=http://milvus:19530
AS_TEI_URL=http://tei:80
```

> 两处密码必须一致；任一项缺失时 `--profile` 启动会直接报错（fail-closed），
> 不会以弱默认密码启动。

### 4.2 启动与探活

```powershell
# 存储与缓存
docker compose -f deploy/docker-compose.yml --profile state up -d

# 检索（需 BGE_M3_DIR 下已放置 bge-m3 权重，离线加载）
# TEI 首次启动要用 CPU 完成权重加载与预热，约 2–5 分钟；`--max-batch-tokens`
# 默认 2048，降低它会更快启动、更省内存（大请求由 TEI 拆成 micro-batch）
docker compose -f deploy/docker-compose.yml --profile rag up -d

# 只校验配置，不启动
docker compose -f deploy/docker-compose.yml --profile state --profile rag config -q
```

管理员登录后查看 `/api/system/info` 的 `middleware` 字段：
`up` / `down` / `not_configured`。阶段 0 只验证 TCP 可达；模型是否加载完成、
集合是否可用在阶段 1 用 TEI `/health` 与 Milvus 集合检查。

### 4.3 数据卷与备份

中间件数据使用 Docker named volume（`asagent_pgdata`、`asagent_redisdata` 等），
避开 Windows 绑定挂载的权限问题。

- **必须备份**：PG（`pg_dump`）、`data/docs`、`configs`、`evals`，过渡期还包括
  SQLite `state.db`。计划任务命令改为：

  ```powershell
  uv run python scripts/backup.py --keep 7 --middleware
  ```

- **不必备份**：Milvus（向量可由 PG 元数据 + `data/docs` 重建）、
  RabbitMQ（队列消息可重放）、Redis 派生缓存。
- **恢复顺序**：PG → `data/docs` → 启动服务 → 触发全量重建索引。

### 4.4 排障

| 现象 | 排查 |
| --- | --- |
| `--profile state up` 报缺变量 | `deploy/.env` 是否有 `AS_PG_PASSWORD` 等 |
| `middleware.postgres=down` | 容器是否 healthy；`.env` DSN 主机名（容器用 `postgres`，宿主用 `127.0.0.1`） |
| Milvus 起不来 | `docker logs asagent-milvus`；确认 `deploy/milvus/*.yaml` 已挂载；首次启动需 1–2 分钟 |
| TEI 启动即退出 | `BGE_M3_DIR` 下是否有 `bge-m3` 目录与权重；`docker logs asagent-tei` |
| TEI 长时间 `Warming up model` | CPU 预热耗时随 `AS_TEI_MAX_BATCH_TOKENS` 增长；保持 1024–2048，勿设 8192 |
| 端口冲突 | `netstat -ano \| findstr :5432`（依次替换端口） |
| 内存吃紧 | 先停 `rag` profile；调小 `AS_TEI_MAX_BATCH_TOKENS` / 客户端批大小（实测 TEI 常驻约 5.8 GB，Milvus 约 130 MB） |

### 4.5 切换到 PostgreSQL + 多 worker（阶段 2）

默认仍是 SQLite 单 worker；切换是显式操作，随时可回滚。

前置：

```powershell
# 宿主直跑时安装连接驱动与客户端
uv sync --extra rag --extra milvus --extra state
# 中间件
docker compose -f deploy/docker-compose.yml --profile state up -d
```

1) 先备份（含 PG/Redis 的中间件数据）：

```powershell
uv run python scripts/backup.py --keep 7 --middleware
```

2) 搬数据（目标非空会拒绝，确认覆盖加 `--force`）：

```powershell
# 预览各表行数，不写入
$env:AS_PG_DSN = "postgresql://asagent:<密码>@127.0.0.1:5432/asagent"
uv run python scripts/migrate_sqlite_to_pg.py --dry-run

# 正式迁移；逐表比较行数与内容哈希，不一致非零退出
uv run python scripts/migrate_sqlite_to_pg.py
```

3) 根 `.env` 打开开关（容器部署时主机名用服务名）：

```dotenv
AS_STORAGE_BACKEND=postgres
AS_PG_DSN=postgresql://asagent:<密码>@postgres:5432/asagent
AS_REDIS_URL=redis://redis:6379/0
AS_WORKERS=2
```

4) 重启并验证：

```powershell
docker compose -f deploy/docker-compose.yml up -d
# 宿主直跑：uv run asagent web --workers 2
```

- 管理员查看 `/api/system/info`：`middleware.postgres/redis` 为 `up`。
- 旧账号可直接登录，会话/知识库/评测历史都在。
- 多 worker 必须同时满足 `AS_STORAGE_BACKEND=postgres` 与 `AS_REDIS_URL`，
  否则 `asagent web` 直接退出（错误提示会写明缺哪项）。
- 备份计划任务改为 `uv run python scripts/backup.py --keep 7 --middleware`。

5) 回滚：把 `AS_STORAGE_BACKEND` 改回 `sqlite`、`AS_WORKERS=1` 后重启。
SQLite 在切换后不再更新，若要再次切回 PG 需重新跑迁移脚本。

### 4.6 切换到 LangGraph 引擎（阶段 3）

默认仍是 v1 手写循环；引擎由 `AS_ENGINE` 选择，API / CLI / 评测共用：

```dotenv
AS_ENGINE=langgraph                  # legacy（默认）| langgraph
AS_CHECKPOINTER=redis                # 可选：把每步摘要镜像到 asagent:ckpt:{session_id}
```

- 切引擎不影响存储与事件契约，前端、SSE、评测报告格式都不变。
- `AS_CHECKPOINTER=redis` 只写观察用摘要（TTL 24h），**不是**恢复依据；
  崩溃/审批恢复仍读 KernelStore 的会话状态。
- 验证：跑一次 `uv run asagent run "数据库里有哪些表？" -p sql`，事件流应包含
  `session_created → user_input → route_selected → context_built → model_response
  → tool_call → tool_result → … → final`。
- 回滚：`AS_ENGINE=legacy` 后重启即可，两个引擎共用同一套事件与存储。

### 4.7 启用记忆子系统（阶段 4）

默认关闭（`AS_MEMORY_ENABLED=false`），关闭时不发布消息、不召回、不启动 worker，
行为与 v1 完全一致。启用是 fail-closed 的：必须同时具备 **postgres + Redis +
RabbitMQ + Milvus/TEI**，缺任何一项 API 启动即报错。

前置：已按 §4.5 切到 PostgreSQL，且 `state`、`rag` 两个 profile 均健康。

```dotenv
# 项目根 .env
AS_MEMORY_ENABLED=true
AS_MEMORY_TOP_K=5                 # 每次注入的长期记忆条数
AS_MEMORY_DAILY_EXTRACT_LIMIT=20  # 每用户每日异步抽取上限（0 表示全部禁用）
AS_MEMORY_MODEL=deepseek-chat     # 抽取模型（需要可靠的 JSON 输出）
```

启动 worker（与 api 同一镜像，profile 为 `memory`）：

```powershell
docker compose -f deploy/docker-compose.yml --profile memory up -d memory-worker
docker logs -f asagent-memory-worker
```

夜间巩固（合并近似条目、衰减、归档弱记忆；不物理删除）：

```powershell
# 容器内一次性执行
docker compose -f deploy/docker-compose.yml --profile memory run --rm memory-worker `
  /app/.venv/bin/asagent worker memory --nightly

# 宿主直跑（已 uv sync --extra state --extra milvus，DSN 指向 127.0.0.1）
uv run asagent worker memory --nightly

# 注册每日 03:47 的计划任务（管理员 PowerShell，可与其他任务一起注册）
powershell -ExecutionPolicy Bypass -File deploy\windows\install-tasks.ps1 -WithMemoryNightly
```

触发规则：同一会话用户轮数 ≥ 3、30 秒去抖（同一会话）、每用户每日配额；
抽取在会话进入 `done` 后异步进行，不阻塞对话。重复消息不会重复抽取（按
`source_session` 去重）。

验收与排障：

```powershell
# 查看某用户的活跃记忆（按用户名；需 AS_MEMORY_ENABLED=true）
uv run asagent memory list --username <用户名> --limit 20
uv run asagent memory stats

# 队列积压 / 死信
docker exec asagent-rabbitmq rabbitmqctl list_queues name messages
# 消费失败的消息进入 memory.dlq；排查后手动转移回 memory.extract
```

- worker 宕机时消息留在 `memory.extract`，恢复后继续消费，不影响对话。
- 召回是 fail-open 的：Milvus/TEI 临时不可用时本轮不注入记忆并记录 warning，
  对话照常进行。
- 禁用：把 `AS_MEMORY_ENABLED` 改回 `false` 并停 worker
  （`docker compose --profile memory stop memory-worker`）。PG 记忆表与 Milvus
  collection 保留，重新开启后立即恢复。

## 5. 备份与恢复

备份内容：`runtime/state.db`（SQLite backup API，WAL 安全）、`data/docs`、
`configs`、`evals`；每次一个时间戳目录，含 `manifest.json`（sha256/大小）。
默认保留最近 7 份。

```powershell
uv run python scripts/backup.py --keep 7
# 产物：backups/YYYYmmdd-HHMMSS/
# 计划任务 ASAgentBackup 每日 03:17 执行同一命令，日志追加到 backups/backup.log
```

Linux 宿主用 `deploy/linux/backup.sh`（cron 调用，`BACKUP_KEEP` 可覆盖）：

```cron
17 3 * * * cd /srv/asagent && BACKUP_KEEP=7 bash deploy/linux/backup.sh >> /var/log/asagent-backup.log 2>&1
```

恢复步骤：

1. 停服务：`docker compose -f deploy/docker-compose.yml down`
2. 先把当前数据再备份一份（防止误操作）
3. 解包目标时间戳目录，将 `state.db` 放回 `runtime/`，`docs/` 放回 `data/docs`，
   `configs/`、`evals/` 放回原位
4. 启动：`docker compose -f deploy/docker-compose.yml up -d`
5. 校验：`/api/health`、管理员登录、会话列表与知识库可见

> `.env` 默认不备份（含密钥）。确需随备份保存时加 `--include-env`，并自行限制
> `backups/` 目录权限。

## 6. 升级与维护

`main` 受保护，维护走「开发分支 → PR → 合并」：

```powershell
git switch main; git pull --ff-only
git switch -c feat/<topic>
# 开发、测试、提交
git push -u origin feat/<topic>
gh pr create --base main --fill      # 或用网页创建 PR
# CI 的 test/web/app/e2e/docker 五个 job 全绿后合并
```

镜像构建在 CI 完成：`main` 每推一次，`ci.yml` 的 `docker` job 就构建并推送
`ghcr.io/wrui8/asagent:latest` 与 `ghcr.io/wrui8/asagent:sha-<短哈希>`。
本机只负责拉取与重启，不再本地构建。

```powershell
# 立即拉取最新镜像并重启（带健康检查；digest 没变就直接返回）
powershell -ExecutionPolicy Bypass -File deploy\pull-update.ps1

# 固定版本 / 回滚
powershell -ExecutionPolicy Bypass -File deploy\pull-update.ps1 -Image ghcr.io/wrui8/asagent:sha-abc1234
```

镜像更新默认是手动的：`install-tasks.ps1` 只注册开机启动与每日备份，不再注册
`ASAgentAutoUpdate`（交互式会话下每 10 分钟会闪一次控制台窗口）。

确实需要自动轮询时，用管理员 PowerShell 以 SYSTEM 账号注册
`deploy\windows\auto-update.ps1`（对比 digest，变化才 `up -d`；健康检查失败会
用 `ghcr.io/wrui8/asagent:rollback` 回滚；日志在 `runtime/auto-update.log`，超过
5 MB 轮转为 `.1`），例如：

```powershell
schtasks /Create /TN ASAgentAutoUpdate /SC MINUTE /MO 10 /RU SYSTEM /RL HIGHEST `
  /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\code\AS\deploy\windows\auto-update.ps1"
```

**包可见性**：`ghcr.io` 的镜像包默认可能是私有的，即使仓库公开。拉取报
`unauthorized` 时二选一：

- 在 GitHub → 你的 Packages → `asagent` → Package settings 把可见性改为 **Public**；
- 或本机 `docker login ghcr.io -u <用户名>`，密码填带 `read:packages` 的 PAT
  （凭据存于 `%USERPROFILE%\.docker\config.json`，`pull-update.ps1` 复用）。

**源码构建回退**（改本地代码、GHCR 不可用时）：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\update.ps1          # 校验 main + git pull + 构建 + 重启
powershell -ExecutionPolicy Bypass -File deploy\update.ps1 -NoPull  # 跳过 git 校验
```

`update.ps1` 把镜像 tag 设为 `asagent-api:local`，不会覆盖 GHCR 的 `latest`，
因此下次 `pull-update.ps1` 仍能正确识别远端新版本。切回镜像模式时执行一次
`docker compose -f deploy/docker-compose.yml pull` 即可。

数据库结构由启动时的轻量迁移处理；升级前建议先跑一次备份。前端静态资源在镜像
内重新构建，无需单独部署 `web/dist`。

### 发布安装包（Windows / Android）

推送 `v*` tag（例如 `v0.1.1`）触发 `.github/workflows/release.yml`：

- `build-windows`：`windows-latest` 构建 Tauri NSIS 安装包与签名更新包
- `build-android`：`ubuntu-latest` 用 Expo prebuild + Gradle 构建 release APK，
  再用 `apksigner` 签名
- `publish`：汇总 artifact，生成 `latest.json`，挂到对应 tag 的 GitHub Release

tag 版本会自动写入 `desktop/src-tauri/tauri.conf.json` 与 `app/app.json`（去掉前缀
`v`），无需手动改版本号。

Android 签名材料只存放在本机（仓库之外），以 Repository Secrets 提供给 CI：
`ANDROID_KEYSTORE_BASE64`、`ANDROID_KEYSTORE_PASSWORD`、`ANDROID_KEY_ALIAS`、
`ANDROID_KEY_PASSWORD`。**签名密钥必须离线备份**，丢失后将无法覆盖升级已安装的
应用。iOS 需要 macOS + Apple 签名，托管 runner 无法构建，暂不提供安装包。

#### Windows 桌面自动更新

Tauri updater 使用 minisign 密钥校验更新包。密钥已在本机生成：

- 私钥：`%USERPROFILE%\.asagent\tauri-updater.key`（**保密并离线备份**，不要提交）
- 公钥：`%USERPROFILE%\.asagent\tauri-updater.key.pub`，已写入
  `desktop/src-tauri/tauri.conf.json` 的 `plugins.updater.pubkey`

在 GitHub → Settings → Secrets and variables → Actions 添加：

- `TAURI_SIGNING_PRIVATE_KEY`：私钥文件全文（用文本编辑器打开后整段复制）
- `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`：生成密钥时设置的密码；本次生成的密钥没有
  密码，可不添加

首次发布前必须配置 `TAURI_SIGNING_PRIVATE_KEY`，否则 `build-windows` 会直接失败
（避免产出无法自动更新的安装包）。

发布后：

- Release 资产包含 `*.exe`、更新签名与 `latest.json`。Tauri CLI 2.1+ 直接以
  `*-setup.exe` 作为 NSIS 更新包，签名为 `*.exe.sig`；更早版本则是
  `*.nsis.zip` + `*.nsis.zip.sig`，发布脚本两种布局都兼容
- 已安装客户端在「MCP 与系统」页点击「检查更新」；updater 读取
  `https://github.com/wrui8/asagent/releases/latest/download/latest.json`，
  校验签名后下载安装，应用退出并由安装程序完成升级
- 更新源固定为 GitHub 最新 Release，回滚需发布更高版本的新 tag（或手动安装旧包）

#### Android 版本提示

`GET /api/app/version` 是公开接口，App 在启动时检查（仅 Android 弹窗）：

```dotenv
AS_APP_LATEST_VERSION=0.2.0      # 最新可用版本；留空取后端 __version__
AS_APP_MIN_VERSION=0.1.0         # 最低支持版本；低于它强制更新，留空则不强制
AS_APP_ANDROID_URL=https://github.com/wrui8/asagent/releases/latest
AS_APP_RELEASE_NOTES=修复若干问题
```

版本比较只看 `major.minor.patch`；网络失败或接口不可用时保持静默，不阻塞启动。

## 7. 端到端测试（E2E）

`e2e/` 是独立 Node 包，使用 Playwright，不调用真实模型：

```powershell
cd e2e
npm ci
npx playwright install chromium
npx playwright test          # 自动 seed 临时库并在 8179 端口起独立后端
```

- 测试数据在 `runtime/e2e-*`，端口 8179，不影响生产容器与 8080
- 失败时截图在 `e2e/test-results/`；CI 会带 `playwright-report` artifact
- 定向运行：`npx playwright test tests/kb.spec.ts`

## 8. 排障

| 现象 | 排查 |
| --- | --- |
| 8080 被占用 / 容器起不来 | 停掉遗留的 uv 后端；`netstat -ano \| findstr :8080` |
| 容器 unhealthy | `docker logs asagent-api`；确认 `.env` 有 `AS_JWT_SECRET` 与 `DEEPSEEK_API_KEY` |
| 502 / Cloudflare 1033 | 容器是否健康；`Get-Service cloudflared`；查看隧道日志 |
| 登录 401/刷新失效 | `AS_COOKIE_SECURE` 与访问协议是否一致；域名在 `AS_ALLOWED_ORIGINS` 中 |
| 时间差 8 小时 | 容器已设 `TZ=Asia/Shanghai`，检查宿主 compose 覆盖 |
| SQLite `database is locked` | 只能有一个进程/容器写 `state.db`；不要同时 uv 直跑 |
| code 场景报路径不存在 | 设置 `AS_SANDBOX_PATH_MAP`；确认 `DOCKER_SOCKET` 已挂载；容器内执行 `docker version` 自测 |
| code 场景权限拒绝（Windows） | Docker Desktop 的 named pipe 在容器内是空目录，无法使用；改宿主直跑或 Linux 部署（见第 1 节说明） |
| code 场景权限拒绝（Linux） | 宿主 socket 权限应对 uid 10001 放开，例如 `chown root:docker /var/run/docker.sock && chmod 660 ...` 并把 uid 10001 加入 docker 组 |
| RAG 报模型找不到 | `BGE_MODEL_DIR` 是否挂到 `/models`；`AS_EMBEDDING_MODEL=/models/bge-base-zh-v1.5` |
| 知识库有文档但检索不到 | 保存后需「重建索引」；看 `/api/kb/status` 的 stale 与任务 |
| 拉镜像 `unauthorized` | GHCR 包可见性改为 Public，或 `docker login ghcr.io`（PAT 需 `read:packages`） |
| 更新没生效 | 手动执行 `deploy\pull-update.ps1`；确认 CI 已发布新镜像、Docker Desktop 在运行 |

Linux 差异：

- `DOCKER_SOCKET=/var/run/docker.sock`；`app`（uid 10001）需能访问该 socket
  （socket 属组通常是宿主 `docker`，镜像内 uid 10001 已加入容器内 `docker` 组，
  但组的 GID 未必与宿主一致；不一致时用 `group_add` 或调整宿主 socket 权限）
- `runtime/`、`data/` 挂载目录必须对 uid 10001 可写（必要时 `chown -R 10001`）
- 不用 Cloudflare 隧道时可用 `deploy/Caddyfile.example` 做 TLS 反代（/api → 8080，
  其余静态文件），或直接用 systemd + uvicorn 的 VPS 方案
- 备份用 cron 调 `deploy/linux/backup.sh`

## 9. 相关文件

| 文件 | 作用 |
| --- | --- |
| `deploy/Dockerfile` | 多阶段镜像（web 构建 + Python 运行时） |
| `deploy/docker-compose.yml` | 端口、卷、环境、健康检查、日志轮转 |
| `deploy/windows/*.ps1` | 自启、备份、cloudflared 服务（`auto-update.ps1` 为可选手动轮询脚本） |
| `deploy/pull-update.ps1` | 拉取 GHCR 镜像、重启容器并做健康检查（支持 `-Image` 固定/回滚） |
| `deploy/update.ps1` | 源码构建回退：`git pull` → 构建 `asagent-api:local` → 重启并健康检查 |
| `deploy/linux/backup.sh` | Linux 备份入口 |
| `deploy/Caddyfile.example` | 可选 TLS 反代示例 |
| `scripts/backup.py` | 备份/保留策略（无第三方依赖） |
| `scripts/seed_e2e.py` | E2E 测试账号与数据重置 |
| `.github/workflows/ci.yml` | pytest/ruff、web、app、e2e，以及 docker 构建并推送 GHCR |
| `.github/workflows/release.yml` | `v*` tag 构建 Windows 桌面安装包 |
| `docs/platform-v2-plan.md` | v2 目标架构、框架图、流程与时序 |
| `docs/platform-v2-impl.md` | v2 分阶段实施步骤、验证与回滚 |
| `src/asagent/api/middleware.py` | 中间件 TCP 探活（`/api/system/info`） |
