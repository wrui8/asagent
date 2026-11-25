import { useEffect, useRef } from "react";

import consoleHtml from "../generated/console-html";
import { setExpiredHandler } from "../lib/api";
import {
  consoleChannel,
  consolePath,
  dispatchConsoleRequest,
  isConsoleFrameMessage,
  readConsoleRequest,
  type ConsoleEvent,
  type ConsoleReply,
  type ConsoleServices,
} from "../lib/console-bridge";
import { consoleApi } from "./api";

function initialRoute() {
  return consolePath(window.location.hash) ?? consolePath(window.location.pathname + window.location.search) ?? "/";
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch { /* The preview may run on an insecure LAN address. */ }
  }
  const field = document.createElement("textarea");
  field.value = text;
  field.readOnly = true;
  field.style.cssText = "position:fixed;opacity:0";
  document.body.append(field);
  field.select();
  try {
    if (!document.execCommand("copy")) throw new Error("浏览器未允许复制，请检查剪贴板权限");
  } finally {
    field.remove();
  }
}

async function exportTrace(filename: string, text: string): Promise<void> {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const download = document.createElement("a");
  download.href = url;
  download.download = filename;
  document.body.append(download);
  download.click();
  download.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function ConsoleHost() {
  const frameRef = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const hostOrigin = window.location.origin;
    let listening = true;
    let currentRoute = initialRoute();

    const send = (message: ConsoleReply | ConsoleEvent) => {
      frame.contentWindow?.postMessage(message, hostOrigin);
    };
    const services: ConsoleServices = {
      ...consoleApi,
      ready: initialRoute,
      route(path, navigation) {
        currentRoute = path;
        const hash = `#${path}`;
        if (window.location.hash === hash) return;
        if (navigation === "PUSH") window.history.pushState(window.history.state, "", hash);
        else window.history.replaceState(window.history.state, "", hash);
      },
      theme() {},
      copyText,
      exportTrace,
    };
    const receive = (event: MessageEvent<unknown>) => {
      const page = frame.contentDocument;
      if (!page || !isConsoleFrameMessage(event, frame.contentWindow, hostOrigin, page.URL)) return;
      const message = readConsoleRequest(event.data);
      if (!message) return;
      void dispatchConsoleRequest(message, services).then((reply) => {
        if (listening && frame.contentDocument === page) send(reply);
      });
    };
    const activate = () => {
      send({ channel: consoleChannel, event: document.visibilityState === "visible" ? "active" : "inactive" });
    };
    const navigate = () => {
      const path = initialRoute();
      if (currentRoute === path) return;
      currentRoute = path;
      send({ channel: consoleChannel, event: "navigate", path });
    };

    window.addEventListener("message", receive);
    window.addEventListener("popstate", navigate);
    window.addEventListener("hashchange", navigate);
    document.addEventListener("visibilitychange", activate);
    setExpiredHandler(() => send({ channel: consoleChannel, event: "expired" }));
    frame.srcdoc = consoleHtml;

    return () => {
      listening = false;
      setExpiredHandler(null);
      document.removeEventListener("visibilitychange", activate);
      window.removeEventListener("hashchange", navigate);
      window.removeEventListener("popstate", navigate);
      window.removeEventListener("message", receive);
    };
  }, []);

  return (
    <iframe
      ref={frameRef}
      title="ASAgent 控制台"
      sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-modals allow-popups allow-popups-to-escape-sandbox"
      style={{ position: "fixed", inset: 0, height: "100%", width: "100%", border: 0, background: "#070b14" }}
    />
  );
}
