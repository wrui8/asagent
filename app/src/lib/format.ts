import type { SessionStatus } from "./types";

export interface StatusMeta {
  label: string;
  color: string;
  terminal: boolean;
}

export function statusMeta(status: SessionStatus | string): StatusMeta {
  switch (status) {
    case "running":
      return { label: "运行中", color: "#22d3ee", terminal: false };
    case "awaiting_approval":
      return { label: "等待审批", color: "#fbbf24", terminal: false };
    case "done":
      return { label: "完成", color: "#34d399", terminal: true };
    case "failed":
      return { label: "失败", color: "#f87171", terminal: true };
    default:
      return { label: status, color: "#8fa1c2", terminal: true };
  }
}

export function fmtMoney(cost: number | null | undefined): string {
  const value = Number(cost ?? 0);
  return Number.isFinite(value) ? `¥${value.toFixed(4)}` : "¥0.0000";
}

export function shortId(id: string, head = 8): string {
  return id.length <= head ? id : id.slice(0, head);
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

export function fmtDateTime(ts: string | null | undefined): string {
  const d = parseTs(ts);
  if (!d) return "—";
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")} ${fmtTime(ts)}`;
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
