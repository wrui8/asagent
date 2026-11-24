import {
  AlertTriangle,
  Cpu,
  FileBarChart,
  FlaskConical,
  Gauge,
  Play,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchEvalJobs,
  fetchEvalReport,
  fetchEvalReports,
  fetchEvalStatus,
  fetchEvalSuites,
  fetchSystemInfo,
  runEval,
} from "../lib/api";
import { errorMessage } from "../lib/errors";
import {
  fmtDateTime,
  fmtMoney,
  fmtRelative,
  fmtTime,
  profileLabel,
} from "../lib/format";
import type { EvalJob, EvalSummary } from "../lib/types";
import { useAuth } from "../stores/auth";
import { useUI } from "../stores/ui";
import { Empty } from "../components/Empty";
import { KpiCard } from "../components/KpiCard";
import { Modal } from "../components/Modal";
import { Tilt } from "../components/Tilt";

function pct(value: number | null | undefined): string {
  return `${((value ?? 0) * 100).toFixed(1)}%`;
}

function jobBadge(status: string): { label: string; cls: string } {
  switch (status) {
    case "running":
      return { label: "运行中", cls: "badge-run" };
    case "done":
      return { label: "完成", cls: "badge-ok" };
    case "failed":
      return { label: "失败", cls: "badge-fail" };
    default:
      return { label: status, cls: "badge-mute" };
  }
}

function passRate(summary: EvalSummary | null | undefined): string {
  if (!summary || !summary.total) return "—";
  return pct(summary.pass_rate);
}

