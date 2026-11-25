# 平台演进方案 v2：框架化与中间件分层

> 项目：学习型企业级 Agent 系统
> 状态：方案已确认，待分阶段实施
> 日期：2026-11-25
> 前置文档：`docs/architecture.md`（v1：手写内核，F0–F6 已交付）、`docs/deploy.md`（现行部署拓扑）
> 本文档回答三件事：**长什么样（框架图）、怎么跑（流程）、什么时候发生什么（时序）**。

---

## 0. 一页纸摘要

v1 用一副手写内核把「路由 → 上下文 → 模型 → 工具 → 护栏 → 检查点」跑通了，单机单
worker 稳定运行。v2 要解决的是**规模化与可演进性**：

| 问题（v1 现状） | v2 方案 |
| --- | --- |
| 编排循环 869 行手写，难扩展条件分支 / 子图 / 人在环路 | **LangGraph 直接替换**（StateGraph + Redis checkpointer） |
| RAG 用 SQLite + numpy 全量暴力检索，文档一多就慢 | **LlamaIndex + Milvus**（dense + sparse 混合检索） |
| embedding 用 bge-base-zh-v1.5（768 维），能力一般 | **bge-m3（1024 维）+ TEI CPU 推理服务** |
| 数据全在 SQLite，多 worker 写会锁 | **PostgreSQL 权威库 + SQLite 本地库**（索引指纹 / 埋点） |
| 5 处进程内状态（任务、僵尸恢复、索引单飞、评测、限流）硬锁单 worker | **Redis** 承接锁 / 计数 / 限流 / 短期上下文 / 检查点 |
| 无长期记忆 | **记忆子系统**：RabbitMQ 异步抽取 + PG 结构化 + Milvus 向量 + Redis 摘要缓存 |
| 部署只有 `api` 一个服务 | **compose profiles 分组**：`core` / `rag` / `state` |

**边界**：单机部署，不承诺高可用（HA、多机、异地容灾属于阶段 5 的可选项）。
**硬约束不变**：云端只用 DeepSeek；桌面/移动端地址写死 `https://agent.woaiwyj.xin`；
`data/docs/*.md` 与知识库版本记录是原始数据，任何迁移都不得丢。

---

## 1. 目标与原则

### 1.1 目标

1. **框架化**：编排交给 LangGraph，RAG 交给 LlamaIndex，不再自研循环与向量检索。
2. **可水平扩**：同一套代码能跑 `uvicorn --workers N`，进程内状态全部外置。
3. **可观测**：每一步仍落事件流，17 种事件类型、SSE 协议、前端契约**保持不变**。
4. **可回滚**：每个阶段都有开关（环境变量 / compose profile），出问题能退回 v1 行为。
5. **可评测**：迁移前后跑同一套黄金集，用通过率说话，不用感觉说话。

### 1.2 设计原则（延续 v1 并新增两条）

- **工具的返回值是不可信数据**：信任边界在代码层，不靠提示词。
- **状态显式、事件溯源**：进度可查、崩溃可恢复、审批可回溯。
- **存储分层，单一权威**：每类数据只有一个写入真相源，缓存层随时可丢。
- **派生数据可丢，原始数据不可丢**：向量索引、摘要缓存、checkpoint 可重建；
  Markdown 原文、PG 中的文档版本与记忆条目必须备份。
- **先加开关再改行为**：新增后端都通过环境变量选择，默认值保持 v1 可用。

---

## 2. 最终选型

| 层 | v1 | v2 | 选择理由 |
| --- | --- | --- | --- |
| 编排 | 手写 `AgentRuntime` | **LangGraph**（StateGraph） | 条件边、子图、`interrupt()` 人在环路、可持久化 checkpoint 都是现成的 |
| RAG 框架 | 自研 `RagIndex` | **LlamaIndex**（Ingestion + Query Engine） | 切块、元数据、混合检索、引用拼装开箱即用 |
| 向量库 | SQLite + numpy 暴力 | **Milvus 2.5+ Standalone** | ANN、标量过滤、dense+sparse 混合、别名原子切换 |
| Embedding | bge-base-zh-v1.5（768） | **bge-m3（1024）** | 长上下文（8192）、多语言、dense+sparse 一体化、MIT |
| 推理服务 | 进程内 sentence-transformers | **TEI（CPU 镜像）** | 与 API 进程解耦、可独立重启与压测；**8 GB 显存不够，必须 CPU** |
| Checkpoint | SQLite `checkpoints` 表 | **Redis**（`langgraph-checkpoint-redis`） | 低延迟、多 worker 共享、TTL 自动清理 |
| 权威库 | SQLite `state.db` | **PostgreSQL** | 多连接并发写、事务、后续可换托管 PG |
| 本地库 | — | **SQLite `runtime/local.db`** | 索引指纹、埋点、任务调度快照，进程本地、丢了可重建 |
| 缓存 / 协调 | 进程内 | **Redis** | 会话锁、并发计数、限流滑窗、记忆摘要缓存 |
| 消息队列 | 无 | **RabbitMQ** | 记忆抽取削峰、夜间批量、失败重试 + 死信队列 |
| 模型 | DeepSeek + vLLM 本地 | 不变 | 云端只用 DeepSeek |

