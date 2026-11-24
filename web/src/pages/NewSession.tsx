import { AlertCircle, Play, ShieldCheck, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useMutation, useQuery } from "@tanstack/react-query";
import { createSession, fetchProfiles } from "../lib/api";
import { errorMessage } from "../lib/errors";
import { fmtMoney, profileLabel } from "../lib/format";
import { rememberSession } from "../lib/nav";
import { useUI } from "../stores/ui";

interface NavState {
  input?: string;
  profile?: string;
}

const PERMISSION_LABEL = ["只读", "写入", "危险写"];

export function NewSession() {
  const navigate = useNavigate();
  const location = useLocation();
  const toast = useUI((s) => s.toast);
  const prefill = (location.state as NavState | null) ?? {};

  const profilesQuery = useQuery({
    queryKey: ["profiles"],
    queryFn: fetchProfiles,
    staleTime: 60_000,
  });

  const [input, setInput] = useState(prefill.input ?? "");
  const [profile, setProfile] = useState(prefill.profile ?? "");
  const [maxSteps, setMaxSteps] = useState(12);
  const [maxCost, setMaxCost] = useState("");
  const [error, setError] = useState("");

  const profiles = profilesQuery.data ?? [];
  const activeProfile = useMemo(() => {
    if (!profiles.length) return null;
    return (
      profiles.find((item) => item.name === profile) ?? profiles[0]
    );
  }, [profiles, profile]);

  const run = useMutation({
    mutationFn: () =>
      createSession({
        profile: activeProfile?.name ?? "assistant",
        input: input.trim(),
        max_steps: maxSteps,
        max_cost_cny: maxCost.trim() ? Number(maxCost) : null,
      }),
    onSuccess: (data) => {
      rememberSession(data.session_id);
      toast("会话已排队，正在启动", "ok");
      navigate(`/sessions/${data.session_id}`);
    },
    onError: (err) => setError(errorMessage(err, "创建会话失败")),
  });

  const submit = () => {
    setError("");
    if (!input.trim()) {
      setError("请填写任务描述");
      return;
    }
    if (!activeProfile) {
      setError("没有可用的场景包");
      return;
    }
    if (maxCost.trim() && (!Number.isFinite(Number(maxCost)) || Number(maxCost) <= 0)) {
      setError("成本上限需为正数，留空则使用情景默认值");
      return;
    }
    run.mutate();
  };

  return (
    <section className="page">
      <div className="page-head">
        <div>
          <div className="eyebrow">launch</div>
          <h2>
            新建<span className="grad-text">会话</span>
          </h2>
          <p>选择场景与预算后提交，任务在后台运行，事件流实时回传。</p>
        </div>
      </div>

      <div className="split-new stagger">
        <div className="card card-pad hud">
          {error ? (
            <div className="form-error">
              <AlertCircle size={14} />
              {error}
            </div>
          ) : null}

          <div className="field">
            <label className="label-sm">任务描述</label>
            <textarea
              className="input"
              rows={5}
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder="例如：统计 2024 年各月销售额，按品类分组排名"
            />
            <div className="hint">
              自然语言即可。Code 场景请说明目标文件与期望行为；Office 写操作需要网页审批。
            </div>
          </div>

          <div className="field-grid">
            <div className="field">
              <label className="label-sm">场景包</label>
              <select
                className="input"
                value={activeProfile?.name ?? ""}
                onChange={(event) => setProfile(event.target.value)}
                disabled={profilesQuery.isLoading}
              >
                {profiles.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name} · {profileLabel(item.name)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label className="label-sm">最大步数</label>
              <input
                className="input"
                type="number"
                min={1}
                max={40}
                value={maxSteps}
                onChange={(event) =>
                  setMaxSteps(
                    Math.max(1, Math.min(40, Number(event.target.value) || 1)),
                  )
                }
              />
            </div>
            <div className="field">
              <label className="label-sm">成本上限（元，可留空）</label>
              <input
                className="input"
                type="number"
                min={0}
                step="0.01"
                value={maxCost}
                placeholder={
                  activeProfile?.max_cost_cny
                    ? `场景默认 ${fmtMoney(activeProfile.max_cost_cny)}`
                    : "使用账户配额"
                }
                onChange={(event) => setMaxCost(event.target.value)}
              />
            </div>
            <div className="field">
              <label className="label-sm">执行方式</label>
              <input
                className="input"
                value="服务端后台任务 · SSE 事件流"
                readOnly
                style={{ color: "var(--muted)" }}
              />
            </div>
          </div>

          <div className="row">
            <button
              className="btn btn-primary"
              onClick={submit}
              disabled={run.isPending || profilesQuery.isLoading || !activeProfile}
            >
              <Play size={15} />
              {run.isPending ? "正在排队…" : "开始运行"}
            </button>
            <button className="btn btn-ghost" onClick={() => navigate("/")}>
              取消
            </button>
          </div>
        </div>

        <div className="stack">
          <div className="card card-pad hud">
            <div className="label-sm" style={{ marginBottom: 11 }}>
              场景参数
            </div>
            {activeProfile ? (
              <div className="stack" style={{ gap: 10 }}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span style={{ color: "var(--muted)" }}>描述</span>
                  <span style={{ textAlign: "right", maxWidth: 190 }}>
                    {activeProfile.description || "无描述"}
                  </span>
                </div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span style={{ color: "var(--muted)" }}>最高权限</span>
                  <span className="badge badge-mute">
                    {PERMISSION_LABEL[activeProfile.max_permission] ??
                      `L${activeProfile.max_permission}`}
                  </span>
                </div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span style={{ color: "var(--muted)" }}>场景预算</span>
                  <span className="mono">
                    {activeProfile.max_cost_cny
                      ? fmtMoney(activeProfile.max_cost_cny)
                      : "账户配额"}
                  </span>
                </div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span style={{ color: "var(--muted)" }}>最大步数</span>
                  <span className="mono">{maxSteps}</span>
                </div>
              </div>
            ) : (
              <div className="skeleton" />
            )}
          </div>

          <div className="card card-pad hud">
            <div
              style={{
                fontWeight: 800,
                fontSize: 12.5,
                color: "var(--blue)",
                marginBottom: 6,
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <ShieldCheck size={14} />
              安全提示
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.7 }}>
              办公写操作会先备份原文件并等待网页审批。Code 场景在隔离沙箱内执行，只允许访问本次会话的私有工作目录。
            </div>
          </div>

          <div className="card card-pad hud">
            <div
              style={{
                fontWeight: 800,
                fontSize: 12.5,
                marginBottom: 6,
                display: "flex",
                alignItems: "center",
                gap: 6,
              }}
            >
              <Sparkles size={14} style={{ color: "var(--violet)" }} />
              完成后可以做什么
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", lineHeight: 1.7 }}>
              在对话页查看事件轨迹、工具调用与成本；产物（artifact）可以单独打开。会话结束后可一键基于原任务继续追问。
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
