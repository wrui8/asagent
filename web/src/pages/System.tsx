import {
  AlertTriangle,
  Boxes,
  Cpu,
  FolderTree,
  PlugZap,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  Waypoints,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchMCPServers, fetchProfiles, fetchSystemInfo } from "../lib/api";
import { errorMessage } from "../lib/errors";
import { profileLabel } from "../lib/format";
import { useAuth } from "../stores/auth";
import { Empty } from "../components/Empty";
import { KpiCard } from "../components/KpiCard";
import { PermissionBadge } from "../components/Badge";
import { Tilt } from "../components/Tilt";

export function System() {
  const me = useAuth((s) => s.user);
  const navigate = useNavigate();
  const [profile, setProfile] = useState("");

  const infoQuery = useQuery({
    queryKey: ["system", "info"],
    queryFn: fetchSystemInfo,
    enabled: me?.role === "admin",
    refetchInterval: 15000,
  });
  const profilesQuery = useQuery({
    queryKey: ["profiles"],
    queryFn: fetchProfiles,
    enabled: me?.role === "admin",
    staleTime: 60_000,
  });
  const profiles = profilesQuery.data ?? [];
  const activeProfile = profile || profiles[0]?.name || "";
  const mcpQuery = useQuery({
    queryKey: ["system", "mcp", activeProfile],
    queryFn: () => fetchMCPServers(activeProfile),
    enabled: me?.role === "admin" && Boolean(activeProfile),
  });

  if (me?.role !== "admin") {
    return (
      <section className="page">
        <Empty icon={<ShieldAlert size={20} />} title="需要管理员权限">
          MCP 与系统信息涉及路径、模型与密钥状态，仅管理员可见。
          <div style={{ marginTop: 14 }}>
            <button className="btn btn-sm" onClick={() => navigate("/")}>
              返回会话
            </button>
          </div>
        </Empty>
      </section>
    );
  }

  const info = infoQuery.data;
  const providerNames = Object.keys(info?.providers ?? {});
  const available = providerNames.filter((name) => info?.providers[name]);
  const models = Object.entries(info?.models ?? {});
  const mcpServers = mcpQuery.data ?? [];

  return (
    <section className="page">
      <div className="page-head stagger">
        <div>
          <div className="eyebrow">platform</div>
          <h2>
            MCP 与<span className="grad-text">系统</span>
          </h2>
          <p>
            运行路径、模型与 provider 状态、各场景声明的 MCP 工具。MCP 列表只读取配置，不会启动子进程。
          </p>
        </div>
        <div className="spacer" />
        <button
          className="btn"
          onClick={() => {
            void infoQuery.refetch();
            void mcpQuery.refetch();
          }}
          disabled={infoQuery.isFetching}
        >
          <RefreshCw size={15} className={infoQuery.isFetching ? "spin" : ""} />
          刷新
        </button>
      </div>

      <div className="kpi-row stagger">
        <Tilt>
          <KpiCard
            label="运行中任务"
            value={info?.running_jobs ?? 0}
            icon={<Boxes size={13} />}
            sub={`全局并发上限 ${info?.global_max_parallel ?? "—"}`}
          />
        </Tilt>
        <Tilt>
          <KpiCard
            label="模型"
            value={models.length}
            icon={<Cpu size={13} />}
            sub={`provider ${providerNames.length} 个`}
          />
        </Tilt>
        <Tilt>
          <KpiCard
            label="可用 provider"
            value={available.length}
            icon={<PlugZap size={13} />}
            sub={
              providerNames.length
                ? providerNames
                    .map((name) => `${name}${info?.providers[name] ? "✓" : "×"}`)
                    .join(" · ")
                : "—"
            }
          />
        </Tilt>
        <Tilt>
          <KpiCard
            label="MCP 工具服务"
            value={mcpServers.length}
            icon={<Waypoints size={13} />}
            sub={activeProfile ? `场景 ${activeProfile}` : "选择场景查看"}
          />
        </Tilt>
      </div>

      {infoQuery.isLoading ? (
        <div className="stack" style={{ gap: 12 }}>
          {[0, 1, 2].map((i) => (
            <div key={i} className="skeleton" style={{ height: 90 }} />
          ))}
        </div>
      ) : infoQuery.isError ? (
        <Empty title="系统信息加载失败">{errorMessage(infoQuery.error)}</Empty>
      ) : info ? (
        <div className="split stagger">
          <div className="stack">
            <div className="card card-pad hud">
              <div
                className="row"
                style={{ justifyContent: "space-between", marginBottom: 12 }}
              >
                <div className="label-sm" style={{ margin: 0 }}>
                  运行路径
                </div>
                <FolderTree size={14} style={{ color: "var(--faint)" }} />
              </div>
              <div className="stack" style={{ gap: 9, fontSize: 12 }}>
                {[
                  ["项目根目录", info.root],
                  ["数据库", info.db_path],
                  ["工作区", info.workspace_dir],
                  ["Trace", info.trace_dir],
                ].map(([label, value]) => (
                  <div
                    key={label}
                    className="row"
                    style={{ justifyContent: "space-between", gap: 14 }}
                  >
                    <span style={{ color: "var(--muted)", flex: "none" }}>{label}</span>
                    <span
                      className="mono"
                      style={{
                        fontSize: 11,
                        wordBreak: "break-all",
                        textAlign: "right",
                      }}
                    >
                      {value}
                    </span>
                  </div>
                ))}
              </div>
              <div className="divider" style={{ margin: "12px 0" }} />
              <div className="row" style={{ gap: 8, flexWrap: "wrap" }}>
                <span
                  className={`badge ${info.cookie_secure ? "badge-ok" : "badge-wait"}`}
                >
                  <span className="dot" />
                  Cookie {info.cookie_secure ? "Secure" : "非 Secure"}
                </span>
                <span
                  className={`badge ${info.code_admin_only ? "badge-wait" : "badge-mute"}`}
                >
                  <span className="dot" />
                  Code 场景{info.code_admin_only ? "仅管理员" : "全员可用"}
                </span>
                <span
                  className={`badge ${
                    info.jwt_secret_is_default ? "badge-fail" : "badge-ok"
                  }`}
                >
                  <span className="dot" />
                  JWT 密钥
                  {info.jwt_secret_is_default ? "为默认值" : "已自定义"}
                </span>
              </div>
              {info.jwt_secret_is_default ? (
                <div className="form-error" style={{ marginTop: 10 }}>
                  <AlertTriangle size={14} />
                  生产环境请设置 AS_JWT_SECRET，默认密钥可被伪造登录。
                </div>
              ) : null}
            </div>

            <div className="card table-wrap hud">
              <div className="toolbar" style={{ padding: "14px 15px 4px" }}>
                <div className="label-sm" style={{ margin: 0 }}>
                  Provider
                </div>
                <div className="spacer" />
                <span className="badge badge-mute" style={{ fontSize: 9 }}>
                  {available.length}/{providerNames.length} 可用
                </span>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>名称</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {providerNames.map((name) => (
                    <tr key={name}>
                      <td className="mono" style={{ fontSize: 12 }}>
                        {name}
                      </td>
                      <td>
                        <span
                          className={`badge ${
                            info.providers[name] ? "badge-ok" : "badge-mute"
                          }`}
                        >
                          <span className="dot" />
                          {info.providers[name] ? "可用" : "未配置密钥"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="card table-wrap hud">
              <div className="toolbar" style={{ padding: "14px 15px 4px" }}>
                <div className="label-sm" style={{ margin: 0 }}>
                  模型
                </div>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>模型 ID</th>
                    <th>Provider</th>
                    <th>Tier</th>
                    <th>工具调用</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map(([modelId, spec]) => (
                    <tr key={modelId}>
                      <td className="mono" style={{ fontSize: 11.5 }}>
                        {modelId}
                      </td>
                      <td>
                        <span className="tag">{spec.provider}</span>
                      </td>
                      <td className="num">{spec.tier}</td>
                      <td>
                        <span
                          className={`badge ${
                            spec.supports_tools ? "badge-ok" : "badge-mute"
                          }`}
                        >
                          {spec.supports_tools ? "支持" : "不支持"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="stack">
            <div className="card table-wrap hud">
              <div className="toolbar" style={{ padding: "14px 15px 4px" }}>
                <div className="label-sm" style={{ margin: 0 }}>
                  MCP 工具服务
                </div>
                <div className="spacer" />
                <select
                  className="input"
                  style={{ width: 170 }}
                  value={activeProfile}
                  onChange={(event) => setProfile(event.target.value)}
                >
                  {profiles.map((item) => (
                    <option key={item.name} value={item.name}>
                      {profileLabel(item.name)} · {item.name}
                    </option>
                  ))}
                </select>
              </div>
              {mcpQuery.isLoading ? (
                <div style={{ padding: 16 }}>
                  <div className="skeleton" />
                </div>
              ) : mcpQuery.isError ? (
                <Empty title="加载失败">{errorMessage(mcpQuery.error)}</Empty>
              ) : mcpServers.length === 0 ? (
                <Empty icon={<PlugZap size={20} />} title="该场景没有 MCP 服务">
                  {activeProfile} 场景未声明 mcp_servers，工具全部来自内置实现。
                </Empty>
              ) : (
                <table>
                  <thead>
                    <tr>
                      <th>名称</th>
                      <th className="hide-sm">命令</th>
                      <th>权限</th>
                      <th style={{ textAlign: "right" }}>超时</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mcpServers.map((server) => (
                      <tr key={server.name}>
                        <td>
                          <div className="cell-title mono">{server.name}</div>
                          <div className="cell-sub">
                            mcp__{server.name}__*
                          </div>
                        </td>
                        <td
                          className="hide-sm mono"
                          style={{ fontSize: 11, wordBreak: "break-all" }}
                        >
                          {server.command} {(server.args ?? []).join(" ")}
                        </td>
                        <td>
                          <PermissionBadge permission={server.permission} />
                        </td>
                        <td className="num">{server.timeout_s}s</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div className="card-pad" style={{ paddingTop: 10 }}>
                <p className="hint" style={{ margin: 0 }}>
                  外部工具以 <span className="mono">mcp__server__tool</span> 注册；
                  写操作仍走三级权限与人工审批。
                </p>
              </div>
            </div>

            <div className="card card-pad hud">
              <div className="row" style={{ gap: 10, marginBottom: 8 }}>
                <ShieldCheck size={16} style={{ color: "var(--ok)" }} />
                <span style={{ fontWeight: 700, fontSize: 13 }}>安全基线</span>
              </div>
              <div className="stack" style={{ gap: 8, fontSize: 12, color: "var(--muted)" }}>
                <span>· 登录使用 JWT + httpOnly 刷新令牌；禁用账号立即吊销。</span>
                <span>· 写操作要求人工审批，审计记录含备份与 sha256。</span>
                <span>· 密钥只回显是否配置，不会返回明文。</span>
                <span>· 评测与 Code 场景可按角色/次数收紧。</span>
              </div>
            </div>
          </div>
        </div>
      ) : null}

      <p className="footnote">
        系统信息每 15 秒自动刷新 · MCP 配置来自场景包 tool_config.mcp_servers · 子进程在会话运行时才启动
      </p>
    </section>
  );
}
