import { Redirect, useFocusEffect, useLocalSearchParams, useRouter } from "expo-router";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import {
  cancelSession,
  decideApproval,
  errorText,
  fetchApprovals,
  fetchEvents,
  fetchSession,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { describeEvent, mergeEvents } from "@/lib/events";
import {
  fmtDateTime,
  fmtMoney,
  fmtTime,
  profileLabel,
  statusMeta,
} from "@/lib/format";
import { approvalArgsPreview, reconcileApproval, startSessionPolling } from "@/lib/session-detail";
import { colors, radius } from "@/lib/theme";
import type { AgentEvent, Approval, Session } from "@/lib/types";

const TONE_COLOR: Record<string, string> = {
  normal: colors.muted,
  accent: colors.accent2,
  ok: colors.ok,
  warn: colors.warn,
};

type SessionAction =
  | { type: "approval"; callId: string; approved: boolean }
  | { type: "cancel" };

type UnconfirmedDecision = { callId: string; approved: boolean; error: string };

export default function SessionScreen() {
  const { status } = useAuth();
  const params = useLocalSearchParams<{ id?: string | string[] }>();
  const id = Array.isArray(params.id) ? params.id[0] : (params.id ?? "");

  if (status === "loading") {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent2} />
      </View>
    );
  }
  if (status === "anon") return <Redirect href="/login" />;
  if (!id) {
    return (
      <View style={styles.center}>
        <Text style={styles.empty}>会话不存在</Text>
      </View>
    );
  }
  return <SessionDetail key={id} id={id} />;
}

