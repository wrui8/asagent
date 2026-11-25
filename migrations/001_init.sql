-- v2 阶段 2：PostgreSQL 权威库初始 schema
-- 与 SQLite（api/db.py + runtime/checkpoint.py）语义一致。
-- JSON 字段一律用 TEXT 存原文，读取侧仍按 json.loads 解析，避免驱动层类型差异。

-- ------------------------------------------------------------------ --
-- 内核：会话 / 事件 / 快照 / 工具幂等 / artifact / 审批
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY,
  profile TEXT NOT NULL,
  title TEXT,
  status TEXT NOT NULL,
  owner_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_owner ON sessions(owner_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS events(
  seq BIGSERIAL PRIMARY KEY,
  session_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, seq);

CREATE TABLE IF NOT EXISTS checkpoints(
  session_id TEXT NOT NULL,
  step INTEGER NOT NULL,
  state_json TEXT NOT NULL,
  last_seq INTEGER NOT NULL,
  ts TEXT NOT NULL,
  PRIMARY KEY(session_id, step)
);

CREATE TABLE IF NOT EXISTS tool_invocations(
  session_id TEXT NOT NULL,
  call_id TEXT NOT NULL,
  tool TEXT NOT NULL,
  args_json TEXT NOT NULL,
  permission INTEGER NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  PRIMARY KEY(session_id, call_id)
);

CREATE TABLE IF NOT EXISTS artifacts(
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  meta_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals(
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  call_id TEXT NOT NULL,
  status TEXT NOT NULL,
  decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_approvals_call ON approvals(session_id, call_id);

-- ------------------------------------------------------------------ --
-- API：用户 / 邀请码 / 令牌 / 配额 / 用量 / 可见性
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  disabled BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS invites(
  id TEXT PRIMARY KEY,
  code_hash TEXT NOT NULL UNIQUE,
  created_by TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user',
  expires_at TEXT,
  max_uses INTEGER NOT NULL DEFAULT 1,
  used_count INTEGER NOT NULL DEFAULT 0,
  revoked BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens(
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_refresh_user ON refresh_tokens(user_id);

CREATE TABLE IF NOT EXISTS user_quotas(
  user_id TEXT PRIMARY KEY,
  max_cost_per_session DOUBLE PRECISION,
  daily_cost_limit DOUBLE PRECISION,
  concurrent_sessions INTEGER NOT NULL DEFAULT 1,
  eval_daily_limit INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_usage(
  user_id TEXT NOT NULL,
  day TEXT NOT NULL,
  cost DOUBLE PRECISION NOT NULL DEFAULT 0,
  PRIMARY KEY(user_id, day)
);

CREATE TABLE IF NOT EXISTS hidden_sessions(
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, session_id)
);

-- ------------------------------------------------------------------ --
-- 知识库元数据（正文仍在 data/docs/*.md）
CREATE TABLE IF NOT EXISTS kb_docs(
  doc_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  sha256 TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0,
  suspicious BOOLEAN NOT NULL DEFAULT FALSE,
  created_by TEXT,
  updated_by TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kb_versions(
  id TEXT PRIMARY KEY,
  doc_id TEXT NOT NULL,
  content TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kb_versions_doc ON kb_versions(doc_id, created_at DESC);

CREATE TABLE IF NOT EXISTS kb_jobs(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  status TEXT NOT NULL,
  doc_ids TEXT,
  message TEXT,
  created_by TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_kb_jobs_status ON kb_jobs(status, created_at DESC);

CREATE TABLE IF NOT EXISTS kb_audit(
  id BIGSERIAL PRIMARY KEY,
  ts TEXT NOT NULL,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  doc_id TEXT,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_kb_audit_ts ON kb_audit(ts DESC);

-- ------------------------------------------------------------------ --
-- 评测任务（报告正文在 runtime/evals/*.json）
CREATE TABLE IF NOT EXISTS eval_jobs(
  id TEXT PRIMARY KEY,
  actor TEXT NOT NULL,
  profile TEXT NOT NULL,
  models TEXT,
  repeat INTEGER NOT NULL DEFAULT 1,
  limit_cases INTEGER,
  status TEXT NOT NULL,
  message TEXT,
  report_id TEXT,
  summary_json TEXT,
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_jobs_created ON eval_jobs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_jobs_status ON eval_jobs(status);
