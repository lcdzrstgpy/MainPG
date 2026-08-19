# 远端 AI 积分计费选择性移植 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从远端提交 `9f762bc` 按函数和代码块移植服务端权威 AI 积分冻结、网关调用和结算闭环，同时保留所有非计费本地实现。

**Architecture:** 账号服务端是钱包、用量记录和上游 AI 密钥的唯一权威；本地产品处理只携带远端会话令牌，按商品预留用量并通过服务端网关执行 AI。移植采用当前本地文件为基准的手工补丁，不 merge `upstream/dev`，不整体 cherry-pick `9f762bc`。

**Tech Stack:** Python 3.12、FastAPI、SQLite、requests、ContextVar、pytest、FastAPI TestClient

## Global Constraints

- 只从 `9f762bc` 移植后端集中式积分计费及其必要 AI 网关接线。
- 不执行整个 `dev` 分支 merge，也不整体 cherry-pick `9f762bc`。
- 所有冲突和非计费行为以当前本地分支 `codex/product-optimization-20260814` 为准。
- 排除 OCR 放行、图片质量门、图片解析、尺寸判定、下载、拆图策略和其他非计费增强。
- 排除 UI、采集进度、工作台布局、主题、导航及 `upstream/dev` 的其他提交。
- 本地工作台不得持有上游 AI 密钥，不得直接修改服务端钱包余额。
- 余额不足返回 402；缺少远端计费会话或服务配置返回 503。
- 用量记录必须校验账号归属、功能键和 `reserved` 状态。
- 成功结算和失败解锁保持幂等；部分预留失败时释放已经成功冻结的积分。
- 当前未提交文件不得修改、暂存或覆盖，尤其是 `web-frontend/src/app/**`、`basic_settings`、`PersonalCenterPage.tsx`、基础设置回归测试和 `local-runtime/wh_local/workbench.sqlite3`。
- 不新增运行时依赖或前端改动。

---

### Task 1: 服务端权威用量 API、远端客户端和并发冻结

**Files:**
- Modify: `local-runtime/wh_local/customer/auth_server.py:1-130,445-650`
- Modify: `local-runtime/wh_local/customer/remote_client.py:60-105`
- Modify: `local-runtime/wh_local/customer/routes.py:1-145`
- Modify: `local-runtime/wh_local/db.py:905-920`
- Modify: `local-runtime/tests/test_customer_billing.py`
- Modify: `local-runtime/tests/test_billing_ai_usage.py`

**Interfaces:**
- Consumes: `reserve_ai_usage`, `settle_ai_usage_success`, `settle_ai_usage_failure`, `_required_account`, `Actor`, `CustomerAuthClient._post`, `transaction`.
- Produces: `CustomerAuthClient.reserve_ai_usage(remote_token, payload)`, `settle_ai_usage_success(remote_token, usage_id, payload)`, `settle_ai_usage_failure(remote_token, usage_id, payload)`; authenticated usage endpoints; remote-only local billing summary; `BEGIN IMMEDIATE` transaction semantics.

- [ ] **Step 1: Add failing authenticated usage API tests**

Extend `test_customer_billing.py` using its existing `_register_and_login` helper. After login, obtain the account id and grant test balance directly on the server database:

