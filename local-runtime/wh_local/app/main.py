from __future__ import annotations

import os

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
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
from ..data_collection.plugin_queue import DataCollectionPluginQueue
from ..data_collection.image_cache import PublicDailySelectionImageCache
from ..db import init_db
from ..modules.basic_settings.router import create_router as create_basic_settings_router
from ..modules.profit_activity import create_profit_activity_router, create_profit_activity_service
from ..modules.product_processing.api.router import create_product_processing_router
from ..modules.product_processing.domain.models import DailySelectionHandoffEnvelope
from ..modules.product_processing.infrastructure.assets import ProductProcessingAssets
from ..modules.product_processing.infrastructure.database import create_database
from ..modules.product_processing.infrastructure.repository import ProductProcessingRepository
from ..modules.product_processing.service import ProductProcessingService
from ..price_verification import (
    PriceVerificationRouteDependencies,
    register_price_verification_routes,
)
from ..price_verification.contracts import PriceVerificationActor
from ..session import (
    Actor,
    actor_from_authorization,
    daily_selection_actor_from_authorization,
)

def _price_verification_actor(
    actor: Actor = Depends(actor_from_authorization),
) -> PriceVerificationActor:
    """Bridge the local host actor to the price-verification workspace."""
    return PriceVerificationActor(actor_id=actor.id, workspace_id=actor.workspace_id)



def _provider_config(actor: DailySelectionActor) -> Mapping[str, Any]:
    """Resolve OneBound credentials from local global configuration."""
    config = default_config()
    return {
        "api_key": config.onebound_1688_api_key,
        "api_secret": config.onebound_1688_api_secret,
        "base_url": config.onebound_1688_base_url,
        "enabled": config.onebound_1688_enabled,
    }


def _provider_factory(config: Mapping[str, Any]) -> OneBound1688Provider:
    """Create the OneBound provider from resolved configuration."""
    return OneBound1688Provider(config)


def create_app(database_path: Path | None = None) -> FastAPI:
    config = default_config()
    db_path = database_path or config.database_path
    init_db(db_path)

    app = FastAPI(title="H Smart Ecommerce Local Runtime", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "database_path": str(db_path)}

    if os.environ.get("WH_LOCAL_ENABLE_DEMO_PAGE", "").strip().lower() in {"1", "true", "yes"}:

        @app.get("/dev/customer-login-demo", response_class=HTMLResponse)
        def customer_login_demo() -> str:
            return AUTH_FLOW_DEMO_HTML

        @app.get("/dev/auth/{page_path:path}", response_class=HTMLResponse)
        def customer_auth_flow_demo(page_path: str = "login") -> str:
            return AUTH_FLOW_DEMO_HTML

    remote_customer_auth = CustomerAuthClient(config.customer_auth_base_url)
    customer_auth = remote_customer_auth if remote_customer_auth.configured() else SQLiteCustomerAuthService(db_path)
    customer_sessions = LocalSessionService(SQLiteCustomerSessionStore(db_path))
    app.include_router(create_customer_router(customer_auth, customer_sessions))

    app.include_router(create_basic_settings_router(db_path))
    plugin_queue = DataCollectionPluginQueue(db_path)
    product_processing = _product_processing_service(db_path)
    app.include_router(create_product_processing_router(product_processing))
    _register_data_collection(app, db_path, plugin_queue, product_processing)
    _register_profit_activity(app, db_path)
    app.include_router(create_product_processing_router(product_processing), prefix="/api")

    # 核价及货源模块
    _register_price_verification(app, db_path, config.data_dir, plugin_queue)

    return app


