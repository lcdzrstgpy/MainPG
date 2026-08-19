# Remote Billing Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep authoritative remote billing while preventing transient TLS failures and 8-thread request bursts from failing an entire product-processing batch.

**Architecture:** Put retry and concurrency control in the single remote billing adapter so all product-processing callers inherit identical behavior. Reuse the workbook entry point's first wallet snapshot for its final-count check, and separate remote service failures from API-key/quota failures in the task UI. Existing text and vision clients already provide bounded gateway retries, so this change verifies rather than duplicates them.

**Tech Stack:** Python 3, urllib, threading, pytest, FastAPI TestClient, React/TypeScript, Node test runner.

## Global Constraints

- Do not bypass remote billing or move provider API keys into the local runtime.
- Do not modify Wanbang/OneBound collection behavior.
- Retry only billing summary and idempotent reserve/settlement calls; never retry deterministic 4xx or protocol failures.
- Reuse the original payload, idempotency key, usage ID, and remote token on every retry.
- Keep Windows packaging paused.

---

### Task 1: Bound and retry remote billing transport

**Files:**
- Modify: `local-runtime/wh_local/customer/remote_client.py`
- Test: `local-runtime/tests/test_customer_billing.py`

**Interfaces:**
- Consumes: existing `CustomerAuthClient._billing_result(function, *args, **kwargs)` call sites.
- Produces: `_BILLING_REQUEST_GATE`, `_BILLING_MAX_ATTEMPTS`, `_BILLING_RETRY_DELAYS`, and an instance `_billing_result` that retries only `CustomerAuthUnavailable`.

- [ ] **Step 1: Write failing retry and concurrency tests**

```python
def test_billing_summary_retries_transient_unavailable(monkeypatch):
    client = CustomerAuthClient("https://customer.example.test")
    calls = 0
    def flaky(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CustomerAuthUnavailable("tls eof")
        return {"wallet": {"available_points": 100}}
    monkeypatch.setattr(client, "_get", flaky)
    monkeypatch.setattr(remote_client.time, "sleep", lambda _delay: None)
    assert client.billing_summary("token")["wallet"]["available_points"] == 100
    assert calls == 2

def test_billing_requests_share_two_slot_gate(monkeypatch):
    client = CustomerAuthClient("https://customer.example.test")
    lock = threading.Lock()
    in_flight = 0
    peak = 0
    def slow(*_args, **_kwargs):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.03)
        with lock:
            in_flight -= 1
        return {"wallet": {"available_points": 100}}
    monkeypatch.setattr(client, "_get", slow)
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _index: client.billing_summary("token"), range(6)))
    assert peak == 2

def test_billing_deterministic_rejection_is_not_retried(monkeypatch):
    client = CustomerAuthClient("https://customer.example.test")
    calls = 0
    def rejected(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise CustomerAuthRejected(402, "insufficient")
    monkeypatch.setattr(client, "_post", rejected)
    with pytest.raises(CustomerAuthRejected):
        client.reserve_ai_usage("token", {"idempotency_key": "same-key"})
    assert calls == 1
```

- [ ] **Step 2: Run RED tests**

Run: `cd local-runtime && pytest -q tests/test_customer_billing.py -k 'retries_transient or two_slot_gate or deterministic_rejection'`

Expected: retry and gate assertions fail against the current single-attempt, unbounded adapter.

- [ ] **Step 3: Implement the bounded retry loop**

```python
_BILLING_MAX_ATTEMPTS = 3
_BILLING_RETRY_DELAYS = (0.2, 0.6)
_BILLING_REQUEST_GATE = threading.BoundedSemaphore(2)

def _billing_result(self, function, *args, **kwargs):
    for attempt in range(_BILLING_MAX_ATTEMPTS):
        try:
            with _BILLING_REQUEST_GATE:
                response = function(*args, **kwargs)
        except CustomerBillingProtocolError:
            raise
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise CustomerBillingProtocolError() from exc
        except CustomerAuthUnavailable as exc:
            if attempt + 1 >= _BILLING_MAX_ATTEMPTS:
                raise CustomerAuthUnavailable("remote billing service is unavailable") from exc
            time.sleep(_BILLING_RETRY_DELAYS[attempt])
            continue
        except CustomerAuthRejected as exc:
            status_code = getattr(exc, "status_code", None)
            if type(status_code) is not int or not 400 <= status_code < 500:
                raise CustomerBillingProtocolError() from exc
            raise CustomerAuthRejected(status_code, "remote billing request was rejected") from exc
        except CustomerBillingPermissionError:
            raise
        except PermissionError as exc:
            raise CustomerBillingPermissionError() from exc
        if not isinstance(response, dict):
            raise CustomerBillingProtocolError()
        return response
    raise AssertionError("unreachable")
```

Keep the existing stable exception messages. Do not apply this wrapper to login, registration, password actions, logout, top-up order creation, or generic `_get`/`_post` calls.

- [ ] **Step 4: Run GREEN and adjacent client tests**

Run: `cd local-runtime && pytest -q tests/test_customer_billing.py`

Expected: all customer billing tests pass.

---

### Task 2: Reuse workbook wallet snapshot and preserve authoritative reservation