```python
def _grant_points(db_path: Path, points: int = 1000) -> str:
    with transaction(db_path) as conn:
        account = conn.execute(
            "SELECT account_id, workspace_id FROM auth_accounts WHERE username = 'billing_user'"
        ).fetchone()
        conn.execute(
            """
            INSERT INTO billing_wallets (account_id, workspace_id, points_balance)
            VALUES (?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET points_balance = excluded.points_balance
            """,
            (account["account_id"], account["workspace_id"], points),
        )
    return str(account["account_id"])


def test_ai_usage_api_reserves_settles_and_reuses_idempotency(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "auth.sqlite3"
    monkeypatch.setenv("WH_EMAIL_CODE_SECRET", _EMAIL_CODE_SECRET)
    monkeypatch.setattr(
        "wh_local.customer.auth_server.TencentCloudSESEmailSender.from_env",
        lambda: object(),
    )
    client = TestClient(create_auth_app(db_path))
    token = _register_and_login(client, db_path)
    account_id = _grant_points(db_path)
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "feature_key": "product_processing.text",
        "idempotency_key": "product-processing-api-test-0001",
        "source_ref": "test:item",
        "metadata": {"task_id": 1, "api_key": "must-not-persist"},
    }

    first = client.post("/api/customer/billing/usage/reserve", json=payload, headers=headers)
    repeated = client.post("/api/customer/billing/usage/reserve", json=payload, headers=headers)
    assert first.status_code == 200
    assert repeated.json()["usage"]["usage_id"] == first.json()["usage"]["usage_id"]

    usage_id = first.json()["usage"]["usage_id"]
    settled = client.post(
        f"/api/customer/billing/usage/{usage_id}/succeed",
        json={"metadata": {"task_id": 1}},
        headers=headers,
    )
    assert settled.status_code == 200
    assert settled.json()["usage"]["status"] == "succeeded"

    with transaction(db_path) as conn:
        usage = conn.execute(
            "SELECT account_id, metadata_json FROM billing_ai_usage_events WHERE usage_id = ?",
            (usage_id,),
        ).fetchone()
    assert usage["account_id"] == account_id
    assert "api_key" not in usage["metadata_json"]
```

Add tests for unsupported feature (400), missing/short idempotency key (400), other-account settlement (404), and failure settlement returning the frozen points.

- [ ] **Step 2: Add a failing concurrent overspend regression**

Extend `test_billing_ai_usage.py`:

```python
def test_concurrent_reservations_cannot_overspend_wallet(tmp_path: Path) -> None:
    database_path = tmp_path / "billing.sqlite3"
    init_db(database_path)
    actor = Actor(id="concurrent-user", username="concurrent-user", role="operator")
    with transaction(database_path) as conn:
        conn.execute("INSERT INTO auth_accounts (account_id, username) VALUES (?, ?)", (actor.id, actor.username))
        conn.execute(
            "INSERT INTO billing_wallets (account_id, workspace_id, points_balance) VALUES (?, ?, 599)",
            (actor.id, actor.workspace_id),
        )

    barrier = threading.Barrier(2)
    def reserve(index: int):
        barrier.wait()
        return reserve_ai_usage(
            database_path,
            actor,
            feature_key="product_processing.image_grid_2k",
            idempotency_key=f"concurrent-reservation-{index:04d}",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve, index) for index in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except BillingError as exc:
                outcomes.append(exc)

    assert sum(isinstance(value, dict) for value in outcomes) == 1
    assert sum(isinstance(value, BillingError) and value.status_code == 402 for value in outcomes) == 1
```

Import `threading`, `ThreadPoolExecutor`, and `BillingError`.

- [ ] **Step 3: Run the focused tests and verify RED**

Run from `local-runtime/`:

```bash
/Applications/anaconda3/bin/python3.12 -m pytest tests/test_customer_billing.py tests/test_billing_ai_usage.py -q
```

Expected: new HTTP endpoints return 404 and the concurrency test may permit both readers past the balance check or fail with lock behavior before `BEGIN IMMEDIATE` is added.

- [ ] **Step 4: Add authenticated reserve and settlement endpoints**

In `auth_server.py`, import the existing billing functions and `Actor`, then add these routes inside `create_auth_app`:

```python
@app.post("/api/customer/billing/usage/reserve")
def reserve_billing_usage(payload: dict[str, Any], authorization: str | None = Header(default=None)) -> dict[str, Any]:
    account = _required_account(db_path, authorization)
    feature_key = str(payload.get("feature_key") or "").strip()
    idempotency_key = str(payload.get("idempotency_key") or "").strip()
    if feature_key not in {"product_processing.text", "product_processing.image_grid_2k"}:
        raise HTTPException(status_code=400, detail="unsupported billing feature")
    if not 16 <= len(idempotency_key) <= 200:
        raise HTTPException(status_code=400, detail="idempotency_key is required")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {
        "ok": True,
        "usage": reserve_ai_usage(
            db_path,
            _billing_actor(account),
            feature_key=feature_key,
            idempotency_key=idempotency_key,
            quantity=1,
            source_ref=str(payload.get("source_ref") or "")[:200],
            metadata=_safe_billing_metadata(metadata),
        ),
    }


@app.post("/api/customer/billing/usage/{usage_id}/succeed")
def settle_billing_usage_success(usage_id: str, payload: dict[str, Any], authorization: str | None = Header(default=None)) -> dict[str, Any]:
    account = _required_account(db_path, authorization)
    _ensure_usage_owner(db_path, usage_id, str(account["account_id"]))
    feature_key = _usage_feature(db_path, usage_id)
    provider, model = _fixed_usage_provider(feature_key)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return {"ok": True, "usage": settle_ai_usage_success(
        db_path,
        usage_id,
        provider=provider,
        model=model,
        provider_task_id=str(payload.get("provider_task_id") or "")[:240],
        input_tokens=_safe_int(payload.get("input_tokens")),
        output_tokens=_safe_int(payload.get("output_tokens")),
        total_tokens=_safe_int(payload.get("total_tokens")),
        metadata=_safe_billing_metadata(metadata),
    )}


@app.post("/api/customer/billing/usage/{usage_id}/fail")
def settle_billing_usage_failure(usage_id: str, payload: dict[str, Any], authorization: str | None = Header(default=None)) -> dict[str, Any]:
    account = _required_account(db_path, authorization)
    _ensure_usage_owner(db_path, usage_id, str(account["account_id"]))
    settle_ai_usage_failure(
        db_path,
        usage_id,
        error_message=str(payload.get("error_message") or "AI operation failed")[:500],
    )
    return {"ok": True, "usage_id": usage_id, "status": "failed"}
```

Add `_billing_actor`, `_ensure_usage_owner`, `_usage_feature`, `_fixed_usage_provider`, `_safe_int`, and `_safe_billing_metadata` with the exact validation and limits from `9f762bc`, but do not copy its unrelated gateway functions yet.

- [ ] **Step 5: Add remote client methods and remote-only summary**

In `remote_client.py` add:

```python
def reserve_ai_usage(self, remote_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    return self._billing_post("/api/customer/billing/usage/reserve", remote_token, payload)

def settle_ai_usage_success(self, remote_token: str, usage_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return self._billing_post(f"/api/customer/billing/usage/{usage_id}/succeed", remote_token, payload)

def settle_ai_usage_failure(self, remote_token: str, usage_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return self._billing_post(f"/api/customer/billing/usage/{usage_id}/fail", remote_token, payload)

def _billing_post(self, path: str, remote_token: str, payload: dict[str, Any]) -> dict[str, Any]:
    if not remote_token:
        raise PermissionError("remote customer session is missing")
    return self._post(path, payload, headers={"Authorization": f"Bearer {remote_token}"})
```

In `customer/routes.py`, remove the local `_billing_summary` fallback. If the injected development fallback service does not expose `billing_summary`, raise `CustomerAuthUnavailable("remote billing service is not configured")`; otherwise make `/billing/summary` call:

```python
return remote_auth.billing_summary(remote_token_from_local_session(authorization))
```

- [ ] **Step 6: Reserve the SQLite writer before balance checks**

Change only the transaction start in `db.py`:

```python
conn.execute("BEGIN IMMEDIATE")
```

Do not change schema, migrations, or other database helpers.

- [ ] **Step 7: Run GREEN and nearby regressions**