function SessionDetail({ id }: { id: string }) {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [syncError, setSyncError] = useState("");
  const [actionError, setActionError] = useState("");
  const [action, setAction] = useState<SessionAction | null>(null);
  const [checkingDecision, setCheckingDecision] = useState(false);
  const seqRef = useRef(0);
  const polling = useRef<{ start: () => void; stop: () => void } | null>(null);
  const inFlightAction = useRef<AbortController | null>(null);
  const unconfirmed = useRef<UnconfirmedDecision | null>(null);

  useEffect(() => () => inFlightAction.current?.abort(), []);

  useFocusEffect(
    useCallback(() => {
      let stopCurrentPoll: (() => void) | undefined;
      let foreground = AppState.currentState == null || AppState.currentState === "active";
      const stop = () => {
        stopCurrentPoll?.();
        stopCurrentPoll = undefined;
      };
      const start = () => {
        stop();
        if (!foreground || inFlightAction.current) return;
        stopCurrentPoll = startSessionPolling({
          initialCursor: seqRef.current,
          loadSession: (signal) => fetchSession(id, signal),
          loadApprovals: (signal) => fetchApprovals(id, signal),
          loadEvents: (cursor, signal) => fetchEvents(id, cursor, signal),
          onUpdate: (update) => {
            seqRef.current = update.cursor;
            setSession(update.session);
            setEvents((previous) => mergeEvents(previous, update.events));
            const attempt = unconfirmed.current;
            let nextApprovals = update.approvals;
            if (attempt) {
              const outcome = reconcileApproval(attempt.callId, attempt.approved, update);
              unconfirmed.current = null;
              setCheckingDecision(false);
              if (outcome === "pending") {
                setActionError(`审批仍待处理，可以重试。${attempt.error}`);
              } else {
                nextApprovals = nextApprovals.filter((item) => item.call_id !== attempt.callId);
                if (outcome === "other-decision") {
                  setActionError("这项审批已有不同的决定，请查看执行轨迹。");
                } else if (outcome === "unconfirmed") {
                  setActionError("这项审批已从待处理列表移除，请在执行轨迹核对结果。");
                }
              }
            }
            setApprovals(nextApprovals);
            setSyncError("");
          },
          onError: (error) => setSyncError(errorText(error, "会话加载失败")),
        });
      };

      const subscription = AppState.addEventListener("change", (state) => {
        foreground = state === "active";
        if (foreground) start();
        else stop();
      });
      polling.current = { start, stop };
      start();

      return () => {
        subscription.remove();
        stop();
        polling.current = null;
      };
    }, [id]),
  );

  const performAction = async (next: SessionAction) => {
    if (inFlightAction.current || unconfirmed.current) return;
    const controller = new AbortController();
    const { signal } = controller;
    inFlightAction.current = controller;
    polling.current?.stop();
    setAction(next);
    setActionError("");
    try {
      if (next.type === "approval") {
        const remaining = await decideApproval(id, next.callId, next.approved, signal);
        if (!signal.aborted) setApprovals(remaining);
      } else {
        await cancelSession(id, signal);
      }
    } catch (error) {
      if (signal.aborted) return;
      if (next.type === "approval") {
        unconfirmed.current = { ...next, error: errorText(error, "审批提交失败") };
        setCheckingDecision(true);
      } else {
        setActionError(errorText(error, "取消失败"));
      }
    } finally {
      if (!signal.aborted) {
        inFlightAction.current = null;
        setAction(null);
        polling.current?.start();
      }
    }
  };

  const meta = session ? statusMeta(session.status) : null;
  const running =
    session?.status === "running" || session?.status === "awaiting_approval";
  const canceling = action?.type === "cancel";
  const controlsDisabled = action !== null || checkingDecision;
  const pending = approvals.filter((item) => item.status === "pending");
  const inputs = events.filter((event) => event.type === "user_input");
  const timeline = events
    .filter((event) => event.type !== "user_input")
    // 内核暂停时写入的 CLI 提示对手机端无意义，审批卡片已经说明状态
    .filter(
      (event) =>
        !(
          event.type === "error" &&
          String(event.payload.message ?? "").includes("人工审批")
        ),
    )
    .slice(-60);
  const subtitle = session
    ? `${profileLabel(session.profile)} · ${fmtMoney(session.cost)} · ${session.step} 步`
    : "加载中";

  return (
    <SafeAreaView style={styles.screen}>
      <View style={styles.topBar}>
        <Pressable accessibilityRole="button" onPress={() => router.dismissTo("/")} style={styles.back}>
          <Text style={styles.backText}>‹ 会话</Text>
        </Pressable>
        <View style={styles.topMid}>
          <Text style={styles.topTitle} numberOfLines={1}>
            {session?.title || "会话详情"}
          </Text>
          <Text style={styles.topSub}>{subtitle}</Text>
        </View>
        <View style={styles.back} />
      </View>

      {syncError ? <Text style={styles.error}>{syncError}</Text> : null}
      {actionError ? <Text style={styles.error}>{actionError}</Text> : null}
      {checkingDecision ? (
        <Text style={styles.syncStatus} accessibilityLiveRegion="polite">正在核对服务器上的审批结果…</Text>
      ) : null}

      {!session ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent2} />
        </View>
      ) : (
        <ScrollView contentContainerStyle={styles.scroll}>
          <View style={styles.statusCard}>
            <View style={styles.statusRow}>
              <View style={[styles.dot, { backgroundColor: meta?.color }]} />
              <Text style={[styles.statusLabel, { color: meta?.color }]}>
                {meta?.label}
              </Text>
              <Text style={styles.cost}>{fmtMoney(session.cost)}</Text>
            </View>
            <Text style={styles.metaLine}>
              {session.session_id} · 创建于 {fmtDateTime(session.created_at)}
            </Text>
            {running ? (
              <View style={styles.running}>
                <ActivityIndicator size="small" color={colors.accent2} />
                <Text style={styles.runningText}>
                  {session.status === "awaiting_approval"
                    ? "等待人工审批后继续"
                    : "Agent 正在执行，自动刷新中"}
                </Text>
              </View>
            ) : null}
          </View>

          {inputs.map((event) => (
            <View key={event.seq} style={styles.userBubble}>
              <Text style={styles.bubbleLabel}>你的指令</Text>
              <Text style={styles.userText}>
                {String(event.payload.text ?? event.payload.input ?? "")}
              </Text>
            </View>
          ))}

          {pending.length ? (
            <View style={styles.approval}>
              <Text style={styles.approvalTitle}>需要你的批准</Text>
              {pending.map((item) => (
                <ApprovalCard
                  key={item.call_id}
                  item={item}
                  disabled={controlsDisabled}
                  submitting={action?.type === "approval" && action.callId === item.call_id}
                  onDecide={(approved) => void performAction({
                    type: "approval",
                    callId: item.call_id,
                    approved,
                  })}
                />
              ))}
            </View>
          ) : null}

          {session.final_answer ? (
            <View style={styles.answer}>
              <Text style={styles.answerLabel}>Agent 结论</Text>
              <Text style={styles.answerText}>{session.final_answer}</Text>
            </View>
          ) : null}

          {session.error && session.status !== "awaiting_approval" ? (
            <View style={styles.failure}>
              <Text style={styles.failureLabel}>错误</Text>
              <Text style={styles.failureText}>{session.error}</Text>
            </View>
          ) : null}

          <Text style={styles.sectionLabel}>
            执行轨迹{events.length > 60 ? "（最近 60 条）" : ""}
          </Text>
          <View style={styles.timeline}>
            {timeline.length === 0 ? (
              <Text style={styles.emptyTrace}>暂无事件</Text>
            ) : (
              timeline.map((event) => {
                const info = describeEvent(event);
                return (
                  <View key={event.seq} style={styles.traceRow}>
                    <View
                      style={[styles.traceDot, { backgroundColor: TONE_COLOR[info.tone] }]}
                    />
                    <View style={styles.traceBody}>
                      <View style={styles.traceHead}>
                        <Text style={styles.traceType}>{info.label}</Text>
                        {info.tag ? (
                          <Text style={styles.traceTag}>{info.tag}</Text>
                        ) : null}
                        <Text style={styles.traceTs}>{fmtTime(event.ts)}</Text>
                      </View>
                      {info.detail ? (
                        <Text style={styles.traceDetail} numberOfLines={4}>
                          {info.detail}
                        </Text>
                      ) : null}
                    </View>
                  </View>
                );
              })
            )}
          </View>

          {running ? (
            <Pressable
              style={({ pressed }) => [styles.cancel, (pressed || canceling) && styles.btnPressed]}
              accessibilityRole="button"
              disabled={controlsDisabled}
              onPress={() => void performAction({ type: "cancel" })}
            >
              <Text style={styles.cancelText}>
                {canceling ? "正在取消…" : "取消本次任务"}
              </Text>
            </Pressable>
          ) : null}
        </ScrollView>
      )}
    </SafeAreaView>
  );
}