export function Evals() {
  const me = useAuth((s) => s.user);
  const navigate = useNavigate();
  const toast = useUI((s) => s.toast);
  const queryClient = useQueryClient();

  const [profile, setProfile] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [repeat, setRepeat] = useState("1");
  const [limit, setLimit] = useState("");
  const [reportId, setReportId] = useState<string | null>(null);

  const suitesQuery = useQuery({
    queryKey: ["evals", "suites"],
    queryFn: fetchEvalSuites,
    enabled: me?.role === "admin",
    staleTime: 60_000,
  });
  const statusQuery = useQuery({
    queryKey: ["evals", "status"],
    queryFn: fetchEvalStatus,
    enabled: me?.role === "admin",
    refetchInterval: (query) =>
      query.state.data?.running ? 1500 : 8000,
  });
  const status = statusQuery.data;
  const running = status?.running ?? false;

  const jobsQuery = useQuery({
    queryKey: ["evals", "jobs"],
    queryFn: () => fetchEvalJobs(20),
    enabled: me?.role === "admin",
    refetchInterval: running ? 1500 : 8000,
  });
  const reportsQuery = useQuery({
    queryKey: ["evals", "reports"],
    queryFn: () => fetchEvalReports(30),
    enabled: me?.role === "admin",
    refetchInterval: running ? 4000 : false,
  });
  const infoQuery = useQuery({
    queryKey: ["system", "info"],
    queryFn: fetchSystemInfo,
    enabled: me?.role === "admin",
    staleTime: 60_000,
  });

  const suites = suitesQuery.data ?? [];
  const readySuites = suites.filter((suite) => suite.ready);
  const selectedProfile =
    profile || readySuites[0]?.profile || suites[0]?.profile || "";
  const modelOptions = Object.keys(infoQuery.data?.models ?? {});

  // 任务完成后报告文件才落盘：last_job 变化时刷新报告与任务列表
  const lastReportId = status?.last_job?.report_id ?? null;
  useEffect(() => {
    if (!lastReportId) return;
    void queryClient.invalidateQueries({ queryKey: ["evals", "reports"] });
    void queryClient.invalidateQueries({ queryKey: ["evals", "jobs"] });
  }, [lastReportId, queryClient]);

  const launch = useMutation({
    mutationFn: () =>
      runEval({
        profile: selectedProfile,
        models: models.length ? models : null,
        repeat: Math.max(1, Math.min(5, Number(repeat) || 1)),
        limit: limit.trim() ? Number(limit) : null,
      }),
    onSuccess: () => {
      toast("评测已启动，进度会自动刷新", "ok");
      void queryClient.invalidateQueries({ queryKey: ["evals"] });
    },
    onError: (error) => toast(errorMessage(error, "启动失败"), "err"),
  });

  if (me?.role !== "admin") {
    return (
      <section className="page">
        <Empty icon={<ShieldAlert size={20} />} title="需要管理员权限">
          评测会真实调用模型并产生费用，仅管理员可触发与查看。
          <div style={{ marginTop: 14 }}>
            <button className="btn btn-sm" onClick={() => navigate("/")}>
              返回会话
            </button>
          </div>
        </Empty>
      </section>
    );
  }

  const lastJob = status?.last_job ?? null;
  const submit = () => {
    if (!selectedProfile) {
      toast("请选择场景", "warn");
      return;
    }
    if (status && !status.can_run) {
      toast("今日评测次数已用完", "warn");
      return;
    }
    launch.mutate();
  };

  return (
    <section className="page">
      <div className="page-head stagger">
        <div>
          <div className="eyebrow">quality</div>
          <h2>
            评测<span className="grad-text">中心</span>
          </h2>
          <p>
            按场景与模型触发 Golden 评测，报告落盘后可回看每个用例的判分原因。同一时刻只允许一个评测任务。
          </p>
        </div>
        <div className="spacer" />
        <button
          className="btn"
          onClick={() => void queryClient.invalidateQueries({ queryKey: ["evals"] })}
        >
          <RefreshCw size={15} className={running ? "spin" : ""} />
          刷新
        </button>
        <button
          className="btn btn-primary"
          disabled={launch.isPending || running || (status ? !status.can_run : false)}
          onClick={submit}
        >
          <Play size={15} />
          {running ? "评测进行中…" : launch.isPending ? "启动中…" : "开始评测"}
        </button>
      </div>

      <div className="kpi-row stagger">
        <Tilt>
          <KpiCard
            label="可用场景"
            value={readySuites.length}
            icon={<FlaskConical size={13} />}
            sub={`共 ${suites.length} 个场景包`}
          />
        </Tilt>
        <Tilt>
          <KpiCard
            label="今日评测"
            value={status?.used_today ?? 0}
            icon={<Gauge size={13} />}
            sub={status?.daily_limit ? `上限 ${status.daily_limit} 次/日` : "不限次数"}
          />
        </Tilt>
        <Tilt>
          <KpiCard
            label="最近通过率"
            value={Math.round((lastJob?.summary?.pass_rate ?? 0) * 100)}
            suffix="%"
            icon={<FileBarChart size={13} />}
            sub={lastJob ? `${lastJob.profile} · ${fmtRelative(lastJob.created_at)}` : "还没有评测记录"}
          />
        </Tilt>
        <Tilt>
          <KpiCard
            label="最近成本"
            value={lastJob?.summary?.total_cost ?? 0}
            decimals={4}
            prefix="¥"
            icon={<Cpu size={13} />}
            sub={lastJob ? `${lastJob.summary?.passed ?? 0}/${lastJob.summary?.total ?? 0} 用例通过` : "—"}
          />
        </Tilt>
      </div>

      <div className="split stagger">
        <div className="stack">
          <div className="card table-wrap hud">
            <div className="toolbar" style={{ padding: "14px 15px 4px" }}>
              <div className="label-sm" style={{ margin: 0 }}>
                场景与评测集
              </div>
              <div className="spacer" />
              <span className="badge badge-mute" style={{ fontSize: 9 }}>
                golden.jsonl
              </span>
            </div>
            {suitesQuery.isLoading ? (
              <div style={{ padding: 16 }}>
                {[0, 1, 2].map((i) => (
                  <div key={i} className="skeleton" style={{ marginBottom: 12 }} />
                ))}
              </div>
            ) : suitesQuery.isError ? (
              <Empty title="加载失败">{errorMessage(suitesQuery.error)}</Empty>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>场景</th>
                    <th className="hide-sm">说明</th>
                    <th style={{ textAlign: "right" }}>用例</th>
                    <th>状态</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {suites.map((suite) => (
                    <tr key={suite.profile}>
                      <td>
                        <div className="cell-title">{profileLabel(suite.profile)}</div>
                        <div className="cell-sub mono">{suite.profile}</div>
                      </td>
                      <td className="hide-sm" style={{ fontSize: 12, color: "var(--muted)" }}>
                        {suite.description || "—"}
                      </td>
                      <td className="num">{suite.cases ?? "—"}</td>
                      <td>
                        <span className={`badge ${suite.ready ? "badge-ok" : "badge-mute"}`}>
                          <span className="dot" />
                          {suite.ready ? "就绪" : "无评测集"}
                        </span>
                      </td>
                      <td>
                        <div className="table-actions">
                          <button
                            className="btn btn-sm btn-ghost"
                            disabled={!suite.ready || running}
                            onClick={() => {
                              setProfile(suite.profile);
                              toast(`已选择 ${profileLabel(suite.profile)}，确认后开始评测`, "ok");
                            }}
                          >
                            选择
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="card table-wrap hud">
            <div className="toolbar" style={{ padding: "14px 15px 4px" }}>
              <div className="label-sm" style={{ margin: 0 }}>
                历史报告
              </div>
              <div className="spacer" />
              <span className="badge badge-mute" style={{ fontSize: 9 }}>
                runtime/evals
              </span>
            </div>
            {(reportsQuery.data ?? []).length === 0 ? (
              <Empty icon={<FileBarChart size={20} />} title="还没有评测报告">
                在右侧选择场景与模型，点击「开始评测」后会生成报告。
              </Empty>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>场景</th>
                    <th>通过率</th>
                    <th className="hide-sm" style={{ textAlign: "right" }}>成本</th>
                    <th className="hide-sm">时间</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {(reportsQuery.data ?? []).map((report) => (
                    <tr key={report.id}>
                      <td>
                        <div className="cell-title">{profileLabel(report.profile)}</div>
                        <div className="cell-sub mono">{report.id}</div>
                      </td>
                      <td>
                        <span
                          className={`badge ${
                            report.summary.pass_rate >= 0.8 ? "badge-ok" : "badge-wait"
                          }`}
                        >
                          {passRate(report.summary)}
                        </span>
                        <span className="cell-sub" style={{ marginLeft: 8 }}>
                          {report.summary.passed ?? 0}/{report.summary.total ?? 0}
                        </span>
                      </td>
                      <td className="num hide-sm">{fmtMoney(report.summary.total_cost)}</td>
                      <td className="hide-sm" style={{ fontSize: 12, color: "var(--muted)" }}>
                        {fmtDateTime(report.created_at)}
                      </td>
                      <td>
                        <div className="table-actions">
                          <button
                            className="btn btn-sm btn-ghost"
                            onClick={() => setReportId(report.id)}
                          >
                            查看
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>

        <div className="stack">
          <div className="card card-pad hud">
            <div
              className="row"
              style={{ justifyContent: "space-between", marginBottom: 12 }}
            >
              <div className="label-sm" style={{ margin: 0 }}>
                触发评测
              </div>
              <span
                className={`badge ${running ? "badge-run" : "badge-mute"}`}
                style={{ fontSize: 9 }}
              >
                <span className="dot" />
                {running ? "运行中" : "空闲"}
              </span>
            </div>

            <div className="field">
              <label className="label-sm">场景</label>
              <select
                className="input"
                value={selectedProfile}
                disabled={running}
                onChange={(event) => setProfile(event.target.value)}
              >
                {suites.map((suite) => (
                  <option key={suite.profile} value={suite.profile} disabled={!suite.ready}>
                    {profileLabel(suite.profile)} · {suite.profile}
                    {suite.ready ? `（${suite.cases ?? "?"} 例）` : "（无评测集）"}
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label className="label-sm">模型（不选则按路由自动）</label>
              <div className="stack" style={{ gap: 6 }}>
                {modelOptions.map((model) => (
                  <label className="check" key={model}>
                    <input
                      type="checkbox"
                      checked={models.includes(model)}
                      disabled={running}
                      onChange={(event) =>
                        setModels((prev) =>
                          event.target.checked
                            ? [...prev, model]
                            : prev.filter((item) => item !== model),
                        )
                      }
                    />
                    <span className="mono" style={{ fontSize: 11.5 }}>
                      {model}
                    </span>
                    {infoQuery.data?.models[model] ? (
                      <span className="cell-sub" style={{ marginLeft: "auto" }}>
                        {infoQuery.data.models[model].provider}
                      </span>
                    ) : null}
                  </label>
                ))}
                {modelOptions.length === 0 ? (
                  <span className="hint">未能读取模型列表，将使用路由自动选择。</span>
                ) : null}
              </div>
            </div>

            <div className="field-grid">
              <div className="field">
                <label className="label-sm">重复次数（1-5）</label>
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={5}
                  value={repeat}
                  disabled={running}
                  onChange={(event) => setRepeat(event.target.value)}
                />
              </div>
              <div className="field">
                <label className="label-sm">用例上限（留空为全部）</label>
                <input
                  className="input"
                  type="number"
                  min={1}
                  max={100}
                  value={limit}
                  disabled={running}
                  onChange={(event) => setLimit(event.target.value)}
                  placeholder="如 2，便于快速冒烟"
                />
              </div>
            </div>

            <button
              className="btn btn-primary btn-block"
              style={{ marginTop: 14 }}
              disabled={launch.isPending || running || (status ? !status.can_run : false)}
              onClick={submit}
            >
              <Play size={14} />
              {running ? "已有任务在运行" : "开始评测"}
            </button>
            <p className="hint" style={{ marginTop: 8 }}>
              评测会真实调用模型，成本按模型计费累计；今日已用 {status?.used_today ?? 0}
              {status?.daily_limit ? ` / ${status.daily_limit}` : " 次（不限）"}。
            </p>
            {status && !status.can_run ? (
              <div className="form-error" style={{ marginTop: 8 }}>
                <AlertTriangle size={14} />
                今日评测次数已用完，请明天再试或由管理员调整配额。
              </div>
            ) : null}
          </div>

          <div className="card card-pad hud">
            <div className="label-sm" style={{ marginBottom: 9 }}>
              任务日志
            </div>
            <div className="stack" style={{ gap: 7 }}>
              {(jobsQuery.data ?? []).slice(0, 8).map((job) => (
                <JobLine
                  key={job.id}
                  job={job}
                  onOpenReport={() => job.report_id && setReportId(job.report_id)}
                />
              ))}
              {(jobsQuery.data ?? []).length === 0 ? (
                <span style={{ fontSize: 12, color: "var(--faint)" }}>
                  暂无评测任务
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </div>

      <p className="footnote">
        报告同时保存为 JSON 与 Markdown · 同一时刻只跑一个任务（单飞）· 评测工作区在 runtime/eval-workspaces 下隔离
      </p>

      {reportId ? (
        <ReportModal reportId={reportId} onClose={() => setReportId(null)} />
      ) : null}
    </section>
  );
}

function JobLine({ job, onOpenReport }: { job: EvalJob; onOpenReport: () => void }) {
  const meta = jobBadge(job.status);
  const scope = job.models?.length ? job.models.join(", ") : "auto";
  return (
    <div className="row" style={{ justifyContent: "space-between", fontSize: 11.5 }}>
      <span className="mono" style={{ color: "var(--faint)" }}>
        {fmtTime(job.created_at)}
      </span>
      <span style={{ color: "var(--muted)" }}>{profileLabel(job.profile)}</span>
      <span className="mono" style={{ color: "var(--faint)" }}>
        {scope}
      </span>
      <span className={`badge ${meta.cls}`} style={{ fontSize: 9 }}>
        {meta.label}
      </span>
      {job.summary ? (
        <span className="mono" style={{ fontSize: 10.5 }}>
          {job.summary.passed}/{job.summary.total}
        </span>
      ) : null}
      {job.report_id ? (
        <button className="btn btn-sm btn-ghost" onClick={onOpenReport}>
          报告
        </button>
      ) : job.message ? (
        <span
          title={job.message}
          style={{
            maxWidth: 140,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            color: "var(--fail)",
          }}
        >
          {job.message}
        </span>
      ) : null}
    </div>
  );
}

function ReportModal({
  reportId,
  onClose,
}: {
  reportId: string;
  onClose: () => void;
}) {
  const query = useQuery({
    queryKey: ["eval-report", reportId],
    queryFn: () => fetchEvalReport(reportId),
  });
  const report = query.data;

  return (
    <Modal
      title={`评测报告 · ${reportId}`}
      onClose={onClose}
      width={860}
      footer={
        <button className="btn btn-primary" onClick={onClose}>
          关闭
        </button>
      }
    >
      {query.isLoading ? (
        <div className="skeleton" style={{ height: 120 }} />
      ) : query.isError ? (
        <Empty title="报告加载失败">{errorMessage(query.error)}</Empty>
      ) : report ? (
        <>
          <div className="card-pad" style={{ padding: "0 0 12px" }}>
            <div className="row" style={{ gap: 20, flexWrap: "wrap" }}>
              <span>
                场景 <b>{profileLabel(report.summary.profile)}</b>
              </span>
              <span>
                通过 <b>{report.summary.passed}</b> / {report.summary.total}
              </span>
              <span>
                通过率 <b>{pct(report.summary.pass_rate)}</b>
              </span>
              <span>
                成本 <b>{fmtMoney(report.summary.total_cost)}</b>
              </span>
            </div>
          </div>

          <div className="table-wrap" style={{ marginBottom: 14 }}>
            <table>
              <thead>
                <tr>
                  <th>模型</th>
                  <th style={{ textAlign: "right" }}>通过 / 总数</th>
                  <th style={{ textAlign: "right" }}>通过率</th>
                  <th style={{ textAlign: "right" }}>成本</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(report.summary.by_model).map(([model, bucket]) => (
                  <tr key={model}>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {model}
                    </td>
                    <td className="num">
                      {bucket.passed} / {bucket.total}
                    </td>
                    <td className="num">{pct(bucket.pass_rate)}</td>
                    <td className="num">{fmtMoney(bucket.cost)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="table-wrap" style={{ maxHeight: 360, overflow: "auto" }}>
            <table>
              <thead>
                <tr>
                  <th>用例</th>
                  <th>模型</th>
                  <th>结果</th>
                  <th style={{ textAlign: "right" }}>步数</th>
                  <th>说明</th>
                </tr>
              </thead>
              <tbody>
                {report.results.map((result, index) => (
                  <tr key={`${result.case_id}-${result.model}-${index}`}>
                    <td className="mono" style={{ fontSize: 11.5 }}>
                      {result.case_id}
                    </td>
                    <td className="mono" style={{ fontSize: 11.5 }}>
                      {result.model}
                    </td>
                    <td>
                      <span className={`badge ${result.passed ? "badge-ok" : "badge-fail"}`}>
                        {result.passed ? "通过" : "失败"}
                      </span>
                    </td>
                    <td className="num">{result.steps}</td>
                    <td style={{ fontSize: 12, color: "var(--muted)" }}>
                      {result.reason || result.error || "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </Modal>
  );
}
