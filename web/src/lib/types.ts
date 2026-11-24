export type Role = "admin" | "user";
export type SessionStatus = "running" | "awaiting_approval" | "done" | "failed";

export interface User {
  id: string;
  username: string;
  role: Role;
  disabled: boolean;
  created_at: string;
  last_login_at: string | null;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
  refresh_token?: string | null;
}

export interface ProfileInfo {
  name: string;
  description: string | null;
  max_permission: number;
  max_cost_cny: number | null;
}

export interface Session {
  session_id: string;
  profile: string;
  title: string | null;
  owner_id: string | null;
  status: SessionStatus;
  cost: number;
  step: number;
  final_answer: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
}

export interface Message {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  tool_calls: Array<Record<string, unknown>>;
  tool_call_id: string | null;
}

export interface AgentEvent {
  seq: number;
  ts: string;
  session_id: string;
  type: string;
  payload: Record<string, unknown>;
}

export interface Approval {
  call_id: string;
  tool: string;
  permission: number;
  args: Record<string, unknown>;
  status: "pending" | "approved" | "denied" | string;
}

export interface KBDoc {
  doc_id: string;
  title: string;
  status: "active" | "deleted" | string;
  size: number;
  sha256: string;
  suspicious: boolean;
  created_at: string;
  updated_at: string;
  created_by: string | null;
  updated_by: string | null;
  indexed: boolean;
  chunks: number;
}

export interface KBDocDetail extends KBDoc {
  content: string;
}

export interface KBDocVersion {
  id: string;
  doc_id: string;
  sha256: string;
  actor: string | null;
  created_at: string;
}

export interface KBStatus {
  docs_total: number;
  docs_deleted: number;
  suspicious_docs: number;
  chunks: number;
  index_docs: number;
  stale: boolean;
  built_at: string | null;
  fingerprint: string;
  indexing: boolean;
  cooldown_s: number;
  last_job: KBJob | null;
}

export interface KBJob {
  id: string;
  kind: string;
  doc_ids: string | null;
  status: "running" | "done" | "failed" | string;
  message: string | null;
  actor: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface KBAuditItem {
  id: number;
  actor: string | null;
  action: string;
  doc_id: string | null;
  detail: string | null;
  created_at: string;
}

export interface Invite {
  id: string;
  role: Role;
  expires_at: string | null;
  max_uses: number;
  used_count: number;
  revoked: boolean;
  created_at: string;
}

export interface InviteCreated extends Invite {
  code: string;
}

export interface Quota {
  max_cost_per_session: number | null;
  daily_cost_limit: number | null;
  concurrent_sessions: number;
  eval_daily_limit: number;
  used_today?: number;
  eval_used_today?: number;
}

export interface SystemInfo {
  root: string;
  db_path: string;
  workspace_dir: string;
  trace_dir: string;
  providers: Record<string, boolean>;
  models: Record<
    string,
    { provider: string; tier: string; supports_tools: boolean }
  >;
  global_max_parallel: number;
  code_admin_only: boolean;
  cookie_secure: boolean;
  jwt_secret_is_default: boolean;
  running_jobs: number;
}

/* ------------------------------ 评测 ------------------------------ */
export interface EvalSuite {
  profile: string;
  description: string | null;
  cases: number | null;
  suite: string | null;
  ready: boolean;
}

export interface EvalModelSummary {
  total: number;
  passed: number;
  cost: number;
  pass_rate: number;
}

export interface EvalSummary {
  profile: string;
  total: number;
  passed: number;
  pass_rate: number;
  total_cost: number;
  by_model: Record<string, EvalModelSummary>;
}

export type EvalJobStatus = "running" | "done" | "failed" | string;

export interface EvalJob {
  id: string;
  actor: string;
  profile: string;
  models: string[] | null;
  repeat: number;
  limit_cases: number | null;
  status: EvalJobStatus;
  message: string | null;
  report_id: string | null;
  summary: EvalSummary | null;
  created_at: string;
  finished_at: string | null;
}

export interface EvalStatus {
  running: boolean;
  job_id: string | null;
  used_today: number;
  daily_limit: number;
  can_run: boolean;
  last_job: EvalJob | null;
}

export interface EvalReportSummary {
  id: string;
  profile: string;
  created_at: string;
  summary: EvalSummary;
}

export interface EvalCaseResult {
  case_id: string;
  model: string;
  passed: boolean;
  reason: string;
  status: string;
  steps: number;
  cost: number;
  error: string | null;
  graders: Array<{ passed: boolean; reason: string }>;
}

export interface EvalReportDetail {
  id: string;
  summary: EvalSummary;
  results: EvalCaseResult[];
}

/* --------------------------- 审计 / MCP --------------------------- */
export interface AuditItem {
  seq: number;
  ts: string;
  session_id: string;
  owner_id: string | null;
  type: string;
  [key: string]: unknown;
}

export interface MCPServer {
  name: string;
  command: string;
  args: string[];
  permission: number;
  timeout_s: number;
}
