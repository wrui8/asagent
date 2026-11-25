import type { BootstrapResult } from "./api";
import type { TokenResponse } from "./types";

export const consoleChannel = "asagent.mobile.v1";
export const consoleBaseUrl = "https://asagent.mobile.invalid/";

const consoleRoutes = new Set(["/", "/login", "/new", "/chat", "/kb", "/evals", "/audit", "/system", "/admin"]);
const requestMethods = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);

type ConsoleRequest = { channel: typeof consoleChannel; id: string; method: string; args: unknown[] };
type ErrorPayload = { message: string; status?: number; code?: string };
export type ConsoleReply = { channel: typeof consoleChannel; id: string } & (
  { result: unknown } | { error: ErrorPayload }
);
export type ConsoleEvent = { channel: typeof consoleChannel } & (
  { event: "active" | "inactive" | "expired" } | { event: "navigate"; path: string }
);
export type ConsoleNavigationType = "POP" | "PUSH" | "REPLACE";
export type ConsoleTheme = "dark" | "light";

export interface ConsoleServices {
  ready: () => string | Promise<string>;
  route: (path: string, navigation: ConsoleNavigationType) => void;
  theme: (value: ConsoleTheme) => void;
  bootstrap: () => Promise<BootstrapResult>;
  login: (username: string, password: string) => Promise<TokenResponse>;
  register: (username: string, password: string, invite: string) => Promise<TokenResponse>;
  logout: () => Promise<boolean>;
  request: (path: string, options: { method?: string; body?: unknown }) => Promise<unknown>;
  copyText: (text: string) => Promise<void>;
  exportTrace: (filename: string, text: string) => Promise<void>;
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function readConsoleRequest(value: unknown): ConsoleRequest | null {
  if (typeof value === "string") {
    try { value = JSON.parse(value) as unknown; } catch { return null; }
  }
  if (!record(value) || value.channel !== consoleChannel ||
    typeof value.id !== "string" || !value.id || value.id.length > 128 ||
    typeof value.method !== "string" || !Array.isArray(value.args)) return null;
  return { channel: consoleChannel, id: value.id, method: value.method, args: value.args };
}

export function consolePath(input: string): string | null {
  const path = input.startsWith("#") ? input.slice(1) : input;
  if (!path.startsWith("/") || /[\\#\s]/.test(path)) return null;
  const split = path.indexOf("?");
  const search = split >= 0 ? path.slice(split) : "";
  const pathname = (split >= 0 ? path.slice(0, split) : path).replace(/\/$/, "") || "/";
  if (consoleRoutes.has(pathname)) return pathname + search;
  const session = /^\/sessions?\/([a-zA-Z0-9_-]+)$/.exec(pathname);
  return session ? `/sessions/${session[1]}${search}` : null;
}

export function incomingConsolePath(input: string | null): string | null {
  if (!input) return null;
  if (input.startsWith("asagent://")) {
    const route = input.slice("asagent://".length);
    return route ? consolePath(`/${route.replace(/^\/+/, "")}`) : null;
  }
  const expo = /^exps?:\/\/[^/]+\/--(\/.*)$/.exec(input);
  return expo ? consolePath(expo[1]) : null;
}

export function isBundledWebViewUrl(url: string): boolean {
  return url === consoleBaseUrl || url.startsWith(`${consoleBaseUrl}#`);
}

export function isExternalConsoleUrl(url: string): boolean {
  const origin = /^https?:\/\/([^/?#]+)/i.exec(url)?.[1];
  return !!origin && !/[\s\\@]/.test(origin) && origin.toLowerCase() !== "asagent.mobile.invalid";
}

export function isConsoleFrameMessage(
  event: { source: unknown; origin: string }, frame: unknown, hostOrigin: string, pageUrl: string,
): boolean {
  return !!frame && event.source === frame && event.origin === hostOrigin &&
    (pageUrl === "about:srcdoc" || pageUrl.startsWith("about:srcdoc#"));
}

class ConsoleError extends Error {
  constructor(message: string, readonly code: string) { super(message); }
}

function invalid(message = "控制台请求无效"): never {
  throw new ConsoleError(message, "invalid_request");
}

function argumentsOf(args: unknown[], count: number): string[] {
  if (args.length !== count || args.some((value) => typeof value !== "string")) invalid();
  return args as string[];
}

function requestPath(path: unknown, method: string): string {
  if (typeof path !== "string" || !path.startsWith("/api/") || /[\\#\s]/.test(path)) invalid();
  const pathname = path.split("?", 1)[0];
  // Reject escaped separators and double escapes before decoding route segments.
  if (/%(?:2f|5c|25)/i.test(pathname)) invalid();
  let decoded: string;
  try { decoded = decodeURIComponent(pathname); } catch { invalid(); }
  if (decoded.slice(1).split("/").some((segment) =>
    !segment || segment === "." || segment === ".." || /[^A-Za-z0-9._~-]/.test(segment),
  )) invalid();
  const normalized = decoded.toLowerCase();
  if ((normalized === "/api/auth" || normalized.startsWith("/api/auth/")) &&
    !(pathname === "/api/auth/me" && method === "GET")) invalid();
  return path;
}

async function runConsoleRequest(message: ConsoleRequest, services: ConsoleServices): Promise<unknown> {
  const { args, method } = message;
  switch (method) {
    case "ready":
      argumentsOf(args, 0);
      return services.ready();
    case "route": {
      const [label, navigation = "REPLACE"] = args;
      if (args.length > 2 || typeof label !== "string" ||
        (navigation !== "POP" && navigation !== "PUSH" && navigation !== "REPLACE")) invalid();
      const path = consolePath(label);
      if (!path) invalid();
      services.route(path, navigation);
      return null;
    }
    case "bootstrap": {
      argumentsOf(args, 0);
      const { status, user } = await services.bootstrap();
      return { status, user };
    }
    case "theme": {
      const [value] = argumentsOf(args, 1);
      if (value !== "dark" && value !== "light") invalid();
      services.theme(value);
      return null;
    }
    case "login": {
      const [username, password] = argumentsOf(args, 2);
      return (await services.login(username, password)).user;
    }
    case "register": {
      const [username, password, invite] = argumentsOf(args, 3);
      return (await services.register(username, password, invite)).user;
    }
    case "logout":
      argumentsOf(args, 0);
      return services.logout();
    case "request": {
      if (args.length !== 2 || !record(args[1])) invalid();
      const options = args[1];
      const verb = options.method === undefined ? "GET" : options.method;
      if (typeof verb !== "string" || !requestMethods.has(verb)) invalid();
      return services.request(requestPath(args[0], verb), { method: verb, body: options.body });
    }
    case "copyText": {
      const [text] = argumentsOf(args, 1);
      return services.copyText(text);
    }
    case "exportTrace": {
      const [filename, text] = argumentsOf(args, 2);
      if (!/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,155}\.json$/.test(filename)) invalid("轨迹文件名无效");
      return services.exportTrace(filename, text);
    }
    default:
      return invalid();
  }
}

export async function dispatchConsoleRequest(
  message: ConsoleRequest, services: ConsoleServices,
): Promise<ConsoleReply> {
  const reply = { channel: consoleChannel, id: message.id } as const;
  try {
    return { ...reply, result: await runConsoleRequest(message, services) ?? null };
  } catch (error) {
    const info: ErrorPayload = { message: error instanceof Error ? error.message : "控制台请求失败" };
    if (record(error)) {
      if (typeof error.code === "string") info.code = error.code;
      if (typeof error.status === "number") info.status = error.status;
    }
    return { ...reply, error: info };
  }
}
