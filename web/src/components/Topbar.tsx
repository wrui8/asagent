import {
  ChevronDown,
  LogOut,
  Moon,
  Plus,
  Search,
  Sun,
  Users,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { routeMeta } from "../lib/nav";
import { useAuth } from "../stores/auth";
import { useUI } from "../stores/ui";

export function Topbar() {
  const location = useLocation();
  const navigate = useNavigate();
  const meta = routeMeta(location.pathname);
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const theme = useUI((s) => s.theme);
  const toggleTheme = useUI((s) => s.toggleTheme);
  const setCmdkOpen = useUI((s) => s.setCmdkOpen);
  const toast = useUI((s) => s.toast);

  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  const initial = user?.username ? user.username.slice(0, 1).toUpperCase() : "AS";

  return (
    <header className="topbar">
      <h1>{meta.title}</h1>
      <span className="crumb">{meta.crumb}</span>
      <div className="spacer" />
      <button
        className="search"
        style={{ border: "1px solid var(--border)" }}
        onClick={() => setCmdkOpen(true)}
        title="打开命令面板"
      >
        <Search size={14} />
        <span style={{ flex: 1, textAlign: "left" }}>搜索或跳转…</span>
        <kbd>Ctrl K</kbd>
      </button>
      <button
        className="icon-btn"
        onClick={(event) =>
          toggleTheme({ x: event.clientX, y: event.clientY })
        }
        title="切换主题"
        aria-label="切换主题"
      >
        {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
      </button>
      <div className="dropdown" ref={menuRef}>
        <div
          className="user-chip"
          onClick={() => setMenuOpen((open) => !open)}
          role="button"
          tabIndex={0}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              setMenuOpen((open) => !open);
            }
          }}
        >
          <div className="avatar">{initial}</div>
          <div className="user-meta">
            <b>{user?.username ?? "—"}</b>
            <span>{user?.role === "admin" ? "管理员" : "普通用户"}</span>
          </div>
          <ChevronDown size={13} style={{ color: "var(--faint)" }} />
        </div>
        {menuOpen ? (
          <div className="user-menu">
            <div className="user-menu-head">
              <b>{user?.username ?? "—"}</b>
              <span>
                {user?.role === "admin" ? "admin" : "user"} ·{" "}
                {user?.last_login_at ? "已登录" : "首次登录"}
              </span>
            </div>
            <div className="user-menu-sep" />
            <button
              className="user-menu-item"
              onClick={() => {
                setMenuOpen(false);
                navigate("/new");
              }}
            >
              <Plus size={14} />
              新建会话
            </button>
            <button
              className="user-menu-item"
              onClick={(event) => {
                setMenuOpen(false);
                toggleTheme({ x: event.clientX, y: event.clientY });
              }}
            >
              {theme === "dark" ? <Sun size={14} /> : <Moon size={14} />}
              {theme === "dark" ? "切换到浅色" : "切换到深色"}
            </button>
            {user?.role === "admin" ? (
              <button
                className="user-menu-item"
                onClick={() => {
                  setMenuOpen(false);
                  navigate("/admin");
                }}
              >
                <Users size={14} />
                用户与邀请码
              </button>
            ) : null}
            <div className="user-menu-sep" />
            <button
              className="user-menu-item danger"
              onClick={() => {
                setMenuOpen(false);
                void logout()
                  .then(() => {
                    toast("已退出登录");
                    navigate("/login", { replace: true });
                  })
                  .catch(() => toast("退出登录失败", "err"));
              }}
            >
              <LogOut size={14} />
              退出登录
            </button>
          </div>
        ) : null}
      </div>
    </header>
  );
}