```bash
/Applications/anaconda3/bin/python3.12 -m pytest tests/test_customer_billing.py tests/test_billing_ai_usage.py tests/test_customer_email_verification.py -q
```

Expected: all selected tests pass with no lock warnings or leaked secret metadata.

- [ ] **Step 8: Commit Task 1**

```bash
git add local-runtime/wh_local/customer/auth_server.py local-runtime/wh_local/customer/remote_client.py local-runtime/wh_local/customer/routes.py local-runtime/wh_local/db.py local-runtime/tests/test_customer_billing.py local-runtime/tests/test_billing_ai_usage.py
git commit -m "feat: centralize ai usage billing"
```

### Task 2: 服务端 AI 网关与本地无密钥适配器

**Files:**
- Create: `local-runtime/wh_local/modules/product_processing/server_ai_proxy.py`
- Modify: `local-runtime/wh_local/customer/auth_server.py`
- Modify: `local-runtime/wh_local/modules/product_processing/doubao_ark.py`
- Modify: `local-runtime/wh_local/modules/product_processing/infrastructure/media.py`
- Modify: `local-runtime/wh_local/modules/product_processing/provider_config.py`
- Create: `local-runtime/tests/test_product_processing_server_ai_gateway.py`

**Interfaces:**
- Consumes: Task 1 usage ownership helpers and authenticated account lookup.
- Produces: `server_ai_context(remote_token, usage_ids)`, `remote_token()`, `usage_id(kind)`, `gateway_base_url()`, `/api/customer/ai/chat`, `/api/customer/ai/image`, and server-managed text/image provider adapters.

- [ ] **Step 1: Write failing context and gateway tests**

Create `test_product_processing_server_ai_gateway.py` with:

```python
def test_server_ai_context_is_scoped_and_propagates_to_worker() -> None:
    assert remote_token() == ""
    assert usage_id("text") == ""
    with server_ai_context("remote-token", {"text": "use-text", "image_grid": "use-image"}):
        context = contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(context.run, lambda: (remote_token(), usage_id("text")))
            assert future.result() == ("remote-token", "use-text")
    assert remote_token() == ""
    assert usage_id("text") == ""
```

Add authenticated chat and image endpoint tests. Seed a reserved usage via the Task 1 API, monkeypatch `auth_server.requests.post/get` with response doubles, and assert:

```python
assert chat.status_code == 200
assert provider_request["headers"]["Authorization"] == "Bearer server-secret"
assert "server-secret" not in json.dumps(chat.json())
```

Also assert missing service secret returns 503, wrong usage feature returns 400, and already-settled usage returns 409.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
/Applications/anaconda3/bin/python3.12 -m pytest tests/test_product_processing_server_ai_gateway.py -q
```

Expected: import or endpoint failures because the context module and gateway routes do not exist.

- [ ] **Step 3: Add the scoped server AI context**

Create `server_ai_proxy.py`:

```python
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from ...config import default_config

_REMOTE_TOKEN: ContextVar[str] = ContextVar("product_processing_remote_token", default="")
_USAGE_IDS: ContextVar[dict[str, str]] = ContextVar("product_processing_usage_ids", default={})

@contextmanager
def server_ai_context(token: str, usage_ids: dict[str, str]) -> Iterator[None]:
    token_marker = _REMOTE_TOKEN.set(str(token or "").strip())
    usage_marker = _USAGE_IDS.set({str(key): str(value) for key, value in usage_ids.items() if value})
    try:
        yield
    finally:
        _USAGE_IDS.reset(usage_marker)
        _REMOTE_TOKEN.reset(token_marker)

def remote_token() -> str:
    return _REMOTE_TOKEN.get()

def usage_id(kind: str) -> str:
    return str(_USAGE_IDS.get().get(kind) or "")

def gateway_base_url() -> str:
    return default_config().customer_auth_base_url.rstrip("/")