> **维度澄清**：Milvus 单向量字段上限是 32768 维，1024 维毫无压力。必须重建的原因是
> **向量空间随模型变化不兼容**（768 维模型产出的向量与 1024 维模型不可比），且 collection
> 的 schema 维度在创建时固定。因此 v2 不迁移旧向量，直接全量重建。

---

## 3. 总体架构

### 3.1 框架图（逻辑分层）

```mermaid
flowchart TB
  subgraph CLIENT["① 客户端"]
    WEB["Web（React）"]
    DESK["桌面端（Tauri）"]
    MOB["移动端（Expo）"]
    CLI["CLI"]
  end

  subgraph EDGE["② 接入层：FastAPI（uvicorn --workers N）"]
    AUTH["JWT 鉴权 · 配额 · 限流"]
    R_SESS["会话 / SSE 事件流"]
    R_KB["知识库管理"]
    R_ADM["管理 / 评测 / 审计"]
  end

  subgraph ORCH["③ 编排层：LangGraph StateGraph"]
    N1["guard_input"]
    N2["route"]
    N3["build_context"]
    N4["model"]
    N5["execute_tools"]
    N6["guard_final"]
    N7["finalize"]
    N1 --> N2 --> N3 --> N4
    N4 -->|tool_calls| N5 --> N3
    N4 -->|final| N6 --> N7
  end

  subgraph INFER["④ 推理层"]
    ROUTER["Model Router（规则 + 失败升级）"]
    DS["DeepSeek API（云）"]
    VLLM["vLLM + Qwen2.5-7B（本地）"]
  end

  subgraph TOOLS["⑤ 工具层"]
    REG["Tool Registry<br/>校验 · 权限 · 审批 · 超时"]
    RAG["RAG 工具<br/>list / search / read"]
    SQLT["SQL 工具（只读）"]
    CODET["Code 沙箱"]
    OFFT["Office 工具"]
  end

  subgraph STATE["⑥ 状态与记忆层"]
    CP["Redis Checkpointer"]
    REDIS["Redis：锁 / 并发 / 限流 / 短期上下文 / 摘要缓存"]
    PG["PostgreSQL：权威库"]
    SQLITE["SQLite local.db：指纹 / 埋点"]
    MQ["RabbitMQ：记忆抽取 / 夜间批量"]
  end

  subgraph VEC["⑦ 检索层"]
    LI["LlamaIndex"]
    TEI["TEI（bge-m3，CPU）"]
    MILVUS["Milvus：kb_chunks / memories"]
  end

  CLIENT --> EDGE
  EDGE --> ORCH
  ORCH --> ROUTER
  ROUTER --> DS
  ROUTER --> VLLM
  ORCH --> REG
  REG --> RAG
  REG --> SQLT
  REG --> CODET
  REG --> OFFT
  ORCH --> CP
  ORCH --> PG
  EDGE --> REDIS
  EDGE --> PG
  RAG --> LI
  LI --> TEI
  LI --> MILVUS
  ORCH -. 会话结束事件 .-> MQ
  MQ -. 抽取 worker .-> PG
  MQ -. 向量写入 .-> MILVUS
  ORCH --> SQLITE
```

要点：

- **接入层无状态**：任何 worker 都能处理任何请求，会话状态从 Redis/PG 读。
- **编排层是图不是循环**：节点职责与 v1 的 `AgentRuntime` 一一对应（见 §11），
  事件类型与前端契约不变。
- **检索层独立成服务**：TEI 与 Milvus 可在 `rag` profile 下单独启停，API 仍可用（降级为无检索）。
- **记忆是旁路**：抽取走 MQ 异步，不阻塞对话主链路；失败丢进 DLQ，不影响当前会话。

### 3.2 部署视图（本机 Docker + compose profiles）

```mermaid
flowchart LR
  subgraph HOST["Windows 宿主机（Docker Desktop）"]
    subgraph P_CORE["profile: core"]
      API["asagent-api<br/>:8080 → 8000"]
    end
    VLLMC["vLLM（宿主进程 :8000，非容器）"]
    subgraph P_RAG["profile: rag"]
      TEIC["tei-bge-m3（CPU）<br/>:8081 → 80"]
      MV["milvus-standalone<br/>:19530<br/>内置 etcd + local storage"]
    end
    subgraph P_STATE["profile: state"]
      PGC["postgres :5432"]
      RDC["redis :6379"]
      MQC["rabbitmq :5672 / :15672"]
    end
    WD["memory-worker<br/>（独立容器，消费 MQ）"]
  end

  CF["Cloudflare Tunnel<br/>agent.woaiwyj.xin"]
  CF -->|HTTPS| API
  API --> TEIC
  API --> TEIC
  API --> MV
  API --> PGC
  API --> RDC
  API --> MQC
  WD --> MQC
  WD --> PGC
  WD --> MV
  WD --> TEIC
  API --> VLLMC
```

