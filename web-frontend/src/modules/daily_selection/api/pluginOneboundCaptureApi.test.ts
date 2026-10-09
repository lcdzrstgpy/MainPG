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
  assert.doesNotMatch(source, /\/shop-batches|shopCollectionApi/);
});

test("desktop plugin capture API exposes candidate review and SKU backfill endpoints", () => {
  assert.match(source, /listPluginOneboundCandidates/);
  assert.match(source, /\/candidates/);
  assert.match(source, /getPluginSkuRepullState/);
  assert.match(source, /sku-repull\/state/);
  assert.match(source, /startPluginSkuRepull/);
  assert.match(source, /sku-repull\/start/);
  assert.match(source, /cancelPluginSkuRepull/);
  assert.match(source, /sku-repull\/cancel/);
  assert.match(source, /confirmPluginCandidates/);
  assert.match(source, /\/confirm/);
  assert.match(source, /offer_ids/);
});
