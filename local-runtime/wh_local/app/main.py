from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.responses import HTMLResponse

from ..config import default_config
from ..customer.auth_service import SQLiteCustomerAuthService
from ..customer.db_store import SQLiteCustomerSessionStore
from ..customer.local_session import LocalSessionService
from ..customer.remote_client import CustomerAuthClient
from ..customer.routes import create_customer_router
from ..data_collection import (
    DailySelectionActor,
    DailySelectionRouteDependencies,
    register_daily_selection_routes,
)
from ..data_collection.provider import OneBound1688Provider
from ..db import init_db
from ..modules.basic_settings.router import create_router as create_basic_settings_router
from ..price_verification import (
    PriceVerificationRouteDependencies,
    register_price_verification_routes,
)
from ..price_verification.contracts import PriceVerificationActor
from ..session import Actor, actor_from_authorization, daily_selection_actor_from_authorization

def _price_verification_actor(
    actor: Actor = Depends(actor_from_authorization),
) -> PriceVerificationActor:
    """Bridge the local host actor to the price-verification workspace."""
    return PriceVerificationActor(actor_id=actor.id, workspace_id=actor.workspace_id)



def _provider_config(actor: DailySelectionActor) -> Mapping[str, Any]:
    """Resolve OneBound 1688 credentials from environment variables."""
    api_key = os.environ.get("DAILY_SELECTION_ONEBOUND_API_KEY", "")
    api_secret = os.environ.get("DAILY_SELECTION_ONEBOUND_API_SECRET", "")
    base_url = os.environ.get(
        "DAILY_SELECTION_ONEBOUND_BASE_URL",
        "https://api.onebound.cn/1688/api_call.php",
    )
    enabled = os.environ.get("DAILY_SELECTION_ONEBOUND_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    return {
        "api_key": api_key,
        "api_secret": api_secret,
        "base_url": base_url,
        "enabled": enabled,
    }


def _provider_factory(config: Mapping[str, Any]) -> OneBound1688Provider:
    """Create the OneBound provider from resolved configuration."""
    return OneBound1688Provider(config)


def create_app(database_path: Path | None = None) -> FastAPI:
    config = default_config()
    db_path = database_path or config.database_path
    init_db(db_path)

    app = FastAPI(title="H Smart Ecommerce Local Runtime", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "database_path": str(db_path)}

    if os.environ.get("WH_LOCAL_ENABLE_DEMO_PAGE", "").strip().lower() in {"1", "true", "yes"}:

        @app.get("/dev/customer-login-demo", response_class=HTMLResponse)
        def customer_login_demo() -> str:
            return CUSTOMER_LOGIN_DEMO_HTML

    remote_customer_auth = CustomerAuthClient(config.customer_auth_base_url)
    customer_auth = remote_customer_auth if remote_customer_auth.configured() else SQLiteCustomerAuthService(db_path)
    customer_sessions = LocalSessionService(SQLiteCustomerSessionStore(db_path))
    app.include_router(create_customer_router(customer_auth, customer_sessions))

    app.include_router(create_basic_settings_router(db_path))
    _register_data_collection(app, db_path)

    # 核价及货源模块
    _register_price_verification(app, db_path, config.data_dir)

    return app


def _register_data_collection(app: FastAPI, db_path: Path) -> None:
    """Register daily-selection routes with the host-owned adapters."""
    dependencies = DailySelectionRouteDependencies(
        resolve_actor=daily_selection_actor_from_authorization,
        provider_config_resolver=_provider_config,
        provider_factory=_provider_factory,
        database_path=db_path,
    )
    register_daily_selection_routes(app.router, dependencies)

def _register_price_verification(app: FastAPI, db_path: Path, data_dir: Path) -> None:
    """Register read-only price-verification routes with host-owned adapters."""
    dependencies = PriceVerificationRouteDependencies(
        resolve_actor=_price_verification_actor,
        database_path=db_path,
        output_root=data_dir / "price-verification",
        provider_config_resolver=_provider_config,
        provider_factory=_provider_factory,
    )
    register_price_verification_routes(app.router, dependencies)