compose profile 的设计目的：**按需启停**。只做对话与 SQL 场景时 `--profile core`
即可；做知识库时加 `rag`；需要多 worker / 记忆时加 `state`。

启动命令（示例，密码只写在 `deploy/.env`，不进仓库）：

```powershell
docker compose -f deploy/docker-compose.yml --profile core --profile rag --profile state up -d
```

### 3.3 数据归属（单一权威）

| 数据 | 权威存储 | 派生 / 缓存 | 丢失影响 |
| --- | --- | --- | --- |
| 用户、邀请码、刷新令牌、配额、用量 | PG | Redis 计数（可重算） | 影响登录与配额 |
| 会话元数据、事件流 | PG `sessions` / `events` | — | 会话历史丢失 |
| 工具箱调用、审批、artifacts | PG | — | 审计与恢复能力下降 |
| LangGraph checkpoint | **Redis** | 可从 `events` 重建 | 运行中的会话需重跑当前步 |
| 文档原文 `data/docs/*.md` | **磁盘文件** | — | 不可恢复，必须备份 |
| 文档版本 | PG `kb_versions` | — | 回滚能力丢失 |
| 文档向量 `kb_chunks` | **Milvus（可重建）** | — | 重建索引即可 |
| 记忆条目 / 版本 / 标签 | **PG** | — | 记忆丢失 |
| 记忆向量 `memories` | **Milvus（可重建）** | — | 从 PG 重新编码 |
| 索引指纹、埋点 | SQLite `local.db` | — | 仅影响增量判断，可重建 |
| 摘要缓存、短期上下文、锁 | Redis | — | 瞬时状态，允许丢失 |

> 索引/向量/缓存都是**派生数据**，允许直接清空重建；原文与 PG 记录是**原始数据**，迁移前必须备份。

---

## 4. 核心流程

### 4.1 单次会话执行流程（LangGraph 图）

```mermaid
flowchart TD
  START(["会话创建 / 恢复"]) --> GI["guard_input<br/>注入检测 · 意图越界"]
  GI -->|拦截| FIN["finalize（拒绝答复）"]
  GI -->|通过| RT["route<br/>规则分流：本地 / 云端"]
  RT --> BC["build_context<br/>固定区 + 记忆 + 历史 + 工具输出"]
  BC --> OVF{"超出窗口?"}
  OVF -->|是| CUT["裁剪 / 摘要<br/>必要时升级大窗口模型"]
  CUT --> MD
  OVF -->|否| MD["model<br/>带工具 schema 调用"]
  MD --> DEC{"决策类型"}
  DEC -->|tool_calls| CHK["权限与预算检查"]
  CHK -->|DANGEROUS 且无审批人| INT["interrupt<br/>会话挂起 awaiting_approval"]
  INT -.->|用户批准/拒绝| RES["Command(resume)"]
  RES --> EX
  CHK -->|READ / 已批准| EX["execute_tools<br/>Registry：校验 · 超时 · 审计"]
  EX --> TO["guard_tool_output<br/>注入打标 · 大输出落 artifact"]
  TO --> CPK["写 checkpoint + 事件"]
  CPK --> LIM{"步数 / 预算 / 循环检测"}
  LIM -->|未超限| BC
  LIM -->|超限| FAIL["finalize（失败 + 原因）"]
  DEC -->|final| GF["guard_final<br/>敏感信息 · 引用完整性"]
  GF -->|通过| FIN2["finalize（成功）"]
  GF -->|不通过且首次| MD
  FIN --> END(["会话结束"])
  FIN2 --> END
  FAIL --> END
```

与 v1 的对应关系：这张图就是 `runtime/loop.py` 的 while 循环 + 四阶段 Guard + 审批挂起，
只是把控制流显式化成节点与边。**终止条件不变**：`final` 通过校验、`max_steps`（12）、
预算耗尽、重复调用检测、用户中断。

### 4.2 知识库索引构建流程

