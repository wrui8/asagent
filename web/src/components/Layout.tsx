import { useEffect, useRef } from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../stores/auth";
import { Background } from "./Background";
import { Cmdk } from "./Cmdk";
import { InstallBar } from "./InstallBar";
import { Sidebar } from "./Sidebar";
import { Toasts } from "./Toasts";
import { Topbar } from "./Topbar";

export function Splash({ label = "正在连接控制台…" }: { label?: string }) {
  return (
    <div className="splash">
      <div className="splash-inner">
        <div className="brand-mark" style={{ width: 46, height: 46, borderRadius: 14 }}>
          AS
        </div>
        <div className="brand-name" style={{ fontSize: 18 }}>
          asagent
        </div>
        <div className="skeleton" style={{ width: 180 }} />
        <div style={{ fontSize: 12, color: "var(--faint)" }}>{label}</div>
      </div>
    </div>
  );
}

export function Layout() {
  const status = useAuth((s) => s.status);
  const location = useLocation();
  const barRef = useRef<HTMLDivElement>(null);

  // 路由切换进度条
  useEffect(() => {
    const el = barRef.current;
    if (!el) return;
    el.style.transition = "width .28s ease, opacity .4s";
    el.style.opacity = "1";
    el.style.width = "12%";
    let width = 12;
    const timer = window.setInterval(() => {
      width = Math.min(width + 16, 88);
      el.style.width = `${width}%`;
    }, 120);
    const finish = window.setTimeout(() => {
      window.clearInterval(timer);
      el.style.width = "100%";
      window.setTimeout(() => {
        el.style.opacity = "0";
        el.style.width = "0";
      }, 260);
    }, 240);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(finish);
    };
  }, [location.pathname, location.search]);

  // 按钮涟漪
  useEffect(() => {
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
      return;
    }
    const onDown = (event: MouseEvent) => {
      const target = (event.target as HTMLElement | null)?.closest?.(
        ".btn",
      ) as HTMLElement | null;
      if (!target) return;
      const rect = target.getBoundingClientRect();
      const size = Math.max(rect.width, rect.height) * 2;
      const span = document.createElement("span");
      span.className = "ripple";
      span.style.width = `${size}px`;
      span.style.height = `${size}px`;
      span.style.left = `${event.clientX - rect.left - size / 2}px`;
      span.style.top = `${event.clientY - rect.top - size / 2}px`;
      target.appendChild(span);
      window.setTimeout(() => span.remove(), 650);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  if (status === "loading") {
    return (
      <>
        <Background />
        <Splash />
      </>
    );
  }
  if (status === "guest") {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: `${location.pathname}${location.search}` }}
      />
    );
  }

  return (
    <>
      <Background />
      <div id="progress" ref={barRef} />
      <div className="app">
        <Sidebar />
        <div className="main">
          <Topbar />
          <Outlet />
        </div>
      </div>
      <Toasts />
      <Cmdk />
      <InstallBar />
    </>
  );
}