app = create_app()


CUSTOMER_LOGIN_DEMO_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>W-H 登录链路临时演示页</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      padding: 42px 18px;
      color: #08213f;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at 12% 12%, rgba(40, 222, 195, .22), transparent 28rem),
        radial-gradient(circle at 84% 16%, rgba(21, 151, 255, .18), transparent 24rem),
        #eaf7ff;
    }
    .wrap { max-width: 1060px; margin: 0 auto; }
    .hero, .card {
      background: rgba(255, 255, 255, .92);
      border: 1px solid #bfe8ff;
      border-radius: 24px;
      box-shadow: 0 24px 60px rgba(38, 92, 128, .16);
    }
    .hero { padding: 34px; margin-bottom: 22px; }
    h1 { margin: 0 0 12px; font-size: clamp(32px, 5vw, 52px); letter-spacing: -0.04em; }
    h2 { margin: 0 0 18px; font-size: 24px; }
    p { line-height: 1.7; color: #57708d; }
    .badge {
      display: inline-flex;
      padding: 9px 14px;
      border-radius: 999px;
      color: #057052;
      background: #dcfce7;
      font-weight: 800;
      margin-bottom: 16px;
    }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 22px; }
    .card { padding: 26px; }
    label { display: block; font-weight: 800; color: #31506d; margin: 14px 0 8px; }
    input {
      width: 100%;
      height: 48px;
      border: 1px solid #b7d7ed;
      border-radius: 14px;
      padding: 0 14px;
      font-size: 16px;
      outline: none;
      background: #f8fcff;
    }
    input:focus { border-color: #1597ff; box-shadow: 0 0 0 4px rgba(21, 151, 255, .12); }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    button {
      border: 0;
      border-radius: 999px;
      padding: 13px 18px;
      color: white;
      font-weight: 900;
      font-size: 15px;
      cursor: pointer;
      background: linear-gradient(135deg, #1597ff, #28dec3);
      box-shadow: 0 12px 28px rgba(21, 151, 255, .22);
    }
    button.secondary { background: #eaf3fb; color: #31506d; box-shadow: none; }
    button.danger { background: linear-gradient(135deg, #ff5c70, #ff8a8a); }
    .actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 20px; }
    .status {
      margin-top: 18px;
      border-radius: 18px;
      padding: 14px 16px;
      background: #f1f9ff;
      border: 1px solid #d3ecff;
      color: #31506d;
      min-height: 54px;
      white-space: pre-wrap;
    }
    .status.ok { border-color: #bbf7d0; background: #f0fdf4; color: #166534; }
    .status.err { border-color: #fecdd3; background: #fff1f2; color: #be123c; }
    pre {
      max-height: 430px;
      overflow: auto;
      border-radius: 18px;
      padding: 18px;
      color: #d9efff;
      background: #071426;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .kv { display: grid; gap: 10px; margin-top: 12px; }
    .kv div {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      padding: 10px 12px;
      border-radius: 12px;
      background: #f6fbff;
      color: #31506d;
    }
    code { background: #e8f4ff; padding: 2px 6px; border-radius: 8px; }
    @media (max-width: 820px) { .grid, .row { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <div class="badge">真实服务器链路演示</div>
      <h1>Customer 登录验证页</h1>
      <p>
        这个临时页面调用当前工作台后端 <code>/api/customer/login</code>。
        后端会继续调用远端账号服务 <code>https://workbench.haocoming.top/auth-api</code>，
        成功后返回本地业务 token：<code>wh_local_***</code>。页面会自动打码 token。
        如通过 Nginx 前缀代理演示，可使用 <code>?api=/local-api</code>。
      </p>
    </section>

    <section class="grid">
      <div class="card">
        <h2>1. 注册 / 登录 / 密码</h2>
        <div class="row">
          <div>
            <label for="username">username</label>
            <input id="username" placeholder="输入演示账号" autocomplete="username" />
          </div>
          <div>
            <label for="password">password</label>
            <input id="password" placeholder="登录密码 / 当前密码" type="password" autocomplete="current-password" />
          </div>
          <div>
            <label for="email">email</label>
            <input id="email" placeholder="注册或找回密码邮箱" autocomplete="email" />
          </div>
          <div>
            <label for="newPassword">new password</label>
            <input id="newPassword" placeholder="修改/重置后的新密码" type="password" autocomplete="new-password" />
          </div>
          <div style="grid-column: 1 / -1;">
            <label for="resetToken">reset token</label>
            <input id="resetToken" placeholder="点击忘记密码后自动填入；未来会改成邮箱链接" />
          </div>
        </div>
        <div class="actions">
          <button class="secondary" onclick="registerAccount()">注册</button>
          <button onclick="login()">登录</button>
          <button class="secondary" onclick="forgotPassword()">忘记密码</button>
          <button class="secondary" onclick="resetPassword()">重置密码</button>
          <button class="secondary" onclick="changePassword()">修改密码</button>
          <button class="secondary" onclick="me()">验证 me</button>
          <button class="danger" onclick="logout()">退出登录</button>
        </div>
        <div id="status" class="status">等待操作。请手动输入账号密码，页面不会内置真实密码。</div>
        <div class="kv">
          <div><strong>本地 token</strong><span id="localToken">未登录</span></div>
          <div><strong>远端 token</strong><span id="remoteToken">未登录</span></div>
          <div><strong>角色</strong><span id="role">-</span></div>
          <div><strong>工作区</strong><span id="workspace">-</span></div>
        </div>
      </div>

      <div class="card">
        <h2>2. 接口输出</h2>
        <pre id="output">{}</pre>
      </div>
    </section>
  </main>

  <script>
    const apiBase = (new URLSearchParams(location.search).get("api") || "").replace(/\\/$/, "");
    let localToken = sessionStorage.getItem("wh_demo_token") || "";

    function maskToken(value) {
      if (!value) return "";
      return value.slice(0, 9) + "***" + value.slice(-6);
    }

    function setStatus(message, type = "") {
      const el = document.getElementById("status");
      el.className = "status " + type;
      el.textContent = message;
    }

    function showOutput(method, path, status, body) {
      const safe = JSON.parse(JSON.stringify(body || {}));
      if (safe.token) safe.token = maskToken(safe.token);
      if (safe.reset_token) safe.reset_token = maskToken(safe.reset_token);
      if (safe.raw && safe.raw.reset_token) safe.raw.reset_token = maskToken(safe.raw.reset_token);
      if (safe.raw && safe.raw.raw && safe.raw.raw.reset_token) safe.raw.raw.reset_token = maskToken(safe.raw.raw.reset_token);
      if (safe.account && safe.account.remote_token) safe.account.remote_token = maskToken(safe.account.remote_token);
      if (safe.account && safe.account.raw && safe.account.raw.token) safe.account.raw.token = maskToken(safe.account.raw.token);
      document.getElementById("output").textContent = method + " " + path + "\\nHTTP " + status + "\\n\\n" + JSON.stringify(safe, null, 2);
    }

    function updateSummary(body) {
      const account = body.account || body || {};
      const remoteToken = account.remote_token || (account.raw && account.raw.token) || "";
      document.getElementById("localToken").textContent = localToken ? maskToken(localToken) : "未登录";
      document.getElementById("remoteToken").textContent = remoteToken ? maskToken(remoteToken) : "无";
      document.getElementById("role").textContent = account.role || body.role || "-";
      document.getElementById("workspace").textContent = account.workspace_code || body.workspace_code || "-";
    }

    async function request(method, path, body) {
      const headers = {"Content-Type": "application/json"};
      if (localToken) headers.Authorization = "Bearer " + localToken;
      const url = apiBase + path;
      const response = await fetch(url, {method, headers, body: body ? JSON.stringify(body) : undefined});
      let data = {};
      try { data = await response.json(); } catch (_) { data = {detail: await response.text()}; }
      showOutput(method, url, response.status, data);
      if (!response.ok) throw new Error(data.detail || data.message || "请求失败");
      return data;
    }

    async function login() {
      try {
        const username = document.getElementById("username").value.trim();
        const password = document.getElementById("password").value;
        if (!username || !password) throw new Error("请输入账号和密码");
        const data = await request("POST", "/api/customer/login", {username, password});
        localToken = data.token || "";
        sessionStorage.setItem("wh_demo_token", localToken);
        updateSummary(data);
        setStatus("登录成功：工作台已返回 wh_local 本地业务 token。", "ok");
      } catch (err) {
        setStatus("登录失败：" + err.message, "err");
      }
    }

    async function registerAccount() {
      try {
        const username = document.getElementById("username").value.trim();
        const email = document.getElementById("email").value.trim();
        const password = document.getElementById("password").value;
        if (!username || !password) throw new Error("请输入账号和密码");
        const data = await request("POST", "/api/customer/register", {
          username,
          email,
          password,
          role: "admin",
          workspace_code: "wh_demo",
          workspace_name: "真实服务器演示工作区"
        });
        setStatus("注册成功：现在可以用这个账号登录。", "ok");
        updateSummary(data.raw || {});
      } catch (err) {
        setStatus("注册失败：" + err.message, "err");
      }
    }

    async function forgotPassword() {
      try {
        const username = document.getElementById("username").value.trim();
        const email = document.getElementById("email").value.trim();
        if (!username && !email) throw new Error("请输入账号或邮箱");
        const data = await request("POST", "/api/customer/forgot-password", {username, email});
        const resetToken = data.raw && data.raw.raw && data.raw.raw.reset_token || data.raw && data.raw.reset_token || "";
        if (resetToken) {
          document.getElementById("resetToken").value = resetToken;
          setStatus("已生成一次性 reset token，并自动填入。开发阶段直接展示；正式阶段会发到邮箱。", "ok");
        } else {
          setStatus("如果账号存在，系统已生成重置凭证。", "ok");
        }
      } catch (err) {
        setStatus("忘记密码请求失败：" + err.message, "err");
      }
    }

    async function resetPassword() {
      try {
        const resetToken = document.getElementById("resetToken").value.trim();
        const newPassword = document.getElementById("newPassword").value;
        if (!resetToken || !newPassword) throw new Error("请输入 reset token 和新密码");
        const data = await request("POST", "/api/customer/reset-password", {reset_token: resetToken, new_password: newPassword});
        localToken = "";
        sessionStorage.removeItem("wh_demo_token");
        updateSummary(data);
        setStatus("密码重置成功：旧登录态已失效，请用新密码重新登录。", "ok");
      } catch (err) {
        setStatus("重置密码失败：" + err.message, "err");
      }
    }

    async function changePassword() {
      try {
        const username = document.getElementById("username").value.trim();
        const currentPassword = document.getElementById("password").value;
        const newPassword = document.getElementById("newPassword").value;
        if (!username || !currentPassword || !newPassword) throw new Error("请输入账号、当前密码和新密码");
        const data = await request("POST", "/api/customer/change-password", {
          username,
          current_password: currentPassword,
          new_password: newPassword
        });
        localToken = "";
        sessionStorage.removeItem("wh_demo_token");
        updateSummary(data);
        setStatus("修改密码成功：旧登录态已失效，请用新密码重新登录。", "ok");
      } catch (err) {
        setStatus("修改密码失败：" + err.message, "err");
      }
    }

    async function me() {
      try {
        if (!localToken) throw new Error("请先登录");
        const data = await request("GET", "/api/customer/me");
        updateSummary(data);
        setStatus("me 查询成功：token 有效。", "ok");
      } catch (err) {
        setStatus("me 查询失败：" + err.message, "err");
      }
    }

    async function logout() {
      try {
        if (!localToken) throw new Error("当前没有 token");
        const data = await request("POST", "/api/customer/logout");
        localToken = "";
        sessionStorage.removeItem("wh_demo_token");
        updateSummary(data);
        setStatus("已退出登录：本地 token 已失效。", "ok");
      } catch (err) {
        setStatus("退出失败：" + err.message, "err");
      }
    }

    document.getElementById("localToken").textContent = localToken ? maskToken(localToken) : "未登录";
  </script>
</body>
</html>
"""