```mermaid
flowchart TD
  A["管理员保存 / 删除文档"] --> B["PG：kb_docs + kb_versions<br/>磁盘：data/docs"]
  B --> C["POST /api/kb/reindex"]
  C --> D{"Redis SETNX<br/>kb:index:lock"}
  D -->|已被占用| D1["409 index_busy"]
  D -->|获取成功| E["发布 kb.index 消息<br/>或进程内任务（阶段 1）"]
  E --> F["读取文档 → LlamaIndex 切块<br/>带 doc_id / title / section 元数据"]
  F --> G["TEI 批量编码<br/>dense 1024 维 + sparse 权重"]
  G --> H["写入新 collection<br/>kb_chunks_vN"]
  H --> I["校验条数 / 抽样召回"]
  I -->|失败| J["保留旧 alias，任务标记失败"]
  I -->|成功| K["alter_alias<br/>kb_chunks → kb_chunks_vN"]
  K --> L["删除旧 collection<br/>更新 SQLite 指纹"]
  L --> M["释放锁，写 kb_jobs 记录"]
  J --> M
```

关键设计：

- **不做旧向量迁移**：模型维度与语义空间都变了，旧 collection 直接废弃。
- **蓝绿切换**：新 collection 全量建好、抽样验证通过后再切 alias，检索不出现空窗；
  任何一步失败都保留旧索引。
- **增量优化**：文档未变化（sha256 相同）时复用已有分块与向量，只重算变更文档。

### 4.3 记忆抽取流程（异步，旁路）

```mermaid
flowchart TD
  A["会话进入 DONE"] --> B{"过滤条件"}
  B -->|轮数 < 3 或 无新信息| Z["跳过"]
  B -->|满足| C["published<br/>memory.extract 消息"]
  C --> D["RabbitMQ 队列<br/>memory.extract"]
  D --> E["memory-worker 消费"]
  E --> F["读取会话事件<br/>用户输入 + 最终答复"]
  F --> G["LLM 结构化抽取<br/>type / content / tags / confidence"]
  G --> H{"与已有记忆相似?"}
  H -->|相似度 > 0.92| I["合并：写 memory_versions<br/>更新 memories.content"]
  H -->|否| J["新建记忆条目<br/>PG memories"]
  I --> K["TEI 编码 → Milvus upsert"]
  J --> K
  K --> L["ACK 消息"]
  G -->|解析失败 / 超时| M["重试 3 次（指数退避）"]
  M -->|仍失败| N["进入 DLQ<br/>人工排查"]
```

记忆类型（第一版只做四类，避免噪声）：

| type | 含义 | 例子 |
| --- | --- | --- |
| `preference` | 用户偏好 | 「回答先给结论，再给细节」 |
| `fact` | 稳定事实 | 「团队用 PostgreSQL 16」 |
| `decision` | 已达成的决定 | 「RAG 选型定为 Milvus」 |
| `entity` | 重要实体与别名 | 「项目代号 AS」 |

抽取是**成本敏感**操作：每用户每日配额、单会话最多一次、低于阈值的会话直接跳过；
夜间批量只做巩固（合并近似条目、衰减旧记忆权重），不重新读原文。

### 4.4 记忆召回流程

```mermaid
flowchart TD
  A["新会话 build_context"] --> B["Redis：mem:{user}:summary"]
  B -->|命中| C["直接注入摘要"]
  B -->|未命中| D["Milvus：按 user_id 过滤<br/>检索 top 20"]
  D --> E["PG：取条目详情与状态"]
  E --> F["重排：相似度 × 重要度 × 时间衰减"]
  F --> G["取 top 5 → 生成摘要"]
  G --> H["写 Redis（TTL 15 min）"]
  H --> C
  C --> I["注入 system 上下文<br/>标记为『长期记忆，可能过时』"]
```

召回结果只作为**参考信息**注入，不覆盖用户当前输入；过期与冲突由模型按提示词规则处理。
记忆相关工具（`search_memory` / `save_memory`）保留给显式调用场景。

---

## 5. 关键时序

### 5.1 一次对话：创建 → 流式事件 → 结束

```mermaid
sequenceDiagram
  autonumber
  participant U as 用户
  participant API as FastAPI Worker
  participant RD as Redis
  participant G as LangGraph
  participant PG as PostgreSQL
  participant M as 模型（DeepSeek/vLLM）
  participant T as 工具注册表
  participant MQ as RabbitMQ

  U->>API: POST /api/sessions
  API->>RD: 并发计数 INCR + 会话锁 SETNX
  API->>PG: 创建 session（running）
  API-->>U: 201 session_id
  U->>API: GET /api/sessions/{id}/stream（SSE）
  API->>G: 启动图（thread_id = session_id）
  G->>RD: 写初始 checkpoint

  loop 每一步
    G->>PG: 追加事件（seq 递增）
    API->>PG: 轮询新事件（cursor）
    API-->>U: SSE: route_selected / context_built
    G->>M: 模型调用（带工具 schema）
    M-->>G: tool_calls 或 final
    G->>T: 执行工具
    T-->>G: 结构化结果（不可信文本包裹）
    G->>PG: tool_call / tool_result / guard_verdict
    G->>RD: checkpoint 快照
  end

  G->>PG: final 事件 + session DONE
  API-->>U: SSE: final（随后关闭连接）
  G->>MQ: 发布 memory.extract（满足条件时）
  API->>RD: 释放并发计数与会话锁
```