```

- [ ] **Step 4: Add authenticated text and image gateways**

Port only these helpers and constants from `9f762bc` into `auth_server.py`:

- `TEXT_CHAT_URL`, `TEXT_MODEL`, `WUYIN_IMAGE_SUBMIT_URL`, `WUYIN_IMAGE_DETAIL_URL`
- `_TEXT_GATEWAY_SEMAPHORE`
- `_require_reserved_usage`
- `_validated_chat_messages`
- `_safe_provider_image_url`
- `_server_text_chat`
- `_first_provider_image_url`
- `_poll_server_wuyin`

Add `/api/customer/ai/chat` and `/api/customer/ai/image` exactly as constrained by the design: account authentication first, reserved-usage validation second, server environment key lookup third, bounded provider request last. Do not settle inside the gateway; settlement remains a separate idempotent call.

- [ ] **Step 5: Redirect text and image adapters to the platform gateway**

In `doubao_ark.py`, replace local `ARK_API_KEY` use with `remote_token()` and `usage_id("text")`, and post to:

```python
f"{gateway_base_url()}/api/customer/ai/chat"
```

with payload:

```python
{"model": "gpt-5.6-terra", "messages": messages, "usage_id": self.usage_id}
```

In `infrastructure/media.py`, add a single `server-managed-wuyin` branch. It must post the reserved image usage to `/api/customer/ai/image`, validate a safe returned URL, download it, and return image bytes. Do not alter OCR, grid geometry, provider image parsing, or any existing non-server-managed branch.

In `provider_config.py`, make text and primary image providers platform-managed, set status output to `server-managed`, and stop reading local upstream keys. Preserve all local model names, timeout values, backup provider configuration, COS handling, and local extensions unless the server-managed branch requires a marker.

- [ ] **Step 6: Run focused tests and product adapter regressions**

```bash
/Applications/anaconda3/bin/python3.12 -m pytest tests/test_product_processing_server_ai_gateway.py tests/test_product_processing_doubao_text.py tests/test_product_processing_image_quality.py -q
```

Expected: all selected tests pass. Existing adapter tests may need explicit `server_ai_context` setup, but their content validation assertions must remain unchanged.

- [ ] **Step 7: Audit excluded image hunks**

```bash
git diff -- local-runtime/wh_local/modules/product_processing/service.py local-runtime/wh_local/modules/product_processing/infrastructure/media.py
```

Expected: no change to the OCR-unavailable branch and no non-gateway image quality behavior copied from `9f762bc`.

- [ ] **Step 8: Commit Task 2**

```bash
git add local-runtime/wh_local/customer/auth_server.py local-runtime/wh_local/modules/product_processing/server_ai_proxy.py local-runtime/wh_local/modules/product_processing/doubao_ark.py local-runtime/wh_local/modules/product_processing/infrastructure/media.py local-runtime/wh_local/modules/product_processing/provider_config.py local-runtime/tests/test_product_processing_server_ai_gateway.py local-runtime/tests/test_product_processing_doubao_text.py
git commit -m "feat: proxy product ai through billing server"
```

### Task 3: 产品处理路由、会话与应用组合根接线

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/api/router.py:1-75,330-410,669-735`
- Modify: `local-runtime/wh_local/app/main.py:185-215`
- Create: `local-runtime/tests/test_product_processing_remote_billing_router.py`

**Interfaces:**
- Consumes: `LocalSessionService`, `CustomerAuthClient.billing_summary`, Task 1 remote token, existing `_billing_points_per_item` and `_billing_quantity`.
- Produces: `create_product_processing_router(..., customer_sessions=None, remote_customer_auth=None)`, `_remote_token(request, sessions)`, and `_billing.remote_token` internal context for the service.

- [ ] **Step 1: Write failing route-helper tests**

Create `test_product_processing_remote_billing_router.py` with a recording remote client and memory session:

