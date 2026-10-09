# POD Reliability Deadline, Fencing, and Billing UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make POD batches recoverable when a coordinator or provider future stops making progress, prevent stale worker writes, and present billing recovery actions only for the billing states they actually handle.

**Architecture:** Keep provider execution in the existing dedicated AI runtime and keep the existing server-managed gateway. Add a durable per-batch execution epoch and progress timestamp; every worker-side mutation is an atomic conditional write fenced by that epoch. Replace unbounded coordinator waits with deadline-aware wait loops, and add a service-owned reaper that revokes the epoch before closing stale work. Billing remains conservative: a provider call in `started` state is uncertain and is never automatically replayed.

**Tech Stack:** Python 3.10+, FastAPI, sqlite3/WAL, `concurrent.futures`, React + TypeScript, existing pytest and frontend TypeScript/Vite build.

## Global Constraints

- Do not add a new POD batch status; continue using `queued`, `generating_patterns`, `compositing`, `generating_titles`, `paused`, `cancelled`, `completed`, `partial_failure`, `failed`, `billing_auth_required`, and `settlement_pending`.
- Do not expose a SuChuang/provider API key to the browser.
- Do not automatically replay a billing outcome whose durable status is `started`.
- Do not use `future.result(timeout=...)` as a thread-killing mechanism; Python executor threads remain alive after the caller times out.
- Reaper and worker writes must use one SQL statement with the epoch predicate; a separate read-then-write check is insufficient.
- Preserve existing billing run statuses and the existing server gateway/reconciliation contract.

## Files and Responsibilities

- Modify `local-runtime/wh_local/modules/pod_customization/migrations/012_batch_execution_fencing.sql`: add durable epoch/progress columns and indexes.
- Modify `local-runtime/wh_local/modules/pod_customization/repository.py`: implement atomic claim, fenced mutations, stale-batch reaping, and billing-run fencing.
- Modify `local-runtime/wh_local/modules/pod_customization/worker.py`: use deadline-aware wait loops, propagate execution context, cancel/fence overdue work, and surface timeout errors.
- Modify `local-runtime/wh_local/modules/pod_customization/service.py`: own the reaper lifecycle and invoke it during normal runtime and shutdown.
- Modify `local-runtime/wh_local/modules/pod_customization/tests/test_worker.py` and `tests/test_persistence.py`: cover timeout, stale writes, reaping, and restart behavior.
- Modify `web-frontend/src/modules/pod_customization/components/PodBatchGallery.tsx` and `pages/PodCustomizationPage.tsx`: split billing actions by status and autosize initial textarea values.
- Modify `web-frontend/src/modules/pod_customization/pages/PodCustomizationPage.test.ts`: cover billing-action visibility and initial autosize wiring.
- Add `docs/superpowers/specs/2026-09-03-pod-item-report-adr.md`: record the deferred item claim/report design without implementing it in this cycle.

---

### Task 1: Add the durable execution epoch and progress lease

**Files:**
- Create: `local-runtime/wh_local/modules/pod_customization/migrations/012_batch_execution_fencing.sql`
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_persistence.py`

**Interfaces:**
- Produce `claim_batch_with_epoch(batch_id: str, *, allow_billing_resume: bool = False) -> int | None`.
- Produce `reap_stuck_batches(*, stale_after_seconds: int, limit: int = 100) -> list[dict[str, object]]`.
- Keep `claim_batch(...) -> bool` as a compatibility wrapper for existing callers.

- [ ] **Step 1: Write the migration and a failing persistence test.**

Add these columns to `pod_customization_batches` through the repository migration mechanism:

```sql
ALTER TABLE pod_customization_batches ADD COLUMN execution_epoch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pod_customization_batches ADD COLUMN last_progress_at TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_pod_batches_progress
    ON pod_customization_batches (status, last_progress_at);