> SSE 仍是**轮询 PG 事件表**，天然支持多 worker：谁接流谁读表，不依赖进程内广播。

### 5.2 审批挂起与恢复（人在环路）

```mermaid
sequenceDiagram
  autonumber
  participant U as 用户
  participant API as FastAPI
  participant G as LangGraph
  participant RD as Redis
  participant PG as PostgreSQL

  G->>G: 命中 DANGEROUS 工具
  G->>PG: 写 approvals(pending) + tool_invocations
  G->>G: interrupt(payload)
  G->>RD: 保存 checkpoint（挂起）
  G->>PG: approval_required 事件
  API-->>U: SSE: approval_required
  Note over API,U: 会话状态 awaiting_approval，工作线程释放

  U->>API: POST /api/sessions/{id}/approve（approve/deny）
  API->>PG: 写 approval_decided 事件
  API->>G: Command(resume={"decision": ...})
  G->>RD: 读取 checkpoint 恢复
  alt 批准
    G->>G: 执行工具
  else 拒绝
    G->>G: 返回 approval_required 结果给模型
  end
  G->>PG: 继续追加事件
  API-->>U: SSE 继续输出
```

**崩溃安全**：恢复时进入 `unknown` 的工具调用一律重新审批，绝不自动重试写操作。

### 5.3 RAG 检索一次问答

```mermaid
sequenceDiagram
  autonumber
  participant G as LangGraph
  participant T as search_docs 工具
  participant LI as LlamaIndex
  participant TEI as TEI（CPU）
  participant MV as Milvus
  participant PG as PostgreSQL

  G->>T: search_docs(query, top_k=5)
  T->>LI: retriever.retrieve
  LI->>TEI: /embed（query，dense 1024）
  TEI-->>LI: query vector
  LI->>TEI: /embed_sparse（可选，BM25/稀疏权重）
  LI->>MV: hybrid search（dense + sparse，过滤 status=active）
  MV-->>LI: top_k hits（doc_id / section / score）
  LI->>PG: 可选：校验文档状态与版本
  LI-->>T: 命中列表 + 文本
  T-->>G: data.hits（citation / doc_id / title / section / score / text）
  G->>G: 拼装引用，继续推理
```

工具返回结构与 v1 完全一致（`data.hits`），前端与评测脚本无需改动。

**降级**：

- TEI 不可用时搜索工具返回明确错误，`build_context` 继续（模型可改用 `list_docs` / `read_doc`）。
- 若所用 TEI 版本不提供 `/embed_sparse`，稀疏侧改用 Milvus 2.5+ 内置 BM25 函数
  （analyzer 分词），dense 侧仍走 bge-m3，混合检索接口不变。
- Milvus 不可用时 `search_docs` 直接失败，不静默返回空结果——避免模型把「检索坏了」
  误判成「知识库没有相关内容」。

### 5.4 多 worker 与僵尸会话恢复

```mermaid
sequenceDiagram
  autonumber
  participant W1 as Worker 1
  participant W2 as Worker 2
  participant RD as Redis
  participant PG as PostgreSQL

  W1->>RD: SET session:{id}:lock（TTL 30s）
  W1->>RD: SET heartbeat:{id}（TTL 60s，每 20s 续期）
  W1->>PG: 事件持续追加

  Note over W1: 进程崩溃
  Note over RD: heartbeat 键到期自动删除
  W2->>PG: 定时扫描 running 会话
  W2->>RD: EXISTS heartbeat?
  RD-->>W2: 0（已死）
  W2->>PG: 标记 FAILED，写 error 事件
  Note over W2: 用户点击「继续」时可 resume
```

v1 的 `_recover_zombies` 在启动时把所有 `RUNNING` 会话一刀切标失败，多 worker 下会互相误杀。
v2 改为**心跳 + TTL 判定**：只有心跳过期的会话才判定为僵尸，且由单一 worker 通过
Redis 锁竞争执行恢复，避免重复处理。

### 5.5 阶段迁移时序（整体节奏）

```mermaid
sequenceDiagram
  autonumber
  participant DEV as 开发
  participant CI as CI
  participant HOST as 本机
  participant PROD as asagent-api

  DEV->>DEV: 阶段 N 功能 + 开关默认关闭
  DEV->>CI: 推送分支 → PR
  CI->>CI: ruff / pytest / web / app / e2e / docker
  CI-->>DEV: 全绿
  DEV->>PROD: 合并 main → CI 构建镜像
  HOST->>PROD: deploy/pull-update.ps1
  HOST->>PROD: 打开阶段开关（环境变量）
  PROD-->>HOST: /api/health + /api/system/info 验证
  Note over HOST,PROD: 异常时关闭开关或回滚 -Image sha-xxxx
```