```python
class RecordingRemoteBilling:
    def __init__(self, available: int):
        self.available = available
        self.tokens: list[str] = []

    def billing_summary(self, token: str) -> dict[str, Any]:
        self.tokens.append(token)
        return {"wallet": {"available_points": self.available}}


def test_billing_context_uses_remote_summary_and_token() -> None:
    payload = {"draft_ids": [1], "title_optimize": True, "grid_image": False, "image_rewrite": False}
    remote = RecordingRemoteBilling(30)
    _attach_billing_context_and_require_points(
        payload,
        Actor(id="user", username="user", role="operator"),
        source_ref="test",
        remote_token="remote-session",
        remote_customer_auth=remote,
    )
    assert remote.tokens == ["remote-session"]
    assert payload["_billing"]["remote_token"] == "remote-session"
    assert payload["_billing"]["estimated_points"] == 30
```

Add tests that missing remote client/token returns 503, insufficient remote balance returns 402, and preflight returns without contacting remote billing.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
/Applications/anaconda3/bin/python3.12 -m pytest tests/test_product_processing_remote_billing_router.py -q
```

Expected: helper signature rejects the new arguments and still reads the local wallet.

- [ ] **Step 3: Inject remote session dependencies into the router**

Extend `create_product_processing_router` with optional keyword-only arguments:

```python
customer_sessions: LocalSessionService | None = None,
remote_customer_auth: CustomerAuthClient | None = None,
```

Add:

```python
def _remote_token(request: Request, sessions: LocalSessionService | None) -> str:
    if sessions is None:
        return ""
    authorization = request.headers.get("authorization") or ""
    token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    session = sessions.store.get_session(token) if token else None
    return str(session.remote_token or "") if session is not None else ""
```

For drafts, workbook, and single-product processing, pass `_remote_token(request, customer_sessions)` and `remote_customer_auth` into `_attach_billing_context_and_require_points`.

- [ ] **Step 4: Replace the local wallet precheck**

Keep the existing estimate calculation, but replace the local database query with:

```python
if remote_customer_auth is None or not remote_token:
    raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "server billing session is unavailable")
summary = remote_customer_auth.billing_summary(remote_token)
wallet = summary.get("wallet") if isinstance(summary.get("wallet"), dict) else {}
available = int(wallet.get("available_points") or 0)
```

Keep the existing 402 message and add `remote_token` to `_billing`. Do not change point estimates or preflight exemptions.

- [ ] **Step 5: Wire both product-processing routers in the app**

In `app/main.py`, pass the already-created `customer_sessions` and `remote_customer_auth` into both unprefixed and `/api` router registrations. Do not move route registration order or change other app modules.

- [ ] **Step 6: Run focused and app regressions**

```bash
/Applications/anaconda3/bin/python3.12 -m pytest tests/test_product_processing_remote_billing_router.py tests/test_app_update.py tests/test_customer_email_verification.py -q
```

Expected: selected tests pass and `create_app` constructs both router prefixes.

- [ ] **Step 7: Commit Task 3**

```bash
git add local-runtime/wh_local/modules/product_processing/api/router.py local-runtime/wh_local/app/main.py local-runtime/tests/test_product_processing_remote_billing_router.py
git commit -m "feat: require remote billing for product processing"
```

### Task 4: 逐商品预留、结算、失败释放与上下文传播

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/service.py:1-130,285-390,1201-1260,1980-2160,2210-2320,2660-3005`
- Create: `local-runtime/tests/test_product_processing_remote_billing_service.py`
- Modify only if required for server context: existing product-processing tests whose mocked adapters now require `server_ai_context`.

**Interfaces:**
- Consumes: Task 1 remote billing client methods, Task 2 `server_ai_context`, Task 3 `_billing.remote_token`.
- Produces: `_submit_with_context`, per-task remote-token state, per-item usage state, `_reserve_product_processing_item_usage`, `_settle_product_processing_item_success`, `_settle_product_processing_item_failure`, and cleanup on completion.

