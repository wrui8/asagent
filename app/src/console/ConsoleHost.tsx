import * as Clipboard from "expo-clipboard";
import { Directory, File, Paths } from "expo-file-system";
import * as SecureStore from "expo-secure-store";
import * as Sharing from "expo-sharing";
import { StatusBar } from "expo-status-bar";
import { useEffect, useRef, useState } from "react";
import { AppState, Linking, Pressable, StyleSheet, Text } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { WebView, type WebViewMessageEvent, type WebViewProps } from "react-native-webview";

import consoleHtml from "../generated/console-html";
import { setExpiredHandler } from "../lib/api";
import {
  consoleBaseUrl,
  consoleChannel,
  consolePath,
  dispatchConsoleRequest,
  incomingConsolePath,
  isBundledWebViewUrl,
  isExternalConsoleUrl,
  readConsoleRequest,
  type ConsoleEvent,
  type ConsoleReply,
  type ConsoleServices,
  type ConsoleTheme,
} from "../lib/console-bridge";
import { colors } from "../lib/theme";
import { consoleApi } from "./api";

const source = { html: consoleHtml, baseUrl: consoleBaseUrl };
const routeKey = "asagent.console.route";
let shareSequence = 0;
type ConsoleWebView = WebView<WebViewProps>;

async function copyText(text: string): Promise<void> {
  if (!(await Clipboard.setStringAsync(text))) throw new Error("无法复制到剪贴板");
}

async function exportTrace(filename: string, text: string): Promise<void> {
  if (!(await Sharing.isAvailableAsync())) throw new Error("此设备无法导出轨迹文件");
  const folder = new Directory(Paths.cache, "asagent-traces", `${Date.now()}-${++shareSequence}`);
  try {
    folder.create({ intermediates: true });
    const file = new File(folder, filename);
    await file.write(text);
    await Sharing.shareAsync(file.uri, { mimeType: "application/json", UTI: "public.json" });
  } finally {
    try { if (folder.exists) await folder.delete(); } catch { /* The operating system can clear the cache. */ }
  }
}

function openExternal(url: string) {
  if (!isExternalConsoleUrl(url)) return;
  void Linking.openURL(url).catch(() => undefined);
}

function replyTo(view: ConsoleWebView, message: ConsoleReply | ConsoleEvent) {
  view.injectJavaScript(`window.__asagentReceive?.(${JSON.stringify(message)}); true;`);
}

export default function ConsoleHost() {
  const view = useRef<ConsoleWebView>(null);
  const currentRoute = useRef<string | null>(null);
  const lastRouteWrite = useRef(Promise.resolve());
  const connectedView = useRef<ConsoleWebView | null>(null);
  const [revision, setRevision] = useState(0);
  const [failed, setFailed] = useState(false);
  const [theme, setTheme] = useState<ConsoleTheme>("dark");
  const backgroundColor = theme === "light" ? "#eef3fc" : "#04060d";

  useEffect(() => {
    const send = (message: ConsoleEvent) => {
      const target = connectedView.current;
      if (target && target === view.current) replyTo(target, message);
    };
    const activity = AppState.addEventListener("change", (state) => {
      send({ channel: consoleChannel, event: state === "active" ? "active" : "inactive" });
    });
    const links = Linking.addEventListener("url", ({ url }) => {
      const path = incomingConsolePath(url);
      if (!path) return;
      currentRoute.current = path;
      send({ channel: consoleChannel, event: "navigate", path });
    });
    setExpiredHandler(() => send({ channel: consoleChannel, event: "expired" }));
    return () => {
      activity.remove();
      links.remove();
      setExpiredHandler(null);
    };
  }, []);

  function reload() {
    connectedView.current = null;
    setFailed(false);
    setRevision((value) => value + 1);
  }

  function receive(event: WebViewMessageEvent) {
    const target = view.current;
    if (!target || !isBundledWebViewUrl(event.nativeEvent.url)) return;
    const message = readConsoleRequest(event.nativeEvent.data);
    if (!message) return;
    if (message.method !== "ready" && target !== connectedView.current) return;

    const services: ConsoleServices = {
      ...consoleApi,
      async ready() {
        const [url, saved] = await Promise.all([
          Linking.getInitialURL().catch(() => null),
          SecureStore.getItemAsync(routeKey).catch(() => null),
        ]);
        return currentRoute.current ?? incomingConsolePath(url ?? null) ?? (saved && consolePath(saved)) ?? "/";
      },
      route(path) {
        currentRoute.current = path;
        lastRouteWrite.current = lastRouteWrite.current
          .then(() => SecureStore.setItemAsync(routeKey, path))
          .catch(() => undefined);
      },
      theme: setTheme,
      copyText,
      exportTrace,
    };
    void dispatchConsoleRequest(message, services).then((reply) => {
      if (target !== view.current) return;
      if (message.method === "ready" && "result" in reply) connectedView.current = target;
      replyTo(target, reply);
    });
  }

  return (
    <SafeAreaView style={[styles.container, { backgroundColor }]}>
      <StatusBar style={theme === "light" ? "dark" : "light"} />
      {failed ? (
        <Pressable accessibilityRole="button" onPress={reload} style={styles.reload}>
          <Text style={styles.reloadLabel}>重新载入控制台</Text>
        </Pressable>
      ) : (
        <WebView<WebViewProps>
          key={revision}
          ref={view}
          source={source}
          style={[styles.webview, { backgroundColor }]}
          originWhitelist={["*"]}
          onMessage={receive}
          onShouldStartLoadWithRequest={(event) => {
            if (event.url === "about:blank" || isBundledWebViewUrl(event.url)) return true;
            if (event.isTopFrame && event.navigationType === "click") openExternal(event.url);
            return false;
          }}
          onOpenWindow={(event) => openExternal(event.nativeEvent.targetUrl)}
          onContentProcessDidTerminate={reload}
          onError={() => setFailed(true)}
          allowsBackForwardNavigationGestures
          contentInsetAdjustmentBehavior="never"
        />
      )}
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  webview: { flex: 1 },
  reload: { alignSelf: "center", margin: "auto", padding: 14, backgroundColor: colors.raised, borderRadius: 10 },
  reloadLabel: { fontWeight: "600", color: colors.text },
});
