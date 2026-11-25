-- v2 阶段 4：记忆子系统（结构化记忆 + 版本 + 标签）
-- 时间字段沿用 TEXT（ISO 8601）与 001_init.sql 保持一致，避免驱动层类型差异。
-- 向量在 Milvus collection `memories`，可由本表内容重建，不参与备份。

CREATE TABLE IF NOT EXISTS memories(
  id               TEXT PRIMARY KEY,
  user_id          TEXT NOT NULL,
  type             TEXT NOT NULL,              -- preference | fact | decision | entity
  content          TEXT NOT NULL,
  importance       DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  confidence       DOUBLE PRECISION NOT NULL DEFAULT 0.5,
  status           TEXT NOT NULL DEFAULT 'active',   -- active | archived
  source_session   TEXT,
  source_seq       INTEGER,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL,
  last_recalled_at TEXT,
  recall_count     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_memories_user
  ON memories(user_id, status, type);
CREATE INDEX IF NOT EXISTS idx_memories_session
  ON memories(source_session);

CREATE TABLE IF NOT EXISTS memory_versions(
  id         BIGSERIAL PRIMARY KEY,
  memory_id  TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  content    TEXT NOT NULL,
  reason     TEXT NOT NULL,                    -- create | merge | manual | nightly-merge
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_versions_memory
  ON memory_versions(memory_id, id DESC);

CREATE TABLE IF NOT EXISTS memory_tags(
  memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  tag       TEXT NOT NULL,
  PRIMARY KEY (memory_id, tag)
);
