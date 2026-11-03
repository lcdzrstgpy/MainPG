import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import { BRAND_LOGO_URL, BRAND_NAME } from "../../../shared/brand";
import {
  PRIVACY_POLICY_VERSION_FALLBACK,
  clearPrivacyConsent,
  readPrivacyConsent,
  resolvePolicyVersion,
  savePrivacyConsent,
} from "../../../shared/privacyConsent";
import {
  syncPrivacyPolicy,
  type PrivacyPolicyDocument,
} from "../../../shared/api/privacyPolicyApi";
import { resetMessagesSync, syncMessages } from "../../../shared/api/messagesApi";
import { httpJson, saveAuthSession } from "../../../transport/http/client";
import { markPodAgreementPending, clearPodAgreementPending } from "../../../shared/components/PodUsageAgreementModal";
import { useLogoFlip } from "./authLogoTransition";

type AuthPageProps = { onEnter: () => void };
type AuthStep = "welcome" | "login" | "register" | "forgot";

type FieldKey =
  | "identifier"
  | "password"
  | "confirmPassword"
  | "email"
  | "emailCode"
  | "invitationCode"
  | "forgotEmail"
  | "forgotCode"
  | "forgotNew"
  | "forgotConfirm";

type FieldError = { field: FieldKey; message: string } | null;

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

/** 会话失效提示由 App 层写入 sessionStorage，读一次即清。 */
function readSessionHintOnce(): string {
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
}

/** 密码可见性图标：细线小眼睛，尺寸跟着 .auth-field-eye 的 font-size/尺寸走。 */
function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M2.4 12S6 5.8 12 5.8 21.6 12 21.6 12 18 18.2 12 18.2 2.4 12 2.4 12Z" />
      <circle cx="12" cy="12" r="3.1" />
    </svg>
  );
}

function EyeOffIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M2.4 12S6 5.8 12 5.8 21.6 12 21.6 12 18 18.2 12 18.2 2.4 12 2.4 12Z" />
      <circle cx="12" cy="12" r="3.1" />
      <path d="M3.6 3.6l16.8 16.8" />
    </svg>
  );
}

