import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { initPlatform, IS_TAURI } from "./lib/platform";
import "./styles/app.css";
import { useAuth } from "./stores/auth";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 5_000,
    },
  },
});

// 仅生产 Web 注册 Service Worker；开发态避免缓存干扰热更新，桌面端不使用
if (
  import.meta.env.PROD &&
  !IS_TAURI &&
  "serviceWorker" in navigator
) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}

const container = document.getElementById("root");
if (!container) throw new Error("缺少 #root 容器");
const root = createRoot(container);

async function start(): Promise<void> {
  // 桌面端先解析 API 地址，随后再恢复登录态
  await initPlatform();
  void useAuth.getState().bootstrap();

  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  );
}

void start();
