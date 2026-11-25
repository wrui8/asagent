import { Platform } from "react-native";
import * as SecureStore from "expo-secure-store";

const REFRESH_KEY = "asagent.mobile.refresh";
let storageQueue = Promise.resolve();

function queueStorageOperation<T>(operation: () => Promise<T>): Promise<T> {
  const result = storageQueue.then(operation);
  storageQueue = result.then(() => undefined, () => undefined);
  return result;
}

/**
 * 原生端用系统安全存储保存刷新令牌；Web 预览降级到 localStorage。
 */
export function readRefreshToken(): Promise<string | null> {
  return queueStorageOperation(async () => {
    if (Platform.OS === "web") {
      if (typeof localStorage === "undefined") return null;
      return localStorage.getItem(REFRESH_KEY);
    }
    return SecureStore.getItemAsync(REFRESH_KEY);
  });
}

export async function writeRefreshToken(token: string | null): Promise<boolean> {
  try {
    await queueStorageOperation(async () => {
      if (Platform.OS === "web") {
        if (typeof localStorage === "undefined") return;
        if (token) localStorage.setItem(REFRESH_KEY, token);
        else localStorage.removeItem(REFRESH_KEY);
        return;
      }
      if (token) await SecureStore.setItemAsync(REFRESH_KEY, token);
      else await SecureStore.deleteItemAsync(REFRESH_KEY);
    });
    return true;
  } catch {
    return false;
  }
}