**Files:**
- Modify: `local-runtime/wh_local/modules/product_processing/api/router.py`
- Test: `local-runtime/tests/test_product_processing_remote_billing_router.py`

**Interfaces:**
- Consumes: `_attach_billing_context_and_require_points(payload, actor, source_ref, remote_token, remote_customer_auth, available_points)`.
- Produces: the helper returns `int | None` (`None` for non-billable/preflight payloads), enabling one wallet summary request per workbook submission while retaining the parsed actual-count check before persistence.

- [ ] **Step 1: Change the existing no-side-effects test to require one summary call per request**

```python
assert [response.status_code for response in responses] == [402, 402]
assert all("150" in response.json()["detail"] for response in responses)
assert counts() == before == (0, False, set())
assert remote.tokens == ["remote-session"] * 2
```

- [ ] **Step 2: Run RED test**

Run: `cd local-runtime && pytest -q tests/test_product_processing_remote_billing_router.py::test_workbook_rechecks_reserve_points_after_real_import_count`

Expected: FAIL because the current route queries the wallet twice per request.

- [ ] **Step 3: Capture and reuse the first summary result**

```python
available_points = _attach_billing_context_and_require_points(
    normalized,
    actor,
    source_ref="product_processing:workbook",
    remote_token=remote_token,
    remote_customer_auth=remote_customer_auth,
)

def final_billing_check(parsed_payload):
    _attach_billing_context_and_require_points(
        parsed_payload,
        actor,
        source_ref="product_processing:workbook",
        remote_token=remote_token,
        remote_customer_auth=remote_customer_auth,
        available_points=available_points,
    )
```

Update `_attach_billing_context_and_require_points` to return `None` from both existing early-return branches and return the validated `available` integer after attaching `_billing`. Existing callers may ignore the return value. If billing is not required for preflight, do not force a remote request. Do not alter per-item reserve or settlement semantics.

- [ ] **Step 4: Run GREEN and focused remote-billing tests**

Run: `cd local-runtime && pytest -q tests/test_product_processing_remote_billing_router.py tests/test_product_processing_remote_billing_service.py`

Expected: all focused tests pass.

---

### Task 3: Distinguish remote service outages in the task UI

**Files:**
- Modify: `web-frontend/src/modules/product_processing/pages/ProductProcessingTaskPage.tsx`
- Test: `web-frontend/src/app/layout/WorkspaceShell.test.ts`

**Interfaces:**
- Produces: `isRemoteServiceError(reason)` and `REMOTE_SERVICE_HINT`; remote service failures are no longer consumed by `isAiConfigError`.

- [ ] **Step 1: Add a failing source-contract test**

```typescript
test("product processing distinguishes remote service outages from API key failures", () => {
  assert.match(taskPageSource, /function isRemoteServiceError/);
  assert.match(taskPageSource, /服务器计费服务暂时不可用/);
  assert.match(taskPageSource, /hasRemoteServiceIssue/);
  assert.doesNotMatch(taskPageSource, /AI_CONFIG_ERROR_RE[\s\S]{0,400}unreachable/);
});
```

- [ ] **Step 2: Run RED test**

Run: `cd web-frontend && node --test src/app/layout/WorkspaceShell.test.ts`

Expected: FAIL because the page currently classifies `unreachable` as an API-key/configuration problem.

- [ ] **Step 3: Implement separate classification and copy**

```typescript
const REMOTE_SERVICE_ERROR_RE = /remote billing service is unavailable|provider is temporarily unreachable/i;
function isRemoteServiceError(reason?: string): boolean {
  return Boolean(reason && REMOTE_SERVICE_ERROR_RE.test(reason));
}
const REMOTE_SERVICE_HINT =
  '服务器计费或 AI 服务暂时不可用，请稍后重试；这不是商品数据或本地 API Key 配置问题。';
```

Exclude remote-service patterns from `isAiConfigError`, render the remote-service hint independently, and preserve the original row reason instead of replacing it with “AI 服务鉴权/额度问题”.

- [ ] **Step 4: Run GREEN and frontend build**

Run: `cd web-frontend && node --test src/app/layout/WorkspaceShell.test.ts`

Run: `cd web-frontend && npm run build`

Expected: the contract test and production build pass.

---

### Task 4: Full verification and commit

**Files:**
- Verify only; do not add `local-runtime/wh_local/workbench.sqlite3`.

- [ ] **Step 1: Run backend regression**

Run: `cd local-runtime && pytest -q tests/test_customer_billing.py tests/test_product_processing_remote_billing_router.py tests/test_product_processing_remote_billing_service.py tests/test_product_processing_server_ai_gateway.py`

Run: `cd local-runtime && pytest -q tests/test_product_processing_*.py`

- [ ] **Step 2: Run static and diff checks**

Run: `python3 -m compileall -q local-runtime/wh_local`

Run: `git diff --check`

- [ ] **Step 3: Audit scope and commit**

Run: `git status --short`

Expected modified scope: remote billing client/tests, product-processing router/tests, task page/frontend contract test, and this plan. Wanbang/OneBound collection and packaging files must be absent.

Commit message: `fix: tolerate transient remote billing failures`
