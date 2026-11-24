import {
  getApiBase,
  IS_TAURI,
  loadDesktopRefreshToken,
  saveDesktopRefreshToken,
} from "./platform";
import type {
  AgentEvent,
  Approval,
  AuditItem,
  EvalJob,
  EvalReportDetail,
  EvalReportSummary,
  EvalStatus,
  EvalSuite,
  Invite,
  InviteCreated,
  KBAuditItem,
  KBDoc,
  KBDocDetail,
  KBDocVersion,
  KBJob,
  KBStatus,
  MCPServer,
  Message,
  ProfileInfo,
  Quota,
  Role,
  Session,
  SystemInfo,
  TokenResponse,
  User,
} from "./types";

const TOKEN_KEY = "asagent.access";

let accessToken: string | null = sessionStorage.getItem(TOKEN_KEY);
let refreshFlight: Promise<boolean> | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
}

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

function notifyExpired(): void {
  setAccessToken(null);
  window.dispatchEvent(new Event("asagent:logout"));
}

async function parseError(resp: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `请求失败（${resp.status}）`;
  try {
    const body = await resp.json();
    if (body && typeof body === "object") {
      if (typeof body.code === "string") code = body.code;
      if (typeof body.message === "string") message = body.message;
      else if (typeof body.detail === "string") message = body.detail;
    }
  } catch {
    /* 非 JSON 响应，保留默认文案 */
  }
  return new ApiError(resp.status, code, message);
}

