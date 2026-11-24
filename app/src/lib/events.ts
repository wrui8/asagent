import type { AgentEvent } from "./types";

export interface TimelineMeta {
  label: string;
  detail: string;
  tone: "normal" | "accent" | "ok" | "warn";
  tag?: string;
}

function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** 事件 → 手机端轨迹一行。 */
export function describeEvent(event: AgentEvent): TimelineMeta {
  const p = event.payload ?? {};
  const label = event.type;

  switch (event.type) {
    case "session_created":
      return { label, detail: `${text(p.profile) || "会话"} · 已创建`, tone: "accent" };
    case "user_input":
      return { label, detail: text(p.text) || text(p.input) || "收到新指令", tone: "normal" };
    case "resumed":
      return { label, detail: "会话恢复执行", tone: "accent" };
    case "route_selected":
      return {
        label,
        detail: [
          text(p.model) || text(p.route) || "自动路由",
          text(p.tier) ? `${text(p.tier)} 层` : "",
        ]
          .filter(Boolean)
          .join(" · "),
        tone: "accent",
      };
    case "context_built":
      return {
        label,
        detail:
          [p.messages ? `${text(p.messages)} 条消息` : ""]
            .filter(Boolean)
            .join(" · ") || "上下文已构建",
        tone: "normal",
      };
    case "model_response": {
      const usage = (p.usage ?? {}) as {
        input_tokens?: number;
        output_tokens?: number;
      };
      const tokens = (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0);
      return {
        label,
        detail:
          [
            p.finish_reason ? `finish=${text(p.finish_reason)}` : "",
            tokens ? `${tokens} tokens` : "",
            p.latency_ms ? `${text(p.latency_ms)}ms` : "",
          ]
            .filter(Boolean)
            .join(" · ") || "模型返回",
        tone: "normal",
      };
    }
    case "tool_call":
      return {
        label,
        tag: text(p.tool) || text(p.name),
        detail: p.permission !== undefined ? `permission ${text(p.permission)}` : "",
        tone: "normal",
      };
    case "tool_result":
      return {
        label,
        detail:
          [
            p.ok === false ? "失败" : "ok",
            p.elapsed_ms !== undefined ? `${text(p.elapsed_ms)}ms` : "",
          ]
            .filter(Boolean)
            .join(" · "),
        tone: p.ok === false ? "warn" : "ok",
      };
    case "guard_verdict":
      return {
        label,
        detail: [text(p.stage), text(p.reason) || text(p.verdict)]
          .filter(Boolean)
          .join(" · "),
        tone: "warn",
      };
    case "approval_required":
      return {
        label,
        tag: text(p.tool),
        detail: "等待人工确认",
        tone: "warn",
      };
    case "approval_decided":
      return {
        label,
        tag: text(p.tool),
        detail: p.approved ? "已批准" : "已拒绝",
        tone: p.approved ? "ok" : "warn",
      };
    case "budget_exceeded":
      return {
        label,
        detail: `累计 ${text(p.cost)} 超出上限 ${text(p.limit)}`,
        tone: "warn",
      };
    case "final":
      return {
        label,
        detail:
          p.cost !== undefined
            ? `会话完成 · 累计 ¥${Number(p.cost).toFixed(4)}`
            : "会话完成",
        tone: "ok",
      };
    case "error":
      return { label, detail: text(p.message) || "执行出错", tone: "warn" };
    default:
      return { label, detail: text(p), tone: "normal" };
  }
}

export function mergeEvents(prev: AgentEvent[], next: AgentEvent[]): AgentEvent[] {
  if (!next.length) return prev;
  const map = new Map<number, AgentEvent>();
  for (const event of prev) map.set(event.seq, event);
  for (const event of next) map.set(event.seq, event);
  return [...map.values()].sort((a, b) => a.seq - b.seq);
}
