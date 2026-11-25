# 平台演进 v2 落地实施计划

> 配套文档：[`platform-v2-plan.md`](platform-v2-plan.md)（目标架构、框架图、流程与时序）
> 日期：2026-11-25
> 本文档只回答「怎么做」：每个阶段改哪些文件、按什么顺序、怎么验证、怎么回滚。

---

## 0. 执行方式

### 0.1 节奏

```text
main 拉分支 → 开发 + 本地验证 → push → PR → CI 五 job 全绿 → 合并 main
  → CI 构建镜像并推 GHCR → 本机 deploy\pull-update.ps1 → 打开开关观察
```

- **一个阶段一个 PR**，阶段 0 只做基座，不改业务逻辑。
- 所有新能力用环境变量控制，**默认值等于 v1 行为**：
  `AS_STORAGE_BACKEND=sqlite`、`AS_RAG_BACKEND=sqlite`、`AS_CHECKPOINTER=sqlite`、
  `AS_ENGINE=legacy`、`AS_MEMORY_ENABLED=false`。
- 开关打开后至少观察一个完整使用周期（≥ 1 天）再进入下一阶段。

### 0.2 硬约束

- 中文文档与注释；文件操作使用完整 Windows 绝对路径。
- 不打印任何密钥、密码、Secret；`.env` 与 `deploy/.env` 均不入库。
- `data/docs/*.md`、PG 中的文档版本与记忆条目是原始数据，迁移前必须备份。
- 云端只用 DeepSeek；桌面/移动端地址写死 `https://agent.woaiwyj.xin`。
- 不自动 `git commit` / `git push`，提交由用户确认后执行。

### 0.3 开关总表

| 变量 | 默认 | 阶段 | 作用 |
| --- | --- | --- | --- |
| `AS_STORAGE_BACKEND` | `sqlite` | 2 | `postgres` 切权威库 |
| `AS_PG_DSN` | 空 | 2 | PG 连接串 |
| `AS_REDIS_URL` | 空 | 0/2 | Redis 连接串（阶段 0 仅探活） |
| `AS_RABBITMQ_URL` | 空 | 4 | MQ 连接串 |
| `AS_RAG_BACKEND` | `sqlite` | 1 | `milvus` 切向量库 |
| `AS_MILVUS_URI` | 空 | 1 | Milvus 地址 |
| `AS_TEI_URL` | 空 | 1 | TEI 地址 |
| `AS_EMBEDDING_MODEL` | 本机 bge 目录 | 1 | 改为 `BAAI/bge-m3` |
| `AS_EMBEDDING_DIM` | `1024` | 1 | 向量维度 |
| `AS_CHECKPOINTER` | `sqlite` | 3 | `redis` 切检查点后端 |
| `AS_ENGINE` | `legacy` | 3 | `langgraph` 切编排引擎 |
| `AS_MEMORY_ENABLED` | `false` | 4 | 记忆子系统总开关（需 PG + Redis + MQ + Milvus/TEI） |
| `AS_MEMORY_TOP_K` | `5` | 4 | 每次注入的长期记忆条数 |
| `AS_MEMORY_DAILY_EXTRACT_LIMIT` | `20` | 4 | 每用户每日异步抽取配额 |
| `AS_MEMORY_MODEL` | `deepseek-chat` | 4 | 抽取模型 |
| `AS_WORKERS` | `1` | 2 | uvicorn worker 数 |

---

## 1. 阶段 0：中间件基座

**目标**：中间件能以 profile 方式按需启动、能被探活、能被备份；业务代码零改动。

### 1.1 交付物

| 文件 | 改动 |
| --- | --- |
| `deploy/docker-compose.yml` | 新增 `state` / `rag` 两组服务与 named volumes |
| `src/asagent/config.py` | `Settings` 新增中间件字段（默认空 / v1 行为） |
| `src/asagent/api/middleware.py`（新增） | TCP 探活 + URL 解析 |
| `src/asagent/api/routers/system.py` | `/api/system/info` 增加 `middleware` 字段 |
| `scripts/backup.py` | 新增 `--middleware`（pg_dump + Redis RDB） |
| `.github/workflows/ci.yml` | 新增 `compose` job 做配置语法校验 |
| `docs/deploy.md` | 新增「中间件」章节 |
| `.env.example` | 新增全部变量与注释 |
| `tests/test_middleware_probe.py`（新增） | 探活单测 |
| `tests/test_backup.py` | 补中间件备份的降级路径测试 |

### 1.2 compose 改造

现有 `api` 服务保持不变（`profiles` 不设置，默认启动）。

新增服务（全部 `127.0.0.1` 绑定，不对外暴露）：

| 服务 | 镜像 | profile | 宿主端口 | 数据卷 |
| --- | --- | --- | --- | --- |
| `postgres` | `postgres:16-alpine` | state | 5432 | `pgdata` |
| `redis` | `redis:7-alpine` | state | 6379 | `redisdata` |
| `rabbitmq` | `rabbitmq:3.13-management-alpine` | state | 5672 / 15672 | `rabbitmqdata` |
| `tei` | `ghcr.io/huggingface/text-embeddings-inference:cpu-latest` | rag | 8081 → 80 | 模型目录只读挂载 |
| `milvus` | `milvusdb/milvus:v2.5.4` | rag | 19530 / 9091 | `milvusdata`（含内嵌 etcd 与向量数据） |

