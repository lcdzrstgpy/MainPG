import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  buildReplicaBatchRequest,
  createEmptyReplicaTarget,
  nextReplicaClientRequestId,
  type ReplicaTargetDraft,
} from "../data/podReplicaModel.ts";

const apiSource = readFileSync(new URL("./podReplicaApi.ts", import.meta.url), "utf8");

function readyTarget(clientId: string, assetId: string, productName: string): ReplicaTargetDraft {
  const base = createEmptyReplicaTarget(clientId);
  return {
    ...base,
    assetId,
    filename: `${assetId}.png`,
    productName,
    listingFields: { title_mode: "long", suggested_price_usd: "19.99", category_name: "抱枕", skus: [{ name: "40cm", declared_price: "6", weight_g: "300" }] },
    specCard: { enabled: true, style: "light", corner: "bottom-right", display_unit: "cm", cells: [["SKU", "Length", "Width", "Height"], ["40cm", "40", "40", "10"]] },
  };
}

test("replica upload posts multipart file and role to the replica images endpoint", () => {
  assert.match(apiSource, /`\$\{API_BASE\}\/replica\/images`/);
  assert.match(apiSource, /form\.append\("file", file\);/);
  assert.match(apiSource, /form\.append\("role", role\);/);
  assert.match(apiSource, /export type ReplicaImageRole|role: ReplicaImageRole/);
});

test("replica batch endpoints use the replica paths and json payloads", () => {
  assert.match(apiSource, /createBatch: \(body: CreateReplicaBatchRequest\) => httpJson<ReplicaBatch>\(`\$\{API_BASE\}\/replica\/batches`, \{\s*method: "POST",\s*body,/);
  assert.match(apiSource, /listBatches: \(limit = 20, offset = 0\) => httpJson<ReplicaBatchListResponse>\(/);
  assert.match(apiSource, /`\$\{API_BASE\}\/replica\/batches\?\$\{new URLSearchParams/);
  assert.match(apiSource, /getBatch: \(batchId: string\) => httpJson<ReplicaBatch>\(`\$\{API_BASE\}\/replica\/batches\/\$\{encodeURIComponent\(batchId\)\}`\)/);
  assert.match(apiSource, /assetExists/);
  assert.match(apiSource, /\/assets\/\$\{encodeURIComponent\(assetId\)\}/);
});

test("create payload uses snake_case keys required by the backend contract", () => {
  const request = buildReplicaBatchRequest({
    client_request_id: "req-1",
    source_asset_id: "asset_source",
    targets: [readyTarget("c1", "asset_a", "抱枕")],
  });
  assert.deepEqual(Object.keys(request), ["client_request_id", "source_asset_id", "title", "creative_prompt", "targets"]);
  assert.deepEqual(Object.keys(request.targets[0]), ["target_asset_id", "product_name", "listing_fields"]);
  const listing = request.targets[0].listing_fields;
  assert.ok("suggested_price_usd" in listing);
  assert.ok("category_name" in listing);
  assert.ok("spec_card" in listing);
});

test("client_request_id stays stable across a retried submit and only changes on explicit recreate", () => {
  const targets = [readyTarget("c1", "asset_a", "抱枕"), readyTarget("c2", "asset_b", "收纳篮")];
  const requestId = nextReplicaClientRequestId();
  const firstAttempt = buildReplicaBatchRequest({ client_request_id: requestId, source_asset_id: "asset_source", targets });
  // 网络重试：复用同一 client_request_id，请求体与首次提交完全一致。
  const retryAttempt = buildReplicaBatchRequest({ client_request_id: requestId, source_asset_id: "asset_source", targets });
  assert.equal(firstAttempt.client_request_id, requestId);
  assert.deepEqual(retryAttempt, firstAttempt);

  // 显式重新创建任务：使用新的请求 ID。
  const recreated = buildReplicaBatchRequest({ client_request_id: nextReplicaClientRequestId(), source_asset_id: "asset_source", targets });
  assert.notEqual(recreated.client_request_id, requestId);
});
