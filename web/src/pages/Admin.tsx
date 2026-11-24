import {
  AlertTriangle,
  Copy,
  KeyRound,
  Plus,
  ShieldAlert,
  Users as UsersIcon,
} from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  createInvite,
  createUser,
  fetchInvites,
  fetchQuota,
  fetchUsers,
  patchUser,
  revokeInvite,
} from "../lib/api";
import { errorMessage } from "../lib/errors";
import {
  copyText,
  fmtDate,
  fmtDateTime,
  fmtMoney,
  fmtRelative,
  shortId,
} from "../lib/format";
import type { InviteCreated, Quota, Role, User } from "../lib/types";
import { useAuth } from "../stores/auth";
import { useUI } from "../stores/ui";
import { Empty } from "../components/Empty";
import { Modal } from "../components/Modal";
import { Tilt } from "../components/Tilt";
import { KpiCard } from "../components/KpiCard";

type UserModal = { mode: "create" } | { mode: "edit"; user: User } | null;

export function Admin() {
  const me = useAuth((s) => s.user);
  const navigate = useNavigate();
  const toast = useUI((s) => s.toast);
  const queryClient = useQueryClient();

  const [userModal, setUserModal] = useState<UserModal>(null);
  const [inviteModal, setInviteModal] = useState(false);
  const [createdInvite, setCreatedInvite] = useState<InviteCreated | null>(null);

  const usersQuery = useQuery({
    queryKey: ["users"],
    queryFn: fetchUsers,
    enabled: me?.role === "admin",
  });
  const users = usersQuery.data ?? [];

  const quotaQueries = useQueries({
    queries: users.map((user) => ({
      queryKey: ["quota", user.id],
      queryFn: () => fetchQuota(user.id),
      staleTime: 30_000,
      enabled: me?.role === "admin",
    })),
  });

  const quotaMap = new Map<string, Quota>();
  users.forEach((user, index) => {
    const quota = quotaQueries[index]?.data;
    if (quota) quotaMap.set(user.id, quota);
  });

  const invitesQuery = useQuery({
    queryKey: ["invites"],
    queryFn: fetchInvites,
    enabled: me?.role === "admin",
  });
  const invites = invitesQuery.data ?? [];

  const revoke = useMutation({
    mutationFn: (id: string) => revokeInvite(id),
    onSuccess: () => {
      toast("邀请码已吊销", "warn");
      void queryClient.invalidateQueries({ queryKey: ["invites"] });
    },
    onError: (error) => toast(errorMessage(error), "err"),
  });

  if (me?.role !== "admin") {
    return (
      <section className="page">
        <Empty icon={<ShieldAlert size={20} />} title="需要管理员权限">
          用户与邀请码只对管理员开放。
          <div style={{ marginTop: 14 }}>
            <button className="btn btn-sm" onClick={() => navigate("/")}>
              返回会话
            </button>
          </div>
        </Empty>
      </section>
    );
  }

  const activeCount = users.filter((user) => !user.disabled).length;
  const adminCount = users.filter((user) => user.role === "admin").length;
  const validInvites = invites.filter(
    (invite) =>
      !invite.revoked &&
      invite.used_count < invite.max_uses &&
      (!invite.expires_at || new Date(invite.expires_at) > new Date()),
  ).length;

  return (
    <section className="page">
      <div className="page-head stagger">
        <div>
          <div className="eyebrow">access</div>
          <h2>
            用户与<span className="grad-text">邀请码</span>
          </h2>
          <p>账号只能通过邀请码注册；配额决定单会话与每日成本上限。</p>
        </div>
        <div className="spacer" />
        <button className="btn" onClick={() => setInviteModal(true)}>
          <KeyRound size={15} />
          生成邀请码
        </button>
        <button className="btn btn-primary" onClick={() => setUserModal({ mode: "create" })}>
          <Plus size={15} />
          新建用户
        </button>
      </div>

      <div className="kpi-row stagger">
        <Tilt>
          <KpiCard label="账号" value={users.length} icon={<UsersIcon size={13} />} sub={`启用 ${activeCount}`} />
        </Tilt>
        <Tilt>
          <KpiCard label="管理员" value={adminCount} icon={<ShieldAlert size={13} />} sub="至少保留一个" />
        </Tilt>
        <Tilt>
          <KpiCard label="有效邀请码" value={validInvites} icon={<KeyRound size={13} />} sub="未用完且未过期" />
        </Tilt>
        <Tilt>
          <KpiCard
            label="已禁用"
            value={users.length - activeCount}
            icon={<AlertTriangle size={13} />}
            sub="禁用后刷新令牌立即吊销"
          />
        </Tilt>
      </div>

      <div className="card table-wrap hud" style={{ marginBottom: 20 }}>
        {usersQuery.isLoading ? (
          <div style={{ padding: 16 }}>
            {[0, 1, 2].map((i) => (
              <div key={i} className="skeleton" style={{ marginBottom: 12 }} />
            ))}
          </div>
        ) : usersQuery.isError ? (
          <Empty title="加载失败">{errorMessage(usersQuery.error)}</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>用户</th>
                <th>角色</th>
                <th>日预算</th>
                <th>并发</th>
                <th>评测/日</th>
                <th>状态</th>
                <th>最近登录</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((user) => {
                const quota = quotaMap.get(user.id);
                return (
                  <tr key={user.id}>
                    <td>
                      <div className="row">
                        <div
                          className="avatar"
                          style={{ width: 27, height: 27, fontSize: 10 }}
                        >
                          {user.username.slice(0, 1).toUpperCase()}
                        </div>
                        <div>
                          <div className="cell-title">{user.username}</div>
                          <div className="cell-sub">{shortId(user.id, 12)}</div>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="badge badge-mute">{user.role}</span>
                    </td>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {quota?.daily_cost_limit != null
                        ? fmtMoney(quota.daily_cost_limit)
                        : "不限"}
                    </td>
                    <td className="num">{quota?.concurrent_sessions ?? "—"}</td>
                    <td className="num">
                      {quota?.eval_daily_limit ? quota.eval_daily_limit : "不限"}
                      {quota?.eval_used_today ? (
                        <div className="cell-sub">今日 {quota.eval_used_today}</div>
                      ) : null}
                    </td>
                    <td>
                      <span className={`badge ${user.disabled ? "badge-fail" : "badge-ok"}`}>
                        <span className="dot" />
                        {user.disabled ? "已禁用" : "正常"}
                      </span>
                    </td>
                    <td style={{ fontSize: 12, color: "var(--muted)" }}>
                      {user.last_login_at ? fmtRelative(user.last_login_at) : "从未登录"}
                    </td>
                    <td>
                      <div className="table-actions">
                        <button
                          className="btn btn-sm btn-ghost"
                          onClick={() => setUserModal({ mode: "edit", user })}
                        >
                          编辑
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

      <div className="card table-wrap hud">
        {invitesQuery.isLoading ? (
          <div style={{ padding: 16 }}>
            <div className="skeleton" style={{ width: "50%" }} />
          </div>
        ) : invites.length === 0 ? (
          <Empty icon={<KeyRound size={20} />} title="还没有邀请码">
            生成邀请码后发给同事，对方注册时填写即可开通账号。
          </Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <th>邀请码</th>
                <th>角色</th>
                <th>用量</th>
                <th>过期</th>
                <th>创建时间</th>
                <th>状态</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {invites.map((invite) => {
                const expired =
                  invite.expires_at && new Date(invite.expires_at) < new Date();
                const usedUp = invite.used_count >= invite.max_uses;
                const state = invite.revoked
                  ? { label: "已吊销", cls: "badge-fail" }
                  : expired
                    ? { label: "已过期", cls: "badge-mute" }
                    : usedUp
                      ? { label: "已用完", cls: "badge-mute" }
                      : { label: "有效", cls: "badge-ok" };
                return (
                  <tr key={invite.id}>
                    <td
                      className="mono"
                      style={{ color: "var(--faint)", fontSize: 12 }}
                      title="邀请码明文只在生成时显示一次，数据库只保存哈希"
                    >
                      已隐藏
                    </td>
                    <td>
                      <span className="badge badge-mute">{invite.role}</span>
                    </td>
                    <td className="num">
                      {invite.used_count} / {invite.max_uses}
                    </td>
                    <td style={{ fontSize: 12, color: "var(--muted)" }}>
                      {invite.expires_at ? fmtDate(invite.expires_at) : "永久"}
                    </td>
                    <td style={{ fontSize: 12, color: "var(--faint)" }}>
                      {fmtDateTime(invite.created_at)}
                    </td>
                    <td>
                      <span className={`badge ${state.cls}`}>{state.label}</span>
                    </td>
                    <td>
                      <div className="table-actions">
                        <button
                          className="btn btn-sm btn-danger-ghost"
                          disabled={
                            revoke.isPending ||
                            invite.revoked ||
                            usedUp ||
                            Boolean(expired)
                          }
                          onClick={() => revoke.mutate(invite.id)}
                        >
                          吊销
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

      <p className="footnote">
        邀请码明文只在生成时显示一次，数据库只保存哈希 · 默认 7 天有效、单人单次使用 · 管理员不能禁用或降级自己
      </p>

      {userModal ? (
        <UserForm
          state={userModal}
          quota={userModal.mode === "edit" ? quotaMap.get(userModal.user.id) : undefined}
          selfId={me.id}
          onClose={() => setUserModal(null)}
          onDone={() => {
            setUserModal(null);
            void queryClient.invalidateQueries({ queryKey: ["users"] });
            void queryClient.invalidateQueries({ queryKey: ["quotas"] });
          }}
        />
      ) : null}

      {inviteModal ? (
        <InviteForm
          onClose={() => setInviteModal(false)}
          onCreated={(invite) => {
            setInviteModal(false);
            setCreatedInvite(invite);
            void queryClient.invalidateQueries({ queryKey: ["invites"] });
          }}
        />
      ) : null}

      {createdInvite ? (
        <Modal
          title="邀请码已生成"
          onClose={() => setCreatedInvite(null)}
          footer={
            <>
              <button
                className="btn"
                onClick={() =>
                  void copyText(createdInvite.code).then(() =>
                    toast("邀请码已复制", "ok"),
                  )
                }
              >
                <Copy size={13} />
                复制邀请码
              </button>
              <button className="btn btn-primary" onClick={() => setCreatedInvite(null)}>
                完成
              </button>
            </>
          }
        >
          <div className="form-error" style={{ color: "var(--warn)", background: "rgba(251,191,36,.08)", borderColor: "rgba(251,191,36,.4)" }}>
            <AlertTriangle size={14} />
            关闭后无法再次查看明文，请立即复制保存。
          </div>
          <div className="codebox" style={{ fontSize: 18, textAlign: "center", letterSpacing: 2 }}>
            {createdInvite.code}
          </div>
          <p className="hint" style={{ marginTop: 10 }}>
            角色 {createdInvite.role} · 最多 {createdInvite.max_uses} 次 · 过期时间{" "}
            {createdInvite.expires_at ? fmtDateTime(createdInvite.expires_at) : "永久"}
          </p>
        </Modal>
      ) : null}
    </section>
  );
}

function UserForm({
  state,
  quota,
  selfId,
  onClose,
  onDone,
}: {
  state: Exclude<UserModal, null>;
  quota?: Quota;
  selfId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useUI((s) => s.toast);
  const editing = state.mode === "edit";
  const [username, setUsername] = useState(editing ? state.user.username : "");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>(editing ? state.user.role : "user");
  const [disabled, setDisabled] = useState(editing ? state.user.disabled : false);
  const [dailyLimit, setDailyLimit] = useState(
    quota?.daily_cost_limit != null ? String(quota.daily_cost_limit) : "",
  );
  const [sessionLimit, setSessionLimit] = useState(
    quota?.max_cost_per_session != null ? String(quota.max_cost_per_session) : "",
  );
  const [concurrency, setConcurrency] = useState(
    quota?.concurrent_sessions != null ? String(quota.concurrent_sessions) : "",
  );
  const [evalLimit, setEvalLimit] = useState(
    quota ? String(quota.eval_daily_limit ?? 0) : "",
  );
  const [error, setError] = useState("");

  const save = useMutation({
    mutationFn: async () => {
      const quotaBody: Record<string, number> = {};
      if (dailyLimit.trim()) quotaBody.daily_cost_limit = Number(dailyLimit);
      if (sessionLimit.trim()) quotaBody.max_cost_per_session = Number(sessionLimit);
      if (concurrency.trim()) quotaBody.concurrent_sessions = Number(concurrency);
      if (evalLimit.trim()) quotaBody.eval_daily_limit = Number(evalLimit);
      if (!editing) {
        return createUser({
          username: username.trim(),
          password,
          role,
        });
      }
      return patchUser(state.user.id, {
        role,
        disabled,
        ...(password ? { password } : {}),
        ...quotaBody,
      });
    },
    onSuccess: () => {
      toast(editing ? "账号已更新" : "账号已创建", "ok");
      onDone();
    },
    onError: (err) => setError(errorMessage(err, "保存失败")),
  });

  const isSelf = editing && state.user.id === selfId;

  const submit = () => {
    setError("");
    if (!editing && !/^[A-Za-z0-9_.-]{4,32}$/.test(username.trim())) {
      setError("用户名 4-32 位，仅限字母、数字、下划线、点和连字符");
      return;
    }
    if ((!editing || password) && password.length < 12) {
      setError("密码至少 12 位");
      return;
    }
    for (const [label, value] of [
      ["单会话成本上限", sessionLimit],
      ["每日成本上限", dailyLimit],
    ] as const) {
      if (value.trim() && (!Number.isFinite(Number(value)) || Number(value) <= 0)) {
        setError(`${label}需为正数，留空表示沿用默认`);
        return;
      }
    }
    if (concurrency.trim()) {
      const value = Number(concurrency);
      if (!Number.isInteger(value) || value < 1 || value > 10) {
        setError("并发数需为 1-10 的整数");
        return;
      }
    }
    if (evalLimit.trim()) {
      const value = Number(evalLimit);
      if (!Number.isInteger(value) || value < 0 || value > 100) {
        setError("评测次数需为 0-100 的整数（0 表示不限）");
        return;
      }
    }
    save.mutate();
  };

  return (
    <Modal
      title={editing ? `编辑用户 · ${state.user.username}` : "新建用户"}
      onClose={onClose}
      width={620}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" disabled={save.isPending} onClick={submit}>
            {save.isPending ? "保存中…" : "保存"}
          </button>
        </>
      }
    >
      {error ? (
        <div className="form-error">
          <AlertTriangle size={14} />
          {error}
        </div>
      ) : null}
      <div className="field-grid">
        <div className="field">
          <label className="label-sm">用户名</label>
          <input
            className="input"
            value={username}
            disabled={editing}
            onChange={(event) => setUsername(event.target.value)}
            placeholder="至少 4 个字符"
          />
        </div>
        <div className="field">
          <label className="label-sm">{editing ? "重置密码（可留空）" : "密码"}</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="至少 12 位"
            autoComplete="new-password"
          />
        </div>
        <div className="field">
          <label className="label-sm">角色</label>
          <select
            className="input"
            value={role}
            disabled={isSelf}
            onChange={(event) => setRole(event.target.value as Role)}
          >
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
        </div>
        {editing ? (
          <div className="field">
            <label className="label-sm">账号状态</label>
            <label className="check" style={{ height: 38 }}>
              <input
                type="checkbox"
                checked={disabled}
                disabled={isSelf}
                onChange={(event) => setDisabled(event.target.checked)}
              />
              禁用账号（现有刷新令牌立即吊销）
            </label>
          </div>
        ) : null}
        <div className="field">
          <label className="label-sm">每日成本上限（元）</label>
          <input
            className="input"
            value={dailyLimit}
            disabled={!editing}
            onChange={(event) => setDailyLimit(event.target.value)}
            placeholder={editing ? "留空则沿用当前值" : "创建后可在编辑中设置"}
          />
        </div>
        <div className="field">
          <label className="label-sm">单会话成本上限（元）</label>
          <input
            className="input"
            value={sessionLimit}
            disabled={!editing}
            onChange={(event) => setSessionLimit(event.target.value)}
            placeholder={editing ? "留空则沿用当前值" : "创建后可在编辑中设置"}
          />
        </div>
        <div className="field">
          <label className="label-sm">并发会话数（1-10）</label>
          <input
            className="input"
            value={concurrency}
            disabled={!editing}
            onChange={(event) => setConcurrency(event.target.value)}
            placeholder={editing ? "留空则沿用当前值" : "创建后可在编辑中设置"}
          />
        </div>
        <div className="field">
          <label className="label-sm">每日评测次数（0 表示不限）</label>
          <input
            className="input"
            value={evalLimit}
            disabled={!editing}
            onChange={(event) => setEvalLimit(event.target.value)}
            placeholder={editing ? "留空则沿用当前值" : "创建后可在编辑中设置"}
          />
        </div>
      </div>
      {isSelf ? (
        <p className="hint">不能禁用或降级当前登录的管理员账号。</p>
      ) : null}
    </Modal>
  );
}

function InviteForm({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (invite: InviteCreated) => void;
}) {
  const toast = useUI((s) => s.toast);
  const [role, setRole] = useState<Role>("user");
  const [days, setDays] = useState("7");
  const [maxUses, setMaxUses] = useState("1");
  const [error, setError] = useState("");

  const create = useMutation({
    mutationFn: () =>
      createInvite({
        role,
        expires_in_days: Math.max(1, Math.min(90, Number(days) || 7)),
        max_uses: Math.max(1, Math.min(100, Number(maxUses) || 1)),
      }),
    onSuccess: (invite) => onCreated(invite),
    onError: (err) => {
      const message = errorMessage(err, "生成失败");
      setError(message);
      toast(message, "err");
    },
  });

  const submit = () => {
    setError("");
    if (!Number(days) || Number(days) < 1 || Number(days) > 90) {
      setError("有效期需为 1-90 天");
      return;
    }
    if (!Number(maxUses) || Number(maxUses) < 1 || Number(maxUses) > 100) {
      setError("可用次数需为 1-100");
      return;
    }
    create.mutate();
  };

  return (
    <Modal
      title="生成邀请码"
      onClose={onClose}
      width={480}
      footer={
        <>
          <button className="btn" onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" disabled={create.isPending} onClick={submit}>
            {create.isPending ? "生成中…" : "生成"}
          </button>
        </>
      }
    >
      {error ? (
        <div className="form-error">
          <AlertTriangle size={14} />
          {error}
        </div>
      ) : null}
      <div className="field">
        <label className="label-sm">注册后角色</label>
        <select
          className="input"
          value={role}
          onChange={(event) => setRole(event.target.value as Role)}
        >
          <option value="user">user</option>
          <option value="admin">admin</option>
        </select>
      </div>
      <div className="field-grid">
        <div className="field">
          <label className="label-sm">有效期（天）</label>
          <input
            className="input"
            type="number"
            min={1}
            max={90}
            value={days}
            onChange={(event) => setDays(event.target.value)}
          />
        </div>
        <div className="field">
          <label className="label-sm">最多使用次数</label>
          <input
            className="input"
            type="number"
            min={1}
            max={100}
            value={maxUses}
            onChange={(event) => setMaxUses(event.target.value)}
          />
        </div>
      </div>
      <p className="hint">
        邀请码明文只显示一次；注册接口在事务内校验并消费次数。
      </p>
    </Modal>
  );
}