> Milvus 用 `ETCD_USE_EMBED=true` + `common.storageType=local` 单容器运行，
> 不再单独部署 etcd / MinIO：MinIO 已停止在 Docker Hub 发布社区镜像，
> 而 Milvus 2.5 原生支持内嵌 etcd 与本地磁盘存储。配置见
> `deploy/milvus/embedEtcd.yaml` 与 `deploy/milvus/user.yaml`。

要点：

- **named volume 而不是绑定挂载**：Windows Docker Desktop 下 PG/Milvus 对绑定目录的
  权限处理不可靠，named volume 由 Docker 管理，升级与备份都更稳。
- **密码 fail-closed**：`postgres`、`rabbitmq` 的密码用
  `${AS_PG_PASSWORD:?请在 deploy/.env 设置 AS_PG_PASSWORD}` 形式，缺失时
  `compose up` 直接报错，避免以弱默认密码启动。
- **api 不写 `depends_on`**：中间件在独立 profile 中，未启用时硬依赖会导致
  `compose up` 失败；应用侧按「未配置即跳过」处理。
- **Redis 淘汰策略用 `volatile-lru`**：只淘汰设置了 TTL 的键，避免 LangGraph
  checkpoint 被内存压力误删（阶段 3 起有实际影响，阶段 0 先定好）。
- **TEI 模型目录**：新增变量 `BGE_M3_DIR` 指向宿主机上含 `bge-m3` 权重的目录，
  以只读方式挂到容器 `/data`，`--model-id /data/bge-m3`，不在启动时从外网下载。

最小启动集：

```powershell
# 只做对话 / SQL：core（默认，不加 profile）
docker compose -f deploy/docker-compose.yml up -d

# 加存储与缓存
docker compose -f deploy/docker-compose.yml --profile state up -d

# 全部（阶段 1 后）
docker compose -f deploy/docker-compose.yml --profile core --profile state --profile rag up -d
```

### 1.3 配置项

`Settings` 新增（全部有默认值，不配置即保持 v1）：

```python
as_storage_backend: Literal["sqlite", "postgres"] = "sqlite"
as_pg_dsn: str = ""
as_redis_url: str = ""
as_rabbitmq_url: str = ""
as_rag_backend: Literal["sqlite", "milvus"] = "sqlite"
as_milvus_uri: str = ""
as_tei_url: str = ""
as_embedding_dim: int = 1024
as_checkpointer: Literal["sqlite", "redis"] = "sqlite"
as_engine: Literal["legacy", "langgraph"] = "legacy"
as_memory_enabled: bool = False
as_workers: int = 1
as_local_db_path: str = "runtime/local.db"
```

`.env.example` 同步补注释：说明默认关闭、密码只写 `deploy/.env`、端口只在本地。

### 1.4 探活

新增 `src/asagent/api/middleware.py`：

- `parse_endpoint(url, default_port)`：从 `postgresql://` / `redis://` / `amqp://` /
  `http://` 解析 host 与 port；无端口时用默认值（5432 / 6379 / 5672 / 19530 / 8081）。
- `tcp_probe(host, port, timeout=1.5)`：返回 `(ok, latency_ms)`，只做 TCP 可达性，
  不引入 PG/Redis 客户端依赖。
- `probe_middleware(settings)`：对五个地址逐个探测；未配置的项返回
  `{"configured": false, "status": "not_configured"}`，不报错。

`/api/system/info` 增加：

```json
"middleware": {
  "postgres": {"configured": true, "status": "up", "latency_ms": 2.4},
  "redis": {"configured": true, "status": "up", "latency_ms": 1.1},
  "rabbitmq": {"configured": false, "status": "not_configured"},
  "milvus": {"configured": false, "status": "not_configured"},
  "tei": {"configured": false, "status": "not_configured"}
}
```

探活放 `asyncio.to_thread` 执行，避免阻塞事件循环；总超时由单项 1.5 s 决定。

> 阶段 0 只验证 TCP 可达。TEI 的模型是否加载完成，阶段 1 改用 `GET /health`；
> Milvus 的 collection 是否可用，阶段 1 改用 pymilvus 的 `has_collection`。

### 1.5 备份

`scripts/backup.py` 新增 `--middleware`：

1. `docker inspect` 确认 `asagent-postgres` / `asagent-redis` 在运行；
2. PG：`docker exec asagent-postgres pg_dump -U <user> -d <db> --no-owner` →
   `postgres.sql`（容器内认证，脚本不接触密码）；
3. Redis：`docker exec asagent-redis redis-cli BGSAVE`，等待完成后
   `docker cp asagent-redis:/data/dump.rdb redis.rdb`；
4. 容器不存在或 docker 不可用 → 打印警告并跳过，**不使整个备份失败**；
5. manifest 增加 `middleware: {"postgres": true, "redis": true}`。

Milvus（含内嵌 etcd）不进入备份：向量是可重建的派生数据，重建来源是 PG 元数据 + `data/docs`。
在 `docs/deploy.md` 的恢复步骤里明确「先恢复 PG 与 docs，再触发全量索引重建」。

阶段 0 默认不开启 `--middleware`（保持现有计划任务行为）；阶段 2 切换 PG 后
把计划任务命令改为带 `--middleware`。

