# ADR: POD Item-Level Claim / Heartbeat / Report Architecture

**Date:** 2026-09-03  
**Status:** Deferred — P2 evaluation gate  
**Authors:** Engineering (via reliability planning session)

---

## Context

The current POD batch execution model holds the entire batch lifecycle inside a long-running server-side coordinator thread. A batch that stops making progress — because the coordinator hangs, a provider future never returns, or the process restarts — remains stuck in an active status with no automatic recovery path until the next service restart or manual intervention.

Tasks 1–4 of the 2026-09-03 reliability sprint address this by adding execution epochs, stale-worker fencing, progress deadlines, and a live reaper. Those changes substantially reduce the blast radius of a stuck batch without requiring a change to the execution model.

This ADR records the next architectural step — item-level claim/report — as a design decision deferred to a later cycle. It must not be implemented until the P0/P1 work is verified in production and the billing ledger mapping described below is approved.

---

## Problem

Even with epochs and a reaper, the coordinator model has an inherent coupling: the server thread must remain alive and making progress for the batch to complete. If the process crashes mid-batch, the reaper recovers it on the next restart, but the window between crash and restart still produces a stuck batch from the user's perspective. More fundamentally, the server is doing work (calling the image provider) that it does not need to own — the coordination overhead adds latency and complicates horizontal scaling.

The item-level claim/report model shifts provider execution responsibility: the server owns task definition, authorization, and billing; the client (desktop application) owns the actual provider call lifecycle. This is already how product processing works via the freeze/settle billing flow.

---

## Proposed API Contract

All endpoints continue to route through the server. The browser never receives a provider API key. Provider calls are submitted to the server gateway, which owns the provider credential and task ledger.

### Claim items

```
POST /api/pod-customization/batches/{batch_id}/items/claim
Request:  { "count": 1–8 }
Response: {
  "items": [
    {
      "item_id": "...",
      "claim_token": "...",
      "fence": 7,
      "lease_expires_at": "2026-09-03T10:05:00Z",
      "generation_params": { ... }
    }
  ]
}
```

`fence` is the current `execution_epoch` of the batch at claim time. `lease_expires_at` is five minutes from now. `generation_params` contains everything the client needs to display progress — it does not contain a provider key.

### Heartbeat

```
POST /api/pod-customization/items/{item_id}/heartbeat
Request:  { "claim_token": "...", "fence": 7 }
Response: { "lease_expires_at": "2026-09-03T10:10:00Z" }
```

Renews the lease by five minutes. Returns `409` if the claim token does not match the current holder or the fence is stale.

### Report result

```
POST /api/pod-customization/items/{item_id}/report
Request: {
  "claim_token": "...",
  "fence": 7,
  "idempotency_key": "...",
  "status": "completed" | "failed",
  "provider_task_id": "...",
  "result_url": "...",
  "error_code": "..."
}
Response: { "accepted": true, "item_status": "completed" | "failed" }
```

The server validates `claim_token` and `fence` before writing. A duplicate `idempotency_key` returns `200` with the stored terminal result rather than `409` — the client can safely retry on network failure. A stale fence (batch was reaped between claim and report) returns `409`; the client discards the result and moves on.

The server, not the client, submits the provider task to the gateway and reconciles a durable `provider_task_id` after client loss. One successful item maps to one immutable server usage record. Failed and uncertain provider outcomes are reconciled through that ledger rather than re-submitted by the client.

---

## Billing Ledger Implications

The current `billing_run.settle()` model is a single terminal call that settles the entire batch plan at once. Item-level reporting requires a different ledger shape: each report creates one usage record, and the batch billing summary is derived from the union of those records rather than from a single settlement event.

This is the primary reason this architecture is deferred. Before implementation, the following must be resolved:

The server-managed gateway already has per-request usage records and a reconciliation loop (`reconcile_pod_gateway_requests`). The question is whether item-level POD reports can reuse that ledger directly, or whether a separate POD item ledger is needed. If the gateway ledger is reused, the `billing_run` abstraction may be retired for batch_initial actions, which affects the existing pause/resume/retry flows. That scope must be approved before a migration is written.

---

## Non-Goals for This Cycle

The following are explicitly out of scope and must not be included in any implementation derived from this ADR:

Browser-direct provider calls: the desktop application must never hold a SuChuang or other provider API key. The server gateway owns provider credentials in all execution paths.

Automatic replay of uncertain calls: a billing outcome whose durable status is `started` (provider accepted the call but result unknown) is never automatically re-submitted by the client. It is resolved only by server-side reconciliation against the provider's own task status API.

Replacement of the existing billing ledger before approval: `billing_run.settle()` and the associated `pod_customization_billing_runs` schema remain in place until a migration plan is reviewed and approved.

New batch statuses: the existing status set (`queued`, `generating_patterns`, `compositing`, `generating_titles`, `paused`, `cancelled`, `completed`, `partial_failure`, `failed`, `billing_auth_required`, `settlement_pending`) is frozen. Any new status requires a separate migration review.

---

## Gate Criteria for P2 Evaluation

This ADR moves from deferred to active only when all of the following are true:

1. P0 epoch fencing, deadline, and reaper work is deployed and has run without false-positive reaps for at least two weeks in production.
2. P1 server gateway reconciliation improvements are complete and the reconciliation loop is confirmed to be settling all gateway-submitted tasks within the expected window.
3. The billing ledger mapping question above has a written answer approved by whoever owns the billing schema — specifically, whether item-level POD reports reuse the gateway ledger or require a new table.
4. A proof-of-concept implementation covers at least the claim and report endpoints, with tests covering stale-fence rejection, duplicate idempotency key replay, and lease expiry, before any migration to production code.

---

## Related Work

- `migrations/012_batch_execution_fencing.sql` — epoch and progress lease columns
- `repository.py: claim_batch_with_epoch`, `reap_stuck_batches` — P0 fencing
- `worker.py: BatchExecutionContext`, `POD_PROGRESS_TIMEOUT_SECONDS` — P0 deadlines
- `service.py: reap_stuck_batches_once`, `_run_stuck_batch_reaper` — P0 live reaper
- `auth_server.py: _run_pod_gateway_reconcile_loop` — P1 gateway reconciliation
- `remote_billing.py: RemotePodBillingCoordinator(server_managed=True)` — existing server-managed gateway path, the foundation this ADR builds on
