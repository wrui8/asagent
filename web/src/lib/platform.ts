/**
 * 平台适配层：Web 与 Tauri 桌面端共用同一套页面。
 *
 * 桌面端通过 Tauri 命令读取构建时写死的服务器地址、用系统 keyring 保存刷新令牌，
 * 避免把长期凭据放进 localStorage / sessionStorage。
 */

interface TauriInvoke {
  invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T>;
}

declare global {
  interface Window {
    __TAURI_INTERNALS__?: TauriInvoke;
  }
}

export const IS_TAURI =
  typeof window !== "undefined" && Boolean(window.__TAURI_INTERNALS__);

let apiBase = (import.meta.env.VITE_API_BASE ?? "").replace(/\/+$/, "");

export function getApiBase(): string {
  return apiBase;
}

function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const bridge = window.__TAURI_INTERNALS__;
  if (!bridge) return Promise.reject(new Error("Tauri bridge unavailable"));
  return bridge.invoke<T>(cmd, args);
}

function normalizeBase(value: string): string {
  return value.trim().replace(/\/+$/, "");
}

/** 在发起任何 API 请求前调用；Web 下为空操作。 */
export async function initPlatform(): Promise<void> {
  if (!IS_TAURI) return;
  try {
    const base = await invoke<string>("get_api_base");
    if (base) apiBase = normalizeBase(base);
  } catch {
    /* 读取失败时退回打包时的 VITE_API_BASE */
  }
}

export async function loadDesktopRefreshToken(): Promise<string | null> {
  if (!IS_TAURI) return null;
  try {
    const token = await invoke<string | null>("load_refresh_token");
    return token && token.length > 0 ? token : null;
  } catch {
    return null;
  }
}

export async function saveDesktopRefreshToken(token: string | null): Promise<void> {
  if (!IS_TAURI) return;
  try {
    if (token) await invoke<void>("store_refresh_token", { token });
    else await invoke<void>("clear_refresh_token");
  } catch {
    /* keyring 不可用时仅本次会话有效，不阻断登录流程 */
  }
}

export interface DesktopUpdateInfo {
  current_version: string;
  version: string;
  notes?: string | null;
}

/** 桌面端检查 GitHub Release 的签名更新；Web 下返回 null。 */
export async function checkDesktopUpdate(): Promise<DesktopUpdateInfo | null> {
  if (!IS_TAURI) return null;
  return invoke<DesktopUpdateInfo | null>("check_desktop_update");
}

/** 下载并安装更新；安装程序接管后应用会退出。 */
export async function installDesktopUpdate(): Promise<void> {
  if (!IS_TAURI) throw new Error("仅桌面客户端支持自动更新");
  await invoke<void>("install_desktop_update");
}
