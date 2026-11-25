import { API_BASE } from "./config";
import { readRefreshToken, writeRefreshToken } from "./storage";
import type {
  AgentEvent,
  AppVersionInfo,
  Approval,
  ProfileInfo,
  Session,
  TokenResponse,
  User,
} from "./types";

let accessToken: string | null = null;
let authVersion = 0;
let logoutVersion: number | null = null;
let refreshFlight: Promise<TokenResponse | null> | null = null;
let expiredHandler: (() => void) | null = null;
const requestTimeoutMs = 15_000;

export type BootstrapResult =
  | { status: "authed"; user: User }
  | { status: "anon" | "retry"; user: null };

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

function aborted(): Error {
  const error = new Error("请求已取消");
  error.name = "AbortError";
  return error;
}

function waitForRequest<T>(pending: Promise<T>, signal?: AbortSignal): Promise<T> {
  if (!signal) return pending;
  return new Promise((resolve, reject) => {
    const cancel = () => reject(aborted());
    void pending.then(resolve, reject).finally(() => signal.removeEventListener("abort", cancel));
    if (signal.aborted) cancel();
    else signal.addEventListener("abort", cancel, { once: true });
  });
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  auth?: boolean;
  signal?: AbortSignal;
}

async function fetchJson<T>(
  path: string,
  options: RequestOptions,
  token: string | null = null,
): Promise<T> {
  const { method = "GET", body, signal } = options;
  if (signal?.aborted) throw aborted();
  const headers: Record<string, string> = { "X-Client": "mobile" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (token) headers.Authorization = `Bearer ${token}`;

  const controller = new AbortController();
  const cancel = () => controller.abort();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    cancel();
  }, requestTimeoutMs);
  signal?.addEventListener("abort", cancel, { once: true });

  try {
    const response = fetch(`${API_BASE}${path}`, {
      method,
      headers,
      credentials: "omit",
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    }).catch(() => {
      throw new ApiError(0, "network_error", "无法连接服务器，请检查网络后重试");
    });
    return await waitForRequest(response.then(async (resp) => {
      if (!resp.ok) throw await parseError(resp);
      if (resp.status === 204) return undefined as T;
      return (await resp.json()) as T;
    }), controller.signal);
  } catch (error) {
    if (timedOut) throw new ApiError(0, "request_timeout", "请求超时，请稍后重试");
    if (signal?.aborted) throw aborted();
    throw error;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}

function beginAuthChange(): number {
  refreshFlight = null;
  return ++authVersion;
}

async function clearCredentials(version: number): Promise<void> {
  if (version !== authVersion) return;
  accessToken = null;
  await writeRefreshToken(null);
}

async function acceptToken(data: TokenResponse, version: number): Promise<TokenResponse> {
  if (version !== authVersion) throw aborted();
  if (data.refresh_token) await writeRefreshToken(data.refresh_token);
  if (version !== authVersion) throw aborted();
  accessToken = data.access_token;
  return data;
}

function isUnauthorized(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 401;
}

async function refreshSession(version: number): Promise<TokenResponse | null> {
  const stored = await readRefreshToken();
  if (!stored || version !== authVersion) return null;
  try {
    const data = await fetchJson<TokenResponse>("/api/auth/refresh", {
      method: "POST",
      body: { refresh_token: stored },
    });
    return await acceptToken(data, version);
  } catch (error) {
    if (version !== authVersion) return null;
    if (!isUnauthorized(error)) throw error;
    await clearCredentials(version);
    return null;
  }
}

function refreshToken(version: number): Promise<TokenResponse | null> {
  if (refreshFlight) return refreshFlight;
  const pending = refreshSession(version).finally(() => {
    if (refreshFlight === pending) refreshFlight = null;
  });
  refreshFlight = pending;
  return pending;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  if (options.auth === false) return fetchJson<T>(path, options);
  const version = authVersion;
  const token = accessToken;
  const checkCurrent = () => {
    if (options.signal?.aborted || version !== authVersion || logoutVersion === version) {
      throw aborted();
    }
  };

  checkCurrent();
  try {
    const data = await fetchJson<T>(path, options, token);
    checkCurrent();
    return data;
  } catch (error) {
    if (!isUnauthorized(error)) throw error;
    checkCurrent();
    const hasNewToken = accessToken && accessToken !== token;
    const refreshed = hasNewToken || await waitForRequest(refreshToken(version), options.signal);
    checkCurrent();
    if (refreshed) {
      const data = await fetchJson<T>(path, options, accessToken);
      checkCurrent();
      return data;
    }

    accessToken = null;
    expiredHandler?.();
    throw error;
  }
}

