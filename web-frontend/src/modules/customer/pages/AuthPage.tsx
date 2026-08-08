import { useState, type FormEvent } from "react";

import { BRAND_LOGO_URL, BRAND_NAME } from "../../../shared/brand";
import { httpJson, saveAuthSession } from "../../../transport/http/client";

type AuthPageProps = { onEnter: () => void };
type AuthMode = "login" | "register";

type LoginResponse = {
  ok?: boolean;
  user_id?: string;
  token: string;
  expires_at?: string;
  account?: Record<string, unknown>;
};

type RegisterResponse = {
  ok?: boolean;
  message?: string;
  raw?: Record<string, unknown>;
};

type AccountInfo = {
  username?: string;
  email?: string;
  role?: string;
  workspace_code?: string;
};

export function AuthPage({ onEnter }: AuthPageProps) {
  const [mode, setMode] = useState<AuthMode>("login");
  const isLogin = mode === "login";

  const [identifier, setIdentifier] = useState("");
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [invitationCode, setInvitationCode] = useState("");
  // 工作区固定为 default（名称留空），注册页不允许用户修改。
  const FIXED_WORKSPACE_CODE = "default";
  const FIXED_WORKSPACE_NAME = "";

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      if (isLogin) {
        const payload = identifier.includes("@")
          ? { email: identifier, password }
          : { username: identifier, password };
        const data = await httpJson<LoginResponse>("/api/customer/login", {
          method: "POST",
          body: payload,
          token: "",
        });
        if (!data.token) throw new Error("登录失败：服务端未返回 token");
        saveAuthSession(data.token, data.account ?? {});
        onEnter();
      } else {
        const data = await httpJson<RegisterResponse>("/api/customer/register", {
          method: "POST",
          body: {
            username: identifier,
            email,
            password,
            invitation_code: invitationCode,
            role: "operator",
            workspace_code: FIXED_WORKSPACE_CODE,
            workspace_name: FIXED_WORKSPACE_NAME,
          },
          token: "",
        });
        if (data.ok === false) throw new Error(data.message ?? "注册失败");
        setMode("login");
        setError("");
        alert("注册成功，请用新账号登录");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "请求失败，请稍后再试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-screen">
      <section className="auth-brand-card">
        <img className="brand-logo-large" src={BRAND_LOGO_URL} alt={BRAND_NAME} />
        <p className="eyebrow">QIFAN ECOMMERCE PLATFORM</p>
        <h1>启凡电商平台</h1>
        <p>面向跨境电商团队的本地运营中台。登录/注册已连接真实后端接口，账号数据由后端账号服务统一管理。</p>
        <div className="auth-feature-list"><span>✓ 模块化工作流</span><span>✓ 本地运行时</span><span>✓ 团队协作预留</span></div>
      </section>
      <section className="auth-form-card">
        <div className="auth-tabs"><button type="button" className={isLogin ? "is-selected" : ""} onClick={() => { setMode("login"); setError(""); }}>登录</button><button type="button" className={!isLogin ? "is-selected" : ""} onClick={() => { setMode("register"); setError(""); }}>注册</button></div>
        <p className="eyebrow">{isLogin ? "WELCOME BACK" : "CREATE ACCOUNT"}</p>
        <h2>{isLogin ? "登录工作台" : "注册账号"}</h2>
        <form onSubmit={handleSubmit}>
          {isLogin ? (
            <label>账号（用户名或邮箱）<input type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="name@example.com" autoComplete="username" required /></label>
          ) : (
            <>
              <label>用户名<input type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="例如：ops-team-a" autoComplete="username" required /></label>
              <label>邮箱<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" autoComplete="email" required /></label>
              <label>邀请码<input type="text" value={invitationCode} onChange={(e) => setInvitationCode(e.target.value)} placeholder="请输入管理员提供的邀请码" autoComplete="off" required /></label>
            </>
          )}
          <label>密码<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={isLogin ? "输入密码" : "至少 8 位"} autoComplete={isLogin ? "current-password" : "new-password"} required /></label>
          {error && <p className="auth-error">{error}</p>}
          <button className="primary-button" type="submit" disabled={busy}>{busy ? "处理中…" : isLogin ? "登录并进入工作台 →" : "注册并进入工作台 →"}</button>
        </form>
        {isLogin && <p className="form-hint">登录后 token 保存在本地浏览器，可自动恢复登录态。</p>}
        {!isLogin && <p className="form-hint">注册成功后自动切换到登录页。</p>}
      </section>
    </main>
  );
}

export type { AccountInfo };
