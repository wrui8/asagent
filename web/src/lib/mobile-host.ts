import type { User } from "./types";

export const IS_MOBILE_HOST = import.meta.env.MODE === "mobile";
const channel = "asagent.mobile.v1";
const instance = Math.random().toString(36).slice(2);
let serial = 0;

export type MobileSession =
  | { status: "authed"; user: User }
  | { status: "anon" | "retry"; user: null };

interface HostMessage {
  channel: string;
  id?: string;
  result?: unknown;
  error?: { message: string; status?: number; code?: string };
  event?: "active" | "inactive" | "expired" | "navigate";
  path?: string;
}

declare global {
  interface Window {
    ReactNativeWebView?: { postMessage: (message: string) => void };
    __asagentReceive?: (message: HostMessage) => void;
  }
}

const pending = new Map<string, (reply: HostMessage) => void>();

function receive(message: HostMessage | null) {
  if (!message || message.channel !== channel) return;
  switch (message.event) {
    case "expired":
      window.dispatchEvent(new Event("asagent:logout"));
      return;
    case "active":
    case "inactive":
      window.dispatchEvent(new Event(`asagent:${message.event}`));
      return;
    case "navigate":
      if (isConsolePath(message.path)) {
        const navigation = new CustomEvent("asagent:navigate", { detail: message.path, cancelable: true });
        // Links may arrive during account restoration, before the router has mounted.
        if (window.dispatchEvent(navigation)) initializeMobileHistory(message.path);
      }
      return;
  }
  if (message.id) pending.get(message.id)?.(message);
}

if (IS_MOBILE_HOST) {
  window.__asagentReceive = receive;
  window.addEventListener("message", (event) => {
    if (window.parent !== window && event.source === window.parent && event.origin === window.origin) {
      receive(event.data);
    }
  });
}

function send<T>(method: string, args: unknown[], signal?: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const id = `${instance}:${++serial}`;
    function cancel() {
      finish();
      reject(new DOMException("请求已取消", "AbortError"));
    }
    // The system share sheet stays open until the user finishes choosing a destination.
    const timer = method === "exportTrace" ? undefined : window.setTimeout(() => {
      finish();
      reject(new Error("移动端连接超时，请重试"));
    }, 60_000);
    function finish() {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", cancel);
      pending.delete(id);
    }
    if (signal?.aborted) {
      cancel();
      return;
    }
    signal?.addEventListener("abort", cancel, { once: true });
    pending.set(id, (reply) => {
      finish();
      if (reply.error) reject(Object.assign(new Error(reply.error.message), reply.error));
      else resolve(reply.result as T);
    });
    const message = { channel, id, method, args };
    try {
      if (window.ReactNativeWebView) {
        window.ReactNativeWebView.postMessage(JSON.stringify(message));
      } else if (window.parent !== window && window.origin !== "null") {
        // srcDoc inherits its parent's security origin even when location.origin is null.
        window.parent.postMessage(message, window.origin);
      } else {
        throw new Error("无法连接移动端控制台");
      }
    } catch (error) {
      finish();
      reject(error);
    }
  });
}

export function callMobile<T>(method: string, ...args: unknown[]): Promise<T> {
  return send<T>(method, args);
}

export function requestMobile<T>(
  path: string,
  options: { method: string; body?: unknown },
  signal?: AbortSignal,
): Promise<T> {
  return send<T>("request", [path, options], signal);
}

export function isConsolePath(path: unknown): path is string {
  return typeof path === "string" && path.startsWith("/") && !path.startsWith("//") && !path.includes("\\");
}

export let mobileEntryPath = "/";

export function hasDocumentHistory() {
  // React Router cannot resolve an absolute route against about:srcdoc or about:blank.
  return window.location.origin !== "null";
}

export function initializeMobileHistory(path: string) {
  mobileEntryPath = isConsolePath(path) ? path : "/";
  if (!hasDocumentHistory()) return;
  const url = new URL(window.location.href);
  url.hash = mobileEntryPath;
  window.history.replaceState(window.history.state, "", url.href);
}
