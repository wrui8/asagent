import {
  Activity,
  AlertTriangle,
  LayoutList,
  Plus,
  RefreshCw,
  Trash2,
  Wallet,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { deleteSession, fetchProfiles, fetchSessions } from "../lib/api";
import { errorMessage } from "../lib/errors";
import {
  fmtDate,
  fmtInt,
  fmtMoney,
  fmtTime,
  profileLabel,
} from "../lib/format";
import type { Session } from "../lib/types";
import { useUI } from "../stores/ui";
import { StatusBadge } from "../components/Badge";
import { Empty } from "../components/Empty";
import { KpiCard } from "../components/KpiCard";
import { Tilt } from "../components/Tilt";

const PAGE_SIZE = 10;

type StatusFilter = "all" | "running" | "awaiting_approval" | "failed" | "done";

const STATUS_TABS: Array<{ key: StatusFilter; label: string }> = [
  { key: "all", label: "全部" },
  { key: "running", label: "运行中" },
  { key: "awaiting_approval", label: "待审批" },
  { key: "failed", label: "失败" },
  { key: "done", label: "已完成" },
];

export function Sessions() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const toast = useUI((s) => s.toast);

  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [profile, setProfile] = useState("all");
  const [page, setPage] = useState(0);

  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: () => fetchSessions({ limit: 100 }),
    refetchInterval: (query) => {
      const rows = query.state.data ?? [];
      return rows.some((row) => row.status === "running" || row.status === "awaiting_approval")
        ? 5000
        : false;
    },
  });

  const profilesQuery = useQuery({
    queryKey: ["profiles"],
    queryFn: fetchProfiles,
    staleTime: 60_000,
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteSession(id),
    onSuccess: () => {
      toast("会话已从列表移除", "ok");
      void queryClient.invalidateQueries({ queryKey: ["sessions"] });
    },
    onError: (error) => toast(errorMessage(error), "err"),
  });

  const rows = sessionsQuery.data ?? [];

  const stats = useMemo(() => {
    const cost = rows.reduce((sum, row) => sum + (row.cost || 0), 0);
    return {
      total: rows.length,
      running: rows.filter((row) => row.status === "running").length,
      awaiting: rows.filter((row) => row.status === "awaiting_approval").length,
      cost,
    };
  }, [rows]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows.filter((row) => {
      if (status !== "all" && row.status !== status) return false;
      if (profile !== "all" && row.profile !== profile) return false;
      if (!q) return true;
      return (
        (row.title ?? "").toLowerCase().includes(q) ||
        row.session_id.toLowerCase().includes(q) ||
        row.profile.toLowerCase().includes(q)
      );
    });
  }, [rows, search, status, profile]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const pageRows = filtered.slice(
    currentPage * PAGE_SIZE,
    currentPage * PAGE_SIZE + PAGE_SIZE,
  );

  useEffect(() => {
    setPage(0);
  }, [search, status, profile]);

  const onDelete = (row: Session) => {
    if (window.confirm(`删除会话「${row.title ?? row.session_id}」？仅从控制台隐藏，内核数据保留。`)) {
      remove.mutate(row.session_id);
    }
  };

  return (
    <section className="page">
      <div className="page-head stagger">
        <div>
          <div className="eyebrow">mission control</div>
          <h2>
            会话<span className="grad-text">控制台</span>
          </h2>
          <p>所有 Agent 任务的运行状态、成本与最终结论。</p>
        </div>
        <div className="spacer" />
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => void sessionsQuery.refetch()}
          disabled={sessionsQuery.isFetching}
        >
          <RefreshCw size={14} className={sessionsQuery.isFetching ? "spin" : ""} />
          刷新
        </button>
        <button className="btn btn-primary" onClick={() => navigate("/new")}>
          <Plus size={15} />
          新建会话
        </button>
      </div>

      <div className="kpi-row stagger">
        <Tilt>
          <KpiCard label="会话总数" value={stats.total} icon={<LayoutList size={13} />} sub="最近 100 条记录" />
        </Tilt>
        <Tilt>
          <KpiCard label="运行中" value={stats.running} icon={<Activity size={13} />} sub="事件流实时同步" />
        </Tilt>
        <Tilt>
          <KpiCard
            label="等待审批"
            value={stats.awaiting}
            valueStyle={{ color: stats.awaiting ? "var(--warn)" : undefined }}
            icon={<AlertTriangle size={13} />}
            sub="需要人工决策"
          />
        </Tilt>
        <Tilt>
          <KpiCard
            label="累计成本"
            value={stats.cost}
            decimals={4}
            prefix="¥"
            icon={<Wallet size={13} />}
            sub="token 用量 × 模型定价"
          />
        </Tilt>
      </div>

      <div className="toolbar stagger">
        <div className="search">
          <SearchIcon />
          <input
            placeholder="按标题或 session id 搜索"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="switch-tabs">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.key}
              className={status === tab.key ? "on" : ""}
              onClick={() => setStatus(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div className="filters">
          <select
            className="input"
            value={profile}
            onChange={(event) => setProfile(event.target.value)}
          >
            <option value="all">全部场景</option>
            {(profilesQuery.data ?? []).map((item) => (
              <option key={item.name} value={item.name}>
                {profileLabel(item.name)}（{item.name}）
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="card table-wrap hud">
        {sessionsQuery.isLoading ? (
          <div style={{ padding: 18 }}>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className="skeleton" style={{ marginBottom: 14 }} />
            ))}
          </div>
        ) : sessionsQuery.isError ? (
          <Empty title="加载失败">{errorMessage(sessionsQuery.error)}</Empty>
        ) : pageRows.length === 0 ? (
          <Empty icon={<LayoutList size={20} />} title={rows.length ? "没有匹配的会话" : "还没有会话"}>
            {rows.length ? "调整筛选条件试试。" : "发起第一个 Agent 任务，执行轨迹会实时出现在这里。"}
            {rows.length ? null : (
              <div style={{ marginTop: 14 }}>
                <button className="btn btn-primary btn-sm" onClick={() => navigate("/new")}>
                  <Plus size={14} />
                  新建会话
                </button>
              </div>
            )}
          </Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>会话</th>
                <th>场景</th>
                <th>状态</th>
                <th className="hide-sm" style={{ textAlign: "right" }}>成本</th>
                <th className="hide-sm" style={{ textAlign: "right" }}>步数</th>
                <th className="hide-sm">更新时间</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {pageRows.map((row) => (
                <tr
                  key={row.session_id}
                  className="clickable"
                  onClick={() => navigate(`/sessions/${row.session_id}`)}
                >
                  <td>
                    <div className="cell-title">{row.title || "未命名会话"}</div>
                    <div className="cell-sub">{row.session_id}</div>
                  </td>
                  <td>
                    <span className="tag">{row.profile}</span>
                  </td>
                  <td>
                    <StatusBadge status={row.status} />
                  </td>
                  <td className="num hide-sm">{fmtMoney(row.cost)}</td>
                  <td className="num hide-sm">{fmtInt(row.step)}</td>
                  <td className="hide-sm">
                    <div style={{ fontWeight: 600 }}>{fmtTime(row.updated_at)}</div>
                    <div className="cell-sub">{fmtDate(row.updated_at)}</div>
                  </td>
                  <td>
                    <div className="table-actions">
                      <button
                        className={`btn btn-sm ${
                          row.status === "awaiting_approval" ? "btn-danger-ghost" : ""
                        }`}
                        onClick={(event) => {
                          event.stopPropagation();
                          navigate(`/sessions/${row.session_id}`);
                        }}
                      >
                        {row.status === "awaiting_approval" ? "审批" : "查看"}
                      </button>
                      <button
                        className="icon-btn"
                        style={{ width: 30, height: 30 }}
                        title="删除会话"
                        disabled={
                          remove.isPending &&
                          remove.variables === row.session_id
                        }
                        onClick={(event) => {
                          event.stopPropagation();
                          onDelete(row);
                        }}
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="row" style={{ marginTop: 14 }}>
        <p className="footnote" style={{ margin: 0 }}>
          点击任意行进入会话 · 列表按更新时间倒序 · 成本 = token 用量 × 模型定价
        </p>
        <div className="spacer" />
        <button
          className="btn btn-sm"
          disabled={currentPage === 0}
          onClick={() => setPage((p) => Math.max(0, p - 1))}
        >
          上一页
        </button>
        <span className="mono" style={{ fontSize: 11, color: "var(--faint)" }}>
          {currentPage + 1} / {pageCount}
        </span>
        <button
          className="btn btn-sm"
          disabled={currentPage >= pageCount - 1}
          onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
        >
          下一页
        </button>
      </div>
    </section>
  );
}

function SearchIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}
