import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, HashRouter, MemoryRouter, useLocation, useNavigate, useNavigationType } from "react-router-dom";
import App from "./App";
import { Background } from "./components/Background";
import { Splash } from "./components/Layout";
import { callMobile, hasDocumentHistory, IS_MOBILE_HOST, mobileEntryPath } from "./lib/mobile-host";
import { LAST_SESSION_KEY } from "./lib/nav";
import { initPlatform, IS_TAURI } from "./lib/platform";
import "./styles/app.css";
import { useAuth } from "./stores/auth";
import { useUI } from "./stores/ui";

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
  !IS_MOBILE_HOST &&
  "serviceWorker" in navigator
) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}

const container = document.getElementById("root");
if (!container) throw new Error("缺少 #root 容器");
const root = createRoot(container);

function MobileRoutes() {
  const location = useLocation();
  const navigate = useNavigate();
  const action = useNavigationType();
  useEffect(() => {
    void callMobile("route", location.pathname + location.search, action).catch(() => undefined);
  }, [location.pathname, location.search, action]);
  useEffect(() => {
    const onNavigate = (event: Event) => {
      const target = (event as CustomEvent<string>).detail;
      event.preventDefault();
      navigate(target, { replace: true });
    };
    window.addEventListener("asagent:navigate", onNavigate);
    return () => window.removeEventListener("asagent:navigate", onNavigate);
  }, [navigate]);
  return <App />;
}

function MobileConsole() {
  const status = useAuth((state) => state.status);
  const theme = useUI((state) => state.theme);
  useEffect(() => {
    if (status !== "loading") void callMobile("theme", theme).catch(() => undefined);
  }, [theme, status]);
  if (status === "loading" || status === "retry") {
    const retry = status === "retry";
    return (
      <>
        <Background />
        <Splash
          label={retry ? "暂时无法恢复登录，请检查网络连接后重试。" : undefined}
          onRetry={retry ? () => void useAuth.getState().bootstrap() : undefined}
        />
      </>
    );
  }
  return hasDocumentHistory()
    ? <HashRouter><MobileRoutes /></HashRouter>
    : <MemoryRouter initialEntries={[mobileEntryPath]}><MobileRoutes /></MemoryRouter>;
}

async function start(): Promise<void> {
  // 桌面端先解析 API 地址，随后再恢复登录态
  await initPlatform();
  if (IS_MOBILE_HOST) {
    useAuth.subscribe((next, previous) => {
      if (previous.user && previous.user.id !== next.user?.id) {
        queryClient.clear();
        localStorage.removeItem(LAST_SESSION_KEY);
      }
    });
    window.addEventListener("asagent:active", () => {
      const auth = useAuth.getState();
      if (auth.status === "retry") void auth.bootstrap();
      if (auth.status === "authed") void queryClient.invalidateQueries({ type: "active" });
    });
  }
  void useAuth.getState().bootstrap();

  root.render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        {IS_MOBILE_HOST ? <MobileConsole /> : <BrowserRouter><App /></BrowserRouter>}
      </QueryClientProvider>
    </StrictMode>,
  );
}

void start();