```

Add a test that creates a batch, calls `claim_batch_with_epoch`, asserts a positive epoch, then calls it again while the batch is active and asserts `None`.

- [ ] **Step 2: Run the focused test and verify it fails.**

Run:

```bash
cd local-runtime
pytest -q wh_local/modules/pod_customization/tests/test_persistence.py -k execution_epoch
```

Expected: FAIL because the migration and method do not exist yet.

- [ ] **Step 3: Implement the migration and atomic claim.**

Implement `claim_batch_with_epoch` in one connection transaction. The update must increment the epoch only when the batch is claimable:

```sql
UPDATE pod_customization_batches
SET status = 'generating_patterns',
    execution_epoch = execution_epoch + 1,
    started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
    updated_at = ?,
    last_progress_at = ?,
    error_message = ''
WHERE batch_id = ?
  AND (status = 'queued' OR (? = 1 AND status = 'billing_auth_required'))
```

Read the new epoch on the same connection before committing and return it. Implement `claim_batch` as `return claim_batch_with_epoch(...) is not None`.

- [ ] **Step 4: Add stale-batch selection and atomic epoch revocation.**

Select only batches whose status is one of `generating_patterns`, `compositing`, or `generating_titles` and whose `last_progress_at` is older than the cutoff. For each selected row, atomically update the batch with:

```sql
UPDATE pod_customization_batches
SET status = ?,
    execution_epoch = execution_epoch + 1,
    error_message = ?,
    updated_at = ?,
    last_progress_at = ?,
    finished_at = ?
WHERE batch_id = ?
  AND execution_epoch = ?
  AND status IN ('generating_patterns', 'compositing', 'generating_titles')
```

Use `partial_failure` only when at least one durable result is completed; otherwise use `failed`. Mark queued/running item, title, and generation-call records terminal with the timeout message. Mark the associated billing run `settlement_pending` when it has an uncertain `started` outcome; never write a nonexistent `pending` status.

- [ ] **Step 5: Run the focused tests and commit.**

Run:

```bash
cd local-runtime
pytest -q wh_local/modules/pod_customization/tests/test_persistence.py -k 'execution_epoch or reap_stuck'
```

Expected: PASS. Commit as `feat(pod): add durable batch execution fencing`.

---

### Task 2: Fence every worker-side mutation

**Files:**
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/worker.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_worker.py`

**Interfaces:**
- Add `BatchExecutionContext(batch_id: str, epoch: int)` in `worker.py`.
- Worker methods that mutate batch-owned state accept `execution: BatchExecutionContext`.
- Repository mutation methods accept `execution_epoch: int | None`; when supplied, their SQL includes the epoch predicate.

- [ ] **Step 1: Write failing stale-write tests.**

Add tests that:

1. claim epoch `1`, reap the batch so it becomes epoch `2`, then attempt `finish_generation_call(..., execution_epoch=1)` and assert zero rows changed and no item is completed;
2. release a blocked provider future after reaping and assert its late result does not create a publication, title, or successful billing outcome;
3. verify a current epoch worker can still persist a normal completed result.

- [ ] **Step 2: Run the tests and verify they fail.**

Run:

```bash
cd local-runtime
pytest -q wh_local/modules/pod_customization/tests/test_worker.py -k 'stale or epoch'
```

Expected: FAIL because existing updates are keyed only by batch/call IDs.

- [ ] **Step 3: Add the atomic epoch predicates.**

Update all worker-reached repository writes, including `mark_generation_call_running`, `finish_generation_call`, `requeue_generation_call`, `finish_style_grid_result`, `fail_style_grid`, `fail_remaining_items`, title completion/failure, `set_batch_status`, and batch count refreshes. Use conditional SQL such as:

```sql
UPDATE pod_customization_style_grid_results
SET status = ?, error_message = ?, updated_at = ?
WHERE batch_id = ?
  AND style_index = ?
  AND variant_index = ?
  AND (SELECT execution_epoch
       FROM pod_customization_batches
       WHERE batch_id = ?) = ?
```

If the row count is zero because the epoch is stale, raise a dedicated `PodExecutionExpired` exception; do not convert it into a provider failure.

- [ ] **Step 4: Propagate the context through the worker.**

After `claim_batch_with_epoch` returns, create `BatchExecutionContext(batch_id, epoch)` and pass it through `_process_batch_authorized`, `_stream_style_attempts`, `_process_style_grids`, title processing, and all generation helpers. Before submitting a new provider call, check the batch status; before persisting a result, rely on the atomic repository fence.

