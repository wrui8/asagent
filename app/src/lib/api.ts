import { API_BASE } from "./config";
import { readRefreshToken, writeRefreshToken } from "./storage";
import type {
  AgentEvent,
  Approval,
  ProfileInfo,
  Session,
  TokenResponse,
  User,
} from "./types";

let accessToken: string | null = null;
let refreshFlight: Promise<boolean> | null = null;
let expiredHandler: (() => void) | null = null;

export function setExpiredHandler(handler: (() => void) | null): void {
  expiredHandler = handler;
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

export function errorText(error: unknown, fallback = "请求失败"): string {
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

async function parseError(resp: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `请求失败（${resp.status}）`;
  try {
    const body = (await resp.json()) as Record<string, unknown>;
    if (typeof body.code === "string") code = body.code;
    if (typeof body.message === "string") message = body.message;
    else if (typeof body.detail === "string") message = body.detail;
  } catch {
    /* 非 JSON 响应 */
  }
  return new ApiError(resp.status, code, message);
}

async function refreshToken(): Promise<boolean> {
  if (refreshFlight) return refreshFlight;
  refreshFlight = (async () => {
    const stored = await readRefreshToken();
    if (!stored) return false;
    try {
      const resp = await fetch(`${API_BASE}/api/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Client": "mobile" },
        body: JSON.stringify({ refresh_token: stored }),
        credentials: "omit",
      });
      if (!resp.ok) {
        if (resp.status === 401) await writeRefreshToken(null);
        return false;
      }
      const data = (await resp.json()) as TokenResponse;
      accessToken = data.access_token;
      if (data.refresh_token) await writeRefreshToken(data.refresh_token);
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
}

export async function request<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const { method = "GET", body, auth = true, retryOn401 = true } = options;
  const headers: Record<string, string> = { "X-Client": "mobile" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;

  const resp = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials: "omit",
    body: body === undefined ? undefined : JSON.stringify(body),
  });

  if (resp.status === 401 && auth) {
    if (retryOn401 && (await refreshToken())) {
      return request<T>(path, { ...options, retryOn401: false });
    }
    accessToken = null;
    expiredHandler?.();
    throw await parseError(resp);
  }

  if (!resp.ok) throw await parseError(resp);
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

/* ------------------------------ 认证 ------------------------------ */

async function acceptToken(data: TokenResponse): Promise<TokenResponse> {
  accessToken = data.access_token;
  if (data.refresh_token) await writeRefreshToken(data.refresh_token);
  return data;
}

export async function login(
  username: string,
  password: string,
): Promise<TokenResponse> {
  const data = await request<TokenResponse>("/api/auth/login", {
    method: "POST",
    body: { username, password },
    auth: false,
  });
  return acceptToken(data);
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
  return acceptToken(data);
}

/** 启动时用安全存储里的刷新令牌恢复登录。 */
export async function bootstrap(): Promise<User | null> {
  const stored = await readRefreshToken();
  if (!stored) return null;
  if (!(await refreshToken())) return null;
  try {
    return await request<User>("/api/auth/me");
  } catch {
    return null;
  }
}

export async function logout(): Promise<void> {
  const stored = await readRefreshToken();
  try {
    await request<void>("/api/auth/logout", {
      method: "POST",
      auth: false,
      body: stored ? { refresh_token: stored } : undefined,
    });
  } finally {
    accessToken = null;
    await writeRefreshToken(null);
  }
}

/* ------------------------------ 会话 ------------------------------ */

export function fetchProfiles(): Promise<ProfileInfo[]> {
  return request<ProfileInfo[]>("/api/profiles");
}

export function fetchSessions(limit = 100): Promise<Session[]> {
  return request<Session[]>(`/api/sessions?limit=${limit}`);
}

export function fetchSession(id: string): Promise<Session> {
  return request<Session>(`/api/sessions/${id}`);
}

export function fetchEvents(id: string, afterSeq = 0): Promise<AgentEvent[]> {
  return request<AgentEvent[]>(`/api/sessions/${id}/events?after_seq=${afterSeq}`);
}

export function createSession(body: {
  profile: string;
  input: string;
  max_steps: number;
  max_cost_cny: number | null;
}): Promise<{ session_id: string; status: string }> {
  return request("/api/sessions", { method: "POST", body });
}

export function cancelSession(id: string): Promise<unknown> {
  return request(`/api/sessions/${id}/cancel`, { method: "POST" });
}

export function fetchApprovals(id: string): Promise<Approval[]> {
  return request<Approval[]>(`/api/sessions/${id}/approvals`);
}

export function decideApproval(
  id: string,
  callId: string,
  approved: boolean,
): Promise<Approval[]> {
  return request<Approval[]>(`/api/sessions/${id}/approvals`, {
    method: "POST",
    body: { call_id: callId, approved, resume: true },
  });
}
