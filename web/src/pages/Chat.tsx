import {
  ArrowLeft,
  Ban,
  Copy,
  Download,
  FileJson,
  ListTree,
  Play,
  Send,
  Square,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cancelSession,
  decideApproval,
  fetchApprovals,
  fetchArtifact,
  fetchEvents,
  fetchMessages,
  fetchSession,
  resumeSession,
} from "../lib/api";
import { errorMessage } from "../lib/errors";
import { mergeEvents } from "../lib/events";
import { copyText, fmtInt, fmtMoney, profileLabel, shortId } from "../lib/format";
import { rememberSession } from "../lib/nav";
import { openSessionStream } from "../lib/sse";
import type { AgentEvent } from "../lib/types";
import type { Approval, Message } from "../lib/types";
import { useUI } from "../stores/ui";
import { ApprovalCard } from "../components/ApprovalCard";
import { StatusBadge } from "../components/Badge";
import { Empty } from "../components/Empty";
import { Markdown } from "../components/Markdown";
import { Modal } from "../components/Modal";
import { Timeline } from "../components/Timeline";

export function Chat() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useUI((s) => s.toast);

  const [draft, setDraft] = useState("");
  const [traceView, setTraceView] = useState<"tl" | "json">("tl");
  const [traceOpen, setTraceOpen] = useState(false);
  const [artifact, setArtifact] = useState<{ id: string; content: string } | null>(null);
  const [artifactLoading, setArtifactLoading] = useState<string | null>(null);
  const messagesRef = useRef<HTMLDivElement>(null);

  const sessionQuery = useQuery({
    queryKey: ["session", id],
    queryFn: () => fetchSession(id),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status === "running") return 2500;
      if (status === "awaiting_approval") return 5000;
      return false;
    },
  });

  const session = sessionQuery.data;
  const status = session?.status;

  const messagesQuery = useQuery({
    queryKey: ["messages", id],
    queryFn: () => fetchMessages(id),
    enabled: Boolean(id),
    refetchInterval: status === "running" || status === "awaiting_approval" ? 3000 : false,
  });

  const eventsQuery = useQuery({
    queryKey: ["events", id],
    queryFn: () => fetchEvents(id, 0),
    enabled: Boolean(id),
    staleTime: Infinity,
  });

  const approvalsQuery = useQuery({
    queryKey: ["approvals", id],
    queryFn: () => fetchApprovals(id),
    enabled: Boolean(id),
    refetchInterval: status === "awaiting_approval" ? 3000 : false,
  });

  const approvals = approvalsQuery.data ?? [];
  const pending = approvals.filter((item) => item.status === "pending");
  const events = eventsQuery.data ?? [];

  useEffect(() => {
    if (session) rememberSession(session.session_id);
  }, [session]);

  // SSE：仅会话处于运行中时接入；其余状态由轮询兜底
  useEffect(() => {
    if (!id || !eventsQuery.isSuccess || status !== "running") return;
    const current =
      queryClient.getQueryData<AgentEvent[]>(["events", id]) ?? [];
    const after = current.length ? current[current.length - 1].seq : 0;
    const close = openSessionStream(id, after, {
      onEvent: (event) => {
        queryClient.setQueryData<AgentEvent[]>(["events", id], (prev) =>
          mergeEvents(prev ?? [], [event]),
        );
      },
      onDone: () => {
        void queryClient.invalidateQueries({ queryKey: ["session", id] });
        void queryClient.invalidateQueries({ queryKey: ["messages", id] });
        void queryClient.invalidateQueries({ queryKey: ["approvals", id] });
        void queryClient.invalidateQueries({ queryKey: ["sessions"] });
      },
      onError: () => {
        /* 网络抖动：轮询会继续推进状态 */
      },
    });
    return close;
  }, [id, status, eventsQuery.isSuccess, queryClient]);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [messagesQuery.data, events.length, pending.length, status]);

  const cancel = useMutation({
    mutationFn: () => cancelSession(id),
    onSuccess: () => {
      toast("已请求停止任务", "warn");
      void queryClient.invalidateQueries({ queryKey: ["session", id] });
    },
    onError: (error) => toast(errorMessage(error), "err"),
  });

  const resume = useMutation({
    mutationFn: () => resumeSession(id),
    onSuccess: () => {
      toast("正在恢复执行", "ok");
      void queryClient.invalidateQueries({ queryKey: ["session", id] });
    },
    onError: (error) => toast(errorMessage(error), "err"),
  });

  const decide = useMutation({
    mutationFn: (vars: { callId: string; approved: boolean }) =>
      decideApproval(id, vars.callId, vars.approved, true),
    onSuccess: (data, vars) => {
      toast(
        vars.approved ? "已批准，会话将自动继续" : "已拒绝该工具调用",
        vars.approved ? "ok" : "warn",
      );
      queryClient.setQueryData<Approval[]>(["approvals", id], data);
      void queryClient.invalidateQueries({ queryKey: ["session", id] });
      void queryClient.invalidateQueries({ queryKey: ["events", id] });
      void queryClient.invalidateQueries({ queryKey: ["messages", id] });
    },
    onError: (error) => toast(errorMessage(error), "err"),
  });

  const visibleMessages = useMemo(() => {
    const rows = messagesQuery.data ?? [];
    return rows.filter(
      (message: Message) =>
        (message.role === "user" || message.role === "assistant") &&
        typeof message.content === "string" &&
        message.content.trim().length > 0,
    );
  }, [messagesQuery.data]);

  const finalAnswer = session?.final_answer?.trim() ?? "";
  const lastAssistant = [...visibleMessages]
    .reverse()
    .find((message) => message.role === "assistant")?.content;
  const showFinalCard =
    finalAnswer.length > 0 && (lastAssistant ?? "").trim() !== finalAnswer;

  const tokenTotal = useMemo(
    () =>
      events.reduce((sum, event) => {
        const usage = event.payload?.usage as
          | { input_tokens?: number; output_tokens?: number }
          | undefined;
        if (!usage) return sum;
        return sum + (usage.input_tokens ?? 0) + (usage.output_tokens ?? 0);
      }, 0),
    [events],
  );

  const artifacts = useMemo(() => {
    const seen = new Set<string>();
    const list: string[] = [];
    for (const event of events) {
      const value = event.payload?.artifact_id;
      if (typeof value === "string" && !seen.has(value)) {
        seen.add(value);
        list.push(value);
      }
    }
    return list;
  }, [events]);

  const openArtifact = async (artifactId: string) => {
    setArtifactLoading(artifactId);
    try {
      const data = await fetchArtifact(id, artifactId);
      setArtifact({ id: data.id, content: data.content });
    } catch (error) {
      toast(errorMessage(error, "读取产物失败"), "err");
    } finally {
      setArtifactLoading(null);
    }
  };

  const sendOrContinue = () => {
    if (!session || session.status === "running" || session.status === "awaiting_approval") {
      return;
    }
    if (!draft.trim()) {
      toast("先输入要继续追问的内容", "warn");
      return;
    }
    navigate("/new", {
      state: { input: draft.trim(), profile: session.profile },
    });
  };

  const copyTrace = () => {
    void copyText(JSON.stringify(events, null, 2)).then(() =>
      toast("轨迹 JSON 已复制", "ok"),
    );
  };

  const exportTrace = () => {
    const blob = new Blob([JSON.stringify(events, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${id}-trace.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  if (sessionQuery.isError) {
    return (
      <section className="page">
        <Empty title="无法打开会话">{errorMessage(sessionQuery.error)}</Empty>
        <div style={{ textAlign: "center" }}>
          <button className="btn" onClick={() => navigate("/")}>
            返回会话列表
          </button>
        </div>
      </section>
    );
  }

  const running = status === "running";
  const awaiting = status === "awaiting_approval";
  const terminal = status === "done" || status === "failed";

  return (
    <section className="page page-chat">
      <div className={`chat-layout ${traceOpen ? "trace-open" : ""}`}>
        <div className="chat-main">
          <div className="chat-head">
            <button
              className="icon-btn mobile-only"
              onClick={() => navigate("/")}
              title="返回列表"
            >
              <ArrowLeft size={15} />
            </button>
            <span className="tag">{session?.profile ?? "—"}</span>
            <h2>{session?.title || session?.final_answer || "会话详情"}</h2>
            {status ? <StatusBadge status={status} /> : null}
            <div className="chat-stats">
              <span className="cost">
                成本 <b>{fmtMoney(session?.cost)}</b>
              </span>
              <span>
                步数 <b>{fmtInt(session?.step)}</b>
              </span>
              <span className="hide-sm">
                tokens <b>{tokenTotal >= 1000 ? `${(tokenTotal / 1000).toFixed(1)}k` : fmtInt(tokenTotal)}</b>
              </span>
              <span className="mono hide-sm" style={{ color: "var(--faint)" }}>
                {shortId(id, 10)}
              </span>
              <button
                className="btn btn-sm mobile-only"
                onClick={() => setTraceOpen((open) => !open)}
              >
                {traceOpen ? "返回对话" : "执行轨迹"}
              </button>
            </div>
          </div>

          <div className="messages" ref={messagesRef}>
            {messagesQuery.isLoading ? (
              <div className="msg" style={{ display: "grid", gap: 12 }}>
                <div className="skeleton" style={{ width: "60%" }} />
                <div className="skeleton" style={{ width: "85%" }} />
                <div className="skeleton" style={{ width: "70%" }} />
              </div>
            ) : null}

            {visibleMessages.map((message, index) => {
              if (message.role === "user") {
                return (
                  <div className="msg msg-user" key={`u-${index}`}>
                    <div className="bubble">{message.content}</div>
                  </div>
                );
              }
              return (
                <div className="msg msg-ai" key={`a-${index}`}>
                  <div className="msg-role">
                    <div className="avatar">AI</div>
                    asagent · {profileLabel(session?.profile ?? "")}
                  </div>
                  <div className="body">
                    <Markdown content={message.content ?? ""} />
                  </div>
                  <div className="msg-foot">
                    <button
                      onClick={() =>
                        void copyText(message.content ?? "").then(() =>
                          toast("已复制", "ok"),
                        )
                      }
                    >
                      复制
                    </button>
                  </div>
                </div>
              );
            })}

            {showFinalCard ? (
              <div className="msg msg-ai">
                <div className="msg-role">
                  <div className="avatar">AI</div>
                  最终回答 · {profileLabel(session?.profile ?? "")}
                </div>
                <div className="body">
                  <Markdown content={finalAnswer} />
                </div>
              </div>
            ) : null}

            {approvals.map((approval) => (
              <ApprovalCard
                key={approval.call_id}
                approval={approval}
                pendingCount={pending.length}
                busy={
                  decide.isPending &&
                  decide.variables?.callId === approval.call_id
                }
                onDecide={(approved) =>
                  decide.mutate({ callId: approval.call_id, approved })
                }
              />
            ))}

            {running ? (
              <div className="msg msg-ai">
                <div className="msg-role">
                  <div className="avatar">AI</div>
                  asagent 正在执行 · 事件实时同步
                </div>
                <div className="body">
                  <div className="skeleton" style={{ width: "72%", marginBottom: 9 }} />
                  <div className="skeleton" style={{ width: "48%" }} />
                </div>
              </div>
            ) : null}

            {session?.error && !awaiting ? (
              <div className="form-error" style={{ margin: "0 auto 20px", maxWidth: 840 }}>
                <Ban size={14} />
                {session.error}
              </div>
            ) : null}

            {!visibleMessages.length && !running && !awaiting && !session?.error ? (
              <Empty title="暂无消息">任务刚刚创建，等待 Agent 开始规划。</Empty>
            ) : null}
          </div>

          <div className="composer">
            <div className="composer-inner">
              <textarea
                placeholder={
                  terminal
                    ? draft
                      ? ""
                      : "输入追问内容，发送后将创建新会话继续"
                    : running
                      ? "任务运行中，可先停止任务再发起新会话"
                      : "等待审批结果后自动继续…"
                }
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                disabled={running || awaiting}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !running && !awaiting) {
                    event.preventDefault();
                    sendOrContinue();
                  }
                }}
              />
              <div className="composer-bar">
                <span className="composer-hint">
                  {awaiting
                    ? "存在待审批工具调用，处理后将自动 resume"
                    : running
                      ? "Enter 禁用 · 任务运行中"
                      : "Enter 创建后续会话 · Shift+Enter 换行"}
                </span>
                {running ? (
                  <button
                    className="btn btn-sm btn-danger-ghost"
                    disabled={cancel.isPending}
                    onClick={() => cancel.mutate()}
                  >
                    <Square size={13} />
                    {cancel.isPending ? "停止中…" : "停止任务"}
                  </button>
                ) : awaiting ? (
                  <button className="btn btn-sm" disabled>
                    等待审批
                  </button>
                ) : (
                  <>
                    {status === "failed" ? (
                      <button
                        className="btn btn-sm"
                        disabled={resume.isPending}
                        onClick={() => resume.mutate()}
                      >
                        <Play size={13} />
                        恢复执行
                      </button>
                    ) : null}
                    <button
                      className="btn btn-sm btn-primary"
                      disabled={!draft.trim()}
                      onClick={sendOrContinue}
                    >
                      <Send size={13} />
                      新建会话继续
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>

        <aside className="trace">
          <div className="trace-head">
            <h3>执行轨迹</h3>
            {running ? (
              <span
                className="badge badge-run"
                style={{ padding: "1px 7px", fontSize: 9 }}
              >
                <span className="dot" />
                LIVE
              </span>
            ) : null}
            <div className="switch-tabs">
              <button
                className={traceView === "tl" ? "on" : ""}
                onClick={() => setTraceView("tl")}
              >
                时间线
              </button>
              <button
                className={traceView === "json" ? "on" : ""}
                onClick={() => setTraceView("json")}
              >
                JSON
              </button>
            </div>
          </div>
          <div className="trace-body">
            {traceView === "tl" ? (
              <Timeline
                events={events}
                live={running}
              />
            ) : (
              <div className="trace-json">
                {events.length ? JSON.stringify(events, null, 2) : "[]"}
              </div>
            )}
            {artifacts.length ? (
              <div style={{ padding: "0 2px 8px" }}>
                <div className="label-sm" style={{ marginTop: 14 }}>
                  工具产物
                </div>
                <div className="row" style={{ flexWrap: "wrap" }}>
                  {artifacts.map((artifactId) => (
                    <button
                      key={artifactId}
                      className="btn btn-sm"
                      disabled={artifactLoading === artifactId}
                      onClick={() => void openArtifact(artifactId)}
                    >
                      <FileJson size={13} />
                      {shortId(artifactId, 10)}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
          <div className="trace-foot">
            <button className="btn btn-sm" onClick={copyTrace}>
              <Copy size={13} />
              复制 JSON
            </button>
            <button className="btn btn-sm" onClick={exportTrace}>
              <Download size={13} />
              导出
            </button>
            <button
              className="btn btn-sm mobile-only"
              onClick={() => setTraceOpen(false)}
            >
              <ListTree size={13} />
              返回对话
            </button>
          </div>
        </aside>
      </div>

      {artifact ? (
        <Modal
          title={`工具产物 · ${artifact.id}`}
          onClose={() => setArtifact(null)}
          footer={
            <>
              <button
                className="btn"
                onClick={() =>
                  void copyText(artifact.content).then(() =>
                    toast("产物内容已复制", "ok"),
                  )
                }
              >
                复制内容
              </button>
              <button className="btn btn-primary" onClick={() => setArtifact(null)}>
                关闭
              </button>
            </>
          }
        >
          <div className="codebox" style={{ maxHeight: "56vh" }}>
            {artifact.content}
          </div>
          <p className="hint" style={{ marginTop: 8 }}>
            只读快照 · 内容来自事件引用的 artifact
          </p>
        </Modal>
      ) : null}
    </section>
  );
}
