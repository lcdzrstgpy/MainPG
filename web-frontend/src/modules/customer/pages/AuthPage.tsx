import { useEffect, useState, type FormEvent } from "react";

import { BRAND_LOGO_URL, BRAND_NAME } from "../../../shared/brand";
import { httpJson, saveAuthSession } from "../../../transport/http/client";

type AuthPageProps = { onEnter: () => void };
type AuthMode = "login" | "register" | "forgot";

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

type EmailCodeResponse = {
  ok?: boolean;
  message?: string;
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
  const [emailCode, setEmailCode] = useState("");
  const [invitationCode, setInvitationCode] = useState("");
  // 忘记密码流程状态
  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotCode, setForgotCode] = useState("");
  const [forgotNewPassword, setForgotNewPassword] = useState("");
  // 工作区固定为 default（名称留空），注册页不允许用户修改。
  const FIXED_WORKSPACE_CODE = "default";
  const FIXED_WORKSPACE_NAME = "";

  const [busy, setBusy] = useState(false);
  const [codeBusy, setCodeBusy] = useState(false);
  const [codeCooldown, setCodeCooldown] = useState(0);
  const [forgotBusy, setForgotBusy] = useState(false);
  const [forgotCodeBusy, setForgotCodeBusy] = useState(false);
  const [forgotCodeCooldown, setForgotCodeCooldown] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (codeCooldown <= 0) return;
    const timer = window.setInterval(() => {
      setCodeCooldown((value) => Math.max(0, value - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [codeCooldown]);

  useEffect(() => {
    if (forgotCodeCooldown <= 0) return;
    const timer = window.setInterval(() => {
      setForgotCodeCooldown((value) => Math.max(0, value - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [forgotCodeCooldown]);

  async function handleSendEmailCode() {
    setError("");
    setNotice("");
    const normalizedEmail = email.trim();
    if (!normalizedEmail || !normalizedEmail.includes("@")) {
      setError("请先输入有效邮箱");
      return;
    }
    setCodeBusy(true);
    try {
      const data = await httpJson<EmailCodeResponse>("/api/customer/email-code", {
        method: "POST",
        body: { email: normalizedEmail, purpose: "register" },
        token: "",
      });
      if (data.ok === false) throw new Error(data.message ?? "验证码发送失败");
      setCodeCooldown(60);
      setNotice("验证码已发送，请检查收件箱和垃圾邮件箱");
    } catch (err) {
      setError(err instanceof Error ? err.message : "验证码发送失败，请稍后再试");
    } finally {
      setCodeBusy(false);
    }
  }

  async function handleSendForgotCode() {
    setError("");
    setNotice("");
    const normalizedEmail = forgotEmail.trim();
    if (!normalizedEmail || !normalizedEmail.includes("@")) {
      setError("请先输入有效邮箱");
      return;
    }
    setForgotCodeBusy(true);
    try {
      const data = await httpJson<EmailCodeResponse>("/api/customer/email-code", {
        method: "POST",
        body: { email: normalizedEmail, purpose: "reset_password" },
        token: "",
      });
      if (data.ok === false) throw new Error(data.message ?? "验证码发送失败");
      setForgotCodeCooldown(60);
      setNotice("验证码已发送，请检查收件箱和垃圾邮件箱");
    } catch (err) {
      setError(err instanceof Error ? err.message : "验证码发送失败，请稍后再试");
    } finally {
      setForgotCodeBusy(false);
    }
  }

  async function handleForgotSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    setForgotBusy(true);
    try {
      const data = await httpJson<{ ok?: boolean; message?: string }>("/api/customer/reset-password", {
        method: "POST",
        body: {
          email: forgotEmail.trim(),
          code: forgotCode,
          new_password: forgotNewPassword,
        },
        token: "",
      });
      if (data.ok === false) throw new Error(data.message ?? "重置失败");
      setMode("login");
      setForgotEmail("");
      setForgotCode("");
      setForgotNewPassword("");
      setError("");
      alert("密码重置成功，请用新密码登录");
    } catch (err) {
      setError(err instanceof Error ? err.message : "重置失败，请稍后再试");
    } finally {
      setForgotBusy(false);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
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
            email_code: emailCode,
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
        <p className="eyebrow">JIEYE ECOMMERCE PLATFORM</p>
        <h1>界野电商平台</h1>
        <p>面向跨境电商团队的本地运营中台。登录/注册已连接真实后端接口，账号数据由后端账号服务统一管理。</p>
        <div className="auth-feature-list"><span>✓ 模块化工作流</span><span>✓ 本地运行时</span><span>✓ 团队协作预留</span></div>
      </section>
      <section className="auth-form-card">
        <div className="auth-tabs"><button type="button" className={isLogin ? "is-selected" : ""} onClick={() => { setMode("login"); setError(""); setNotice(""); }}>登录</button><button type="button" className={mode === "register" ? "is-selected" : ""} onClick={() => { setMode("register"); setError(""); setNotice(""); }}>注册</button></div>
        <p className="eyebrow">{isLogin ? "WELCOME BACK" : mode === "register" ? "CREATE ACCOUNT" : "RESET PASSWORD"}</p>
        <h2>{isLogin ? "登录工作台" : mode === "register" ? "注册账号" : "找回密码"}</h2>
        {mode === "forgot" ? (
          <form onSubmit={handleForgotSubmit}>
            <label>邮箱<input type="email" value={forgotEmail} onChange={(e) => setForgotEmail(e.target.value)} placeholder="name@example.com" autoComplete="email" required /></label>
            <label>邮箱验证码<div className="auth-code-row"><input type="text" value={forgotCode} onChange={(e) => setForgotCode(e.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="输入 6 位验证码" inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" required /><button type="button" onClick={handleSendForgotCode} disabled={forgotCodeBusy || forgotCodeCooldown > 0}>{forgotCodeBusy ? "发送中…" : forgotCodeCooldown > 0 ? `${forgotCodeCooldown}s 后重发` : "获取验证码"}</button></div></label>
            <label>新密码<input type="password" value={forgotNewPassword} onChange={(e) => setForgotNewPassword(e.target.value)} placeholder="至少 8 位" autoComplete="new-password" required /></label>
            {error && <p className="auth-error">{error}</p>}
            {notice && <p className="auth-notice">{notice}</p>}
            <button className="primary-button" type="submit" disabled={forgotBusy}>{forgotBusy ? "处理中…" : "重置密码"}</button>
            <p className="form-hint"><button type="button" className="link-button" onClick={() => { setMode("login"); setError(""); setNotice(""); }}>← 返回登录</button></p>
          </form>
        ) : (
          <>
            <form onSubmit={handleSubmit}>
              {isLogin ? (
                <label>账号（用户名或邮箱）<input type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="name@example.com" autoComplete="username" required /></label>
              ) : (
                <>
                  <label>用户名<input type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)} placeholder="例如：ops-team-a" autoComplete="username" required /></label>
                  <label>邮箱<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="name@example.com" autoComplete="email" required /></label>
                  <label>邮箱验证码<div className="auth-code-row"><input type="text" value={emailCode} onChange={(e) => setEmailCode(e.target.value.replace(/\D/g, "").slice(0, 6))} placeholder="输入 6 位验证码" inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" required /><button type="button" onClick={handleSendEmailCode} disabled={codeBusy || codeCooldown > 0}>{codeBusy ? "发送中…" : codeCooldown > 0 ? `${codeCooldown}s 后重发` : "获取验证码"}</button></div></label>
                  <label>邀请码<input type="text" value={invitationCode} onChange={(e) => setInvitationCode(e.target.value)} placeholder="请输入管理员提供的邀请码" autoComplete="off" required /></label>
                </>
              )}
              <label>密码<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder={isLogin ? "输入密码" : "至少 8 位"} autoComplete={isLogin ? "current-password" : "new-password"} required /></label>
              {error && <p className="auth-error">{error}</p>}
              {notice && <p className="auth-notice">{notice}</p>}
              <button className="primary-button" type="submit" disabled={busy}>{busy ? "处理中…" : isLogin ? "登录并进入工作台 →" : "注册并进入工作台 →"}</button>
            </form>
            {isLogin && <p className="form-hint">登录后 token 保存在本地浏览器，可自动恢复登录态。<button type="button" className="link-button" onClick={() => { setMode("forgot"); setError(""); setNotice(""); }}>忘记密码？</button></p>}
            {mode === "register" && <p className="form-hint">注册成功后自动切换到登录页。</p>}
          </>
        )}
      </section>
    </main>
  );
}

export type { AccountInfo };