- [ ] **Step 1: Write failing service state-machine tests**

Create a fake remote client that records reserve/succeed/fail calls, then test private orchestration units without calling real providers:

```python
def test_item_usage_reserves_selected_features_and_settles_success(service, monkeypatch) -> None:
    remote = RecordingBillingClient()
    monkeypatch.setattr(service_module, "CustomerAuthClient", lambda *_args, **_kwargs: remote)
    service._task_remote_tokens[7] = "remote-token"
    settings = {
        "_billing": {"source_ref": "task:test", "pricing_version": "v1"},
        "processing_scope": ["title", "four_grid"],
        "title_optimize": True,
        "description": False,
        "size": False,
        "grid_image": True,
        "image_rewrite": False,
    }

    usage_ids = service._reserve_product_processing_item_usage(7, 11, settings)
    assert usage_ids == {"text": "use-text", "image_grid": "use-image"}
    service._settle_product_processing_item_success(7, 11, settings, {"ai_notes": ["ok"]})
    assert remote.succeeded == [("remote-token", "use-text"), ("remote-token", "use-image")]
    assert service._reserved_usage_ids(7, 11) == {}
```

Add:

- text-only and image-only scope tests;
- preflight/no-token returns no reservations;
- processing failure calls fail for every reserved usage;
- second reservation failure calls fail for the already-reserved first usage before re-raising;
- settlement failure does not erase still-unsettled usage state;
- `_submit_with_context` exposes the correct usage ids inside media worker threads;
- task completion removes `_task_remote_tokens[task_id]` and per-item usage state.

- [ ] **Step 2: Run focused test and verify RED**

```bash
/Applications/anaconda3/bin/python3.12 -m pytest tests/test_product_processing_remote_billing_service.py -q
```

Expected: missing context helper, token store, reservation methods, and remote settlement behavior.

- [ ] **Step 3: Add task token and per-item usage state**

In `ProductProcessingService.__init__` initialize typed dictionaries:

```python
self._task_remote_tokens: dict[int, str] = {}
self._server_usage_ids: dict[tuple[int, int], dict[str, str]] = {}
```

In `process_drafts`, pop `remote_token` from the internal billing payload before persisted settings are created, and store it under the created task id. Do not persist the token to SQLite or output artifacts.

- [ ] **Step 4: Add safe reservation and settlement helpers**

Implement the remote methods from `9f762bc`, with one required safety improvement: if one feature reserves successfully and a later feature reservation fails, release all earlier reservations before re-raising.

```python
def _reserve_product_processing_item_usage(self, task_id: int, item_id: int, settings: dict[str, Any]) -> dict[str, str]:
    billing = settings.get("_billing") if isinstance(settings.get("_billing"), dict) else {}
    token = self._task_remote_token(task_id)
    if not token or bool(settings.get("preflight_only")):
        return {}
    client = CustomerAuthClient(default_config().customer_auth_base_url, timeout_seconds=20)
    features = self._billable_product_processing_features(settings)
    usage_ids: dict[str, str] = {}
    try:
        for kind, feature_key in features:
            response = client.reserve_ai_usage(token, {
                "feature_key": feature_key,
                "idempotency_key": f"product_processing:{task_id}:{item_id}:{kind}",
                "source_ref": self._text(billing.get("source_ref"))[:200],
                "metadata": {"task_id": task_id, "item_id": item_id, "pricing_version": billing.get("pricing_version", "")},
            })
            usage = response.get("usage") if isinstance(response, dict) else {}
            value = self._text(usage.get("usage_id")) if isinstance(usage, dict) else ""
            if not value:
                raise RuntimeError("server billing did not return usage_id")
            usage_ids[kind] = value
    except Exception as exc:
        self._settle_product_processing_item_failure(task_id, usage_ids, exc)
        raise
    self._store_reserved_usage_ids(task_id, item_id, usage_ids)
    return usage_ids
```