For billing outcomes, add the same epoch to the in-memory `PodBillingRun` and make `record`, `start`, and `settle` reject a stale run. Persist the epoch on the billing-run row or verify it through the batch join in the same SQL transaction.

- [ ] **Step 5: Run worker tests and commit.**

Run:

```bash
cd local-runtime
pytest -q wh_local/modules/pod_customization/tests/test_worker.py
```

Expected: PASS, including existing pause/cancel/retry tests. Commit as `feat(pod): reject stale worker writes`.

---

### Task 3: Replace unbounded coordinator waits with progress deadlines

**Files:**
- Modify: `local-runtime/wh_local/modules/pod_customization/worker.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/runtime.py` only if a shared deadline helper is needed
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_worker.py`

**Interfaces:**
- Add constants `POD_PROGRESS_TIMEOUT_SECONDS` and `POD_WAIT_POLL_SECONDS` in `worker.py` or a POD config module.
- Add `_wait_for_progress(futures, *, deadline: float) -> tuple[set[Future[Any]], set[Future[Any]]]`.

- [ ] **Step 1: Define the timeout semantics before changing code.**

Use an inactivity deadline, not a fixed total batch duration. A 100-style batch can legitimately run longer than ten minutes. Set the default inactivity timeout above the existing 600-second provider polling ceiling (for example 900 seconds), and reset `last_progress_at` whenever a provider call, post-process job, or title job completes or a durable state transition succeeds.

- [ ] **Step 2: Write failing coordinator timeout tests.**

Add a runtime whose future never completes. Assert that the worker exits after the test-configured short inactivity timeout, the batch is terminal, and the blocked future's later release cannot mutate state. Add a second test where one future completes repeatedly while another is blocked; progress must prevent premature reaping until the inactivity window expires.

- [ ] **Step 3: Replace `wait(... FIRST_COMPLETED)` loops.**

In `_stream_style_attempts`, calculate the remaining deadline with `time.monotonic()`, call `wait(..., timeout=remaining, return_when=FIRST_COMPLETED)`, and handle an empty `done` set by raising the timeout path. Cancel pending futures best-effort, revoke the execution epoch through the repository, and let the stale futures terminate or be ignored by fencing.

- [ ] **Step 4: Replace `as_completed(...)` loops.**

Use the same repeated `wait` pattern for title futures and any remaining generation-future path. Do not wrap `future.result(timeout=...)` around an already-completed future and call that a deadline. On timeout, persist the timeout reason once, then allow the normal `finally` billing logic to handle known versus uncertain outcomes.

- [ ] **Step 5: Run focused tests and commit.**

Run:

```bash
cd local-runtime
pytest -q wh_local/modules/pod_customization/tests/test_worker.py -k 'timeout or deadline or blocked or title'
```

Expected: PASS. Commit as `feat(pod): bound coordinator waits by progress deadline`.

---

### Task 4: Add and operate the live reaper

**Files:**
- Modify: `local-runtime/wh_local/modules/pod_customization/service.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/repository.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_persistence.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_worker.py`

**Interfaces:**
- Add `PodCustomizationService._reaper_stop: threading.Event` and `_reaper_thread: threading.Thread | None`.
- Add `_run_stuck_batch_reaper()` with a 60-second wait interval.
- Add `PodCustomizationService.reap_stuck_batches_once()` for deterministic tests and operator diagnostics.

- [ ] **Step 1: Write lifecycle tests.**

Test that `start_workers=True` starts exactly one reaper, `reap_stuck_batches_once()` expires an old batch, and `close()` sets the stop event and joins the thread without waiting on provider executor threads. Test that `start_workers=False` starts no reaper.

- [ ] **Step 2: Implement the reaper lifecycle.**

Create the stop event before starting workers. Start a daemon thread only when `start_workers=True`. The loop calls `reap_stuck_batches(stale_after_seconds=POD_PROGRESS_TIMEOUT_SECONDS)` and waits on the stop event for 60 seconds. In `close()`, set the event, join the reaper for a bounded interval, then close the existing worker/runtime resources.

- [ ] **Step 3: Make reaping idempotent and observable.**

Log batch ID, old epoch, new terminal status, timeout reason, and billing action keys without logging provider credentials or tokens. A second reaper pass must return no rows for the already-revoked epoch.

- [ ] **Step 4: Run persistence and lifecycle tests and commit.**

Run:

```bash
cd local-runtime
pytest -q wh_local/modules/pod_customization/tests/test_persistence.py wh_local/modules/pod_customization/tests/test_worker.py -k 'reap or reaper or close'
```

Expected: PASS. Commit as `feat(pod): add live stale-batch reaper`.

---

### Task 5: Split billing recovery UI by durable billing status

**Files:**
- Modify: `web-frontend/src/modules/pod_customization/components/PodBatchGallery.tsx`
- Modify: `web-frontend/src/modules/pod_customization/pages/PodCustomizationPage.tsx`
- Test: `web-frontend/src/modules/pod_customization/pages/PodCustomizationPage.test.ts`

**Interfaces:**
- Add a pure helper in the gallery/model layer: `billingRecoveryAction(run: PodBillingRun) -> "reauthorize" | "settle" | "reconcile" | null`.
- Keep existing API calls; do not add a new billing endpoint for this UI task.

- [ ] **Step 1: Add failing source-level tests.**

Assert that `auth_required` renders “重新授权并恢复” only when no outcome has status `started`, `settlement_pending` renders “重试计费结算”, and an uncertain started outcome renders no ordinary-user retry button. Assert that a failed batch with no pending run renders no billing action.

- [ ] **Step 2: Implement status-aware rendering.**

Filter pending runs by the active batch, inspect their outcome statuses, and render at most one action. Use explanatory copy: “仅恢复账务，不会重新生成图片” for settlement retry. Do not change the existing retry-failed image/title action.

- [ ] **Step 3: Add initial textarea autosize.**

Attach refs to the two multiline business textareas and run an effect after mount and whenever their values change:

```tsx
useLayoutEffect(() => {
  textareas.current.forEach((textarea) => {
    if (!textarea) return;
    textarea.style.height = "36px";
    textarea.style.height = `${Math.max(36, textarea.scrollHeight)}px`;
  });
}, [businessFields.core_selling_points, businessFields.excluded_elements]);
```

Retain the existing `onChange` call for immediate feedback.

- [ ] **Step 4: Run frontend verification and commit.**

Run:

```bash
cd web-frontend
npm run build
```

Expected: TypeScript check and Vite build pass. Commit as `fix(pod): align billing recovery actions with run status`.

---

### Task 6: Make server-gateway reconciliation timely and stoppable

**Files:**
- Inspect and modify only as required: `local-runtime/wh_local/customer/auth_server.py`
- Inspect and modify only as required: `local-runtime/wh_local/customer/remote_client.py`
- Inspect and modify only as required: `local-runtime/wh_local/modules/pod_customization/remote_billing.py`
- Test: `local-runtime/tests/test_product_processing_server_ai_gateway.py`
- Test: `local-runtime/wh_local/modules/pod_customization/tests/test_runtime_isolation.py`

**Interfaces:**
- Preserve `provider_task_id`, `submit_uncertain`, `polling`, terminal failure, and server reconciliation semantics.
- Preserve `RemotePodBillingCoordinator(server_managed=True)` behavior that does not return provider credentials to the desktop/browser.
- Add `_run_pod_gateway_reconcile_loop(stop_event: threading.Event, database_path: Path, *, interval_seconds: float) -> None` in `auth_server.py`.

- [ ] **Step 1: Add regression tests for restart/reconciliation cases and sweep shutdown.**

Cover: provider task ID persisted before polling; restart reconciliation settles confirmed success; terminal provider failure releases usage once; submit outcome without a task ID is not automatically resubmitted; repeated reconciliation is idempotent; and a pre-set stop event prevents the periodic loop from making a second reconciliation call.

- [ ] **Step 2: Run the gateway tests before changing code.**

Run:

```bash
cd local-runtime
pytest -q tests/test_product_processing_server_ai_gateway.py wh_local/modules/pod_customization/tests/test_runtime_isolation.py
```

Expected: existing tests pass; new tests initially fail only for uncovered cases.

- [ ] **Step 3: Replace the hourly-only reconciliation sleep with a bounded loop.**

Extract the existing reconciliation work into a loop driven by `Event.wait`, so shutdown can stop it promptly. Reconcile gateway requests every 60 seconds; retain the existing batch-freeze release once per hour:

```python
def _run_pod_gateway_reconcile_loop(
    stop_event: threading.Event,
    database_path: Path,
    *,
    interval_seconds: float = 60.0,
) -> None:
    release_due_at = time.monotonic()
    while not stop_event.is_set():
        try:
            reconcile_pod_gateway_requests(database_path)
            if time.monotonic() >= release_due_at:
                release_expired_batch_freezes(database_path)
                release_due_at = time.monotonic() + 60 * 60
        except Exception:
            pass
        stop_event.wait(interval_seconds)
