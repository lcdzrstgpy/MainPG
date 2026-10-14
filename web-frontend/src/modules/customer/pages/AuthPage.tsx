import { useEffect, useRef, useState, type FormEvent } from "react";

import { BRAND_LOGO_URL, BRAND_NAME } from "../../../shared/brand";
import { resetMessagesSync, syncMessages } from "../../../shared/api/messagesApi";
import { httpJson, saveAuthSession } from "../../../transport/http/client";
import {
  clearSavedPassword,
  deleteSavedLogin,
  fetchSavedLogins,
  revealSavedPassword,
  saveLogin,
} from "../loginCredentials";
import { matchSavedAccount, type SavedLoginAccount } from "../loginCredentialsModel";

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
  const [confirmPassword, setConfirmPassword] = useState("");
  const [email, setEmail] = useState("");
  const [emailCode, setEmailCode] = useState("");
  const [invitationCode, setInvitationCode] = useState("");
  // 忘记密码流程状态
  const [forgotEmail, setForgotEmail] = useState("");
  const [forgotCode, setForgotCode] = useState("");
  const [forgotNewPassword, setForgotNewPassword] = useState("");
  const [forgotConfirmPassword, setForgotConfirmPassword] = useState("");
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
  // 单端登录被拒时的自救：显示"强制登录"按钮，点击后撤销旧会话放行。
  const [forceLogin, setForceLogin] = useState(false);
  // 会话失效原因提示（被顶替/过期）：App 层写入 sessionStorage，登录页读一次即清。
  const [sessionHint, setSessionHint] = useState(() => {
    try {
      const value = window.sessionStorage.getItem("wh_login_hint");
      if (value) {
        window.sessionStorage.removeItem("wh_login_hint");
        return value;
      }
    } catch {
      // ignore
    }
    return "";
  });
  // 政策同意：登录与注册均需勾选《界野隐私政策》后才可提交。
  const [agreed, setAgreed] = useState(false);
  // 账号管理：本机保存登录过的账号（含可选保存的密码，见 loginCredentials.ts）。
  // 列表来自本地加密存储；本地服务不可用时静默为空，登录流程不受影响。
  const [savedAccounts, setSavedAccounts] = useState<SavedLoginAccount[]>([]);
  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  // 「记住密码」默认不勾；勾选（或选中已保存密码的账号）时登录成功才保存密码。
  const [rememberPassword, setRememberPassword] = useState(false);
  const accountFieldRef = useRef<HTMLDivElement | null>(null);
  const identifierRef = useRef(identifier);
  const pickSequenceRef = useRef(0);

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

  // 记录最新账号输入值：异步取回密码时用于判断选择是否已被后续操作覆盖。
  useEffect(() => {
    identifierRef.current = identifier;
  }, [identifier]);

  // 登录页加载：拉取本机保存的账号，自动填充最近使用的账号（及其密码），
  // 让高频用户打开即登录；仅在用户尚未输入时填充，不覆盖手输内容。
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const accounts = await fetchSavedLogins();
      if (cancelled || accounts.length === 0) return;
      setSavedAccounts(accounts);
      const latest = accounts[0];
      // 只有「用户还没动账号框」时才自动填充。用户一旦已经输入（含浏览器自动填充），
      // 后续的密码回填与「记住密码」回显必须一并放弃 —— 否则会把 accounts[0] 的密码
      // 填进用户自己敲的另一个账号里，变成「账号 A + 密码 B」。
      const canAutofill = !identifierRef.current.trim();
      setIdentifier((current) => current || latest.identifier);
      if (!canAutofill || !latest.has_password) return;
      // 与 pickSavedAccount 共用同一个序号：两者互相作废，谁后发起谁生效。
      const sequence = ++pickSequenceRef.current;
      // 已保存过密码：勾选框回显为「记住」，并尽力取回密码填充。
      setRememberPassword(true);
      const pwd = await revealSavedPassword(latest.identifier);
      if (cancelled || pickSequenceRef.current !== sequence) return;
      if (identifierRef.current.trim() !== latest.identifier) return;
      if (pwd) setPassword((current) => current || pwd);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // 账号下拉：点击字段以外区域或按 Esc 关闭。
  useEffect(() => {
    if (!accountMenuOpen) return;
    function handlePointerDown(event: MouseEvent) {
      if (!accountFieldRef.current?.contains(event.target as Node)) {
        setAccountMenuOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setAccountMenuOpen(false);
    }
    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [accountMenuOpen]);

  function handleIdentifierChange(value: string) {
    setIdentifier(value);
    // 手输值与已保存账号精确一致时回显其「记住密码」状态；否则回到默认不勾。
    const match = matchSavedAccount(savedAccounts, value);
    setRememberPassword(Boolean(match?.has_password));
  }

  /** 切换「登录 / 注册」：两者账号框语义不同（登录=用户名或邮箱，注册=用户名），
   *  必须一并复位，否则登录页输入的邮箱会被带进注册页当用户名提交。 */
  function switchAuthMode(next: AuthMode) {
    setMode(next);
    setIdentifier("");
    setPassword("");
    setConfirmPassword("");
    setRememberPassword(false);
    setAccountMenuOpen(false);
    setError("");
    setNotice("");
  }

  async function pickSavedAccount(account: SavedLoginAccount) {
    setIdentifier(account.identifier);
    setAccountMenuOpen(false);
    setRememberPassword(account.has_password);
    if (!account.has_password) {
      setPassword("");
      return;
    }
    const sequence = ++pickSequenceRef.current;
    const pwd = await revealSavedPassword(account.identifier);
    // 慢响应守卫：期间又选了别的账号或改动了输入框则丢弃本次填充。
    if (
      pickSequenceRef.current !== sequence ||
      identifierRef.current.trim() !== account.identifier
    ) {
      return;
    }
    setPassword(pwd ?? "");
  }

  async function removeSavedAccount(target: string) {
    await deleteSavedLogin(target);
    const next = savedAccounts.filter((item) => item.identifier !== target);
    setSavedAccounts(next);
    if (next.length === 0) setAccountMenuOpen(false);
    // 删掉的正是当前输入框里的账号时，同步清掉已回填的密码与「记住密码」勾选：
    // 否则下一次点登录时 saveLogin(identifier, password, rememberPassword) 会把刚
    // 删掉的账号连密码原样写回本机存储，删除等于白做。
    if (identifierRef.current.trim() === target) {
      setPassword("");
      setRememberPassword(false);
    }
  }

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
      if (forgotNewPassword !== forgotConfirmPassword) {
        setError("两次输入的新密码不一致，请重新确认");
        return;
      }
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
      // 密码已重置：清掉本机此前保存的该账号旧密码，避免下次自动填入过期密码。
      void clearSavedPassword(forgotEmail.trim());
      setMode("login");
      setForgotEmail("");
      setForgotCode("");
      setForgotNewPassword("");
      setForgotConfirmPassword("");
      setError("");
      alert("密码重置成功，请用新密码登录");
    } catch (err) {
      setError(err instanceof Error ? err.message : "重置失败，请稍后再试");
    } finally {
      setForgotBusy(false);
    }
  }

  async function doLogin(force: boolean) {
    setError("");
    setNotice("");
    setForceLogin(false);
    setBusy(true);
    try {
      if (!agreed) {
        setError("请先阅读并同意《界野隐私政策》");
        return;
      }
      const payload: Record<string, string | boolean> = identifier.includes("@")
        ? { email: identifier, password }
        : { username: identifier, password };
      if (force) payload.force = true;
      const data = await httpJson<LoginResponse>("/api/customer/login", {
        method: "POST",
        body: payload,
        token: "",
      });
      if (!data.token) throw new Error("登录失败：服务端未返回登录凭证");
      saveAuthSession(data.token, data.account ?? {});
      // 登录成功：记住账号（勾了「记住密码」才连同密码保存）；失败静默，
      // 不阻塞进入工作台（本地请求毫秒级，页面切换不影响其完成）。
      void saveLogin(identifier.trim(), password, rememberPassword);
      // 换号后本地消息表还留着上一个账号的定向公告：立刻同步一次，让服务端
      // 在线列表把无关消息撤回。不 await（登录不该被公告服务拖慢），工作台里
      // 的公告弹窗/消息中心会复用同一个在飞请求先等它完成再渲染。
      resetMessagesSync();
      void syncMessages();
      onEnter();
    } catch (err) {
      const message = err instanceof Error ? err.message : "请求失败，请稍后再试";
      setError(message);
      // 账号已在其他设备登录：给"强制登录"自救入口，避免被锁死等 5 分钟。
      setForceLogin(message.includes("已在其他设备登录"));
    } finally {
      setBusy(false);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setNotice("");
    setBusy(true);
    try {
      if (!agreed) {
        setError("请先阅读并同意《界野隐私政策》");
        return;
      }
      if (!isLogin && password !== confirmPassword) {
        setError("两次输入的密码不一致，请重新确认");
        return;
      }
      if (isLogin) {
        await doLogin(false);
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
        setPassword("");
        setConfirmPassword("");
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
      <span className="auth-halo auth-halo-a" aria-hidden="true" />
      <span className="auth-halo auth-halo-b" aria-hidden="true" />
      <section className="auth-brand-card">
        <img className="brand-logo-large" src={BRAND_LOGO_URL} alt={BRAND_NAME} />
        <p className="eyebrow">JIEYE ECOMMERCE PLATFORM</p>
        <h1>界野电商平台</h1>
        <p>面向跨境电商团队的本地运营中台。</p>
        <div className="auth-feature-list"><span>✓ 模块化工作流</span><span>✓ 本地运行时</span></div>
      </section>
      <section className="auth-form-card">
        <div className="auth-tabs"><button type="button" className={isLogin ? "is-selected" : ""} onClick={() => switchAuthMode("login")}>登录</button><button type="button" className={mode === "register" ? "is-selected" : ""} onClick={() => switchAuthMode("register")}>注册</button></div>
        <p className="eyebrow">{isLogin ? "WELCOME BACK" : mode === "register" ? "CREATE ACCOUNT" : "RESET PASSWORD"}</p>
        <h2>{isLogin ? "登录工作台" : mode === "register" ? "注册账号" : "找回密码"}</h2>
        {sessionHint && <p className="auth-notice">{sessionHint}</p>}
        {mode === "forgot" ? (
          <form onSubmit={handleForgotSubmit}>
            <label>邮箱<input type="email" value={forgotEmail} onChange={(e) => setForgotEmail(e.target.value)} onInvalid={(e) => e.currentTarget.setCustomValidity(e.currentTarget.validity.typeMismatch ? "请输入有效的邮箱地址" : "请输入邮箱")} onInput={(e) => e.currentTarget.setCustomValidity("")} placeholder="name@example.com" autoComplete="email" required /></label>
            <label>邮箱验证码<div className="auth-code-row"><input type="text" value={forgotCode} onChange={(e) => setForgotCode(e.target.value.replace(/\D/g, "").slice(0, 6))} onInvalid={(e) => e.currentTarget.setCustomValidity(e.currentTarget.validity.patternMismatch ? "请输入 6 位数字验证码" : "请输入邮箱验证码")} onInput={(e) => e.currentTarget.setCustomValidity("")} placeholder="输入 6 位验证码" inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" required /><button type="button" onClick={handleSendForgotCode} disabled={forgotCodeBusy || forgotCodeCooldown > 0}>{forgotCodeBusy ? "发送中…" : forgotCodeCooldown > 0 ? `${forgotCodeCooldown}s 后重发` : "获取验证码"}</button></div></label>
            <label>新密码<input type="password" value={forgotNewPassword} onChange={(e) => setForgotNewPassword(e.target.value)} onInvalid={(e) => e.currentTarget.setCustomValidity("请输入新密码")} onInput={(e) => e.currentTarget.setCustomValidity("")} placeholder="至少 8 位" autoComplete="new-password" required /></label>
            <label>确认新密码<input type="password" value={forgotConfirmPassword} onChange={(e) => setForgotConfirmPassword(e.target.value)} onInvalid={(e) => e.currentTarget.setCustomValidity("请再次输入新密码")} onInput={(e) => e.currentTarget.setCustomValidity("")} placeholder="再次输入新密码" autoComplete="new-password" required /></label>
            {error && <p className="auth-error">{error}</p>}
            {notice && <p className="auth-notice">{notice}</p>}
            <button className="primary-button" type="submit" disabled={forgotBusy}>{forgotBusy ? "处理中…" : "重置密码"}</button>
            <p className="form-hint"><button type="button" className="link-button" onClick={() => { setMode("login"); setForgotConfirmPassword(""); setError(""); setNotice(""); }}>← 返回登录</button></p>
          </form>
        ) : (
          <>
            <form onSubmit={handleSubmit}>
              {isLogin ? (
                <div className="auth-saved-field" ref={accountFieldRef}>
                  <label>账号（用户名或邮箱）<input type="text" value={identifier} onChange={(e) => handleIdentifierChange(e.target.value)} onFocus={() => setAccountMenuOpen(true)} onClick={() => setAccountMenuOpen(true)} onInvalid={(e) => e.currentTarget.setCustomValidity("请输入账号")} onInput={(e) => e.currentTarget.setCustomValidity("")} placeholder="name@example.com" autoComplete="off" required /></label>
                  {accountMenuOpen && savedAccounts.length > 0 && (
                    <div className="auth-saved-menu" role="listbox" aria-label="已保存的账号">
                      <p className="auth-saved-menu-head">已保存的账号</p>
                      {savedAccounts.map((account) => (
                        <div key={account.identifier} role="option" aria-selected={identifier.trim() === account.identifier} tabIndex={0} className="auth-saved-item" onClick={() => void pickSavedAccount(account)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); void pickSavedAccount(account); } }}>
                          <span className="auth-saved-avatar" aria-hidden="true">{account.identifier.slice(0, 1).toUpperCase()}</span>
                          <span className="auth-saved-name">{account.identifier}</span>
                          {account.has_password ? <span className="auth-saved-tag is-on">已记密码</span> : <span className="auth-saved-tag">不含密码</span>}
                          <button type="button" className="auth-saved-remove" title="移除账号" aria-label={`移除账号 ${account.identifier}`} onClick={(e) => { e.stopPropagation(); void removeSavedAccount(account.identifier); }}>×</button>
                        </div>
                      ))}
                      <p className="auth-saved-menu-foot">登录成功后会记住新账号</p>
                    </div>
                  )}
                </div>
              ) : (
                <>
                  <label>用户名<input type="text" value={identifier} onChange={(e) => setIdentifier(e.target.value)} onInvalid={(e) => e.currentTarget.setCustomValidity("请输入用户名")} onInput={(e) => e.currentTarget.setCustomValidity("")} placeholder="例如：ops-team-a" autoComplete="username" required /></label>
                  <label>邮箱<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} onInvalid={(e) => e.currentTarget.setCustomValidity(e.currentTarget.validity.typeMismatch ? "请输入有效的邮箱地址" : "请输入邮箱")} onInput={(e) => e.currentTarget.setCustomValidity("")} placeholder="name@example.com" autoComplete="email" required /></label>
                  <label>邮箱验证码<div className="auth-code-row"><input type="text" value={emailCode} onChange={(e) => setEmailCode(e.target.value.replace(/\D/g, "").slice(0, 6))} onInvalid={(e) => e.currentTarget.setCustomValidity(e.currentTarget.validity.patternMismatch ? "请输入 6 位数字验证码" : "请输入邮箱验证码")} onInput={(e) => e.currentTarget.setCustomValidity("")} placeholder="输入 6 位验证码" inputMode="numeric" autoComplete="one-time-code" pattern="\d{6}" required /><button type="button" onClick={handleSendEmailCode} disabled={codeBusy || codeCooldown > 0}>{codeBusy ? "发送中…" : codeCooldown > 0 ? `${codeCooldown}s 后重发` : "获取验证码"}</button></div></label>
                </>
              )}
              {/* 密码框统一 new-password：密码记忆由应用内「账号管理」接管（登录页下拉自动
                  填充），避免浏览器原生密码管理器重复弹窗、且其自动填充不会同步进受控组件。 */}
              <label>密码<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} onInvalid={(e) => e.currentTarget.setCustomValidity("请输入密码")} onInput={(e) => e.currentTarget.setCustomValidity("")} placeholder={isLogin ? "输入密码" : "至少 8 位"} autoComplete="new-password" required /></label>
              {isLogin && <label className="auth-agree auth-remember"><input type="checkbox" checked={rememberPassword} onChange={(e) => setRememberPassword(e.target.checked)} /><span>记住密码，下次自动填充</span></label>}
              {!isLogin && <label>确认密码<input type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} onInvalid={(e) => e.currentTarget.setCustomValidity("请再次输入密码")} onInput={(e) => e.currentTarget.setCustomValidity("")} placeholder="再次输入密码" autoComplete="new-password" required /></label>}
              {!isLogin && <label>邀请码<input type="text" value={invitationCode} onChange={(e) => setInvitationCode(e.target.value)} placeholder="选填" autoComplete="off" /></label>}
              <label className="auth-agree"><input type="checkbox" checked={agreed} onChange={(e) => setAgreed(e.target.checked)} /><span>我已阅读并同意 <a className="link-button" href="/privacy" target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()}>《界野隐私政策》</a></span></label>
              {error && <p className="auth-error">{error}</p>}
              {forceLogin && (
                <button
                  type="button"
                  className="auth-force-login"
                  disabled={busy}
                  onClick={() => doLogin(true)}
                >
                  {busy ? "处理中…" : "强制登录（将退出其他设备）"}
                </button>
              )}
              {notice && <p className="auth-notice">{notice}</p>}
              <button className="primary-button" type="submit" disabled={busy}>{busy ? "处理中…" : isLogin ? "登录并进入工作台 →" : "注册并进入工作台 →"}</button>
            </form>
            {isLogin && <p className="form-hint"><button type="button" className="link-button" onClick={() => { setMode("forgot"); setForgotConfirmPassword(""); setError(""); setNotice(""); }}>忘记密码？</button></p>}
            {mode === "register" && <p className="form-hint">注册成功后自动切换到登录页。</p>}
          </>
        )}
      </section>
    </main>
  );
}

export type { AccountInfo };
