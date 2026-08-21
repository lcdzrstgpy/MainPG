import assert from "node:assert/strict";
import test from "node:test";

import { POD_BATCH_COUNTS, businessFieldsForApi, isActiveBatchStatus, isPodBatchCount, podBatchStatusLabel } from "./podCustomizationModel.ts";

test("POD count accepts custom integers from 1 through 200", () => {
  assert.deepEqual(POD_BATCH_COUNTS, [2, 10, 20, 40, 60, 100]);
  assert.equal(isPodBatchCount(1), true);
  assert.equal(isPodBatchCount(200), true);
  assert.equal(isPodBatchCount(0), false);
  assert.equal(isPodBatchCount(201), false);
  assert.equal(isPodBatchCount(1.5), false);
});

test("billing recovery statuses are visible and only settlement keeps polling", () => {
  assert.equal(isActiveBatchStatus("settlement_pending"), true);
  assert.equal(isActiveBatchStatus("billing_auth_required"), false);
  assert.equal(podBatchStatusLabel("settlement_pending"), "等待计费结算");
  assert.equal(podBatchStatusLabel("billing_auth_required"), "需要重新授权");
});

test("business list fields are normalized at the API boundary", () => {
  const payload = businessFieldsForApi({
    product_name: "旅行杯",
    product_category: "户外饮具",
    target_market: "US",
    target_audience: "通勤",
    core_selling_points: "轻量、防漏",
    design_theme: "山野",
    style_keywords: "复古, 粗线条",
    color_preferences: "松绿、砂岩黄",
    excluded_elements: "Logo",
  });
  assert.deepEqual(payload.core_selling_points, ["轻量", "防漏"]);
  assert.deepEqual(payload.style_keywords, ["复古", "粗线条"]);
});
