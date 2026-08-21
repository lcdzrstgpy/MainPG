import assert from "node:assert/strict";
import test from "node:test";

import {
  formatShopCollectionError,
  getShopBatchActions,
  isActiveShopBatchStatus,
  isTerminalShopBatchStatus,
  shopBatchProgress,
  shopBatchStatusLabel,
  type ShopCollectionBatch,
} from "./shopCollectionModel.ts";

function batch(overrides: Partial<ShopCollectionBatch> = {}): ShopCollectionBatch {
  return {
    batch_id: "batch-1",
    workspace_id: "workspace-1",
    actor_id: "actor-1",
    status: "enriching",
    shop_sid: "seller-1",
    shop_name: "示例店铺",
    discovered_count: 10,
    succeeded_count: 3,
    failed_count: 1,
    created_count: 3,
    refreshed_count: 0,
    skipped_count: 0,
    created_at: "2026-08-20T00:00:00Z",
    updated_at: "2026-08-20T00:01:00Z",
    ...overrides,
  };
}

test("maps every server batch status to a readable Chinese label", () => {
  assert.equal(shopBatchStatusLabel("queued"), "等待开始");
  assert.equal(shopBatchStatusLabel("resolving"), "识别店铺");
  assert.equal(shopBatchStatusLabel("listing"), "发现商品");
  assert.equal(shopBatchStatusLabel("enriching"), "补全详情");
  assert.equal(shopBatchStatusLabel("pausing"), "正在暂停");
  assert.equal(shopBatchStatusLabel("paused"), "已暂停");
  assert.equal(shopBatchStatusLabel("cancelling"), "正在取消");
  assert.equal(shopBatchStatusLabel("cancelled"), "已取消");
  assert.equal(shopBatchStatusLabel("completed"), "采集完成");
  assert.equal(shopBatchStatusLabel("partial"), "部分完成");
  assert.equal(shopBatchStatusLabel("failed"), "采集失败");
});
test("calculates finite progress from the counters returned by the batch API", () => {
  assert.equal(shopBatchProgress(batch()), 40);
  assert.equal(shopBatchProgress(batch({ discovered_count: 0, status: "listing" })), 0);
  assert.equal(shopBatchProgress(batch({ status: "partial", discovered_count: 10, succeeded_count: 7, failed_count: 3 })), 100);
  assert.equal(shopBatchProgress(batch({ status: "cancelled", discovered_count: 10, succeeded_count: 3, failed_count: 1 })), 40);
  assert.equal(shopBatchProgress(batch({ discovered_count: Number.NaN, succeeded_count: Number.POSITIVE_INFINITY, failed_count: -3 })), 0);
});

test("identifies active and terminal server lifecycle states", () => {
  assert.equal(isActiveShopBatchStatus("enriching"), true);
  assert.equal(isActiveShopBatchStatus("paused"), false);
  assert.equal(isActiveShopBatchStatus("cancelling"), true);
  assert.equal(isTerminalShopBatchStatus("partial"), true);
  assert.equal(isTerminalShopBatchStatus("failed"), true);
  assert.equal(isTerminalShopBatchStatus("paused"), false);
});

test("only exposes controls supported by the current batch state", () => {
  assert.deepEqual(getShopBatchActions(batch()), { pause: true, resume: false, cancel: true, retryFailed: false });
  assert.deepEqual(getShopBatchActions(batch({ status: "paused" })), { pause: false, resume: true, cancel: true, retryFailed: false });
  assert.deepEqual(getShopBatchActions(batch({ status: "partial" })), { pause: false, resume: false, cancel: false, retryFailed: true });
  assert.deepEqual(getShopBatchActions(batch({ status: "completed", failed_count: 0 })), { pause: false, resume: false, cancel: false, retryFailed: false });
  assert.deepEqual(getShopBatchActions(batch({ status: "completed", failed_count: 1 })), { pause: false, resume: false, cancel: false, retryFailed: false });
  assert.deepEqual(getShopBatchActions(batch({ status: "cancelled", failed_count: 1 })), { pause: false, resume: false, cancel: false, retryFailed: false });
  assert.deepEqual(getShopBatchActions(batch({ status: "failed", failed_count: 1 })), { pause: false, resume: false, cancel: false, retryFailed: true });
});

test("formats API errors without exposing authorization or credential values", () => {
  assert.equal(formatShopCollectionError(new Error("请求失败（503）")), "请求失败（503）");
  const formatted = formatShopCollectionError(new Error(
    "api-key: api-key-secret; {\"api_secret\":\"api-secret-value\", \"token\": \"token-value\"}; x-api-key=header-key; Authorization: Basic basic-credential; session_id=session-value; Cookie: session=cookie-value",
  ));
  for (const secret of ["api-key-secret", "api-secret-value", "token-value", "header-key", "basic-credential", "session-value", "cookie-value"]) {
    assert.doesNotMatch(formatted, new RegExp(secret));
  }
  assert.match(formatted, /\[已隐藏\]/);
  assert.equal(formatShopCollectionError(null), "整店采集请求失败，请稍后重试");
});
