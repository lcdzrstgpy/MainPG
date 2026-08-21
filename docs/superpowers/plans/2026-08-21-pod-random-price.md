# POD Random Per-Link Billing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Charge each successful POD style a server-selected integer price from 40 through 45 points while leaving ordinary product-processing billing unchanged.

**Architecture:** Extend the existing batch freeze record with a server-validated billing profile, frozen pricing rule version, and per-link price snapshot. The shared batch service selects and persists POD prices atomically; settlement branches only for the POD profile, while the existing product-processing path remains byte-for-byte compatible at its public interface.

**Tech Stack:** Python 3.12, FastAPI, SQLite, pytest.

## Global Constraints

- POD prices are independently selected from `40, 41, 42, 43, 44, 45` by the server.
- Prices are integer points and are immutable across idempotent retries, restarts, and regrant.
- A successful POD style is charged its snapshot price; a failed style releases its whole snapshot price; provider retries never add charges.
- Existing product-processing AI code and default batch pricing remain unchanged.
- Do not create a second wallet, ledger, or legacy POD billing route.

---

### Task 1: Persist POD pricing snapshots in the shared batch ledger

**Files:**
- Modify: `local-runtime/wh_local/db.py`
- Modify: `local-runtime/wh_local/billing.py`
- Test: `local-runtime/tests/test_billing_batch_pricing.py`

**Interfaces:**
- Consumes: existing `freeze_batch_points(..., link_count, scope, idempotency_key)` and `settle_batch_points(...)`.
- Produces: optional `billing_profile: str = "product_processing"` on `freeze_batch_points`; POD response fields `billing_profile`, `rule_version`, and `link_prices`; unchanged defaults for existing callers.

- [x] **Step 1: Write failing persistence and settlement tests**

Add tests that monkeypatch the server price selector to `[40, 45]`, freeze two links with `billing_profile="pod_random_v1"`, assert a 85-point lock and immutable idempotent snapshot, then settle one complete POD link and one failed POD link and assert charge 40/refund 45. Add a legacy product freeze assertion of 45 points with profile omitted.

- [x] **Step 2: Run the focused tests and verify RED**

Run: `cd local-runtime && python -m pytest -q tests/test_billing_batch_pricing.py`

Expected: failure because `freeze_batch_points` does not accept `billing_profile` and the schema lacks snapshot columns.

- [x] **Step 3: Add backward-compatible schema columns**

Add these columns to fresh schema and `_migrate_core_schema`:

```python
_ensure_column(conn, "billing_batch_freezes", "billing_profile", "TEXT NOT NULL DEFAULT 'product_processing'")
_ensure_column(conn, "billing_batch_freezes", "rule_version", "INTEGER NOT NULL DEFAULT 0")
_ensure_column(conn, "billing_batch_freezes", "link_prices_json", "TEXT NOT NULL DEFAULT '[]'")
```

- [x] **Step 4: Implement atomic POD price selection and settlement**

Validate profiles against `{"product_processing", "pod_random_v1"}`. For POD, select each whole-point price with `secrets.randbelow(6) + 40`, convert to ledger units using `PIC_UNIT_SCALE`, persist JSON and the current rule version, and derive the lock from the sum. During settlement, require exactly one `title` and one `four_grid` status per link; charge the snapshot only when both are `success`, otherwise refund that link's snapshot. Set POD `refunded_points` to `frozen_points - charged_points`; do not change the ordinary product branch.

- [x] **Step 5: Run focused billing tests and verify GREEN**

Run: `cd local-runtime && python -m pytest -q tests/test_billing_batch_pricing.py`

Expected: all tests pass.

### Task 2: Mark only POD freezes with the server billing profile

**Files:**
- Modify: `local-runtime/wh_local/customer/auth_server.py`
- Modify: `local-runtime/wh_local/modules/pod_customization/billing_contract.py`
- Test: `local-runtime/tests/test_batch_direct_full_chain.py`
- Test: `local-runtime/tests/test_pod_customization_billing_contract.py`
- Test: `local-runtime/tests/test_pod_customization_remote_billing.py`

**Interfaces:**
- Consumes: `freeze_batch_points(..., billing_profile="pod_random_v1")` from Task 1.
- Produces: POD freeze payload field `billing_profile: "pod_random_v1"`; server rejects unknown profiles and requires POD create permission for that profile.

- [x] **Step 1: Write failing route and adapter tests**

Assert `PodCallPlan.product_batch_freeze_payload()` includes `billing_profile: "pod_random_v1"`. Add an auth-server test proving a POD-profile freeze returns only prices in 40..45 and a normal freeze still locks 45 per link. Assert an arbitrary profile receives HTTP 400.

- [x] **Step 2: Run focused tests and verify RED**

Run: `cd local-runtime && python -m pytest -q tests/test_pod_customization_billing_contract.py tests/test_pod_customization_remote_billing.py tests/test_batch_direct_full_chain.py`

Expected: payload assertion fails and the route ignores the profile.

- [x] **Step 3: Implement the narrow route/profile wiring**

Have the batch freeze route parse `billing_profile`, accept only `product_processing` or `pod_random_v1`, call `_require_pod_create_permission` for POD, and pass the profile to `freeze_batch_points`. Add the fixed POD profile field to `product_batch_freeze_payload`; leave settlement payload folding and provider-key grants unchanged.

- [x] **Step 4: Run focused contract tests and verify GREEN**

Run: `cd local-runtime && python -m pytest -q tests/test_pod_customization_billing_contract.py tests/test_pod_customization_remote_billing.py tests/test_batch_direct_full_chain.py`

Expected: all tests pass.

### Task 3: Regression verification and commit

**Files:**
- Verify only; no new production files.

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: verified POD random billing with unchanged product-processing behavior.

- [x] **Step 1: Run billing, POD, and product-processing regressions**

Run: `cd local-runtime && python -m pytest -q tests/test_billing_batch_pricing.py tests/test_batch_direct_full_chain.py tests/test_product_processing_direct_billing.py tests/test_pod_customization_billing_contract.py tests/test_pod_customization_remote_billing.py wh_local/modules/pod_customization/tests`

Expected: all selected tests pass.

- [x] **Step 2: Run compile and diff checks**

Run: `cd local-runtime && python -m compileall -q wh_local`

Run: `git diff --check`

Expected: both commands exit 0.

- [x] **Step 3: Commit only POD billing files**

```bash
git add docs/superpowers/plans/2026-08-21-pod-random-price.md \
  local-runtime/wh_local/db.py \
  local-runtime/wh_local/billing.py \
  local-runtime/wh_local/customer/auth_server.py \
  local-runtime/wh_local/modules/pod_customization/billing_contract.py \
  local-runtime/tests/test_billing_batch_pricing.py \
  local-runtime/tests/test_batch_direct_full_chain.py \
  local-runtime/tests/test_pod_customization_billing_contract.py \
  local-runtime/tests/test_pod_customization_remote_billing.py
git commit -m "feat(pod): randomize per-style billing"
```
