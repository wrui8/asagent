import { NavLink } from "react-router-dom";
import { NAV_ITEMS } from "../lib/nav";
import { useAuth } from "../stores/auth";

const GROUPS = ["Workspace", "Platform"] as const;

export function Sidebar() {
  const user = useAuth((s) => s.user);

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="brand-mark">AS</div>
        <div>
          <div className="brand-name">asagent</div>
          <div className="brand-sub">agent console</div>
        </div>
      </div>

      <nav className="nav" aria-label="主导航">
        {GROUPS.map((group) => {
          const items = NAV_ITEMS.filter(
            (item) =>
              item.group === group && (!item.adminOnly || user?.role === "admin"),
          );
          if (!items.length) return null;
          return (
            <div key={group}>
              <div className="nav-group">{group}</div>
              {items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    data-hide-mobile={item.hideMobile ? "true" : undefined}
                    className={({ isActive }) =>
                      `nav-item ${isActive ? "active" : ""}`
                    }
                  >
                    <Icon strokeWidth={1.8} />
                    {item.label}
                  </NavLink>
                );
              })}
            </div>
          );
        })}
      </nav>

      <div className="sidebar-foot">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span>当前账号</span>
          <b style={{ color: "var(--text)", fontFamily: "var(--mono)" }}>
            {user?.username ?? "—"}
          </b>
        </div>
        <div style={{ marginTop: 4 }}>
          {user?.role === "admin" ? "管理员 · 可访问全部会话" : "普通用户 · 仅自己的会话"}
        </div>
        <div className="kbd-hint">
          <kbd>Ctrl</kbd>
          <kbd>K</kbd> 命令面板
        </div>
      </div>
    </aside>
  );
}
