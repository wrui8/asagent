import { LogOut, Moon, Plus, RefreshCw, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { NAV_ITEMS } from "../lib/nav";
import { useAuth } from "../stores/auth";
import { useUI } from "../stores/ui";

interface Command {
  key: string;
  ic: string;
  label: string;
  keywords: string;
  adminOnly?: boolean;
  run: () => void;
}

export function Cmdk() {
  const open = useUI((s) => s.cmdkOpen);
  const setOpen = useUI((s) => s.setCmdkOpen);
  const toggleTheme = useUI((s) => s.toggleTheme);
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const navigate = useNavigate();
  const location = useLocation();

  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const go = (to: string) => {
    setOpen(false);
    navigate(to);
  };

  const commands = useMemo<Command[]>(() => {
    const navCommands: Command[] = NAV_ITEMS.map((item) => ({
      key: item.to,
      ic: item.label.slice(0, 2),
      label: item.to === "/" ? "会话列表" : item.label,
      keywords: `${item.label} ${item.to}`,
      adminOnly: item.adminOnly,
      run: () => go(item.to),
    }));
    return [
      { key: "new", ic: "新建", label: "新建会话", keywords: "new create", run: () => go("/new") },
      ...navCommands,
      {
        key: "reindex",
        ic: "重建",
        label: "重建知识库索引",
        keywords: "reindex rebuild",
        run: () => go("/kb?reindex=1"),
      },
      {
        key: "theme",
        ic: "主题",
        label: "切换浅色 / 深色主题",
        keywords: "theme dark light",
        run: () => {
          setOpen(false);
          toggleTheme({ x: window.innerWidth - 40, y: 30 });
        },
      },
      {
        key: "logout",
        ic: "退出",
        label: "退出登录",
        keywords: "logout sign out",
        run: () => {
          setOpen(false);
          void logout().then(() => navigate("/login", { replace: true }));
        },
      },
    ].filter((item) => !item.adminOnly || user?.role === "admin");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user?.role, location.pathname]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter(
      (item) =>
        item.label.toLowerCase().includes(q) ||
        item.ic.toLowerCase().includes(q) ||
        item.keywords.toLowerCase().includes(q),
    );
  }, [commands, query]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen(!open);
      } else if (event.key === "Escape" && open) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, setOpen]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setIndex(0);
    const timer = window.setTimeout(() => inputRef.current?.focus(), 30);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    setIndex(0);
  }, [query]);

  if (!open) return null;

  const runAt = (position: number) => {
    const item = filtered[position];
    if (item) item.run();
  };

  const icons: Record<string, ReactNode> = {
    new: <Plus size={13} />,
    reindex: <RefreshCw size={13} />,
    theme: <Moon size={13} />,
    logout: <LogOut size={13} />,
  };

  return (
    <div
      className="cmdk"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) setOpen(false);
      }}
    >
      <div className="cmdk-box" role="dialog" aria-modal="true" aria-label="命令面板">
        <div className="cmdk-input">
          <Search size={16} style={{ color: "var(--faint)" }} />
          <input
            ref={inputRef}
            value={query}
            aria-label="搜索命令"
            placeholder="跳转页面、执行操作…"
            autoComplete="off"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing) return;
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setIndex((i) => Math.min(i + 1, Math.max(filtered.length - 1, 0)));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setIndex((i) => Math.max(i - 1, 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                runAt(index);
              }
            }}
          />
          <button
            type="button"
            className="icon-btn mobile-only"
            aria-label="关闭命令面板"
            onClick={() => setOpen(false)}
          >
            <X size={16} />
          </button>
        </div>
        <div className="cmdk-list">
          {filtered.length === 0 ? (
            <div className="empty" style={{ padding: "22px 10px" }}>
              没有匹配的命令
            </div>
          ) : (
            filtered.map((item, i) => (
              <button
                type="button"
                key={item.key}
                className={`cmd-item ${i === index ? "on" : ""}`}
                onPointerMove={() => setIndex(i)}
                onClick={() => runAt(i)}
              >
                <span className="ic" aria-hidden="true">{icons[item.key] ?? item.ic}</span>
                {item.label}
                <span className="kbd" aria-hidden="true">{i === index ? "↵" : ""}</span>
              </button>
            ))
          )}
        </div>
        <div className="cmdk-foot">
          <span>
            <kbd>↑↓</kbd> 选择
          </span>
          <span>
            <kbd>↵</kbd> 打开
          </span>
          <span>
            <kbd>esc</kbd> 关闭
          </span>
        </div>
      </div>
    </div>
  );
}
