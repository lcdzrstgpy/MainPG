import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { podStyleTitleRegenerateRequest } from "../data/styleTitleRequest.ts";

test("title retry uses the isolated POD endpoint", () => {
  assert.deepEqual(podStyleTitleRegenerateRequest("batch / 1", 12), {
    path: "/api/pod-customization/batches/batch%20%2F%201/styles/12/title/regenerate",
    options: { method: "POST", body: {} },
  });
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