### 1.6 CI

新增 `compose` job（不启动容器，只校验配置）：

```yaml
compose:
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - name: Validate compose profiles
      env:
        AS_PG_PASSWORD: ci-dummy-only
        AS_RABBITMQ_PASSWORD: ci-dummy-only
      run: |
        docker compose -f deploy/docker-compose.yml config -q
        docker compose -f deploy/docker-compose.yml --profile state config -q
        docker compose -f deploy/docker-compose.yml --profile rag config -q
```

单测覆盖探活模块与备份的降级路径，不需要真实中间件。

### 1.7 验证步骤

```powershell
# 1) 静态
uv run ruff check .
uv run pytest -q

# 2) compose 语法
$env:AS_PG_PASSWORD="local-dev-only"; $env:AS_RABBITMQ_PASSWORD="local-dev-only"
docker compose -f deploy/docker-compose.yml config -q
docker compose -f deploy/docker-compose.yml --profile state config -q

# 3) 起 state profile（首次拉镜像较慢）
docker compose -f deploy/docker-compose.yml --profile state up -d
docker compose -f deploy/docker-compose.yml ps
docker exec asagent-postgres pg_isready -U asagent
docker exec asagent-redis redis-cli ping
docker exec asagent-rabbitmq rabbitmq-diagnostics -q ping

# 4) 应用探活（管理员 token）
#    /api/system/info 的 middleware.postgres/redis/rabbitmq 应为 up

# 5) 备份演练
uv run python scripts/backup.py --middleware --keep 7
#    检查备份目录中的 postgres.sql、redis.rdb 与 manifest.json

# 6) 业务回归：v1 行为不变
uv run pytest -q
```

若本机拉镜像失败（网络/磁盘），记录现象并只完成 1、2、6 三项，不伪造第 3–5 项结果。

### 1.8 回滚

`--profile` 不启用即等同 v1；已启动的中间件可 `docker compose stop postgres redis rabbitmq`。
业务代码零改动，无需回滚镜像。

---

## 2. 阶段 1：RAG 迁移（Milvus + TEI + bge-m3）

### 2.1 依赖与镜像

- `pyproject.toml` 新增 optional extra：

  ```toml
  [project.optional-dependencies]
  milvus = ["pymilvus>=2.4,<2.6"]
  ```

  TEI 编码走 HTTP，无额外 Python 依赖。`pymilvus` 客户端大版本需与服务端
  一致（compose 固定 `milvusdb/milvus:v2.5.x`）。`rag` extra 保留（torch 仅用于
  旧后端回退）；Dockerfile 增加 `uv sync --frozen --no-dev --extra rag --extra milvus`。
- 准备 bge-m3 权重到 `BGE_M3_DIR`（离线目录，避免启动时下载）。

### 2.2 索引适配层

| 文件 | 改动 |
| --- | --- |
| `src/asagent/tools/rag/factory.py`（新增） | `make_index(...)` 按 `AS_RAG_BACKEND` 返回 `RagIndex` / `MilvusIndex`；`IndexLike` 协议 |
| `src/asagent/tools/rag/milvus_backend.py`（新增） | pymilvus 直连 + TEI HTTP 嵌入 + collection/alias 管理 |
| `src/asagent/tools/rag/tei.py`（新增） | TEI `/embed` 客户端（分批、`truncate`/`normalize`、重试一次） |
| `src/asagent/tools/rag/index.py` | 不改：`RagIndex` 保持 v1 实现 |
| `src/asagent/tools/rag/tools.py` | 构造器类型改 `IndexLike`，经工厂创建 |
| `src/asagent/api/kb.py`、`api/routers/kb.py` | `make_index()` 走工厂；类型改 `IndexLike` |

Milvus collection 设计：

| 项 | 值 |
| --- | --- |
| collection | `kb_chunks_<YYYYmmddHHMMSSffffff>`，别名 `kb_chunks` |
| 主键 | `chunk_id`（`{doc_id}#{seq}`，VARCHAR 512） |
| 标量 | `doc_id`(256)、`chunk_index`(INT64)、`title`(1024)、`section`(1024)、`text`(8192) |
| 向量 | `dense`（FLOAT_VECTOR，1024，TEI 已 L2 归一化） |
| 索引 | HNSW（M=16，efConstruction=200，COSINE） |
| 过滤 | 无状态字段；增删按 `doc_id` 重建 |
| 元数据 | 文档哈希 / fingerprint / built_at 存本地 `data/rag.meta.sqlite3` |

检索打分与 v1 一致：`0.7 × COSINE 相似度 + 0.3 × lexical`，候选 `max(top_k×4, 20)`。

### 2.3 数据重建（一次性）

1. `uv run asagent rag-index --force`（`AS_RAG_BACKEND=milvus`）：新建集合 →
   逐篇切块 + TEI 编码 + insert → flush → `alter_alias` 指向新集合 → 删旧集合；
2. 增量 `rag-index` 按内容哈希跳过未变文档，删除的文档按 `doc_id` 清理；
3. 抽样 query 对比 v1 命中，确认引用可溯源；旧 SQLite `data/rag.sqlite3`
   保留一周后再删。

> 不做 768→1024 的向量迁移：语义空间不兼容，直接全量重建。

### 2.4 实施记录与偏差（2026-11-25）

