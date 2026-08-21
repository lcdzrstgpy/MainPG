import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./pluginOneboundCaptureApi.ts", import.meta.url), "utf8");

test("desktop plugin capture API exposes prepared-batch start and failed-item retry", () => {
  assert.match(source, /\/desktop\/data-collection\/plugin-onebound-batches/);
  assert.match(source, /listPluginOneboundCaptureBatches/);
  assert.match(source, /getPluginOneboundCaptureBatch/);
  assert.match(source, /listPluginOneboundCaptureItems/);
  assert.match(source, /listPluginOneboundCaptureItems\(batchId: string, limit = 80, offset = 0\)/);
  assert.match(source, /items\?\$\{query\}/);
  assert.match(source, /retryPluginOneboundCaptureFailures/);
  assert.match(source, /retry-failed/);
  assert.match(source, /startPluginOneboundCaptureBatch/);
  assert.match(source, /\$\{PLUGIN_BATCHES_PATH\}\/\$\{encodeURIComponent\(batchId\)\}\/start/);
  assert.doesNotMatch(source, /createBatch|pause|resume|cancel/);
});
