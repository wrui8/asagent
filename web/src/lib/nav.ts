import {
  BookOpen,
  Cpu,
  FlaskConical,
  LayoutList,
  MessageSquare,
  ScrollText,
  Users,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  group: "Workspace" | "Platform";
  adminOnly?: boolean;
  hideMobile?: boolean;
  end?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "会话", icon: LayoutList, group: "Workspace", end: true },
  { to: "/chat", label: "对话", icon: MessageSquare, group: "Workspace" },
  { to: "/kb", label: "知识库", icon: BookOpen, group: "Workspace" },
  {
    to: "/evals",
    label: "评测",
    icon: FlaskConical,
    group: "Workspace",
    adminOnly: true,
    hideMobile: true,
  },
  { to: "/audit", label: "审计", icon: ScrollText, group: "Workspace" },
  {
    to: "/system",
    label: "MCP 与系统",
    icon: Cpu,
    group: "Platform",
    adminOnly: true,
    hideMobile: true,
  },
  {
    to: "/admin",
    label: "用户与邀请码",
    icon: Users,
    group: "Platform",
    adminOnly: true,
    hideMobile: true,
  },
];

export interface RouteMeta {
  title: string;
  crumb: string;
}

export function routeMeta(pathname: string): RouteMeta {
  if (pathname === "/") return { title: "会话", crumb: "Workspace / 全部会话" };
  if (pathname === "/new") return { title: "新建会话", crumb: "Workspace / 启动任务" };
  if (pathname === "/chat") return { title: "对话", crumb: "Workspace / 当前对话" };
  if (pathname.startsWith("/sessions/")) {
    return { title: "对话", crumb: "Workspace / 会话详情" };
  }
  if (pathname === "/kb") return { title: "知识库", crumb: "Knowledge / 文档与索引" };
  if (pathname === "/evals") return { title: "评测", crumb: "Quality / 评测中心" };
  if (pathname === "/audit") return { title: "审计", crumb: "Security / 写操作审计" };
  if (pathname === "/system") return { title: "MCP 与系统", crumb: "Platform / 运行状态" };
  if (pathname === "/admin") return { title: "用户与邀请码", crumb: "Access / 账号治理" };
  return { title: "asagent", crumb: "Workspace" };
}

export const LAST_SESSION_KEY = "asagent.lastSession";

export function rememberSession(sessionId: string): void {
  localStorage.setItem(LAST_SESSION_KEY, sessionId);
}

export function lastSession(): string | null {
  return localStorage.getItem(LAST_SESSION_KEY);
}