- **不使用 LlamaIndex**：仅需「切块 + 编码 + 向量库」，自研 `MilvusIndex`
  复用现有 `split_markdown`，依赖面积更小、行为与 v1 完全可控。
- **TEI CPU 预热开销**：`--max-batch-tokens` 取 2048（默认），预热约 60 秒、
  内存峰值约 5.8 GB；8192 时预热 >7 分钟、峰值 11 GB 并触发 OOM。bge-m3 的
  `1_Pooling/config.json` 为 CLS pooling，compose 显式 `--pooling cls`。
- **权重**：bge-m3 仓库只有 `pytorch_model.bin`（2.27 GB），TEI ORT 后端缺 onnx
  时自动回落 candle，可按 Bert 架构加载，实测可用。
- **实测**：12 篇文档 / 80 片段；`status` = `{backend: milvus, stale: false}`；
  golden 集检索层 9/9 命中关键信息，端到端评测 **10/10** 通过（qwen2.5-7b-awq）。

### 2.5 验证

- RAG 黄金集 `evals/rag/golden.jsonl` 通过率 **10/10**（或 ≥ v1 基线）。
- `search_docs` 返回结构与字段名不变；前端引用卡片正常。
- 重建 500 文档 ≤ 15 min；单条 query（CPU）P95 < 500 ms。
- TEI / Milvus 任一宕机时，搜索工具返回明确错误，`list_docs` / `read_doc` 仍可用。

### 2.6 回滚

`AS_RAG_BACKEND=sqlite` + 恢复 `data/rag.sqlite3`（保留期内），重启 API。
Milvus 侧数据为派生数据，可随时由 `data/docs` 重建。

---

## 3. 阶段 2：存储分层 + 多 worker

### 3.1 PG schema 与迁移

依赖：optional extra `state`（`psycopg[binary,pool]>=3.2` + `redis>=5.0`）；
未安装时只有显式选择 postgres/Redis 才报错。同步 psycopg 足够（API 层用线程池执行）。

| 文件 | 内容 |
| --- | --- |
| `migrations/001_init.sql` | 全部表 + 索引 + `schema_version` 表 |
| `src/asagent/storage/pg.py`（新增） | 连接池、迁移执行器（启动时幂等执行）、`PgKernelStore` / `PgWebStore` |
| `src/asagent/storage/protocols.py`（新增） | `KernelStore` / `WebStore` Protocol，逐一覆盖 v1 方法签名 |
| `src/asagent/storage/factory.py`（新增） | `build_storage(config)` 按 `AS_STORAGE_BACKEND` 返回统一 Storage |
| `src/asagent/storage/redis_state.py`（新增） | 单飞锁/心跳、跨 worker 占用计数、固定窗口限流 |
| `scripts/migrate_sqlite_to_pg.py`（新增） | 一次性搬运 + 行数/内容 sha256 校验（`--dry-run` / `--force`） |

迁移表清单（沿用 v1 语义）：

```text
users  invites  refresh_tokens  user_quotas  user_usage  hidden_sessions
kb_docs  kb_versions  kb_jobs  kb_audit  eval_jobs
sessions  events  checkpoints  tool_invocations  artifacts  approvals
```

步骤：

1. 跑备份（含 `--middleware`）；
2. 建 schema → 跑迁移脚本（事务分批，可重入）；
3. 校验：每表行数一致；抽样 100 条 `events.payload_json` sha256 一致；
4. `AS_STORAGE_BACKEND=postgres` 切读；保留 SQLite 只读 7 天；
5. 一周无异常后，SQLite 降级为 `runtime/local.db`（仅指纹与埋点）。

### 3.2 进程内状态下沉

| v1 位置 | v2 实现 |
| --- | --- |
| `api/jobs.py` `JobManager` | 会话/事件/快照写 PG；Redis `SET NX` 锁 `job:{session}`（TTL 60 s，后台任务每 20 s 续期）+ `busy:{owner}` 集合 |
| `api/app.py` `_recover_zombies` | 仍按 PG 中 `running` 状态恢复；单机重启时清理，多 worker 下锁过期兜底 |
| `api/kb.py` `KBIndexRunner` | Redis `SET NX kb:index`（TTL 900 s，任务结束 Lua 比较释放） |
| `api/evals.py` `EvalJobManager` | Redis `SET NX eval`（TTL 3600 s），任务状态写 PG `eval_jobs` |
| `api/limiter.py` `SlidingWindowLimiter` | `RedisWindowLimiter`：固定窗口 `INCR limit:{key}:{bucket}`（窗口 60 s，TTL 62 s） |

限流偏差：计划中的 ZSET 滑动窗口换成了固定窗口计数，换取单次 `INCR` 的原子性与
更少的 Redis 往返；代价是最多一个窗口的边界误差（`limit` 以内的正常使用不受影响）。

### 3.3 多 worker

- `cli.py` 的 `web` 命令增加 `--workers`，默认读 `AS_WORKERS`；`>1` 时强制要求
  `AS_STORAGE_BACKEND=postgres` 且配置 `AS_REDIS_URL`，否则直接退出（fail-closed）。
- 多 worker 必须走 `uvicorn.run("asagent.api.app:create_app", factory=True, workers=n)`
  ——每个进程独立建 app、独立连接池。