每个阶段都遵循「**先发布、默认关；再开开关、观察；稳定后成为默认**」的节奏。

---

## 6. 迁移路线（五阶段）

### 阶段 0：中间件基座（不碰业务代码）

| 项目 | 内容 |
| --- | --- |
| compose | `deploy/docker-compose.yml` 增加 `pg` / `redis` / `rabbitmq` / `tei` / `milvus`（内置 etcd + 本地磁盘存储），用 profiles 分组 |
| 配置 | 新增 `AS_PG_DSN`、`AS_REDIS_URL`、`AS_RABBITMQ_URL`、`AS_MILVUS_URI`、`AS_TEI_URL`、`AS_STORAGE_BACKEND`、`AS_RAG_BACKEND`、`AS_CHECKPOINTER`、`AS_WORKERS` |
| 健康 | `/api/system/info` 返回各中间件的连通性；`/api/health` 保持轻量 |
| 备份 | `scripts/backup.py` 扩展：`pg_dump` + Milvus 快照 + Redis BGSAVE + SQLite |
| CI | service containers 跑 PG / Redis / RabbitMQ；Milvus 在集成 job 内用 compose 起 |
| 验收 | 全部中间件 healthy；应用仍以 v1 行为运行（开关全关）；备份可恢复 |

**回滚**：关掉新服务即可，业务代码零改动。

### 阶段 1：RAG 迁移（Milvus + TEI + bge-m3）

| 项目 | 内容 |
| --- | --- |
| 适配器 | 新增 `factory.make_index` 按 `AS_RAG_BACKEND=sqlite|milvus` 选后端；`RagIndex` 保持 v1 不动 |
| 嵌入 | `AS_TEI_URL`；bge-m3 由 TEI（CPU）编码，1024 维 L2 归一化 |
| 索引 | 新 collection 全量构建 → alias 切换；旧 collection 删除 |
| 检索 | dense（1024）COSINE + 沿用 v1 的 0.7/0.3 打分；`top_k` 沿用 profile |
| 验收 | RAG 黄金集 10/10；引用可溯源；检索结果与 v1 一致 |

> 实施偏差：未引入 LlamaIndex，改为 pymilvus 直连（切块与打分复用现有实现），
> 详见 `platform-v2-impl.md` 2.4。

**回滚**：`AS_RAG_BACKEND=sqlite` 退回旧索引（旧数据保留到验收通过后一周再清）。

### 阶段 2：存储分层 + 多 worker

| 项目 | 内容 |
| --- | --- |
| 迁移 | `runtime/state.db` → PG；一次性脚本 + 校验行数；SQLite 降级为 `local.db` |
| 进程内状态 | `JobManager` → PG 会话/事件 + Redis `job:{session}` 锁（60 s TTL / 20 s 心跳）；`KBIndexRunner` / `EvalJobManager` → Redis SETNX（900 s / 3600 s）；`SlidingWindowLimiter` → Redis 固定窗口 INCR |
| 部署 | `asagent web --workers N`（或 `AS_WORKERS`，从 2 起步）；强制 postgres + Redis，否则启动即退出 |
| 验收 | 并发 20 会话压测无 500；kill 任一 worker 后其余继续服务；僵尸恢复不误杀 |
| 实施 | 已本地实测：PG 往返 7 条、Redis 5 条、SQLite→PG 17 表校验一致、2 worker 共享限流生效（详见 `platform-v2-impl.md` §3.5） |

**回滚**：`AS_STORAGE_BACKEND=sqlite` + `AS_WORKERS=1`（迁移后的数据保留在 PG，不丢）。

### 阶段 3：LangGraph 直接替换

| 项目 | 内容 |
| --- | --- |
| 删除 | `runtime/loop.py` 的 `AgentRuntime` 与手写循环 |
| 新增 | `runtime/graph.py`（`build_engine` + StateGraph）、Redis checkpointer |
| 改造 | `api/jobs.py`、`eval/runner.py`、`cli.py` 调用方；事件写入与 17 种事件类型不变 |
| 验收 | 四场景评测基线与 v1 一致或更好；审批挂起/恢复通过；崩溃后 resume 不重复副作用 |
| 实施 | 已落地 `runtime/graph.py`（StateGraph 七节点）+ `AS_ENGINE` 开关，默认 legacy；Redis 检查点为摘要镜像（标准 Redis 无 RediSearch），恢复仍以 KernelStore 为准（详见 `platform-v2-impl.md` §4.6） |

**回滚**：保留 `AS_ENGINE=legacy|langgraph` 一个版本周期，旧代码只冻结不再开发。

### 阶段 4：记忆子系统

