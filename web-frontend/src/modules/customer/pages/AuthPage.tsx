import { useState } from "react";

type AuthPageProps = { onEnter: () => void };
type AuthMode = "login" | "register";

export function AuthPage({ onEnter }: AuthPageProps) {
  const [mode, setMode] = useState<AuthMode>("login");
  const isLogin = mode === "login";

  return (
    <main className="auth-screen">
      <section className="auth-brand-card">
        <span className="brand-mark brand-mark-large">智</span>
        <p className="eyebrow">SMART ECOMMERCE PLATFORM</p>
        <h1>智能电商平台</h1>
        <p>面向跨境电商团队的本地运营中台。此版本仅展示前端框架，不连接真实账户或后端接口。</p>
        <div className="auth-feature-list"><span>✓ 模块化工作流</span><span>✓ 本地运行时</span><span>✓ 团队协作预留</span></div>
      </section>
      <section className="auth-form-card">
        <div className="auth-tabs"><button className={isLogin ? "is-selected" : ""} onClick={() => setMode("login")}>登录</button><button className={!isLogin ? "is-selected" : ""} onClick={() => setMode("register")}>注册</button></div>
        <p className="eyebrow">{isLogin ? "WELCOME BACK" : "CREATE LOCAL PROFILE"}</p>
        <h2>{isLogin ? "登录工作台" : "创建演示账号"}</h2>
        <form onSubmit={(event) => { event.preventDefault(); onEnter(); }}>
          {!isLogin && <label>显示名称<input placeholder="例如：运营小组 A" /></label>}
          <label>账号<input type="text" placeholder="name@example.com" defaultValue={isLogin ? "demo@mainpg.local" : ""} /></label>
          <label>密码<input type="password" placeholder="输入任意内容即可演示" /></label>
          <button className="primary-button" type="submit">{isLogin ? "登录并进入工作台" : "注册并进入工作台"} →</button>
        </form>
        <p className="form-hint">演示模式：点击按钮即可进入主界面，不会提交或保存账号信息。</p>
      </section>
    </main>
  );
}
