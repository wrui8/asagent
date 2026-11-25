import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { fetchApprovals, fetchEvents, fetchMessages, fetchSession } from "./api";
import { errorMessage } from "./errors";
import { mergeEvents } from "./events";
import { IS_MOBILE_HOST } from "./mobile-host";
import type { AgentEvent } from "./types";
import { useUI } from "../stores/ui";

interface DecisionAttempt {
  callId: string;
  approved: boolean;
}

export function useMobileChat(id: string) {
  const queryClient = useQueryClient();
  const toast = useUI((state) => state.toast);
  const [syncError, setSyncError] = useState("");
  const [checkingCallId, setCheckingCallId] = useState<string | null>(null);
  const unconfirmed = useRef<DecisionAttempt | null>(null);
  const mutating = useRef(false);
  const polling = useRef<{ stop: () => void; start: () => void } | null>(null);

  useEffect(() => {
    if (!IS_MOBILE_HOST || !id) return;
    const eventsKey = ["events", id];
    let foreground = true;
    let controller: AbortController | undefined;
    let timer: number | undefined;

    const stop = () => {
      controller?.abort();
      window.clearTimeout(timer);
    };

    const tick = async (signal: AbortSignal) => {
      let delay = 1500;
      try {
        const previous = queryClient.getQueryData<AgentEvent[]>(eventsKey) ?? [];
        const cursor = previous[previous.length - 1]?.seq ?? 0;
        const [session, approvals, messages, fresh] = await Promise.all([
          fetchSession(id, signal),
          fetchApprovals(id, signal),
          fetchMessages(id, signal),
          fetchEvents(id, cursor, signal),
        ]);
        if (signal.aborted) return;

        const terminal = session.status === "done" || session.status === "failed";
        let events = mergeEvents(previous, fresh);
        if (terminal) {
          // The parallel event read may have finished before the terminal state was read.
          const lastSeq = events[events.length - 1]?.seq ?? cursor;
          events = mergeEvents(events, await fetchEvents(id, lastSeq, signal));
        }
        if (signal.aborted) return;

        let visibleApprovals = approvals;
        const attempt = unconfirmed.current;
        if (attempt) {
          const decision = events.find((event) =>
            event.type === "approval_decided" && event.payload.call_id === attempt.callId,
          )?.payload.approved;
          const current = approvals.find((item) => item.call_id === attempt.callId);
          const outcome = typeof decision !== "boolean"
            ? current?.status
            : decision ? "approved" : "denied";
          if (outcome === "approved" || outcome === "denied") {
            const recorded = outcome === "approved";
            visibleApprovals = approvals.filter((item) => item.call_id !== attempt.callId || item.status !== "pending");
            if (recorded === attempt.approved) {
              toast(recorded ? "已核实该调用已批准" : "已核实该调用已拒绝", "ok");
            } else {
              toast("这项审批已有不同的决定，请查看执行轨迹", "warn");
            }
          } else if (outcome === "pending") {
            toast("服务器显示这项审批仍待处理，可以重试", "warn");
          } else {
            toast("这项审批已从待处理列表移除，请在执行轨迹核对结果", "warn");
          }
          unconfirmed.current = null;
          setCheckingCallId(null);
        }
        queryClient.setQueryData(["session", id], session);
        queryClient.setQueryData(["approvals", id], visibleApprovals);
        queryClient.setQueryData(["messages", id], messages);
        queryClient.setQueryData(eventsKey, events);
        setSyncError("");
        if (terminal) {
          void queryClient.invalidateQueries({ queryKey: ["sessions"] });
          return;
        }
      } catch (error) {
        if (signal.aborted) return;
        setSyncError(errorMessage(error, "连接中断，正在重新同步会话"));
        delay = 4000;
      }
      if (!signal.aborted) timer = window.setTimeout(() => void tick(signal), delay);
    };

    const start = () => {
      stop();
      if (!foreground || document.hidden || mutating.current) return;
      controller = new AbortController();
      void tick(controller.signal);
    };
    const onActive = () => {
      foreground = true;
      start();
    };
    const onInactive = () => {
      foreground = false;
      stop();
    };
    polling.current = { start, stop };
    start();
    document.addEventListener("visibilitychange", start);
    window.addEventListener("focus", start);
    window.addEventListener("asagent:active", onActive);
    window.addEventListener("asagent:inactive", onInactive);
    return () => {
      stop();
      polling.current = null;
      document.removeEventListener("visibilitychange", start);
      window.removeEventListener("focus", start);
      window.removeEventListener("asagent:active", onActive);
      window.removeEventListener("asagent:inactive", onInactive);
    };
  }, [id, queryClient, toast]);

  const beginMutation = () => {
    if (!IS_MOBILE_HOST) return true;
    if (mutating.current || unconfirmed.current) return false;
    mutating.current = true;
    polling.current?.stop();
    return true;
  };
  const finishMutation = () => {
    if (!IS_MOBILE_HOST) return;
    mutating.current = false;
    polling.current?.start();
  };
  const verifyDecision = (attempt: DecisionAttempt) => {
    unconfirmed.current = attempt;
    setCheckingCallId(attempt.callId);
  };

  return { syncError, checkingCallId, beginMutation, finishMutation, verifyDecision, retry: () => polling.current?.start() };
}
