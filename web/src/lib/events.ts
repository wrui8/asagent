import type { AgentEvent } from "./types";

export interface TimelineMeta {
  cls: string;
  label: string;
  detail: string;
  tag?: string;
}

const TYPE_LABEL: Record<string, string> = {
  session_created: "session_created",
  user_input: "user_input",
  resumed: "resumed",
  route_selected: "route_selected",
  context_built: "context_built",
  model_response: "model_response",
  protocol_repair: "protocol_repair",
  tool_call: "tool_call",
  tool_result: "tool_result",
  guard_verdict: "guard_verdict",
  approval_required: "approval_required",
  approval_decided: "approval_decided",
  audit: "audit",
  budget_exceeded: "budget_exceeded",
  checkpoint: "checkpoint",
  final: "final",
  error: "error",
};

const TYPE_CLASS: Record<string, string> = {
  route_selected: "route",
  context_built: "",
  model_response: "",
  tool_call: "tool",
  tool_result: "ok",
  approval_required: "warn",
  approval_decided: "ok",
  audit: "warn",
  budget_exceeded: "warn",
  guard_verdict: "warn",
  final: "final",
  error: "warn",
};

function text(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** 把事件转成时间线展示信息（不含 HTML，渲染侧统一转义）。 */
export function describeEvent(event: AgentEvent): TimelineMeta {
  const p = event.payload ?? {};
  const label = TYPE_LABEL[event.type] ?? event.type;
  const base = { cls: TYPE_CLASS[event.type] ?? "", label };

  switch (event.type) {
    case "session_created":
      return { ...base, cls: "route", detail: `${text(p.profile) || "会话"} · 已创建` };
    case "user_input":
      return {
        ...base,
        detail: text(p.text) || text(p.input) || "收到新指令",
      };
    case "resumed":
      return { ...base, cls: "route", detail: "会话恢复执行" };
    case "route_selected":
      return {
        ...base,
        cls: "route",
        detail: [
          text(p.model) || text(p.route) || "自动路由",
          text(p.tier) ? `${text(p.tier)} 层` : "",
          p.escalated ? `升级 ${text(p.escalated)}` : "",
        ]
          .filter(Boolean)
          .join(" · "),
      };
    case "context_built":
      return {
        ...base,
        detail: [
          p.messages ? `${text(p.messages)} 条消息` : "",
          p.tokens ? `~${text(p.tokens)} tokens` : "",
        ]
          .filter(Boolean)
          .join(" · ") || "上下文已构建",
      };
    case "model_response": {
      const usage = (p.usage ?? {}) as {
        input_tokens?: number;
        output_tokens?: number;
      };
      const tokens = (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0);
      return {
        ...base,
        detail: [
          p.finish_reason ? `finish=${text(p.finish_reason)}` : "",
          tokens ? `${tokens} tokens` : "",
          p.latency_ms ? `${text(p.latency_ms)}ms` : "",
        ]
          .filter(Boolean)
          .join(" · ") || "模型返回",
      };
    }
    case "tool_call":
      return {
        ...base,
        tag: text(p.tool) || text(p.name),
        detail: [
          p.permission !== undefined ? `permission ${text(p.permission)}` : "",
          text(p.call_id),
        ]
          .filter(Boolean)
          .join(" · "),
      };
    case "tool_result":
      return {
        ...base,
        cls: p.ok === false ? "warn" : "ok",
        detail: [
          p.ok === false ? `失败${p.error_code ? `（${text(p.error_code)}）` : ""}` : "ok",
          p.elapsed_ms !== undefined ? `${text(p.elapsed_ms)}ms` : "",
          p.truncated ? "已截断" : "",
          p.artifact_id ? `artifact ${text(p.artifact_id)}` : "",
          p.injection_suspected ? "疑似注入" : "",
        ]
          .filter(Boolean)
          .join(" · "),
      };
    case "guard_verdict":
      return {
        ...base,
        cls: "warn",
        detail: [text(p.stage), text(p.reason) || text(p.verdict)]
          .filter(Boolean)
          .join(" · "),
      };
    case "approval_required":
      return {
        ...base,
        tag: text(p.tool),
        detail: `${text(p.call_id)} · 等待人工确认`,
      };
    case "approval_decided":
      return {
        ...base,
        cls: p.approved ? "ok" : "warn",
        tag: text(p.tool),
        detail: [p.approved ? "已批准" : "已拒绝", text(p.call_id)]
          .filter(Boolean)
          .join(" · "),
      };
    case "audit":
      return {
        ...base,
        cls: "warn",
        tag: text(p.tool),
        detail: [text(p.path) || text(p.target), p.ok === false ? "失败" : "成功"]
          .filter(Boolean)
          .join(" · "),
      };
    case "budget_exceeded":
      return {
        ...base,
        cls: "warn",
        detail: `累计 ${text(p.cost)} 超出上限 ${text(p.limit)}`,
      };
    case "checkpoint":
      return { ...base, detail: `step ${text(p.step)} · 已保存` };
    case "final": {
      const roundSteps = typeof p.round_steps === "number" ? p.round_steps : null;
      const roundCost = typeof p.round_cost === "number" ? p.round_cost : null;
      const round = [
        roundSteps !== null ? `本轮 ${roundSteps} 步` : "",
        roundCost !== null ? `本轮 ¥${roundCost.toFixed(4)}` : "",
      ]
        .filter(Boolean)
        .join(" · ");
      const total = p.cost !== undefined ? `累计 ¥${Number(p.cost).toFixed(4)}` : "";
      return {
        ...base,
        cls: "final",
        detail: ["会话完成", round, total].filter(Boolean).join(" · "),
      };
    }
    case "error":
      return { ...base, cls: "warn", detail: text(p.message) || "执行出错" };
    default:
      return { ...base, detail: text(p) };
  }
}

/** 合并事件并按 seq 去重排序。 */
export function mergeEvents(prev: AgentEvent[], next: AgentEvent[]): AgentEvent[] {
  if (!next.length) return prev;
  const map = new Map<number, AgentEvent>();
  for (const event of prev) map.set(event.seq, event);
  for (const event of next) map.set(event.seq, event);
  return [...map.values()].sort((a, b) => a.seq - b.seq);
}
