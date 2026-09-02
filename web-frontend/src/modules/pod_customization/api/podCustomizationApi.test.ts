import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { podStyleTitleRegenerateRequest } from "../data/styleTitleRequest.ts";
import { podBillingPendingRequest, podBillingResumeRequest } from "../data/billingRuns.ts";
import type { PodBillingRunListResponse } from "../types/index.ts";

test("title retry uses the isolated POD endpoint", () => {
  assert.deepEqual(podStyleTitleRegenerateRequest("batch / 1", 12), {
    path: "/api/pod-customization/batches/batch%20%2F%201/styles/12/title/regenerate",
    options: { method: "POST", body: {} },
  });
});

test("billing recovery uses the authenticated list and asynchronous resume endpoints", () => {
  assert.deepEqual(podBillingPendingRequest(), {
    path: "/api/pod-customization/billing-runs/pending",
  });
  assert.deepEqual(podBillingResumeRequest("run / 1"), {
    path: "/api/pod-customization/billing-runs/run%20%2F%201/resume",
    options: { method: "POST", body: {} },
  });
  const backendPayload = {
    runs: [{
      id: "run-1", action_type: "batch_initial", target_id: "batch-1", batch_id: "batch-1",
      freeze_id: "freeze-1", rule_version: 7, expires_at: "2099-01-01T00:00:00Z",
      status: "auth_required", error_message: "sign in", created_at: "2026-08-21T00:00:00Z",
      updated_at: "2026-08-21T00:01:00Z",
    }], total: 1,
  } satisfies PodBillingRunListResponse;
  assert.equal(backendPayload.runs[0].status, "auth_required");
  assert.equal(backendPayload.runs[0].rule_version, 7);
});

test("batch retry posts separated image and title style selections", () => {
  const source = readFileSync(new URL("./podCustomizationApi.ts", import.meta.url), "utf8");
  assert.match(source, /retryFailed: \(batchId: string, body: PodBatchRetryRequest\) => httpJson<PodBatchRetryResult>\(/);
  assert.match(source, /batches\/\$\{encodeURIComponent\(batchId\)\}\/retry-failed/);
  assert.match(source, /\{ method: "POST", body \}/);
});

test("export selection updates the individual style through the POD endpoint", () => {
  const source = readFileSync(new URL("./podCustomizationApi.ts", import.meta.url), "utf8");
  assert.match(source, /updateExportSelection: \(batchId: string, styleIndex: number, selected: boolean\) => httpJson<\{ style_index: number; export_selected: boolean \}>\(/);
  assert.match(source, /batches\/\$\{encodeURIComponent\(batchId\)\}\/styles\/\$\{styleIndex\}\/export-selection/);
  assert.match(source, /\{ method: "PATCH", body: \{ selected \} \}/);
});