| 项目 | 内容 |
| --- | --- |
| schema | PG：`memories` / `memory_versions` / `memory_tags`；Milvus：`memories` collection |
| 写入 | 会话 DONE → RabbitMQ → `memory-worker` 抽取 → PG + Milvus |
| 读取 | 会话开始召回 top 5 → Redis 摘要缓存 |
| 批量 | 夜间巩固 / 衰减 / 淘汰；DLQ 告警 |
| 验收 | 记忆召回评测集通过率 ≥ 基线；抽取失败不影响会话；配额与审计可查 |

**回滚**：`AS_MEMORY_ENABLED=false`，记忆相关表与 collection 保留不动。

### 阶段 5：后置（可选）

- 观测：Langfuse / OpenTelemetry，替换或补充 JSONL trace。
- 网关：LiteLLM 统一多 provider 路由（当前不需要，DeepSeek 单provider）。
- 高可用：PG 主从、Redis Sentinel、Milvus 集群、多机 API —— 单机不承诺 HA。

---

## 7. 配置与端口

### 7.1 新增环境变量

```dotenv
# 存储后端（阶段 2）
AS_STORAGE_BACKEND=sqlite            # sqlite | postgres
AS_PG_DSN=postgresql://asagent:***@127.0.0.1:5432/asagent

# 缓存与协调（阶段 0 起逐步启用）
AS_REDIS_URL=redis://127.0.0.1:6379/0

# 消息队列（阶段 4）
AS_RABBITMQ_URL=amqp://asagent:***@127.0.0.1:5672/

# 检索（阶段 1）
AS_RAG_BACKEND=sqlite                # sqlite | milvus
AS_MILVUS_URI=http://127.0.0.1:19530
AS_TEI_URL=http://127.0.0.1:8081
AS_EMBEDDING_MODEL=BAAI/bge-m3
AS_EMBEDDING_DIM=1024

# 编排（阶段 3）
AS_CHECKPOINTER=sqlite               # sqlite | redis
AS_ENGINE=langgraph                  # legacy | langgraph

# 记忆（阶段 4）
AS_MEMORY_ENABLED=false
AS_MEMORY_TOP_K=5
AS_MEMORY_DAILY_EXTRACT_LIMIT=20

# 并发（阶段 2）
AS_WORKERS=1
```

> 所有密码只放在 `deploy/.env` 或本机 `.env`（均已被 gitignore），文档与代码只引用变量名。

### 7.2 端口分配

| 服务 | 宿主端口 | 容器端口 | 说明 |
| --- | --- | --- | --- |
| asagent-api | 8080 | 8000 | 对外由 Cloudflare 隧道接入 |
| vLLM | 8000 | — | 宿主进程，非容器 |
| TEI | 8081 | 80 | CPU 镜像 |
| Milvus | 19530 / 9091 | 同 | gRPC / metrics（etcd 内嵌，不单独暴露端口） |
| PostgreSQL | 5432 | 5432 | 权威库 |
| Redis | 6379 | 6379 | 缓存 / 锁 / checkpoint |
| RabbitMQ | 5672 / 15672 | 同 | AMQP / 管理台 |

端口冲突检查：`netstat -ano | findstr :5432`（依次替换）。

---

## 8. 资源与容量

### 8.1 显存（已实测）

| 项目 | 数值 |
| --- | --- |
| GPU | RTX 4060 Laptop |
| 总显存 | 8188 MiB |
| 已用 | 7673 MiB（vLLM 约 6.7 GB + 桌面合成约 1 GB） |
| 空闲 | **285 MiB** |

结论：bge-m3 放 GPU 必然 OOM，**TEI 使用 CPU 镜像**。vLLM 参数保持
`--gpu-memory-utilization 0.82 --max-model-len 4096 --max-num-seqs 4`。

### 8.2 内存与 CPU（估算，按 16 GB 内存规划）

| 组件 | 内存估算 | CPU |
| --- | --- | --- |
| PostgreSQL | 300–600 MB | 低 |
| Redis | 50–150 MB | 低 |
| RabbitMQ | 200–400 MB | 低 |
| Milvus（内置 etcd + local storage） | 0.8–1.5 GB | 中 |
| TEI（bge-m3，4–6 线程） | 2.5–3.5 GB（FP32 权重约 2.2 GB） | 中高 |
| API（4 worker） | 0.8–1.5 GB | 中 |
| memory-worker | 300–600 MB | 低 |

合计约 **5.5–9 GB**。内存不足时的降级顺序：TEI 线程降到 2 → Milvus 改
`milvus-lite`（仅 Linux/WSL 可用）→ 记忆 worker 与 API 合进程（阶段 4 之前不启）。

### 8.3 检索性能目标

| 指标 | 目标 |
| --- | --- |
| TEI 单条 query 编码（CPU） | P95 < 500 ms |
| 单文档（2k 字）索引 | < 3 s |
| 全库（500 文档）重建 | < 15 min（夜间执行） |
| Milvus top-5 检索 | P95 < 50 ms |
| 记忆召回（含摘要） | P95 < 300 ms（缓存命中 < 20 ms） |