def _register_data_collection(
    app: FastAPI,
    db_path: Path,
    plugin_queue: DataCollectionPluginQueue,
    product_processing: ProductProcessingService,
) -> None:
    """Register daily-selection routes with the host-owned adapters."""
    dependencies = DailySelectionRouteDependencies(
        resolve_actor=daily_selection_actor_from_authorization,
        provider_config_resolver=_provider_config,
        provider_factory=_provider_factory,
        database_path=db_path,
        plugin_queue=plugin_queue,
        plugin_draft_writer=product_processing,
        handoff_consumer=lambda handoffs: product_processing.consume_daily_selection_handoffs(
            [
                DailySelectionHandoffEnvelope.model_validate(
                    handoff.model_dump(mode="python")
                )
                for handoff in handoffs
            ]
        ),
        image_cache=PublicDailySelectionImageCache(),
    )
    register_daily_selection_routes(app.router, dependencies)


def _register_profit_activity(app: FastAPI, db_path: Path) -> None:
    """Register profit-activity routes against the shared runtime database."""
    service = create_profit_activity_service(db_path)
    app.include_router(create_profit_activity_router(service, db_path), prefix="/api")


def _product_processing_service(db_path: Path) -> ProductProcessingService:
    """Create the downstream owner once; data collection only sends handoffs to it."""
    database_url = f"sqlite:///{db_path.resolve().as_posix()}"
    return ProductProcessingService(
        ProductProcessingRepository(create_database(database_url)),
        ProductProcessingAssets(db_path.parent / "product-processing-assets"),
    )