Use this exact feature selection so service reservations stay aligned with the existing route estimate; do not infer usage from unrelated AI notes after processing:

```python
def _billable_product_processing_features(self, settings: dict[str, Any]) -> list[tuple[str, str]]:
    scope = set(settings.get("processing_scope") or [])
    text_enabled = (
        bool({"title", "details", "product_dimensions"} & scope)
        or bool(settings.get("title_optimize", True))
        or bool(settings.get("description", True))
        or bool(settings.get("size", True))
    )
    image_enabled = (
        "four_grid" in scope
        or bool(settings.get("grid_image", True))
        or bool(settings.get("image_rewrite", True))
    )
    features: list[tuple[str, str]] = []
    if text_enabled:
        features.append(("text", "product_processing.text"))
    if image_enabled:
        features.append(("image_grid", "product_processing.image_grid_2k"))
    return features
```

On success, settle each stored usage and clear only the usages confirmed settled. On failure, best-effort call failure settlement for every stored usage, then clear those successfully released. Preserve any failed cleanup ids in memory for retry/logging rather than falsely marking them cleared.

- [ ] **Step 5: Wrap item processing in server context**

Move reservation inside the item `try` so partial reservation failures reach cleanup, then execute `_process_one` under:

```python
with server_ai_context(self._task_remote_token(task_id), usage_ids):
    return self._run_with_item_heartbeat(...)
```

Use `contextvars.copy_context()` through `_submit_with_context` for only the media executor submissions that can call the server-managed image adapter. Do not replace unrelated executors or alter OCR code.

- [ ] **Step 6: Add failure and task cleanup hooks**

- When a processed item reports non-completed status, release its reserved usage.
- When a future raises, preserve a top-level `reason` field so the failure release has an auditable message.
- After `finish_task` succeeds, remove the task token and any empty per-item usage entries.
- If the task pauses, keep state needed for resume.
- Do not write the remote token into repository settings, reports, logs, or exception messages.

- [ ] **Step 7: Run focused service tests and nearby product regressions**

```bash
/Applications/anaconda3/bin/python3.12 -m pytest tests/test_product_processing_remote_billing_service.py tests/test_product_processing_reliability.py tests/test_product_processing_text_quality.py tests/test_product_processing_image_quality.py -q
```

Expected: all selected tests pass with no network calls.

- [ ] **Step 8: Run the complete scoped backend verification**

```bash
/Applications/anaconda3/bin/python3.12 -m pytest \
  tests/test_customer_billing.py \
  tests/test_billing_ai_usage.py \
  tests/test_product_processing_server_ai_gateway.py \
  tests/test_product_processing_remote_billing_router.py \
  tests/test_product_processing_remote_billing_service.py \
  tests/test_product_processing_doubao_text.py \
  tests/test_product_processing_image_quality.py \
  tests/test_product_processing_reliability.py \
  tests/test_customer_email_verification.py -q
```

Expected: all selected tests pass on Python 3.12.

- [ ] **Step 9: Audit the selective port**

```bash
git diff --stat 2f91923..HEAD
git diff 2f91923..HEAD -- local-runtime/wh_local/modules/product_processing/service.py
git diff 9f762bc^ 9f762bc -- local-runtime/wh_local/modules/product_processing/service.py
git diff --check 2f91923..HEAD
```

Verify manually:

- billing/context hunks are present;
- the `four_grid:ocr_unavailable` change from `9f762bc` is absent;
- no frontend, collection progress, layout, navigation, or unrelated remote file is committed;
- all pre-existing dirty files remain unstaged.

- [ ] **Step 10: Commit Task 4**

```bash
git add local-runtime/wh_local/modules/product_processing/service.py local-runtime/tests/test_product_processing_remote_billing_service.py
git commit -m "feat: settle product processing usage remotely"
```