```

Create the event and daemon thread in `create_auth_app`; in the FastAPI shutdown handler set the event and join the thread with a short bounded timeout. Use the existing durable request row and task ID as the idempotency boundary. Do not add client-side provider credentials, and do not make the local worker replay a call whose billing outcome is `started`.

- [ ] **Step 4: Run the gateway suite and commit.**

Expected: all focused gateway tests pass. Commit as `fix(pod): harden server gateway reconciliation`.

---

### Task 7: Document the deferred item claim/report architecture

**Files:**
- Create: `docs/superpowers/specs/2026-09-03-pod-item-report-adr.md`

- [ ] **Step 1: Record the API contract without implementing it.**

Define these request/response shapes around server-managed provider execution:

```text
POST /api/pod-customization/batches/{batch_id}/items/claim
-> {"items":[{"item_id":"...","claim_token":"...","fence":7,"lease_expires_at":"..."}]}

POST /api/pod-customization/items/{item_id}/heartbeat
{ "claim_token":"...", "fence":7 }
-> {"lease_expires_at":"..."}

POST /api/pod-customization/items/{item_id}/report
{ "claim_token":"...", "fence":7, "idempotency_key":"...", "status":"completed|failed",
  "provider_task_id":"...", "result_url":"...", "error_code":"..." }
