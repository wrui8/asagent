import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";
import { useState } from "react";
import type { Approval } from "../lib/types";
import { PermissionBadge } from "./Badge";

interface Props {
  approval: Approval;
  pendingCount: number;
  busy: boolean;
  checking?: boolean;
  expandParams?: boolean;
  onDecide: (approved: boolean) => void;
}

/** 危险写操作的审批卡片：批准后由后端自动 resume。 */
export function ApprovalCard({ approval, pendingCount, busy, checking, expandParams, onDecide }: Props) {
  const [expanded, setExpanded] = useState(false);
  const status = approval.status;
  const decided = status !== "pending";
  const args = JSON.stringify(approval.args ?? {}, null, 2);
  const preview = expandParams ? args.split("\n", 10).join("\n").slice(0, 600) : args;
  const long = preview !== args;

  return (
    <div
      className={`approval ${decided ? "done" : ""} ${
        status === "approved" ? "approved" : status === "denied" ? "denied" : ""
      }`}
    >
      <div className="approval-head">
        <div className="icon">
          <AlertTriangle size={15} />
        </div>
        <div>
          <h3>AUTHORIZATION REQUIRED</h3>
          <p>
            {checking
              ? "正在核对服务器上的审批结果…"
              : decided
              ? "该调用已有决策"
              : "危险写操作已暂停 · 批准后自动 resume 会话"}
          </p>
        </div>
      </div>
      <div className="approval-body">
        <div className="approval-meta">
          <span className="tag hot">{approval.tool}</span>
          <PermissionBadge permission={approval.permission} />
          <span className="tag">{approval.call_id}</span>
          {pendingCount > 1 ? <span className="tag">共 {pendingCount} 项待审批</span> : null}
        </div>
        <div className={`codebox${expanded ? " approval-args-expanded" : ""}`}>
          {long && !expanded ? `${preview}…` : args}
        </div>
        {long ? (
          <button
            className="btn btn-sm approval-args-toggle"
            type="button"
            aria-expanded={expanded}
            onClick={() => setExpanded((open) => !open)}
          >
            {expanded ? "收起参数" : "查看完整参数"}
          </button>
        ) : null}
      </div>
      <div className="approval-foot">
        <button
          className="btn btn-success btn-sm"
          disabled={busy || decided}
          onClick={() => onDecide(true)}
        >
          <CheckCircle2 size={14} />
          批准并继续
        </button>
        <button
          className="btn btn-danger-ghost btn-sm"
          disabled={busy || decided}
          onClick={() => onDecide(false)}
        >
          <XCircle size={14} />
          拒绝
        </button>
        <p className="hint">
          执行前自动备份原文件
          <br />
          全部决策后自动 resume
        </p>
      </div>
      {decided ? (
        <div className="approval-decision">
          {status === "approved" ? <CheckCircle2 size={15} /> : <XCircle size={15} />}
          {status === "approved" ? "已批准，等待继续执行" : "已拒绝，该调用不会执行"}
        </div>
      ) : null}
    </div>
  );
}
