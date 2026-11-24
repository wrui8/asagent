import Constants from "expo-constants";
import * as WebBrowser from "expo-web-browser";
import { Alert, Platform } from "react-native";

import { fetchAppVersion } from "./api";

function parseVersion(value: string): number[] {
  const parts = value.replace(/^v/i, "").split(/[.+-]/);
  return [0, 1, 2].map((index) => {
    const parsed = Number.parseInt(parts[index] ?? "0", 10);
    return Number.isFinite(parsed) ? parsed : 0;
  });
}

/** 语义化版本比较：a > b 返回正数，a < b 返回负数。 */
export function compareVersions(left: string, right: string): number {
  const a = parseVersion(left);
  const b = parseVersion(right);
  for (let index = 0; index < 3; index += 1) {
    const diff = (a[index] ?? 0) - (b[index] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
}

/**
 * 启动时静默检查版本：网络失败或接口不可用时不打扰用户。
 * iOS 尚未发布安装包，只在 Android 上提示。
 */
export async function checkAppUpdate(): Promise<void> {
  if (Platform.OS !== "android") return;
  const current = Constants.expoConfig?.version ?? "0.0.0";
  try {
    const info = await fetchAppVersion();
    const openDownload = () => {
      if (info.android_url) void WebBrowser.openBrowserAsync(info.android_url);
    };
    if (compareVersions(current, info.min_supported) < 0) {
      Alert.alert(
        "需要更新",
        `当前版本 ${current} 已不受支持，请安装 ${info.latest} 后继续使用。`,
        [{ text: "下载更新", onPress: openDownload }],
        { cancelable: false },
      );
      return;
    }
    if (compareVersions(current, info.latest) < 0) {
      Alert.alert(
        "发现新版本",
        info.notes || `新版本 ${info.latest} 已发布，当前版本 ${current}。`,
        [
          { text: "稍后", style: "cancel" },
          { text: "去下载", onPress: openDownload },
        ],
      );
    }
  } catch {
    /* 离线或接口异常时保持静默，不阻塞启动 */
  }
}
