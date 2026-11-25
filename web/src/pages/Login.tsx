import { AlertCircle, ArrowRight, Moon, Sun } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { Background } from "../components/Background";
import { errorMessage } from "../lib/errors";
import { IS_TAURI } from "../lib/platform";
import { IS_MOBILE_HOST } from "../lib/mobile-host";
import { useAuth } from "../stores/auth";
import { useUI } from "../stores/ui";

type Mode = "login" | "register";

export function Login() {
  const status = useAuth((s) => s.status);
  const login = useAuth((s) => s.login);
  const register = useAuth((s) => s.register);
  const theme = useUI((s) => s.theme);
  const toggleTheme = useUI((s) => s.toggleTheme);
  const navigate = useNavigate();
  const location = useLocation();

  const from =
    (location.state as { from?: string } | null)?.from &&
    !(location.state as { from?: string }).from!.startsWith("/login")
      ? (location.state as { from: string }).from
      : "/";

  const [mode, setMode] = useState<Mode>("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [invite, setInvite] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  if (status === "authed") return <Navigate to={from} replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setError("");
    if (mode === "register") {
      if (username.trim().length < 4) {
        setError("用户名至少 4 个字符（字母、数字、下划线、点或连字符）");
        return;
      }
      if (password.length < 12) {
        setError("密码至少 12 位");
        return;
      }
      if (!invite.trim()) {
        setError("请填写邀请码");
        return;
      }
    } else if (!username.trim() || !password) {
      setError("请输入用户名和密码");
      return;
    }
    setBusy(true);
    try {
      if (mode === "login") {
        await login(username.trim(), password);
      } else {
        await register(username.trim(), password, invite.trim());
      }
      navigate(from, { replace: true });
    } catch (err) {
      setError(errorMessage(err, mode === "login" ? "登录失败" : "注册失败"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Background />
      <div className="login-wrap">
        <div className="login-hero">
          <div className="scanline" />
          <div className="eyebrow">asagent platform</div>
          <h2>
            把 Agent 的每一步
            <br />
            都变成<span className="grad-text">可审阅的结果</span>
          </h2>
          <p>
            手写运行时、事件溯源、三级权限与人工审批。控制台让你看到路由决策、工具调用、成本与最终结论，而不是一个黑盒对话框。
          </p>
          <div className="hero-points">
            <div className="hero-point">
              <span className="tick">✓</span>
              <span>事件级执行轨迹，每一步都可回放</span>
            </div>
            <div className="hero-point">
              <span className="tick">✓</span>
              <span>危险写操作强制人工审批，执行前自动备份</span>
            </div>
            <div className="hero-point">
              <span className="tick">✓</span>
              <span>会话级、每日、全局三层成本上限</span>
            </div>
            <div className="hero-point">
              <span className="tick">✓</span>
              <span>SQL / RAG / Code / Office 四个场景包</span>
            </div>
          </div>
        </div>

        <div className="login-form-wrap" style={{ position: "relative" }}>
          <button
            className="icon-btn"
            style={{ position: "absolute", right: 22, top: 22 }}
            onClick={(event) => toggleTheme({ x: event.clientX, y: event.clientY })}
            title="切换主题"
          >
            {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
          </button>
          <div className="login-card">
            <div className="switch-tabs" style={{ marginBottom: 20 }}>
              <button
                className={mode === "login" ? "on" : ""}
                onClick={() => {
                  setMode("login");
                  setError("");
                }}
              >
                登录
              </button>
              <button
                className={mode === "register" ? "on" : ""}
                onClick={() => {
                  setMode("register");
                  setError("");
                }}
              >
                邀请码注册
              </button>
            </div>

            <h3>{mode === "login" ? "欢迎回来" : "凭邀请码加入"}</h3>
            <p>
              {mode === "login"
                ? "使用你的账号继续。"
                : "账号由管理员发放邀请码后开通。"}
            </p>

            {error ? (
              <div className="form-error">
                <AlertCircle size={14} />
                {error}
              </div>
            ) : null}

            <form onSubmit={submit}>
              {mode === "register" ? (
                <div className="field">
                  <label className="label-sm">邀请码</label>
                  <input
                    className="input mono"
                    value={invite}
                    onChange={(event) => setInvite(event.target.value)}
                    placeholder="INV-XXXX-XXXX"
                    autoComplete="off"
                  />
                </div>
              ) : null}
              <div className="field">
                <label className="label-sm">用户名</label>
                <input
                  className="input"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  placeholder={mode === "register" ? "至少 4 个字符" : "wangrui"}
                  autoComplete="username"
                />
              </div>
              <div className="field">
                <label className="label-sm">密码</label>
                <input
                  className="input"
                  type="password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  placeholder={mode === "register" ? "至少 12 位" : "••••••••••••"}
                  autoComplete={mode === "register" ? "new-password" : "current-password"}
                />
                {mode === "register" ? (
                  <div className="hint">建议使用密码管理器生成，至少 12 位。</div>
                ) : null}
              </div>
              <div className="row" style={{ marginBottom: 17 }}>
                <span style={{ fontSize: 12, color: "var(--muted)" }}>
                  {IS_MOBILE_HOST
                    ? "手机端使用系统安全存储保持登录，浏览器预览使用本机存储"
                    : IS_TAURI
                    ? "桌面端使用系统安全存储保存刷新令牌（30 天）"
                    : "登录状态通过 httpOnly 刷新令牌保持 30 天"}
                </span>
              </div>
              <button className="btn btn-primary btn-block" disabled={busy} type="submit">
                {busy ? "处理中…" : mode === "login" ? "登录" : "创建账号"}
                {busy ? null : <ArrowRight size={15} />}
              </button>
            </form>
            <p style={{ margin: "16px 0 0", fontSize: 11, color: "var(--faint)" }}>
              云端模型由服务端统一配置；页面不会显示任何 API key。
            </p>
          </div>
        </div>
      </div>
    </>
  );
}
