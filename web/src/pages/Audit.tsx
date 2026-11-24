import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  RefreshCw,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchAudit, fetchSessions } from "../lib/api";
import { errorMessage } from "../lib/errors";
import { fmtDateTime, profileLabel, shortId } from "../lib/format";
import type { AuditItem } from "../lib/types";
import { useAuth } from "../stores/auth";
import { Empty } from "../components/Empty";
import { Modal } from "../components/Modal";
import { PermissionBadge } from "../components/Badge";

function asText(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function Audit() {
  const me = useAuth((s) => s.user);
  const [searchParams] = useSearchParams();
  const isAdmin = me?.role === "admin";

  const [sessionId, setSessionId] = useState(
    searchParams.get("session_id") ?? "",
  );
  const [limit, setLimit] = useState("50");
  const [detail, setDetail] = useState<AuditItem | null>(null);

  const sessionsQuery = useQuery({
    queryKey: ["audit", "sessions", isAdmin],
    queryFn: () => fetchSessions(isAdmin ? { all: true, limit: 100 } : { limit: 100 }),
  });
  const sessions = sessionsQuery.data ?? [];
  // 普通用户必须落到某个会话上；管理员默认看全局
  const activeSession = sessionId || (!isAdmin ? (sessions[0]?.session_id ?? "") : "");

  const auditQuery = useQuery({
    queryKey: ["audit", "list", activeSession, limit],
    queryFn: () => fetchAudit(activeSession || undefined, Number(limit)),
    enabled: Boolean(isAdmin || activeSession),
    refetchInterval: 8000,
  });
  const items = auditQuery.data ?? [];

  const selected = sessions.find((session) => session.session_id === activeSession);

  if (!isAdmin && !sessionsQuery.isLoading && sessions.length === 0) {
    return (
      <section className="page">
        <div className="page-head">
          <div>
            <div className="eyebrow">security</div>
            <h2>
              写操作<span className="grad-text">审计</span>
            </h2>
            <p>工具写操作的路径、备份、哈希与结果，按会话记录。</p>
          </div>
        </div>
        <Empty icon={<FileText size={20} />} title="还没有可审计的会话">
          先创建一个会话，写操作会记录在这里。管理员可查看全局审计。
        </Empty>
      </section>
    );
  }

  return (
    <section className="page">
      <div className="page-head stagger">
        <div>
          <div className="eyebrow">security</div>
          <h2>
            写操作<span className="grad-text">审计</span>
          </h2>
          <p>
            每次文件写入、备份与沙箱操作都会留下事件：路径、备份位置、sha256 与结果。
            {isAdmin ? "管理员可查看全局审计。" : "普通用户只能查看自己的会话。"}
          </p>
        </div>
        <div className="spacer" />
        <button
          className="btn"
          onClick={() => void auditQuery.refetch()}
          disabled={auditQuery.isFetching}
        >
          <RefreshCw size={15} className={auditQuery.isFetching ? "spin" : ""} />
          刷新
        </button>
      </div>

      <div className="card table-wrap hud" style={{ marginBottom: 16 }}>
        <div className="toolbar" style={{ padding: "14px 15px" }}>
          <div className="label-sm" style={{ margin: 0 }}>
            审计范围
          </div>
          <div className="spacer" />
          <select
            className="input"
            style={{ maxWidth: 340 }}
            value={activeSession}
            onChange={(event) => setSessionId(event.target.value)}
          >
            {isAdmin ? <option value="">全部会话（全局）</option> : null}
            {sessions.map((session) => (
              <option key={session.session_id} value={session.session_id}>
                {session.title || session.session_id} · {profileLabel(session.profile)}
              </option>
            ))}
          </select>
          <select
            className="input"
            style={{ width: 110 }}
            value={limit}
            onChange={(event) => setLimit(event.target.value)}
          >
            <option value="50">最近 50</option>
            <option value="100">最近 100</option>
            <option value="200">最近 200</option>
          </select>
        </div>

        {auditQuery.isLoading ? (
          <div style={{ padding: 16 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ marginBottom: 12 }} />
            ))}
          </div>
        ) : auditQuery.isError ? (
          <Empty title="加载失败">{errorMessage(auditQuery.error)}</Empty>
        ) : items.length === 0 ? (
          <Empty icon={<ShieldCheck size={20} />} title="没有写操作记录">
            {selected
              ? `会话「${selected.title || shortId(selected.session_id)}」还没有产生写操作。`
              : "当前范围内还没有写操作审计。"}
          </Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>时间</th>
                <th>会话</th>
                <th>工具</th>
                <th>权限</th>
                <th className="hide-sm">路径</th>
                <th>结果</th>
                <th className="hide-sm">备份 / 哈希</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const ok = item.ok === true;
                const own = item.owner_id && item.owner_id === me?.id;
                const backupPath = asText(item.backup);
                // 备份文件名形如 20261123T1726141364790551_notes.md，去掉时间戳前缀更易读
                const backupName =
                  backupPath === "—"
                    ? "—"
                    : (backupPath.split(/[\\/]/).filter(Boolean).pop() ?? backupPath).replace(
                        /^\d{8}T\d+_/,
                        "",
                      );
                return (
                  <tr key={`${item.session_id}-${item.seq}`}>
                    <td className="mono" style={{ fontSize: 11.5, whiteSpace: "nowrap" }}>
                      {fmtDateTime(item.ts)}
                    </td>
                    <td>
                      <div className="mono" style={{ fontSize: 11 }}>
                        {shortId(item.session_id, 10)}
                      </div>
                      <div className="cell-sub">
                        {isAdmin
                          ? own
                            ? "我"
                            : shortId(item.owner_id ?? "—", 8)
                          : "本会话"}
                      </div>
                    </td>
                    <td>
                      <span className="tag">{asText(item.tool)}</span>
                    </td>
                    <td>
                      <PermissionBadge permission={Number(item.permission ?? 0)} />
                    </td>
                    <td
                      className="hide-sm mono"
                      style={{ fontSize: 11.5, maxWidth: 260, wordBreak: "break-all" }}
                    >
                      {asText(item.path)}
                    </td>
                    <td>
                      <span className={`badge ${ok ? "badge-ok" : "badge-fail"}`}>
                        <span className="dot" />
                        {ok ? "成功" : asText(item.error_code)}
                      </span>
                    </td>
                    <td className="hide-sm" style={{ fontSize: 11 }}>
                      <div
                        className="cell-sub mono ellipsis"
                        style={{ maxWidth: 220 }}
                        title={item.backup ? asText(item.backup) : "无备份"}
                      >
                        {item.backup ? `备份 ${backupName}` : "无备份"}
                      </div>
                      <div className="cell-sub mono">
                        {item.sha256 ? `sha ${shortId(asText(item.sha256), 12)}` : "无哈希"}
                      </div>
                    </td>
                    <td>
                      <div className="table-actions">
                        <button
                          className="btn btn-sm btn-ghost"
                          onClick={() => setDetail(item)}
                        >
                          详情
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      <div className="card card-pad hud" style={{ maxWidth: 760 }}>
        <div className="row" style={{ gap: 10, marginBottom: 8 }}>
          {isAdmin ? (
            <ShieldCheck size={16} style={{ color: "var(--ok)" }} />
          ) : (
            <AlertTriangle size={16} style={{ color: "var(--warn)" }} />
          )}
          <span style={{ fontWeight: 700, fontSize: 13 }}>
            {isAdmin ? "管理员视图：全局可见所有用户的写操作" : "普通用户视图：仅自己的会话"}
          </span>
        </div>
        <p className="hint" style={{ margin: 0 }}>
          审计来自内核事件流（EventType.AUDIT），与 trace 同源；删除会话不会删除审计记录。
        </p>
      </div>

      <p className="footnote">
        备份文件在 runtime/backups 下 · 权限 READ / WRITE / DANGEROUS · 自动刷新间隔 8 秒
      </p>

      {detail ? (
        <Modal
          title={`审计详情 · seq ${detail.seq}`}
          onClose={() => setDetail(null)}
          width={680}
          footer={
            <button className="btn btn-primary" onClick={() => setDetail(null)}>
              关闭
            </button>
          }
        >
          <div className="row" style={{ gap: 16, marginBottom: 12, flexWrap: "wrap" }}>
            <span className="badge badge-ok">
              <CheckCircle2 size={12} />
              {detail.type}
            </span>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              {fmtDateTime(detail.ts)}
            </span>
            <span className="mono" style={{ fontSize: 11 }}>
              {detail.session_id}
            </span>
          </div>
          <div className="codebox" style={{ maxHeight: 360 }}>
            {JSON.stringify(detail, null, 2)}
          </div>
        </Modal>
      ) : null}
    </section>
  );
}