---

## 9. 风险与应对

| 风险 | 影响 | 应对 |
| --- | --- | --- |
| SQLite → PG 迁移丢数据 | 会话与用户数据不可恢复 | 迁移前双备份；脚本幂等 + 行数/抽样校验；保留旧库 7 天 |
| LangGraph 直接替换回归面大 | 行为不一致、评测掉分 | 事件契约不变；开关 `AS_ENGINE`；四场景评测全量回归；灰度一周 |
| 7 个中间件启动顺序与资源 | 本机起不来或抖动 | compose `depends_on` + healthcheck；profiles 按需启；文档给最小启动集 |
| 8 GB 显存 | GPU 侧 OOM | TEI 走 CPU；vLLM 参数不调大；不做本地多模型共存 |
| 记忆抽取噪声与成本 | 记忆质量差、费用上升 | 只抽四类；会话过滤；每用户每日配额；夜间巩固；召回结果标注可信度 |
| SSE 轮询 PG 压力 | 高并发时 DB QPS 上升 | 轮询间隔可调（默认 0.2 s）；按 session 索引；后续可换 LISTEN/NOTIFY |
| CPU 编码拖慢索引 | 重建窗口长 | 夜间批量；增量索引；TEI 批量大小与线程调优 |
| Windows 上 Milvus Lite 不可用 | 本地开发不便 | 开发用 Docker standalone；CI 用容器 |

---

## 10. 验收标准

| 阶段 | 通过条件（全部满足） |
| --- | --- |
| 0 | `docker compose ... ps` 全部 healthy；`/api/system/info` 显示依赖连通；备份→恢复演练成功；CI 绿 |
| 1 | RAG 黄金集 10/10；引用可溯源；索引重建可回滚；检索延迟达标 |
| 2 | 20 并发压测 0 个 5xx；kill worker 不中断服务；僵尸恢复准确；`pytest` 全绿 |
| 3 | 四场景评测不低于 v1 基线；审批挂起/恢复通过；resume 无重复副作用 |
| 4 | 记忆召回评测达标；抽取失败不影响会话；DLQ 可查；关闭开关可完全停用 |

通用门禁：`ruff check` 干净、`pytest` 全绿、Web/App/E2E/镜像 CI 五个 job 绿、
生产 `asagent-api` healthy、公网 `https://agent.woaiwyj.xin` 可用。

---

## 11. 与 v1 的映射

| v1 组件（文件） | v2 去向 | 契约是否变化 |
| --- | --- | --- |
| `runtime/loop.py` `AgentRuntime` | LangGraph `StateGraph`（`runtime/graph.py`） | 否：事件类型、SSE、工具协议不变 |
| `runtime/checkpoint.py` `CheckpointStore` | PG 事件/审批表 + Redis checkpointer | 否：事件 payload 不变 |
| `runtime/context.py` `ContextBuilder` | 图节点 `build_context`（+ 记忆注入） | 否：裁剪规则沿用 |
| `runtime/factory.py` `build_runtime` | `build_engine`（构建图与依赖） | 调用方签名小幅调整 |
| `tools/rag/index.py` `RagIndex` | LlamaIndex 门面 + Milvus 后端 | 否：工具 `data.hits` 结构不变 |
| `api/jobs.py` `JobManager` | PG 任务表 + Redis 锁 | 否：路由层 API 不变 |
| `api/limiter.py` `SlidingWindowLimiter` | Redis ZSET 滑窗 | 否 |
| `api/app.py` `_recover_zombies` | 心跳 + TTL + 单例锁 | 否 |
| `tools/registry.py` | 保留，作为图节点 `execute_tools` 的实现 | 否 |
| `guard/pipeline.py` | 保留，拆成 `guard_input` / `guard_tool_output` / `guard_final` 节点 | 否 |
| `eval/runner.py` | 改为调用图引擎，评测输入输出不变 | 否：golden.jsonl 格式不变 |

> 一句话：**换的是引擎与存储，不换的是事件模型、工具契约、评测格式与前端接口。**

---

## 12. 实施顺序（下一步）

1. **阶段 0**：compose profiles、配置项、健康检查、备份脚本扩展、CI service containers。
   业务代码零改动，先让「基座能起、能测、能备份」。
2. 阶段 1：RAG 迁移与全量重建索引。
3. 阶段 2：PG 迁移 + 进程内状态下沉 + 多 worker。
4. 阶段 3：LangGraph 替换。
5. 阶段 4：记忆子系统。
6. 阶段 5：按需（观测 / 网关 / HA）。

每个阶段独立分支 → PR → CI 绿 → 合并 → 镜像 → 本机 `deploy\pull-update.ps1`，
开关默认关闭，验证通过后再打开。