/* ------------------------------ 认证 ------------------------------ */

export async function login(
  username: string,
  password: string,
): Promise<TokenResponse> {
  const version = beginAuthChange();
  const data = await request<TokenResponse>("/api/auth/login", {
    method: "POST",
    body: { username, password },
    auth: false,
  });
  return acceptToken(data, version);
}

export async function register(
  username: string,
  password: string,
  inviteCode: string,
): Promise<TokenResponse> {
  const version = beginAuthChange();
  const data = await request<TokenResponse>("/api/auth/register", {
    method: "POST",
    body: { username, password, invite_code: inviteCode },
    auth: false,
  });
  return acceptToken(data, version);
}

/** 启动时用安全存储里的刷新令牌恢复登录。 */
export async function bootstrap(): Promise<BootstrapResult> {
  const version = authVersion;
  if (logoutVersion === version) return { status: "anon", user: null };
  try {
    const data = await refreshToken(version);
    if (data && version === authVersion) return { status: "authed", user: data.user };
    return { status: "anon", user: null };
  } catch {
    return { status: version === authVersion ? "retry" : "anon", user: null };
  }
}

export async function logout(): Promise<boolean> {
  const version = beginAuthChange();
  logoutVersion = version;
  const stored = readRefreshToken().catch(() => null);
  try {
    const removed = await writeRefreshToken(null);
    const token = await stored;
    const revoked = token ? request<void>("/api/auth/logout", {
      method: "POST",
      auth: false,
      body: { refresh_token: token },
    }).then(() => true, () => false) : false;
    const signedOut = removed || await revoked;
    if (signedOut && version === authVersion) accessToken = null;
    return signedOut;
  } finally {
    if (logoutVersion === version) logoutVersion = null;
  }
}

/* ------------------------------ 会话 ------------------------------ */

/** 公开接口：无需登录即可检查客户端版本。 */
export function fetchAppVersion(): Promise<AppVersionInfo> {
  return request<AppVersionInfo>("/api/app/version", { auth: false });
}

export function fetchProfiles(): Promise<ProfileInfo[]> {
  return request<ProfileInfo[]>("/api/profiles");
}

export function fetchSessions(limit = 100): Promise<Session[]> {
  return request<Session[]>(`/api/sessions?limit=${limit}`);
}

export function fetchSession(id: string, signal?: AbortSignal): Promise<Session> {
  return request<Session>(`/api/sessions/${id}`, { signal });
}

export function fetchEvents(id: string, afterSeq = 0, signal?: AbortSignal): Promise<AgentEvent[]> {
  return request<AgentEvent[]>(`/api/sessions/${id}/events?after_seq=${afterSeq}`, { signal });
}

export function createSession(body: {
  profile: string;
  input: string;
  max_steps: number;
  max_cost_cny: number | null;
}): Promise<{ session_id: string; status: string }> {
  return request("/api/sessions", { method: "POST", body });
}

export function cancelSession(id: string, signal?: AbortSignal): Promise<unknown> {
  return request(`/api/sessions/${id}/cancel`, { method: "POST", signal });
}

export function fetchApprovals(id: string, signal?: AbortSignal): Promise<Approval[]> {
  return request<Approval[]>(`/api/sessions/${id}/approvals`, { signal });
}

export function decideApproval(
  id: string,
  callId: string,
  approved: boolean,
  signal?: AbortSignal,
): Promise<Approval[]> {
  return request<Approval[]>(`/api/sessions/${id}/approvals`, {
    method: "POST",
    body: { call_id: callId, approved, resume: true },
    signal,
  });
}