function ApprovalCard({ item, disabled, submitting, onDecide }: {
  item: Approval;
  disabled: boolean;
  submitting: boolean;
  onDecide: (approved: boolean) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { full, preview } = approvalArgsPreview(item.args ?? {});

  return (
    <View style={styles.approvalItem}>
      <Text style={styles.approvalTool}>
        {item.tool} · 权限 {item.permission}
      </Text>
      <Text style={styles.approvalArgs} selectable={expanded}>
        {expanded ? full : preview}
      </Text>
      {preview !== full ? (
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ expanded }}
          style={styles.expandArgs}
          onPress={() => setExpanded((value) => !value)}
        >
          <Text style={styles.expandArgsText}>{expanded ? "收起参数" : "查看完整参数"}</Text>
        </Pressable>
      ) : null}
      <View style={styles.approvalActions}>
        {submitting ? <ActivityIndicator color={colors.accent2} size="small" /> : null}
        <Pressable
          accessibilityRole="button"
          style={({ pressed }) => [styles.reject, (pressed || disabled) && styles.btnPressed]}
          disabled={disabled}
          onPress={() => onDecide(false)}
        >
          <Text style={styles.rejectText}>拒绝</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          style={({ pressed }) => [styles.approve, (pressed || disabled) && styles.btnPressed]}
          disabled={disabled}
          onPress={() => onDecide(true)}
        >
          <Text style={styles.approveText}>批准</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  center: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.bg,
  },
  empty: { color: colors.muted, fontSize: 13 },
  topBar: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderBottomWidth: 1,
    borderBottomColor: colors.border,
  },
  back: { width: 64 },
  backText: { color: colors.accent2, fontSize: 14 },
  topMid: { flex: 1, alignItems: "center" },
  topTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  topSub: { color: colors.faint, fontSize: 11, marginTop: 3 },
  error: {
    color: colors.danger,
    fontSize: 12,
    paddingHorizontal: 20,
    paddingTop: 10,
  },
  syncStatus: { color: colors.muted, paddingHorizontal: 20, paddingTop: 10 },
  scroll: { paddingHorizontal: 16, paddingTop: 14, paddingBottom: 40, gap: 12 },
  statusCard: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: 16,
  },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  statusLabel: { fontSize: 13, fontWeight: "700" },
  cost: { marginLeft: "auto", color: colors.text, fontSize: 15, fontWeight: "800" },
  metaLine: { color: colors.faint, fontSize: 11, marginTop: 10 },
  running: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 12 },
  runningText: { color: colors.muted, fontSize: 12 },
  userBubble: {
    backgroundColor: colors.raised,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    borderTopRightRadius: 6,
    padding: 14,
    alignSelf: "flex-end",
    maxWidth: "92%",
  },
  bubbleLabel: {
    color: colors.accent2,
    fontSize: 10,
    fontWeight: "700",
    letterSpacing: 1,
    marginBottom: 6,
  },
  userText: { color: colors.text, fontSize: 14, lineHeight: 21 },
  approval: {
    backgroundColor: "rgba(251,191,36,0.08)",
    borderWidth: 1,
    borderColor: "rgba(251,191,36,0.45)",
    borderRadius: radius.lg,
    padding: 16,
  },
  approvalTitle: { color: colors.warn, fontSize: 13, fontWeight: "800" },
  approvalItem: { marginTop: 12 },
  approvalTool: { color: colors.text, fontSize: 13, fontWeight: "700" },
  approvalArgs: {
    color: colors.muted,
    fontSize: 11,
    lineHeight: 16,
    fontFamily: "monospace",
    backgroundColor: colors.black,
    borderRadius: radius.sm,
    padding: 10,
    marginTop: 8,
  },
  expandArgs: { alignSelf: "flex-start", justifyContent: "center", minHeight: 44 },
  expandArgsText: { color: colors.accent2, fontSize: 13, fontWeight: "700" },
  approvalActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    justifyContent: "flex-end",
    gap: 10,
    marginTop: 12,
  },
  reject: {
    borderWidth: 1,
    borderColor: colors.danger,
    borderRadius: radius.sm,
    paddingHorizontal: 18,
    paddingVertical: 9,
  },
  rejectText: { color: colors.danger, fontSize: 13, fontWeight: "700" },
  approve: {
    backgroundColor: colors.ok,
    borderRadius: radius.sm,
    paddingHorizontal: 22,
    paddingVertical: 9,
    minWidth: 76,
    alignItems: "center",
  },
  approveText: { color: "#04121a", fontSize: 13, fontWeight: "800" },
  btnPressed: { opacity: 0.7 },
  answer: {
    backgroundColor: "rgba(52,211,153,0.08)",
    borderWidth: 1,
    borderColor: "rgba(52,211,153,0.4)",
    borderRadius: radius.lg,
    padding: 16,
  },
  answerLabel: {
    color: colors.ok,
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 1.2,
    marginBottom: 8,
  },
  answerText: { color: colors.text, fontSize: 14, lineHeight: 22 },
  failure: {
    backgroundColor: "rgba(248,113,113,0.08)",
    borderWidth: 1,
    borderColor: "rgba(248,113,113,0.4)",
    borderRadius: radius.lg,
    padding: 16,
  },
  failureLabel: {
    color: colors.danger,
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 1.2,
    marginBottom: 8,
  },
  failureText: { color: colors.text, fontSize: 13, lineHeight: 20 },
  sectionLabel: {
    color: colors.faint,
    fontSize: 10,
    fontWeight: "800",
    letterSpacing: 1.6,
    marginTop: 6,
  },
  timeline: {
    backgroundColor: colors.card,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.lg,
    padding: 14,
    gap: 14,
  },
  emptyTrace: { color: colors.faint, fontSize: 12 },
  traceRow: { flexDirection: "row", gap: 10 },
  traceDot: { width: 6, height: 6, borderRadius: 3, marginTop: 6 },
  traceBody: { flex: 1 },
  traceHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  traceType: { color: colors.text, fontSize: 12, fontWeight: "600" },
  traceTag: {
    color: colors.accent2,
    fontSize: 10,
    backgroundColor: colors.raised,
    borderRadius: 4,
    paddingHorizontal: 6,
    paddingVertical: 1,
  },
  traceTs: { marginLeft: "auto", color: colors.faint, fontSize: 10 },
  traceDetail: { color: colors.muted, fontSize: 11, lineHeight: 16, marginTop: 3 },
  cancel: { alignItems: "center", paddingVertical: 16 },
  cancelText: { color: colors.faint, fontSize: 13 },
});