-> {"accepted":true,"item_status":"completed|failed"}
```

Specify a five-minute initial lease renewed by heartbeat, a unique idempotency key per report, a `409` response for stale claim token/fence, and a `200` response replaying the stored terminal result for a duplicate idempotency key. The server, not the browser, submits/polls the provider task and reconciles a recorded `provider_task_id` after client loss. One successful item maps to one immutable server usage record; failed and uncertain provider outcomes are reconciled through that ledger rather than re-submitted by the client.

- [ ] **Step 2: Record explicit non-goals.**

State that browser-direct SuChuang calls, provider-key exposure, automatic replay of uncertain calls, and replacement of the existing billing ledger are out of scope for this cycle.

- [ ] **Step 3: Review the ADR as the P2 gate.**

Do not create endpoints or migrations until P0/P1 tests pass and the billing ledger mapping is approved.

---

### Task 8: Full verification and staged rollout

**Files:**
- Modify: `docs/superpowers/plans/2026-09-03-pod-reliability-deadline-fencing.md` only for recorded deviations.

- [ ] **Step 1: Run all focused backend tests.**

```bash
cd local-runtime
pytest -q wh_local/modules/pod_customization/tests
pytest -q tests/test_product_processing_server_ai_gateway.py
```

- [ ] **Step 2: Run frontend type/build verification.**

```bash
cd web-frontend
npm run build
```

- [ ] **Step 3: Run a manual failure drill.**

Use a test runtime that never completes one provider future. Verify: the UI stops showing an active batch after the configured inactivity window; the batch has a timeout error; the execution epoch has advanced; the late future cannot change items or billing outcomes; and a pending billing run is not automatically replayed.

- [ ] **Step 4: Roll out behind configuration.**

Deploy with reaper enabled but a conservative inactivity threshold first. Monitor timeout counts, stale-write rejections, `settlement_pending` counts, and server-gateway reconciliation results. Increase/decrease the threshold only after observing normal batch duration by batch size.

- [ ] **Step 5: Prepare rollback.**

Keep the migration additive. If the reaper causes false positives, disable the reaper loop through configuration while retaining epoch-aware writes; do not remove columns or roll back the migration destructively.
