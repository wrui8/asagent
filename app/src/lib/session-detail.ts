import { mergeEvents } from "./events";
import { statusMeta } from "./format";
import type { AgentEvent, Approval, Session } from "./types";

interface SessionUpdate {
  session: Session;
  approvals: Approval[];
  events: AgentEvent[];
  cursor: number;
}

interface SessionPollingOptions {
  initialCursor: number;
  loadSession: (signal: AbortSignal) => Promise<Session>;
  loadApprovals: (signal: AbortSignal) => Promise<Approval[]>;
  loadEvents: (cursor: number, signal: AbortSignal) => Promise<AgentEvent[]>;
  onUpdate: (update: SessionUpdate) => void;
  onError: (error: unknown) => void;
}

export function startSessionPolling(options: SessionPollingOptions): () => void {
  const controller = new AbortController();
  const { signal } = controller;
  let cursor = options.initialCursor;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const tick = async () => {
    let delay = 1500;
    try {
      const [session, approvals, initialEvents] = await Promise.all([
        options.loadSession(signal),
        options.loadApprovals(signal),
        options.loadEvents(cursor, signal),
      ]);
      if (signal.aborted) return;

      const terminal = statusMeta(session.status).terminal;
      let events = initialEvents;
      if (terminal) {
        // The initial event request may have completed before the terminal status was read.
        const afterSeq = initialEvents.at(-1)?.seq ?? cursor;
        events = mergeEvents(initialEvents, await options.loadEvents(afterSeq, signal));
        if (signal.aborted) return;
      }
      cursor = events.at(-1)?.seq ?? cursor;
      options.onUpdate({ session, approvals, events, cursor });
      if (terminal) return;
    } catch (error) {
      if (signal.aborted) return;
      options.onError(error);
      delay = 4000;
    }
    if (!signal.aborted) timer = setTimeout(() => void tick(), delay);
  };

  void tick();
  return () => {
    controller.abort();
    if (timer !== undefined) clearTimeout(timer);
  };
}

export function reconcileApproval(
  callId: string,
  requestedApproval: boolean,
  { approvals, events }: Pick<SessionUpdate, "approvals" | "events">,
): "confirmed" | "other-decision" | "pending" | "unconfirmed" {
  const decision = events.findLast(
    (event) => event.type === "approval_decided" && event.payload.call_id === callId,
  )?.payload.approved;
  if (typeof decision === "boolean") {
    return decision === requestedApproval ? "confirmed" : "other-decision";
  }
  const approval = approvals.find((item) => item.call_id === callId);
  if (approval?.status === "pending") return "pending";
  if (approval?.status === "approved" || approval?.status === "denied") {
    return (approval.status === "approved") === requestedApproval ? "confirmed" : "other-decision";
  }
  return "unconfirmed";
}

export function approvalArgsPreview(args: Approval["args"]): { full: string; preview: string } {
  const full = JSON.stringify(args, null, 2);
  const clipped = full.split("\n", 10).join("\n").slice(0, 600);
  return { full, preview: clipped === full ? full : `${clipped}…` };
}