export function AuthPage({ onEnter }: AuthPageProps) {
  // 会话失效原因提示（被顶替/过期）：登录页只消费一次。
  const [sessionHint] = useState(readSessionHintOnce);
  // 每次进入都从首屏开始；welcome = 首屏，其余三态共用同一个表单面板。
  const [step, setStep] = useState<AuthStep>("welcome");
  const isLogin = step === "login";
  const isWelcome = step === "welcome";
  // 只有用户主动切步时才播入场动画：首帧不播，避免打开页面先闪一下。
  const [entered, setEntered] = useState(false);

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
  // 字段级错误：红边 + 字段下方 12px 红字，替代过去「整块红条」的做法。
  const [fieldError, setFieldError] = useState<FieldError>(null);
  const [showPassword, setShowPassword] = useState(false);
  // 单端登录被拒时的自救：显示"强制登录"按钮，点击后撤销旧会话放行。
  const [forceLogin, setForceLogin] = useState(false);
  // 政策同意：登录与注册均需勾选《界野隐私政策》后才可提交。
  // 首次必须由用户**主动**勾选（不默认勾选同意，合规要求，见 shared/privacyConsent.ts）；
  // 勾过之后本地记住、下次直接回填；取消勾选即撤回记录；协议版本变更会自动失效并重新征求。
  const [agreed, setAgreed] = useState(() => readPrivacyConsent());
  // 隐私政策：正文托管在服务端，版本以远端为准、拉不到就用内置常量兜底。
  const [policyVersion, setPolicyVersion] = useState(PRIVACY_POLICY_VERSION_FALLBACK);

  // 账号密码记忆交给浏览器密码管理器（autocomplete），应用内不再自建账号库。
  // 表单改由 JS 校验（表单加了 noValidate），失败时把焦点送回对应输入框。
  const fieldRefs = useRef<Partial<Record<FieldKey, HTMLInputElement | null>>>({});
  const { logoRef, slotRef } = useLogoFlip(step);

  /**
   * 拿到远端隐私政策文档后的统一处理：版本以远端为准，正文地址用 public_url；
   * 版本变了就把「已同意」按新版本重新判定，触发重新勾选。
   */
  const applyPolicyDocument = useCallback((doc: PrivacyPolicyDocument) => {
    const nextVersion = resolvePolicyVersion(doc);
    setPolicyVersion(nextVersion);
    setAgreed((current) => current && readPrivacyConsent(nextVersion));
  }, []);

  // 启动时静默同步一次隐私政策（异步、不阻塞首屏）：远端失败会自动退回本地留存副本
  useEffect(() => {
    let alive = true;
    void syncPrivacyPolicy().then((result) => {
      if (!alive || !result.document) return;
      applyPolicyDocument(result.document);
    });
    return () => {
      alive = false;
    };
  }, [applyPolicyDocument]);

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

  /** 校验失败：定位到具体字段，并把焦点送回去。 */
  function fail(field: FieldKey, message: string) {
    setFieldError({ field, message });
    fieldRefs.current[field]?.focus();
  }

  function clearFieldError(field: FieldKey) {
    setFieldError((current) => (current?.field === field ? null : current));
  }

  function handleIdentifierChange(value: string) {
    setIdentifier(value);
    clearFieldError("identifier");
  }

  /** 勾选即记录「已同意」；取消勾选同时撤回该记录（合规要求提供便捷的撤回方式）。 */
  function handleAgreeChange(next: boolean) {
    setAgreed(next);
    if (next) savePrivacyConsent(undefined, policyVersion);
    else clearPrivacyConsent();
  }

  /** 切换步骤：登录 / 注册 / 找回三者账号框语义不同（登录=用户名或邮箱，注册=用户名），
   *  必须一并复位，否则登录页输入的邮箱会被带进注册页当用户名提交。 */
  function goToStep(next: AuthStep) {
    setEntered(true);
    setStep(next);
    setIdentifier("");
    setPassword("");
    setConfirmPassword("");
    setShowPassword(false);
    setError("");
    setNotice("");
    setFieldError(null);
    setForceLogin(false);
  }

  async function handleSendEmailCode() {
    setError("");
    setNotice("");
    clearFieldError("email");
    const normalizedEmail = email.trim();
    if (!normalizedEmail || !normalizedEmail.includes("@")) {
      fail("email", "请先输入有效邮箱");
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
    clearFieldError("forgotEmail");
    const normalizedEmail = forgotEmail.trim();
    if (!normalizedEmail || !normalizedEmail.includes("@")) {
      fail("forgotEmail", "请先输入有效邮箱");
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
    setFieldError(null);
    setForgotBusy(true);
    try {
      if (!forgotEmail.trim().includes("@")) {
        fail("forgotEmail", "请输入有效的邮箱地址");
        return;
      }
      if (!/^\d{6}$/.test(forgotCode.trim())) {
        fail("forgotCode", "请输入 6 位数字验证码");
        return;
      }
      if (!forgotNewPassword) {
        fail("forgotNew", "请输入新密码");
        return;
      }
      if (forgotNewPassword !== forgotConfirmPassword) {
        fail("forgotConfirm", "两次输入的新密码不一致，请重新确认");
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
      setStep("login");
      setForgotEmail("");
      setForgotCode("");
      setForgotNewPassword("");
      setForgotConfirmPassword("");
      setFieldError(null);
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
      // 平台把「是否已确认 POD 协议」随登录态一起下发：已确认则本次登录不再弹窗；
      // 否则置位待阅读，用户首次点进 POD 模块时强制阅读 8 秒并勾选。
      if (data.account?.pod_agreement_confirmed === true) {
        clearPodAgreementPending();
      } else {
        markPodAgreementPending();
      }
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
    setFieldError(null);
    setBusy(true);
    try {
      // 原生校验（required / onInvalid）已由表单的 noValidate 关掉：它设的 customValidity
      // 是粘性的，「点已保存账号」这类程序化回填不触发 input 事件，清不掉，于是会出现
      // 框里明明有值、浏览器仍弹「请输入账号」的情况。这里统一按表单顺序自己校验。
      if (!identifier.trim()) {
        fail("identifier", isLogin ? "请输入账号" : "请输入用户名");
        return;
      }
      if (!isLogin && !email.trim().includes("@")) {
        fail("email", "请输入有效的邮箱地址");
        return;
      }
      if (!isLogin && !/^\d{6}$/.test(emailCode.trim())) {
        fail("emailCode", "请输入 6 位数字验证码");
        return;
      }
      if (!password) {
        fail("password", "请输入密码");
        return;
      }
      if (!isLogin && !confirmPassword) {
        fail("confirmPassword", "请再次输入密码");
        return;
      }
      if (!agreed) {
        setError("请先阅读并同意《界野隐私政策》");
        return;
      }
      if (!isLogin && password !== confirmPassword) {
        fail("confirmPassword", "两次输入的密码不一致，请重新确认");
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
        setStep("login");
        setPassword("");
        setConfirmPassword("");
        setFieldError(null);
        setError("");
        alert("注册成功，请用新账号登录");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "请求失败，请稍后再试");
    } finally {
      setBusy(false);
    }
  }

  const title = isLogin ? "登录工作台" : step === "register" ? "创建账户" : "找回密码";
  const subtitle = isLogin
    ? "使用你的界野账号继续"
    : step === "register"
      ? "注册后即可使用界野电商平台"
      : "通过邮箱验证码重置登录密码";
  const submitLabel = isLogin ? "登录" : step === "register" ? "注册" : "重置密码";
  const busyLabel = isLogin ? "登录中…" : step === "register" ? "注册中…" : "处理中…";
  const invalid = (field: FieldKey) => fieldError?.field === field;

  const accountField = (
    <div className="auth-field">
      <label className="auth-field-label" htmlFor="auth-identifier">
        {isLogin ? "账号（用户名或邮箱）" : "用户名"}
      </label>
      <input
        id="auth-identifier"
        ref={(el) => {
          fieldRefs.current.identifier = el;
        }}
        type="text"
        value={identifier}
        onChange={(e) => handleIdentifierChange(e.target.value)}
        autoComplete="username"
        aria-invalid={invalid("identifier")}
      />
    </div>
  );

  const passwordField = (
    <div className={`auth-field${password ? " has-action is-eye" : ""}`}>
      <label className="auth-field-label" htmlFor="auth-password">
        {isLogin ? "密码" : "密码（至少 8 位）"}
      </label>
      <input
        id="auth-password"
        ref={(el) => {
          fieldRefs.current.password = el;
        }}
        // 清空密码后强制回到隐藏态，避免之后重新输入的内容以明文显示。
        type={showPassword && password ? "text" : "password"}
        value={password}
        onChange={(e) => {
          const next = e.target.value;
          setPassword(next);
          if (!next) setShowPassword(false);
          clearFieldError("password");
        }}
        autoComplete={isLogin ? "current-password" : "new-password"}
        aria-invalid={invalid("password")}
      />
      {password && (
        <button
          type="button"
          className="auth-field-action auth-field-eye"
          aria-pressed={showPassword}
          aria-label={showPassword ? "隐藏密码" : "显示密码"}
          title={showPassword ? "隐藏密码" : "显示密码"}
          onClick={() => setShowPassword((value) => !value)}
        >
          {showPassword ? <EyeOffIcon /> : <EyeIcon />}
        </button>
      )}
    </div>
  );

  const privacyCheck = (
    <label className="auth-check auth-privacy">
      <input type="checkbox" checked={agreed} onChange={(e) => handleAgreeChange(e.target.checked)} />
      <span className="auth-check-box" aria-hidden="true">
        <svg viewBox="0 0 24 24">
          <path d="M6.4 12.7l3.8 3.8L17.9 8" />
        </svg>
      </span>
      <span>
        {isLogin ? "登录前请阅读并同意" : "注册前请阅读并同意"}{" "}
        <a
          className="auth-link"
          href="/privacy"
          target="_blank"
          rel="noopener noreferrer"
          onClick={(event) => {
            // 在 <label> 里：只阻止「顺带勾选」，不拦默认的跳转
            event.stopPropagation();
          }}
        >
          《界野隐私政策》
        </a>
      </span>
    </label>
  );

  return (
    <main className="auth-page" data-enter={entered ? "true" : "false"}>
      <header className="auth-nav">
        <button type="button" className="auth-nav-brand" onClick={() => goToStep("welcome")}>
          {BRAND_NAME}
        </button>
        <nav className="auth-nav-links" aria-label="条款">
          <a className="auth-link" href="/privacy" target="_blank" rel="noopener noreferrer">
            隐私政策
          </a>
        </nav>
      </header>

      <div className="auth-stage">
        {entered && !isWelcome && (
          <button type="button" className="auth-link auth-back" onClick={() => goToStep("welcome")}>
            ← 返回
          </button>
        )}
        {isWelcome ? (
          <section className="auth-hero">
            <div className="auth-logo-slot auth-logo-slot-hero" ref={slotRef}>
              <img ref={logoRef} src={BRAND_LOGO_URL} alt={BRAND_NAME} width={1070} height={630} decoding="async" />
            </div>
            <div className="auth-hero-copy">
              <h1>界无界，野有径</h1>
              <p>商品、店铺与订单数据全部留在本机，敏感信息不出电脑</p>
            </div>
            <div className="auth-hero-actions">
              <button type="button" className="auth-submit auth-hero-cta" onClick={() => goToStep("login")}>
                登录
              </button>
              <p className="auth-hero-links">
                <button type="button" className="auth-link" onClick={() => goToStep("register")}>
                  创建账户
                </button>
              </p>
            </div>
          </section>
        ) : (
          <section className="auth-panel">
            <div className="auth-logo-slot auth-logo-slot-form" ref={slotRef}>
              <img ref={logoRef} src={BRAND_LOGO_URL} alt="" width={1070} height={630} decoding="async" />
            </div>

            <div className="auth-panel-body" key={step}>
              <header className="auth-panel-head">
                <h1>{title}</h1>
                <p>{subtitle}</p>
                {sessionHint && <p className="auth-session-hint" role="status">{sessionHint}</p>}
              </header>

              {step === "forgot" ? (
                <form className="auth-form" onSubmit={handleForgotSubmit} noValidate>
                  <div className="auth-field-block">
                    <div className="auth-field-group" data-invalid={invalid("forgotEmail") || invalid("forgotCode")}>
                      <div className="auth-field">
                        <label className="auth-field-label" htmlFor="auth-forgot-email">邮箱</label>
                        <input
                          id="auth-forgot-email"
                          ref={(el) => {
                            fieldRefs.current.forgotEmail = el;
                          }}
                          type="email"
                          value={forgotEmail}
                          onChange={(e) => {
                            setForgotEmail(e.target.value);
                            clearFieldError("forgotEmail");
                          }}
                          autoComplete="email"
                          aria-invalid={invalid("forgotEmail")}
                        />
                      </div>
                      <div className="auth-field has-action">
                        <label className="auth-field-label" htmlFor="auth-forgot-code">邮箱验证码</label>
                        <input
                          id="auth-forgot-code"
                          ref={(el) => {
                            fieldRefs.current.forgotCode = el;
                          }}
                          type="text"
                          value={forgotCode}
                          onChange={(e) => {
                            setForgotCode(e.target.value.replace(/\D/g, "").slice(0, 6));
                            clearFieldError("forgotCode");
                          }}
                          inputMode="numeric"
                          autoComplete="one-time-code"
                          aria-invalid={invalid("forgotCode")}
                        />
                        <button
                          type="button"
                          className="auth-field-action"
                          onClick={handleSendForgotCode}
                          disabled={forgotCodeBusy || forgotCodeCooldown > 0}
                        >
                          {forgotCodeBusy ? "发送中…" : forgotCodeCooldown > 0 ? `${forgotCodeCooldown}s 后重发` : "获取验证码"}
                        </button>
                      </div>
                    </div>
                    {fieldError && (invalid("forgotEmail") || invalid("forgotCode")) && (
                      <p className="auth-field-error" role="alert">{fieldError.message}</p>
                    )}
                  </div>

                  <div className="auth-field-block">
                    <div className="auth-field-group" data-invalid={invalid("forgotNew") || invalid("forgotConfirm")}>
                      <div className="auth-field">
                        <label className="auth-field-label" htmlFor="auth-forgot-new">新密码（至少 8 位）</label>
                        <input
                          id="auth-forgot-new"
                          ref={(el) => {
                            fieldRefs.current.forgotNew = el;
                          }}
                          type="password"
                          value={forgotNewPassword}
                          onChange={(e) => {
                            setForgotNewPassword(e.target.value);
                            clearFieldError("forgotNew");
                          }}
                          autoComplete="new-password"
                          aria-invalid={invalid("forgotNew")}
                        />
                      </div>
                      <div className="auth-field">
                        <label className="auth-field-label" htmlFor="auth-forgot-confirm">确认新密码</label>
                        <input
                          id="auth-forgot-confirm"
                          ref={(el) => {
                            fieldRefs.current.forgotConfirm = el;
                          }}
                          type="password"
                          value={forgotConfirmPassword}
                          onChange={(e) => {
                            setForgotConfirmPassword(e.target.value);
                            clearFieldError("forgotConfirm");
                          }}
                          autoComplete="new-password"
                          aria-invalid={invalid("forgotConfirm")}
                        />
                      </div>
                    </div>
                    {fieldError && (invalid("forgotNew") || invalid("forgotConfirm")) && (
                      <p className="auth-field-error" role="alert">{fieldError.message}</p>
                    )}
                  </div>

                  {error && <p className="auth-alert" role="alert">{error}</p>}
                  {notice && <p className="auth-notice" role="status">{notice}</p>}
                  <button className="auth-submit" type="submit" disabled={forgotBusy}>
                    {forgotBusy ? (
                      <>
                        <span className="auth-spinner" aria-hidden="true" />
                        处理中…
                      </>
                    ) : (
                      "重置密码"
                    )}
                  </button>
                </form>
              ) : (
                <form className="auth-form" onSubmit={handleSubmit} noValidate>
                  <div className="auth-field-block">
                    <div className="auth-field-group" data-invalid={invalid("identifier") || invalid("password") || invalid("email") || invalid("emailCode")}>
                      {isLogin ? (
                        <>
                          {accountField}
                          {passwordField}
                        </>
                      ) : (
                        <>
                          {accountField}
                          <div className="auth-field">
                            <label className="auth-field-label" htmlFor="auth-email">邮箱</label>
                            <input
                              id="auth-email"
                              ref={(el) => {
                                fieldRefs.current.email = el;
                              }}
                              type="email"
                              value={email}
                              onChange={(e) => {
                                setEmail(e.target.value);
                                clearFieldError("email");
                              }}
                              autoComplete="email"
                              aria-invalid={invalid("email")}
                            />
                          </div>
                          <div className="auth-field has-action">
                            <label className="auth-field-label" htmlFor="auth-email-code">邮箱验证码</label>
                            <input
                              id="auth-email-code"
                              ref={(el) => {
                                fieldRefs.current.emailCode = el;
                              }}
                              type="text"
                              value={emailCode}
                              onChange={(e) => {
                                setEmailCode(e.target.value.replace(/\D/g, "").slice(0, 6));
                                clearFieldError("emailCode");
                              }}
                              inputMode="numeric"
                              autoComplete="one-time-code"
                              aria-invalid={invalid("emailCode")}
                            />
                            <button
                              type="button"
                              className="auth-field-action"
                              onClick={handleSendEmailCode}
                              disabled={codeBusy || codeCooldown > 0}
                            >
                              {codeBusy ? "发送中…" : codeCooldown > 0 ? `${codeCooldown}s 后重发` : "获取验证码"}
                            </button>
                          </div>
                        </>
                      )}
                    </div>
                    {fieldError && (invalid("identifier") || invalid("password") || invalid("email") || invalid("emailCode")) && (
                      <p className="auth-field-error" role="alert">{fieldError.message}</p>
                    )}
                  </div>

                  {isLogin ? (
                    <div className="auth-field-block">
                      <div className="auth-row">
                        {privacyCheck}
                        <button
                          type="button"
                          className="auth-link auth-forgot"
                          onClick={() => goToStep("forgot")}
                        >
                          忘记密码
                          <span className="auth-forgot-arrow" aria-hidden="true">↗</span>
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="auth-field-block">
                      <div className="auth-field-group" data-invalid={invalid("password") || invalid("confirmPassword") || invalid("invitationCode")}>
                        {passwordField}
                        <div className="auth-field">
                          <label className="auth-field-label" htmlFor="auth-confirm">确认密码</label>
                          <input
                            id="auth-confirm"
                            ref={(el) => {
                              fieldRefs.current.confirmPassword = el;
                            }}
                            type="password"
                            value={confirmPassword}
                            onChange={(e) => {
                              setConfirmPassword(e.target.value);
                              clearFieldError("confirmPassword");
                            }}
                            autoComplete="new-password"
                            aria-invalid={invalid("confirmPassword")}
                          />
                        </div>
                        <div className="auth-field">
                          <label className="auth-field-label" htmlFor="auth-invite">邀请码（选填）</label>
                          <input
                            id="auth-invite"
                            ref={(el) => {
                              fieldRefs.current.invitationCode = el;
                            }}
                            type="text"
                            value={invitationCode}
                            onChange={(e) => setInvitationCode(e.target.value)}
                            autoComplete="off"
                            aria-invalid={invalid("invitationCode")}
                          />
                        </div>
                      </div>
                      {fieldError && (invalid("password") || invalid("confirmPassword") || invalid("invitationCode")) && (
                        <p className="auth-field-error" role="alert">{fieldError.message}</p>
                      )}
                    </div>
                  )}

                  {!isLogin && (
                    <div className="auth-field-block">
                      {privacyCheck}
                    </div>
                  )}

                  {error && <p className="auth-alert" role="alert">{error}</p>}
                  {forceLogin && (
                    <button type="button" className="auth-force" disabled={busy} onClick={() => doLogin(true)}>
                      {busy ? "处理中…" : "强制登录（将退出其他设备）"}
                    </button>
                  )}
                  {notice && <p className="auth-notice" role="status">{notice}</p>}

                  <button className="auth-submit" type="submit" disabled={busy || !agreed}>
                    {busy ? (
                      <>
                        <span className="auth-spinner" aria-hidden="true" />
                        {busyLabel}
                      </>
                    ) : (
                      submitLabel
                    )}
                  </button>
                </form>
              )}

              {isLogin && (
                <p className="auth-footer">
                  没有账号？
                  <button type="button" className="auth-link" onClick={() => goToStep("register")}>
                    创建账户
                  </button>
                </p>
              )}
              {step === "register" && (
                <p className="auth-footer">
                  已有账号？
                  <button type="button" className="auth-link" onClick={() => goToStep("login")}>
                    登录
                  </button>
                </p>
              )}
              {step === "forgot" && (
                <p className="auth-footer">
                  <button type="button" className="auth-link" onClick={() => goToStep("login")}>
                    ← 返回登录
                  </button>
                </p>
              )}
            </div>
          </section>
        )}
      </div>
    </main>
  );
}

export type { AccountInfo };
