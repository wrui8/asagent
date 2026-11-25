import type { SessionStatus } from "./types";
import { callMobile, IS_MOBILE_HOST } from "./mobile-host";

export interface StatusMeta {
  label: string;
  cls: string;
  terminal: boolean;
}

export function statusMeta(status: SessionStatus | string): StatusMeta {
  switch (status) {
    case "running":
      return { label: "运行中", cls: "badge-run", terminal: false };
    case "awaiting_approval":
      return { label: "等待审批", cls: "badge-wait", terminal: true };
    case "done":
      return { label: "完成", cls: "badge-ok", terminal: true };
    case "failed":
      return { label: "失败", cls: "badge-fail", terminal: true };
    default:
      return { label: status, cls: "badge-mute", terminal: true };
  }
}

export function fmtMoney(cost: number | null | undefined): string {
  const value = Number(cost ?? 0);
  if (!Number.isFinite(value)) return "¥0.0000";
  return `¥${value.toFixed(4)}`;
}

export function fmtInt(value: number | null | undefined): string {
  return new Intl.NumberFormat("zh-CN").format(Number(value ?? 0));
}

function parseTs(ts: string | null | undefined): Date | null {
  if (!ts) return null;
  const parsed = new Date(ts.includes("T") ? ts : ts.replace(" ", "T"));
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export function fmtTime(ts: string | null | undefined): string {
  const d = parseTs(ts);
  if (!d) return "—";
  return d.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function fmtClock(ts: string | null | undefined): string {
  const d = parseTs(ts);
  if (!d) return "—";
  return d.toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function fmtDate(ts: string | null | undefined): string {
  const d = parseTs(ts);
  if (!d) return "—";
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}

export function fmtDateTime(ts: string | null | undefined): string {
  const d = parseTs(ts);
  if (!d) return "—";
  return `${fmtDate(ts)} ${fmtTime(ts)}`;
}

export function fmtRelative(ts: string | null | undefined): string {
  const d = parseTs(ts);
  if (!d) return "—";
  const diff = Date.now() - d.getTime();
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)} 天前`;
  return fmtDate(ts);
}

export function fmtBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(2)} MB`;
}

export function shortId(id: string, head = 8): string {
  return id.length <= head ? id : id.slice(0, head);
}

export function profileLabel(name: string): string {
  const map: Record<string, string> = {
    sql: "SQL 数据分析",
    rag: "知识问答",
    code: "研发助手",
    office: "办公自动化",
    assistant: "通用助手",
  };
  return map[name] ?? name;
}

export function copyText(text: string): Promise<void> {
  if (IS_MOBILE_HOST) return callMobile<void>("copyText", text);
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const el = document.createElement("textarea");
  el.value = text;
  el.style.position = "fixed";
  el.style.opacity = "0";
  document.body.appendChild(el);
  el.select();
  document.execCommand("copy");
  el.remove();
  return Promise.resolve();
}
