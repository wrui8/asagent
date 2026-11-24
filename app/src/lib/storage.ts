import { Platform } from "react-native";
import * as SecureStore from "expo-secure-store";

const REFRESH_KEY = "asagent.mobile.refresh";

/**
 * 原生端用系统安全存储保存刷新令牌；Web 预览降级到 localStorage。
 */
export async function readRefreshToken(): Promise<string | null> {
  try {
    if (Platform.OS === "web") {
      if (typeof localStorage === "undefined") return null;
      return localStorage.getItem(REFRESH_KEY);
    }
    return await SecureStore.getItemAsync(REFRESH_KEY);
  } catch {
    return null;
  }
}

export async function writeRefreshToken(token: string | null): Promise<void> {
  try {
    if (Platform.OS === "web") {
      if (typeof localStorage === "undefined") return;
      if (token) localStorage.setItem(REFRESH_KEY, token);
      else localStorage.removeItem(REFRESH_KEY);
      return;
    }
    if (token) await SecureStore.setItemAsync(REFRESH_KEY, token);
    else await SecureStore.deleteItemAsync(REFRESH_KEY);
  } catch {
    /* 安全存储不可用时不阻断登录，本次会话仍可用 */
  }
}