- `create_app` 的 lifespan 只做幂等初始化（迁移、僵尸恢复、残留评测任务收尾）。
- SSE 继续轮询 PG `events`（天然多 worker）；轮询间隔保持 `stream_poll_s`。
- 部署：`asagent web --host 0.0.0.0 --port 8000 --workers 4`。

### 3.4 验证

- 20 并发会话压测：0 个 5xx，事件序号连续无重复。
- `kill` 任一 worker 后其余继续服务；心跳过期的会话被正确标记，未过期的不误杀。
- 索引/评测任务在任一 worker 提交后，全局只有一个实例运行。
- 限流在多个 worker 间共享计数（同一用户并发第 N+1 个请求被拒）。

### 3.5 实施记录与偏差（2026-11-25）

已完成并本地实测：

| 项 | 结果 |
| --- | --- |
| PG 往返用例（`tests/test_storage_pg.py`） | 7 条通过（kernel/web 全路径、审批、邀请码、KB、评测任务、迁移幂等） |
| Redis 用例（`tests/test_redis_state.py`，fakeredis+lua） | 5 条通过（锁互斥、令牌续期/释放、占用计数、限流、命名空间） |
| SQLite → PG 迁移 | 413 会话 / 6526 事件 / 1530 快照 / 850 工具调用等 17 张表行数与内容哈希全部一致 |
| postgres + redis 起 API | 登录、会话列表、知识库（12 篇）、系统信息、审计均 200 |
| `--workers 2` | 40 并发健康检查全 200；同用户名登录 10 次后第 11 次起 429（跨进程共享计数） |
| 回滚 | `AS_STORAGE_BACKEND=sqlite` 时 232 条既有测试全绿 |

偏差说明：

- 计划文件 `storage/repo.py` 拆成了 `protocols.py`（类型契约）+ `pg.py`（实现）。
- Redis 滑动窗口 → 固定窗口（见 3.2）。
- SSE 轮询在 `pg` 后端按 `events.seq` 游标增量读取；SQLite 与 PG 的 `seq` 语义一致。
- 评测任务与知识库索引的「锁 + 任务表」都保留进程内 `asyncio.Task`，Redis 锁负责
  跨进程互斥；worker 崩溃后靠 TTL 自愈，任务表中 `running` 记录在下次启动时标记失败。
- 容器内根 `.env` 的连接串必须用服务名（`postgres:5432` / `redis:6379`），
  宿主直跑用 `127.0.0.1`；两者不要共用同一份 `.env` 部署。

### 3.6 回滚

`AS_STORAGE_BACKEND=sqlite` + `AS_WORKERS=1`；迁移期间的数据仍在 PG，不丢。
注意 PG 侧在切回 SQLite 后不会再更新，如需再次切换要重新跑迁移脚本。

---

## 4. 阶段 3：LangGraph 替换

### 4.1 图定义

新增 `src/asagent/runtime/graph.py`：

```python
def build_engine(config, context_builder, router, registry, guard, store) -> CompiledGraph: ...
```

节点与 v1 一一对应（见 `platform-v2-plan.md` §4.1）：

| 节点 | v1 来源 |
| --- | --- |
| `guard_input` | `GuardStage.INPUT` |
| `route` | `ModelPolicy` + `ModelRouter.initial` |
| `build_context` | `ContextBuilder.build` |
| `model` | `ModelClient.complete` + 协议修复 |
| `execute_tools` | `ToolRegistry.invoke` |
| `guard_tool_output` | `GuardStage.TOOL_OUTPUT` |
| `guard_final` | `GuardStage.FINAL` + Critic |
| `finalize` | 状态收尾 + `final` 事件 |

状态字段沿用 `SessionState`：`messages`、`step`、`plan`、`tool_calls`、`budget`、
`error`、`pending_approval`。

### 4.2 检查点与审批

- `AS_CHECKPOINTER=redis`：`langgraph-checkpoint-redis`，`thread_id = session_id`，
  前缀 `asagent:ckpt:`。
- 危险工具：节点内 `interrupt({"call_id": ..., "tool": ..., "args": ...})`；
  `POST /api/sessions/{id}/approve` 发 `Command(resume={"decision": "approve"|"deny"})`。
- 崩溃恢复：`checkpoint` 为 `started` 的写操作一律重新审批（沿用 v1 语义）。

### 4.3 调用方改造

| 文件 | 改动 |
| --- | --- |
| `runtime/loop.py` | 删除 `AgentRuntime`；保留 `RunResult` 等共享类型 |
| `runtime/factory.py` | `build_runtime` → `build_engine` |
| `api/jobs.py` | 改为 `engine.astream` / `ainvoke`，事件写入逻辑不变 |
| `eval/runner.py` | 改为调用 `build_engine`；评测输入输出格式不变 |
| `cli.py` | `run` / `chat` / `resume` 改走引擎 |
| `tests/` | 替换 mock 入口；新增图级别测试（节点顺序、interrupt/resume） |

事件写入保持 17 种类型与 payload 字段不变，前端与 SSE 无需改。

### 4.4 验证

- 四场景评测（sql/rag/code/office）通过率不低于 v1 基线（见 `docs/eval-baseline.md`）。
- 审批挂起 → 批准/拒绝 → 恢复，事件序列与 v1 一致。
- 进程 kill 后 resume：读操作重跑，写操作重新审批，无重复副作用。
- `AS_ENGINE=legacy` 可回退（保留一个版本周期）。