async function desktopRefresh(): Promise<boolean> {
  const stored = await loadDesktopRefreshToken();
  if (!stored) return false;
  try {
    const resp = await fetch(`${getApiBase()}/api/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Client": "desktop" },
      credentials: "omit",
      body: JSON.stringify({ refresh_token: stored }),
    });
    if (!resp.ok) {
      // 令牌已轮换失效才清除；网络或服务端异常保留，下次再试
      if (resp.status === 401) await saveDesktopRefreshToken(null);
      return false;
    }
    const data = (await resp.json()) as TokenResponse;
    setAccessToken(data.access_token);
    if (data.refresh_token) await saveDesktopRefreshToken(data.refresh_token);
    window.dispatchEvent(new CustomEvent("asagent:token", { detail: data }));
    return true;
  } catch {
    return false;
  }
}

export async function refreshToken(): Promise<boolean> {
  if (refreshFlight) return refreshFlight;
  refreshFlight = (async () => {
    try {
      if (IS_TAURI) return await desktopRefresh();
      const resp = await fetch(`${getApiBase()}/api/auth/refresh`, {
        method: "POST",
        credentials: "include",
      });
      if (!resp.ok) return false;
      const data = (await resp.json()) as TokenResponse;
      setAccessToken(data.access_token);
      window.dispatchEvent(new CustomEvent("asagent:token", { detail: data }));
      return true;
    } catch {
      return false;
    } finally {
      refreshFlight = null;
    }
  })();
  return refreshFlight;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  auth?: boolean;
  retryOn401?: boolean;
  signal?: AbortSignal;
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const {
    method = "GET",
    body,
    auth = true,
    retryOn401 = true,
    signal,
  } = options;

  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (IS_TAURI) headers["X-Client"] = "desktop";
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;

  const resp = await fetch(`${getApiBase()}${path}`, {
    method,
    headers,
    credentials: IS_TAURI ? "omit" : "include",
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });

  if (resp.status === 401 && auth) {
    if (retryOn401 && (await refreshToken())) {
      return request<T>(path, { ...options, retryOn401: false });
    }
    notifyExpired();
    throw await parseError(resp);
  }

  if (!resp.ok) throw await parseError(resp);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/* ------------------------------ 认证 ------------------------------ */
export async function login(username: string, password: string): Promise<TokenResponse> {
  const data = await request<TokenResponse>("/api/auth/login", {
    method: "POST",
    body: { username, password },
    auth: false,
  });
  setAccessToken(data.access_token);
  if (IS_TAURI && data.refresh_token) {
    await saveDesktopRefreshToken(data.refresh_token);
  }
  return data;
}

export async function register(
  username: string,
  password: string,
  inviteCode: string,
): Promise<TokenResponse> {
  const data = await request<TokenResponse>("/api/auth/register", {
    method: "POST",
    body: { username, password, invite_code: inviteCode },
    auth: false,
  });
  setAccessToken(data.access_token);
  if (IS_TAURI && data.refresh_token) {
    await saveDesktopRefreshToken(data.refresh_token);
  }
  return data;
}

export async function logout(): Promise<void> {
  try {
    const refresh = IS_TAURI ? await loadDesktopRefreshToken() : null;
    await request<void>("/api/auth/logout", {
      method: "POST",
      auth: false,
      body: refresh ? { refresh_token: refresh } : undefined,
    });
  } finally {
    setAccessToken(null);
    if (IS_TAURI) await saveDesktopRefreshToken(null);
  }
}

/* ------------------------------ 会话 ------------------------------ */
export function fetchMe(): Promise<User> {
  return request<User>("/api/auth/me");
}

export function fetchProfiles(): Promise<ProfileInfo[]> {
  return request<ProfileInfo[]>("/api/profiles");
}

export interface SessionQuery {
  limit?: number;
  offset?: number;
  profile?: string;
  status?: string;
  all?: boolean;
}

export function fetchSessions(query: SessionQuery = {}): Promise<Session[]> {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      params.set(key, String(value));
    }
  });
  const qs = params.toString();
  return request<Session[]>(`/api/sessions${qs ? `?${qs}` : ""}`);
}

export function fetchSession(id: string): Promise<Session> {
  return request<Session>(`/api/sessions/${id}`);
}

export function fetchMessages(id: string): Promise<Message[]> {
  return request<Message[]>(`/api/sessions/${id}/messages`);
}

export function fetchEvents(id: string, afterSeq = 0): Promise<AgentEvent[]> {
  return request<AgentEvent[]>(`/api/sessions/${id}/events?after_seq=${afterSeq}`);
}

export interface CreateSessionBody {
  profile: string;
  input: string;
  max_steps: number;
  max_cost_cny: number | null;
}

export function createSession(
  body: CreateSessionBody,
): Promise<{ session_id: string; status: string }> {
  return request("/api/sessions", { method: "POST", body });
}

export function cancelSession(id: string): Promise<unknown> {
  return request(`/api/sessions/${id}/cancel`, { method: "POST" });
}

export function resumeSession(id: string): Promise<unknown> {
  return request(`/api/sessions/${id}/resume`, { method: "POST" });
}

export function deleteSession(id: string): Promise<void> {
  return request<void>(`/api/sessions/${id}`, { method: "DELETE" });
}

export function fetchArtifact(
  sessionId: string,
  artifactId: string,
): Promise<{ id: string; session_id: string; content: string }> {
  return request(`/api/sessions/${sessionId}/artifacts/${artifactId}`);
}

/* ------------------------------ 审批 ------------------------------ */
export function fetchApprovals(id: string): Promise<Approval[]> {
  return request<Approval[]>(`/api/sessions/${id}/approvals`);
}

export function decideApproval(
  id: string,
  callId: string,
  approved: boolean,
  resume = true,
): Promise<Approval[]> {
  return request<Approval[]>(`/api/sessions/${id}/approvals`, {
    method: "POST",
    body: { call_id: callId, approved, resume },
  });
}

/* ------------------------------ 知识库 ------------------------------ */
export function fetchKBDocs(includeDeleted = true): Promise<KBDoc[]> {
  return request<KBDoc[]>(`/api/kb/docs?include_deleted=${includeDeleted}`);
}

export function fetchKBDoc(docId: string): Promise<KBDocDetail> {
  return request<KBDocDetail>(`/api/kb/docs/${docId}`);
}

export function createKBDoc(body: {
  doc_id: string;
  title?: string | null;
  content: string;
}): Promise<KBDocDetail> {
  return request<KBDocDetail>("/api/kb/docs", { method: "POST", body });
}

export function updateKBDoc(
  docId: string,
  body: { title?: string | null; content?: string },
): Promise<KBDocDetail> {
  return request<KBDocDetail>(`/api/kb/docs/${docId}`, { method: "PUT", body });
}

export function deleteKBDoc(docId: string): Promise<KBDoc> {
  return request<KBDoc>(`/api/kb/docs/${docId}`, { method: "DELETE" });
}

export function restoreKBDoc(docId: string): Promise<KBDocDetail> {
  return request<KBDocDetail>(`/api/kb/docs/${docId}/restore`, { method: "POST" });
}

export function fetchKBVersions(docId: string): Promise<KBDocVersion[]> {
  return request<KBDocVersion[]>(`/api/kb/docs/${docId}/versions`);
}

export function rollbackKBDoc(
  docId: string,
  versionId: string,
): Promise<KBDocDetail> {
  return request<KBDocDetail>(`/api/kb/docs/${docId}/rollback`, {
    method: "POST",
    body: { version_id: versionId },
  });
}

export function fetchKBStatus(): Promise<KBStatus> {
  return request<KBStatus>("/api/kb/status");
}

export function reindexKB(docIds?: string[]): Promise<{ job_id: string; status: string }> {
  return request("/api/kb/reindex", {
    method: "POST",
    body: docIds && docIds.length ? { doc_ids: docIds } : {},
  });
}

export function fetchKBJobs(): Promise<KBJob[]> {
  return request<KBJob[]>("/api/kb/jobs");
}

export function fetchKBAudit(): Promise<KBAuditItem[]> {
  return request<KBAuditItem[]>("/api/kb/audit");
}

/* ------------------------------ 管理 ------------------------------ */
export function fetchUsers(): Promise<User[]> {
  return request<User[]>("/api/users");
}

export function createUser(body: {
  username: string;
  password: string;
  role: Role;
}): Promise<User> {
  return request<User>("/api/users", { method: "POST", body });
}

export function patchUser(
  userId: string,
  body: {
    role?: Role;
    disabled?: boolean;
    password?: string;
    max_cost_per_session?: number | null;
    daily_cost_limit?: number | null;
    concurrent_sessions?: number | null;
    eval_daily_limit?: number | null;
  },
): Promise<User> {
  return request<User>(`/api/users/${userId}`, { method: "PATCH", body });
}

export function fetchQuota(userId: string): Promise<Quota> {
  return request<Quota>(`/api/users/${userId}/quota`);
}

export function fetchInvites(): Promise<Invite[]> {
  return request<Invite[]>("/api/invites");
}

export function createInvite(body: {
  role: Role;
  expires_in_days: number;
  max_uses: number;
}): Promise<InviteCreated> {
  return request<InviteCreated>("/api/invites", { method: "POST", body });
}

export function revokeInvite(inviteId: string): Promise<Invite> {
  return request<Invite>(`/api/invites/${inviteId}/revoke`, { method: "POST" });
}

export function fetchSystemInfo(): Promise<SystemInfo> {
  return request<SystemInfo>("/api/system/info");
}

export function fetchAudit(sessionId?: string, limit = 50): Promise<AuditItem[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (sessionId) params.set("session_id", sessionId);
  return request<AuditItem[]>(`/api/audit?${params.toString()}`);
}

/* ------------------------------ 评测 ------------------------------ */
export function fetchEvalSuites(): Promise<EvalSuite[]> {
  return request<EvalSuite[]>("/api/evals/suites");
}

export function fetchEvalStatus(): Promise<EvalStatus> {
  return request<EvalStatus>("/api/evals/status");
}

export function fetchEvalJobs(limit = 20): Promise<EvalJob[]> {
  return request<EvalJob[]>(`/api/evals/jobs?limit=${limit}`);
}

export function fetchEvalJob(jobId: string): Promise<EvalJob> {
  return request<EvalJob>(`/api/evals/jobs/${jobId}`);
}

export function fetchEvalReports(limit = 30): Promise<EvalReportSummary[]> {
  return request<EvalReportSummary[]>(`/api/evals/reports?limit=${limit}`);
}

export function fetchEvalReport(reportId: string): Promise<EvalReportDetail> {
  return request<EvalReportDetail>(
    `/api/evals/reports/${encodeURIComponent(reportId)}`,
  );
}

export function runEval(body: {
  profile: string;
  models?: string[] | null;
  repeat?: number;
  limit?: number | null;
}): Promise<{ job_id: string; status: string }> {
  return request("/api/evals/run", { method: "POST", body });
}

/* --------------------------- MCP 与系统 --------------------------- */
export function fetchMCPServers(profile: string): Promise<MCPServer[]> {
  return request<MCPServer[]>(
    `/api/system/mcp?profile=${encodeURIComponent(profile)}`,
  );
}
