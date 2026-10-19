import assert from "node:assert/strict";
import test from "node:test";

import {
  formatShopCollectionError,
  getShopBatchActions,
  isActiveShopBatchStatus,
  isTerminalShopBatchStatus,
  shopBatchProgress,
  shopBatchStatusLabel,
  shopItemDisplayTitle,
  type ShopCollectionBatch,
  type ShopCollectionItem,
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
  assert.equal(shopBatchProgress(batch({ status: "completed", discovered_count: 0, succeeded_count: 0, failed_count: 0 })), 100);
  assert.equal(shopBatchProgress(batch({ status: "failed", discovered_count: 0, succeeded_count: 0, failed_count: 0 })), 0);
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
  assert.deepEqual(getShopBatchActions(batch({ status: "failed", failed_count: 0, discovered_count: 0, listing_complete: false })), { pause: false, resume: false, cancel: false, retryFailed: true });
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

function shopItem(overrides: Partial<ShopCollectionItem> = {}): ShopCollectionItem {
  return {
    item_id: "batch-1:1003429062189",
    batch_id: "batch-1",
    workspace_id: "workspace-1",
    offer_id: "1003429062189",
    source_url: "https://detail.1688.com/offer/1003429062189.html",
    source_title: "",
    detail_status: "succeeded",
    intake_action: "created",
    created_at: "2026-10-19T00:00:00Z",
    updated_at: "2026-10-19T00:00:00Z",
    ...overrides,
  };
}

test("uses the real product title from the item row or the enriched candidate", () => {
  assert.equal(shopItemDisplayTitle(shopItem({ source_title: "保温杯 500ml" })), "保温杯 500ml");
  // 历史批次：item.source_title 为空，但 candidate_json 里已经有详情标题。
  assert.equal(
    shopItemDisplayTitle(shopItem({ candidate: { source_title: "收纳盒 大号" } })),
    "收纳盒 大号",
  );
  assert.equal(
    shopItemDisplayTitle(shopItem({ candidate: { title: "travel mug" } })),
    "travel mug",
  );
  assert.equal(
    shopItemDisplayTitle(shopItem({ candidate: { product_name: "折叠衣架" } })),
    "折叠衣架",
  );
});

test("never shows the offer id as the product name", () => {
  assert.equal(
    shopItemDisplayTitle(shopItem({ source_title: "1003429062189" })),
    "未获取到商品名",
  );
  assert.equal(shopItemDisplayTitle(shopItem()), "未获取到商品名");
  assert.equal(
    shopItemDisplayTitle(shopItem({ detail_status: "pending" })),
    "商品信息补全中…",
  );
  assert.equal(
    shopItemDisplayTitle(shopItem({ detail_status: "running" })),
    "商品信息补全中…",
  );
});