### 4.5 回滚

`AS_ENGINE=legacy`，旧代码冻结但可运行；两个版本共用同一套事件与存储。

### 4.6 实施记录与偏差（已完成）

已落地 `src/asagent/runtime/graph.py`：`GraphEngine` 继承 `AgentRuntime` 的共享工具
（工具执行、审批挂起、崩溃恢复、客户端缓存），用 `StateGraph` 组织七个节点：

```
step → route → build_context → model ─┬→ tools ──→ step
                                      ├→ guard_final ──→ step
                                      └→ finish（终态）
```

- **语义保持**：上下文溢出逐级压缩、模型失败升级、协议修复（文本工具调用、
  `require_tool_before_final`、`final_requires_tools`）、循环检测、只读终答拦截、
  注入提醒、预算上限，全部按 v1 的分支顺序复刻；事件类型与 payload 不变。
- **引擎选择**：`build_runtime` 按 `AS_ENGINE=legacy|langgraph` 返回对应实例，API、
  CLI、评测调用方无需改动；默认仍为 `legacy`，保留一个版本周期。
- **检查点（偏差）**：`langgraph-checkpoint-redis` 依赖 Redis Stack（RediSearch），
  与 compose 中的标准 Redis 不兼容；且权威状态已在 KernelStore 中逐步落盘。
  因此 `AS_CHECKPOINTER=redis` 时把每个超级步的摘要写入
  `asagent:ckpt:{session_id}`（TTL 24h）用于观察与排障，恢复仍以 KernelStore 为准，
  避免两套状态源分叉。`AS_CHECKPOINTER=sqlite` 不写镜像。
- **审批挂起（偏差）**：未采用图内 `interrupt()` + `Command(resume=...)`。危险工具在
  节点内沿用 v1 的「不执行、不清 pending、置 `awaiting_approval`」；`approve` 后再次
  `run(resume=True)`，由 `_recover_pending` 按审批记录执行或拒绝。该路径已有 v1
  测试覆盖，语义与事件序列一致，且无需在 HTTP 层持有图执行句柄。
- **测试**：`tests/test_graph_engine.py` 12 条，覆盖工具循环、终答改写、注入提醒、
  强制检索、最大步数、审批挂起/恢复（含事件序列）、Redis 检查点、上下文溢出、
  预算上限、pending 读工具恢复、工厂引擎选择，以及与 v1 的事件类型序列对比；
  `tests/test_runtime.py` 的 v1 用例全部保留。
- **本地验证**：`AS_ENGINE=langgraph` + 本地 vLLM（qwen2.5-7b-awq）跑通 SQL
  场景（list_tables → tool_result → final，2 步）；`ruff` 全绿，
  `pytest` 252 passed / 7 skipped。
- **四场景回归**（`--models deepseek-chat`，与 legacy 同模型对照）：

  | 场景 | langgraph | legacy | 结论 |
  | --- | --- | --- | --- |
  | sql | 6/6 | 6/6 | 一致 |
  | rag | 9/10 | 9/10 | `rag_sla_p1_response` 两引擎同失败，属既有用例问题 |
  | office | 5/5（¥0.0578） | 5/5（¥0.0578） | 步数与成本逐项一致 |
  | code | 5/5（¥0.1035） | 5/5（¥0.1029） | 步数一致，金额差为模型输出随机性 |

  注：office 首次用本地 `qwen2.5-7b-awq` 跑出全部超时，排查为 vLLM 单请求卡死
  （GPU 仅余 327 MiB，单请求 90s 无响应），`docker restart vllm-local` 后仍复现，
  与引擎无关；改用 office 默认模型 deepseek-chat 后两引擎均 5/5。
- **待办**：灰度一周后删除 legacy 代码。

---

## 5. 阶段 4：记忆子系统

### 5.1 依赖与 schema

- 依赖：`aio-pika`（RabbitMQ）。
- PG：

```sql
CREATE TABLE memories (
  id            TEXT PRIMARY KEY,
  user_id       TEXT NOT NULL,
  type          TEXT NOT NULL,          -- preference | fact | decision | entity
  content       TEXT NOT NULL,
  importance    REAL NOT NULL DEFAULT 0.5,
  confidence    REAL NOT NULL DEFAULT 0.5,
  status        TEXT NOT NULL DEFAULT 'active',  -- active | archived
  source_session TEXT,
  source_seq    INTEGER,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX memories_user_idx ON memories (user_id, status, type);

CREATE TABLE memory_versions (
  id         BIGSERIAL PRIMARY KEY,
  memory_id  TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  reason     TEXT NOT NULL,             -- create | merge | manual
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE memory_tags (
  memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  tag       TEXT NOT NULL,
  PRIMARY KEY (memory_id, tag)
);
```

- Milvus collection `memories`：`memory_id` 主键、`user_id` 标量过滤、`dense` 1024 维。

### 5.2 异步抽取

