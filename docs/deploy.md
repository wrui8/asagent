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
# 注册计划任务：登录启动栈 + 每日 03:17 备份 + 每 10 分钟拉取镜像（需管理员）
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

## 4. 备份与恢复

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

## 5. 升级与维护

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

自动更新：`install-tasks.ps1` 会注册 `ASAgentAutoUpdate` 计划任务，每 10 分钟
`docker compose pull` 一次，仅当本地镜像 digest 与 GHCR 不同才 `up -d`。健康检查
失败会用上一镜像（`ghcr.io/wrui8/asagent:rollback`）重建并再次检查，日志在
`runtime/auto-update.log`（超过 5 MB 轮转为 `.1`）。

```powershell
Get-ScheduledTask ASAgentAutoUpdate
Get-Content runtime\auto-update.log -Tail 20
```

**包可见性**：`ghcr.io` 的镜像包默认可能是私有的，即使仓库公开。拉取报
`unauthorized` 时二选一：

- 在 GitHub → 你的 Packages → `asagent` → Package settings 把可见性改为 **Public**；
- 或本机 `docker login ghcr.io -u <用户名>`，密码填带 `read:packages` 的 PAT
  （凭据存于 `%USERPROFILE%\.docker\config.json`，自动更新任务复用）。

**源码构建回退**（改本地代码、GHCR 不可用时）：

```powershell
powershell -ExecutionPolicy Bypass -File deploy\update.ps1          # 校验 main + git pull + 构建 + 重启
powershell -ExecutionPolicy Bypass -File deploy\update.ps1 -NoPull  # 跳过 git 校验
```

`update.ps1` 把镜像 tag 设为 `asagent-api:local`，不会覆盖 GHCR 的 `latest`，
因此自动更新任务仍能正确识别远端新版本。切回镜像模式时执行一次
`docker compose -f deploy/docker-compose.yml pull` 即可。

数据库结构由启动时的轻量迁移处理；升级前建议先跑一次备份。前端静态资源在镜像
内重新构建，无需单独部署 `web/dist`。

### 发布安装包（Windows / Android）

推送 `v*` tag（例如 `v0.1.0`）触发 `.github/workflows/release.yml`：

- `build-windows`：`windows-latest` 构建 Tauri NSIS 安装包
- `build-android`：`ubuntu-latest` 用 Expo prebuild + Gradle 构建 release APK，
  再用 `apksigner` 签名
- `publish`：汇总两个 artifact，挂到对应 tag 的 GitHub Release

Android 签名材料只存放在本机（仓库之外），以 Repository Secrets 提供给 CI：
`ANDROID_KEYSTORE_BASE64`、`ANDROID_KEYSTORE_PASSWORD`、`ANDROID_KEY_ALIAS`、
`ANDROID_KEY_PASSWORD`。**签名密钥必须离线备份**，丢失后将无法覆盖升级已安装的
应用。iOS 需要 macOS + Apple 签名，托管 runner 无法构建，暂不提供安装包。

## 6. 端到端测试（E2E）

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

## 7. 排障

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
| 自动更新没生效 | `Get-ScheduledTask ASAgentAutoUpdate`；看 `runtime/auto-update.log`；Docker Desktop 是否在运行 |

Linux 差异：

- `DOCKER_SOCKET=/var/run/docker.sock`；`app`（uid 10001）需能访问该 socket
  （socket 属组通常是宿主 `docker`，镜像内 uid 10001 已加入容器内 `docker` 组，
  但组的 GID 未必与宿主一致；不一致时用 `group_add` 或调整宿主 socket 权限）
- `runtime/`、`data/` 挂载目录必须对 uid 10001 可写（必要时 `chown -R 10001`）
- 不用 Cloudflare 隧道时可用 `deploy/Caddyfile.example` 做 TLS 反代（/api → 8080，
  其余静态文件），或直接用 systemd + uvicorn 的 VPS 方案
- 备份用 cron 调 `deploy/linux/backup.sh`

## 8. 相关文件

| 文件 | 作用 |
| --- | --- |
| `deploy/Dockerfile` | 多阶段镜像（web 构建 + Python 运行时） |
| `deploy/docker-compose.yml` | 端口、卷、环境、健康检查、日志轮转 |
| `deploy/windows/*.ps1` | 自启、备份、每 10 分钟镜像更新任务、cloudflared 服务 |
| `deploy/pull-update.ps1` | 拉取 GHCR 镜像、重启容器并做健康检查（支持 `-Image` 固定/回滚） |
| `deploy/update.ps1` | 源码构建回退：`git pull` → 构建 `asagent-api:local` → 重启并健康检查 |
| `deploy/linux/backup.sh` | Linux 备份入口 |
| `deploy/Caddyfile.example` | 可选 TLS 反代示例 |
| `scripts/backup.py` | 备份/保留策略（无第三方依赖） |
| `scripts/seed_e2e.py` | E2E 测试账号与数据重置 |
| `.github/workflows/ci.yml` | pytest/ruff、web、app、e2e，以及 docker 构建并推送 GHCR |
| `.github/workflows/release.yml` | `v*` tag 构建 Windows 桌面安装包 |