def _register_price_verification(
    app: FastAPI,
    db_path: Path,
    data_dir: Path,
    plugin_queue: DataCollectionPluginQueue,
) -> None:
    """Register read-only price-verification routes with host-owned adapters."""
    dependencies = PriceVerificationRouteDependencies(
        resolve_actor=_price_verification_actor,
        database_path=db_path,
        output_root=data_dir / "price-verification",
        provider_config_resolver=_provider_config,
        provider_factory=_provider_factory,
        plugin_queue=plugin_queue,
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
          role: "operator",
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


AUTH_FLOW_DEMO_HTML = """
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>W-H 账号登录临时前端</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: #1d1d1f;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: #f5f5f7;
    }
    a { color: #06c; text-decoration: none; font-weight: 500; cursor: pointer; }
    a:hover { text-decoration: underline; }
    .shell { min-height: 100vh; display: grid; place-items: center; padding: 40px 20px; }
    .layout {
      width: min(980px, 100%);
      display: grid;
      grid-template-columns: .9fr 1fr;
      gap: 18px;
      align-items: stretch;
    }
    .brand, .panel {
      background: rgba(255, 255, 255, .96);
      border: 1px solid rgba(0, 0, 0, .08);
      border-radius: 28px;
      box-shadow: 0 18px 46px rgba(0, 0, 0, .08);
    }
    .brand {
      padding: 40px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 610px;
    }
    .panel { padding: 42px; }
    .badge {
      display: inline-flex;
      width: fit-content;
      padding: 7px 12px;
      border-radius: 999px;
      color: #1d1d1f;
      background: #f5f5f7;
      border: 1px solid rgba(0, 0, 0, .08);
      font-size: 13px;
      font-weight: 600;
      margin-bottom: 26px;
    }
    h1 {
      margin: 0 0 18px;
      font-size: clamp(40px, 5vw, 58px);
      letter-spacing: -0.055em;
      line-height: 1.04;
      font-weight: 700;
    }
    h2 { margin: 0 0 10px; font-size: 34px; letter-spacing: -0.045em; font-weight: 700; }
    p { color: #6e6e73; line-height: 1.65; font-size: 15px; }
    .steps { display: grid; gap: 10px; margin-top: 30px; }
    .step {
      display: flex; gap: 12px; align-items: center;
      padding: 12px 0;
      color: #515154;
      border-top: 1px solid rgba(0, 0, 0, .08);
    }
    .step b {
      display: grid; place-items: center; flex: 0 0 26px; height: 26px;
      border-radius: 999px; color: #fff; background: #1d1d1f;
      font-size: 13px;
      font-weight: 600;
    }
    label { display: block; margin: 18px 0 8px; color: #1d1d1f; font-size: 13px; font-weight: 600; }
    input, select {
      width: 100%; height: 52px; padding: 0 15px; border-radius: 12px;
      border: 1px solid #d2d2d7; outline: none; background: #fff; font-size: 17px;
      color: #1d1d1f;
    }
    input:focus, select:focus { border-color: #0071e3; box-shadow: 0 0 0 4px rgba(0, 113, 227, .14); }
    .row { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .actions { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 24px; align-items: center; }
    button {
      border: 0; border-radius: 999px; padding: 12px 22px; min-height: 44px;
      color: #fff; font-weight: 600; font-size: 15px; cursor: pointer;
      background: #0071e3;
      box-shadow: none;
    }
    button:hover { background: #0077ed; }
    button.secondary { color: #06c; background: transparent; padding-left: 0; padding-right: 0; }
    button.secondary:hover { background: transparent; text-decoration: underline; }
    button.danger { background: #ff3b30; }
    .status {
      margin-top: 22px; min-height: 48px; padding: 13px 15px; border-radius: 14px;
      white-space: pre-wrap; background: #f5f5f7; border: 1px solid rgba(0, 0, 0, .08); color: #515154;
      font-size: 14px;
    }
    .status.ok { border-color: #b7e4c7; background: #f1fbf4; color: #146c2e; }
    .status.err { border-color: #ffd0d2; background: #fff2f2; color: #b00020; }
    .kv { display: grid; gap: 10px; margin-top: 18px; }
    .kv div {
      display: flex; justify-content: space-between; gap: 16px;
      padding: 13px 0; color: #515154; border-bottom: 1px solid rgba(0, 0, 0, .08);
    }
    pre {
      max-height: 220px; overflow: auto; padding: 15px; border-radius: 14px;
      color: #f5f5f7; background: #1d1d1f; white-space: pre-wrap; word-break: break-word;
      font-size: 12px;
    }
    code { background: #f5f5f7; padding: 2px 6px; border-radius: 7px; color: #515154; }
    .hidden { display: none; }
    .topline { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 20px; }
    @media (max-width: 900px) {
      .shell { padding: 18px; }
      .layout { grid-template-columns: 1fr; }
      .brand { min-height: auto; padding: 30px; }
      .panel { padding: 30px; }
      .row { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <section class="layout">
      <aside class="brand">
        <div>
          <div class="badge">W-H 真实账号链路演示</div>
          <h1 id="brandTitle">账号登录系统</h1>
          <p>
            这是临时前端，但按真实用户流程拆成多个页面：
            注册、登录、忘记密码、重置密码、工作台首页、修改密码。
            页面调用工作台后端，再由后端调用远端账号服务。
          </p>
          <div class="steps">
            <div class="step"><b>1</b><span>注册成功后跳转登录页</span></div>
            <div class="step"><b>2</b><span>登录成功后跳转工作台首页</span></div>
            <div class="step"><b>3</b><span>忘记密码生成一次性凭证，再跳转重置密码页</span></div>
            <div class="step"><b>4</b><span>修改或重置密码后清空登录态，需要重新登录</span></div>
          </div>
        </div>
        <p>API 前缀：<code id="apiLabel"></code></p>
      </aside>

      <section class="panel">
        <div id="viewLogin">
          <h2>登录</h2>
          <p>输入账号密码，成功后进入工作台首页。</p>
          <label>用户名 / 邮箱</label>
          <input id="loginIdentifier" autocomplete="username" />
          <label>密码</label>
          <input id="loginPassword" type="password" autocomplete="current-password" />
          <div class="actions">
            <button onclick="login()">登录</button>
            <a id="toRegister">没有账号？注册</a>
            <a id="toForgot">忘记密码？</a>
          </div>
        </div>

        <div id="viewRegister" class="hidden">
          <h2>注册账号</h2>
          <p>创建账号后自动跳回登录页。</p>
          <div class="row">
            <div><label>用户名</label><input id="regUsername" autocomplete="username" /></div>
            <div><label>邮箱</label><input id="regEmail" autocomplete="email" /></div>
          </div>
          <label>密码</label>
          <input id="regPassword" type="password" autocomplete="new-password" />
          <label>角色</label>
          <select id="regRole"><option value="operator">operator（普通用户，默认）</option></select>
          <div class="actions">
            <button onclick="registerAccount()">注册</button>
            <a id="toLoginFromRegister">已有账号？去登录</a>
          </div>
        </div>

        <div id="viewForgot" class="hidden">
          <h2>忘记密码</h2>
          <p>开发演示阶段会直接返回 reset token；正式阶段这里应改为发送邮箱链接。</p>
          <label>用户名 / 邮箱</label>
          <input id="forgotIdentifier" autocomplete="username" />
          <div class="actions">
            <button onclick="forgotPassword()">获取重置凭证</button>
            <a id="toLoginFromForgot">想起来了？去登录</a>
          </div>
        </div>

        <div id="viewReset" class="hidden">
          <h2>重置密码</h2>
          <p>使用一次性 reset token 设置新密码。成功后跳回登录页。</p>
          <label>Reset Token</label>
          <input id="resetToken" />
          <label>新密码</label>
          <input id="resetNewPassword" type="password" autocomplete="new-password" />
          <div class="actions">
            <button onclick="resetPassword()">重置密码</button>
            <a id="toLoginFromReset">返回登录</a>
          </div>
        </div>

        <div id="viewDashboard" class="hidden">
          <div class="topline">
            <div>
              <h2>工作台首页</h2>
              <p>这里模拟登录后进入系统。后续可接左侧栏各业务模块。</p>
            </div>
            <button class="danger" onclick="logout()">退出</button>
          </div>
          <div class="kv">
            <div><strong>本地 token</strong><span id="dashLocalToken">-</span></div>
            <div><strong>远端 token</strong><span id="dashRemoteToken">-</span></div>
            <div><strong>用户</strong><span id="dashUser">-</span></div>
            <div><strong>角色</strong><span id="dashRole">-</span></div>
            <div><strong>工作区</strong><span id="dashWorkspace">-</span></div>
          </div>
          <div class="actions">
            <button onclick="me()">刷新登录态</button>
            <button class="secondary" onclick="go('/dev/auth/change-password')">修改密码</button>
          </div>
        </div>

        <div id="viewChange" class="hidden">
          <h2>修改密码</h2>
          <p>已登录用户知道旧密码时修改密码。成功后退出登录并返回登录页。</p>
          <label>用户名 / 邮箱</label>
          <input id="changeIdentifier" autocomplete="username" readonly />
          <label>当前密码</label>
          <input id="changeOldPassword" type="password" autocomplete="current-password" />
          <label>新密码</label>
          <input id="changeNewPassword" type="password" autocomplete="new-password" />
          <div class="actions">
            <button onclick="changePassword()">确认修改</button>
            <button class="secondary" onclick="go('/dev/auth/dashboard')">返回首页</button>
          </div>
        </div>

        <div id="status" class="status">等待操作。</div>
        <pre id="output">{}</pre>
      </section>
    </section>
  </main>

  <script>
    const apiBase = (new URLSearchParams(location.search).get("api") || "").replace(/\\/$/, "");
    const apiQuery = apiBase ? "?api=" + encodeURIComponent(apiBase) : "";
    let localToken = sessionStorage.getItem("wh_demo_token") || "";
    let account = JSON.parse(sessionStorage.getItem("wh_demo_account") || "{}");

    document.getElementById("apiLabel").textContent = apiBase || "同源 /api/customer";

    function maskToken(value) {
      if (!value) return "";
      return value.slice(0, 9) + "***" + value.slice(-6);
    }

    function go(path) {
      history.pushState({}, "", path + apiQuery);
      render();
    }

    function replaceTo(path) {
      history.replaceState({}, "", path + apiQuery);
      render();
    }

    function setFlash(message, type = "") {
      sessionStorage.setItem("wh_demo_flash", JSON.stringify({message, type}));
    }

    function resetFeedback() {
      const el = document.getElementById("status");
      el.className = "status";
      el.textContent = "等待操作。";
      document.getElementById("output").textContent = "{}";
    }

    function consumeFlash() {
      const raw = sessionStorage.getItem("wh_demo_flash");
      if (!raw) return false;
      sessionStorage.removeItem("wh_demo_flash");
      try {
        const data = JSON.parse(raw);
        setStatus(data.message || "", data.type || "");
        return true;
      } catch (_) {
        return false;
      }
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

    function saveSession(data) {
      localToken = data.token || "";
      account = data.account || {};
      sessionStorage.setItem("wh_demo_token", localToken);
      sessionStorage.setItem("wh_demo_account", JSON.stringify(account));
    }

    function clearSession() {
      localToken = "";
      account = {};
      sessionStorage.removeItem("wh_demo_token");
      sessionStorage.removeItem("wh_demo_account");
    }

    function fillDashboard() {
      const remoteToken = account.remote_token || (account.raw && account.raw.token) || "";
      document.getElementById("dashLocalToken").textContent = localToken ? maskToken(localToken) : "-";
      document.getElementById("dashRemoteToken").textContent = remoteToken ? maskToken(remoteToken) : "-";
      document.getElementById("dashUser").textContent = account.username || "-";
      document.getElementById("dashRole").textContent = account.role || "-";
      document.getElementById("dashWorkspace").textContent = account.workspace_code || "-";
    }

    function render() {
      resetFeedback();
      const path = location.pathname;
      const views = ["viewLogin", "viewRegister", "viewForgot", "viewReset", "viewDashboard", "viewChange"];
      views.forEach(id => document.getElementById(id).classList.add("hidden"));
      const map = {
        "/dev/auth/register": "viewRegister",
        "/dev/auth/forgot-password": "viewForgot",
        "/dev/auth/reset-password": "viewReset",
        "/dev/auth/dashboard": "viewDashboard",
        "/dev/auth/change-password": "viewChange",
      };
      let view = map[path] || "viewLogin";
      if ((view === "viewDashboard" || view === "viewChange") && !localToken) {
        setFlash("请先登录后再访问该页面。", "err");
        history.replaceState({}, "", "/dev/auth/login" + apiQuery);
        view = "viewLogin";
      }
      document.getElementById(view).classList.remove("hidden");
      document.getElementById("brandTitle").textContent = {
        viewLogin: "欢迎回来",
        viewRegister: "创建账号",
        viewForgot: "找回密码",
        viewReset: "设置新密码",
        viewDashboard: "进入工作台",
        viewChange: "账号安全",
      }[view];
      if (view === "viewDashboard") fillDashboard();
      if (view === "viewChange") {
        document.getElementById("changeIdentifier").value = account.username || account.email || "";
      }
      if (view === "viewReset") {
        document.getElementById("resetToken").value = sessionStorage.getItem("wh_demo_reset_token") || document.getElementById("resetToken").value;
      }
      consumeFlash();
    }

    async function registerAccount() {
      try {
        const username = document.getElementById("regUsername").value.trim();
        const email = document.getElementById("regEmail").value.trim();
        const password = document.getElementById("regPassword").value;
        const role = document.getElementById("regRole").value;
        if (!username || !password) throw new Error("请输入用户名和密码");
        await request("POST", "/api/customer/register", {
          username, email, password, role,
          workspace_code: "wh_demo",
          workspace_name: "真实服务器演示工作区"
        });
        setFlash("注册成功，已跳转到登录页。", "ok");
        document.getElementById("loginIdentifier").value = username;
        go("/dev/auth/login");
      } catch (err) {
        setStatus("注册失败：" + err.message, "err");
      }
    }

    async function login() {
      try {
        const identifier = document.getElementById("loginIdentifier").value.trim();
        const password = document.getElementById("loginPassword").value;
        if (!identifier || !password) throw new Error("请输入账号和密码");
        const payload = identifier.includes("@") ? {email: identifier, password} : {username: identifier, password};
        const data = await request("POST", "/api/customer/login", payload);
        saveSession(data);
        setFlash("登录成功，已进入工作台首页。", "ok");
        go("/dev/auth/dashboard");
      } catch (err) {
        setStatus("登录失败：" + err.message, "err");
      }
    }

    async function forgotPassword() {
      try {
        const identifier = document.getElementById("forgotIdentifier").value.trim();
        if (!identifier) throw new Error("请输入账号或邮箱");
        const payload = identifier.includes("@") ? {email: identifier} : {username: identifier};
        const data = await request("POST", "/api/customer/forgot-password", payload);
        const resetToken = data.raw && data.raw.raw && data.raw.raw.reset_token || data.raw && data.raw.reset_token || "";
        if (resetToken) {
          sessionStorage.setItem("wh_demo_reset_token", resetToken);
          document.getElementById("resetToken").value = resetToken;
          setFlash("已生成一次性 reset token，跳转到重置密码页。正式阶段应改为邮件链接。", "ok");
        } else {
          setFlash("如果账号存在，系统已生成重置凭证。", "ok");
        }
        go("/dev/auth/reset-password");
      } catch (err) {
        setStatus("忘记密码失败：" + err.message, "err");
      }
    }

    async function resetPassword() {
      try {
        const resetToken = document.getElementById("resetToken").value.trim();
        const newPassword = document.getElementById("resetNewPassword").value;
        if (!resetToken || !newPassword) throw new Error("请输入 reset token 和新密码");
        await request("POST", "/api/customer/reset-password", {reset_token: resetToken, new_password: newPassword});
        clearSession();
        sessionStorage.removeItem("wh_demo_reset_token");
        setFlash("密码重置成功，已跳转登录页，请用新密码登录。", "ok");
        go("/dev/auth/login");
      } catch (err) {
        setStatus("重置密码失败：" + err.message, "err");
      }
    }

    async function changePassword() {
      try {
        if (!localToken) throw new Error("请先登录");
        const identifier = account.username || account.email || document.getElementById("changeIdentifier").value.trim();
        const currentPassword = document.getElementById("changeOldPassword").value;
        const newPassword = document.getElementById("changeNewPassword").value;
        if (!identifier || !currentPassword || !newPassword) throw new Error("请输入当前密码和新密码");
        const payload = identifier.includes("@")
          ? {email: identifier, current_password: currentPassword, new_password: newPassword}
          : {username: identifier, current_password: currentPassword, new_password: newPassword};
        await request("POST", "/api/customer/change-password", payload);
        clearSession();
        setFlash("修改密码成功，旧登录态已失效，请重新登录。", "ok");
        go("/dev/auth/login");
      } catch (err) {
        setStatus("修改密码失败：" + err.message, "err");
      }
    }

    async function me() {
      try {
        if (!localToken) throw new Error("请先登录");
        const data = await request("GET", "/api/customer/me");
        setStatus("登录态有效。", "ok");
        if (data.username) account = {...account, ...data};
        fillDashboard();
      } catch (err) {
        setStatus("登录态验证失败：" + err.message, "err");
      }
    }

    async function logout() {
      try {
        if (localToken) await request("POST", "/api/customer/logout");
      } catch (_) {
      } finally {
        clearSession();
        setFlash("已退出登录，跳转回登录页。", "ok");
        go("/dev/auth/login");
      }
    }

    document.getElementById("toRegister").onclick = () => go("/dev/auth/register");
    document.getElementById("toForgot").onclick = () => go("/dev/auth/forgot-password");
    document.getElementById("toLoginFromRegister").onclick = () => go("/dev/auth/login");
    document.getElementById("toLoginFromForgot").onclick = () => go("/dev/auth/login");
    document.getElementById("toLoginFromReset").onclick = () => go("/dev/auth/login");
    window.onpopstate = render;

    const savedReset = sessionStorage.getItem("wh_demo_reset_token") || "";
    if (savedReset) document.getElementById("resetToken").value = savedReset;
    if (account.username) {
      document.getElementById("changeIdentifier").value = account.username;
      document.getElementById("loginIdentifier").value = account.username;
    }
    render();
  </script>
</body>
</html>
"""