| 文件 | 内容 |
| --- | --- |
| `src/asagent/memory/worker.py`（新增） | 独立进程 `asagent worker memory`，消费 `memory.extract` |
| `src/asagent/memory/extractor.py` | LLM 结构化抽取 + 四类过滤 + 置信度 |
| `src/asagent/memory/store.py` | PG 事务 + Milvus upsert + 相似度合并（> 0.92） |
| `src/asagent/memory/recall.py` | 召回 + 重排 + 摘要 |
| `src/asagent/api/jobs.py` | 会话 DONE 后发布消息（去抖 30 s，满足过滤条件才发） |

队列设计：

```text
exchange: asagent.memory (direct)
queue:    memory.extract      routing key: extract
queue:    memory.dlq          DLQ，重试 3 次后进入
```

### 5.3 召回注入

`build_context` 节点在「固定区」之后插入「长期记忆」段：

```text
<long_term_memory note="以下为历史记忆，可能过时，冲突时以当前输入为准">
- [preference] 回答先给结论，再给细节
- [decision] RAG 选型定为 Milvus
</long_term_memory>
```

摘要缓存键 `mem:{user_id}:summary`，TTL 15 min；记忆变更时主动删除。

### 5.4 夜间批量

`asagent worker memory --nightly`（计划任务 03:47 执行）：

1. 合并近似条目（embedding 相似度 > 0.95 且同 type）；
2. 时间衰减：`importance *= 0.98`（30 天未召回）；
3. `importance < 0.1` 且 90 天未召回 → `archived`（不物理删除）；
4. 输出统计（新增/合并/归档/失败）到日志与 PG。

### 5.5 验证

- 记忆召回评测集通过率 ≥ 基线；注入后答案不出现「记忆覆盖当前输入」。
- 抽取 worker 宕机：消息堆积在队列，恢复后继续消费，不影响对话。
- `AS_MEMORY_ENABLED=false`：不发布消息、不召回、worker 不启动。

### 5.6 回滚

关闭开关并停 `memory-worker`；PG 记忆表与 Milvus collection 保留，数据不删。

### 5.7 实施记录与偏差（已完成）

模块（均在 `src/asagent/memory/`）：

| 文件 | 内容 |
| --- | --- |
| `types.py` / `__init__.py` | `MemoryCandidate`、`MemoryItem`、`MEMORY_TYPES` 与行转换 |
| `extractor.py` | 中文 prompt；`parse_candidates` 去围栏、`json_repair` 容错、类型白名单、4–300 字、置信度 ≥ 0.6、去重、最多 5 条 |
| `store.py` | `PgMemoryStore`：create/merge/archive/touch_recall/list*/find_by_session/decay_stale/archive_weak/stats/versions；所有变更写 `memory_versions` |
| `vector.py` | `MemoryVectorIndex`：Milvus collection `memories`（HNSW COSINE），TEI 编码；`query` 用 `user_id` 标量过滤 |
| `recall.py` | 向量召回 → 过滤 active/归属 → `sim × (0.5+0.5·importance) × 90 天半衰期` 重排 → Top-K → `<long_term_memory>` 段 → Redis 缓存（`mem:{user_id}:summary`，TTL 900s）→ `touch_recall` |
| `policy.py` | 轮数 ≥ 3、30 秒去抖（同会话 SET NX）、每用户每日配额（`mem:quota:{user}:{day}`） |
| `publish.py` | `MemoryPublisher` + 短连接 AMQP 发布（direct exchange `asagent.memory`，queue `memory.extract`）；失败只 warning |
| `writer.py` | 逐条 embed → 同 type/active/相似度 ≥ 0.92 合并，否则新建；随后 upsert 向量 |
| `service.py` | `extract_session`：读事件 → 轮数/重复检查 → 抽取 → 写入 → 失效缓存；`build_memory_service` 按开关装配并在缺依赖时 fail-closed |
| `nightly.py` | 衰减（30 天 ×0.98）→ 归档（importance<0.1 且 90 天未召回）→ 同 type 余弦 ≥ 0.95 合并（败者 archived + 删向量）→ 失效缓存 |
| `worker.py` | 消费 `memory.extract`，prefetch=1；进程内指数退避重试 3 次，耗尽或坏消息进 `memory.dlq` |

接线：

- `runtime/loop.py`（legacy）与 `runtime/graph.py`（LangGraph）：每次运行只召回一次，
  作为独立的 system 消息插入主提示之后；异常 fail-open（warning + 无记忆）。
- `runtime/context.py`：`build(extra_system=…)`，预算扣除该段 token。
- `runtime/factory.py`：`build_runtime(..., memory=None)` 透传。
- `api/jobs.py`：任务以 `done` 结束后调用 `MemoryPublisher.maybe_publish`（异常吞掉）。
- `api/app.py`：启动时装配服务与发布器；关闭时 `aclose`。
- `cli.py`：`asagent worker memory [--nightly]`、`asagent memory list/stats`。
- `deploy/docker-compose.yml`：新增 `memory-worker`（profile `memory`）。
- `deploy/windows/install-tasks.ps1 -WithMemoryNightly`：注册每日 03:47 巩固任务。

偏差与取舍：

- **时间字段用 TEXT（ISO 8601）**，与 `001_init.sql` 一致，便于 SQLite/PG 归档脚本
  共用；计划中的 `TIMESTAMPTZ` 未采用。
- **索引**：`idx_memories_user(user_id, status, type)`、`idx_memories_session(source_session)`；
  额外 `memory_versions(memory_id, id DESC)` 便于查最新版本。
- **抽取模型**默认 `deepseek-chat`；`AS_MEMORY_MODEL` 可换本地模型，但需稳定的 JSON 输出。
- **召回缓存**：RedisState 前缀为 `asagent:`，实际键为 `asagent:mem:*`。
- **发布连接**：每次发布新建短连接，换取 API 进程不长期持有 RabbitMQ 连接。
- **测试**：`tests/test_memory.py` 25 条（解析/策略/召回/写入/夜间/发布/worker/服务/
  两个引擎注入与 fail-open）；`tests/test_memory_pg.py` 3 条（`AS_TEST_PG_DSN` 存在时
  才执行）。`AS_MEMORY_ENABLED=false` 时全部旧用例行为不变。
- **本地验证（2026-11-25）**：`ruff` 全绿；`pytest` 277 passed / 10 skipped；
  `test_memory_pg.py` 3 条在本地 PostgreSQL 上通过（`002_memory.sql` 幂等应用）；
  Milvus + TEI 冒烟通过（1024 维、`user_id` 标量过滤生效、增删正常，临时集合已删除）。
  注：Milvus 删除为逻辑删除，短时间内的检索仍可能返回已删 id，因此召回与写入
  都以 PG 的 `status='active'` 为准做二次过滤。
- **compose 校验**：`docker compose --profile memory --profile state --profile rag
  config -q` 通过；CI 的 compose job 同步加入 `--profile memory`。

---

## 6. 验收闸门（每阶段必过）

| 闸门 | 命令 / 检查 |
| --- | --- |
| 静态检查 | `uv run ruff check .` |
| 单测 | `uv run pytest -q` 全绿 |
| compose | `docker compose ... config -q` |
| 前端 | `npm run typecheck`、`npm run build`（web） |
| 移动端 | `npx tsc --noEmit`（app） |
| E2E | `cd e2e && npx playwright test` |
| 镜像 | CI `docker` job 绿，GHCR 有新 digest |
| 生产 | `asagent-api` healthy；`/api/health` 正常 |
| 公网 | `https://agent.woaiwyj.xin` 登录、会话、知识库可用 |

---

## 7. 常用命令速查

```powershell
# 启动最小栈
docker compose -f deploy/docker-compose.yml up -d

# 启动中间件
docker compose -f deploy/docker-compose.yml --profile state --profile rag up -d

# 只校验配置
docker compose -f deploy/docker-compose.yml --profile state --profile rag config -q

# 查看中间件日志
docker logs -f asagent-postgres
docker logs -f asagent-milvus
docker logs -f asagent-tei

# 应用探活
curl.exe -H "Authorization: Bearer <token>" http://127.0.0.1:8080/api/system/info

# 备份（阶段 2 起带中间件）
uv run python scripts/backup.py --middleware --keep 7

# 本地全量门禁
uv run ruff check .; uv run pytest -q

# 部署更新
powershell -ExecutionPolicy Bypass -File deploy\pull-update.ps1
```

---

## 8. 实施清单

### 阶段 0

- [ ] compose 增加 `state` / `rag` 服务与 named volumes
- [ ] `Settings` 新增中间件字段，`.env.example` 同步
- [ ] `api/middleware.py` 探活 + `/api/system/info` 接入
- [ ] `scripts/backup.py --middleware`
- [ ] CI `compose` job
- [ ] `docs/deploy.md` 中间件章节
- [ ] ruff + pytest + compose config 验证
- [ ] 本机启动 `state` profile 并探活（网络允许时）

### 阶段 1

- [x] `milvus` extra 依赖 + Dockerfile
- [x] `make_index` 工厂 + `MilvusIndex` + `TEIClient`
- [x] bge-m3 权重就绪、TEI 启动
- [x] 全量重建 + alias 切换
- [x] RAG 评测 10/10
- [x] 前端引用回归（`search_docs` 输出字段与 citation 格式未变；评测中引用用例通过）

### 阶段 2

- [x] PG schema + 迁移执行器
- [x] SQLite → PG 数据迁移与校验
- [x] 五处进程内状态下沉 Redis/PG
- [x] `--workers` 支持与压测
- [ ] 备份切换到 PG（`scripts/backup.py --middleware` 已可用，部署切换后设为默认）
- [ ] 多 worker 部署观察 1 天

### 阶段 3

- [x] `runtime/graph.py` 图定义（七节点，语义对齐 v1）
- [x] 检查点：Redis 摘要镜像 + KernelStore 权威恢复（interrupt/Command 未采用，见 §4.6）
- [x] 调用方（api/jobs、eval、cli）经 `build_runtime` 自动选择，无需逐个改造
- [x] 图级等价性测试 12 条 + 本地真实模型冒烟
- [x] 四场景评测回归（sql 6/6、rag 9/10、office 5/5、code 5/5，与 legacy 对照一致）
- [ ] 灰度一周后删除 legacy 代码

### 阶段 4

- [ ] PG 记忆表 + Milvus collection
- [ ] 抽取 worker + RabbitMQ 拓扑 + DLQ
- [ ] 召回注入 build_context
- [ ] 夜间批量任务
- [ ] 记忆评测集与开关验证

---

*阶段 0 完成并通过验收后，再进入阶段 1。任何阶段发现问题，先关开关回退，再定位修复。*
