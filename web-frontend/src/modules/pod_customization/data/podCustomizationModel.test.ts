import assert from "node:assert/strict";
import test from "node:test";

import {
  POD_BATCH_COUNTS,
  businessFieldsForApi,
  canCancelPodBatch,
  canPausePodBatch,
  canRegeneratePodStyle,
  canRegeneratePodStyleTitle,
  isBillingInterruptedPodBatch,
  canRetryPodBatchFailed,
  canResumePodBatch,
  formatPodBatchWaitingTime,
  groupPodStyleRows,
  isActiveBatchStatus,
  isPodBatchCount,
  listingFieldsForApi,
  podBatchStatusLabel,
  podBatchStatusDetail,
} from "./podCustomizationModel.ts";

test("POD style results present the lifestyle panel as the primary image and hero as material", () => {
  const rows = groupPodStyleRows({
    style_grid: true,
    business_fields: { product_name: "Laundry Hamper" },
    style_titles: [],
    items: [
      { id: "hero", style_index: 1, variant_index: 1, role: "hero", status: "completed", public_url: "https://images.example.com/hero.png" },
      { id: "detail-a", style_index: 1, variant_index: 2, role: "detail_a", status: "completed", public_url: "https://images.example.com/detail-a.png" },
      { id: "detail-b", style_index: 1, variant_index: 3, role: "detail_b", status: "completed", public_url: "https://images.example.com/detail-b.png" },
      { id: "lifestyle", style_index: 1, variant_index: 4, role: "lifestyle", status: "completed", public_url: "https://images.example.com/lifestyle.png" },
    ],
  });

  assert.deepEqual(rows[0].results.map((item) => item?.id), ["lifestyle", "detail-a", "detail-b", "hero"]);
});

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

test("pause cancel and resume guards follow the backend state gate", () => {
  assert.equal(canPausePodBatch("queued"), true);
  assert.equal(canPausePodBatch("generating_titles"), true);
  assert.equal(canPausePodBatch("paused"), false);
  assert.equal(canPausePodBatch("completed"), false);
  assert.equal(canCancelPodBatch("generating_patterns"), true);
  assert.equal(canCancelPodBatch("pausing"), true);
  assert.equal(canCancelPodBatch("paused"), true);
  assert.equal(canCancelPodBatch("completed"), false);
  assert.equal(canResumePodBatch("paused"), true);
  assert.equal(canResumePodBatch("queued"), false);
  assert.equal(canRetryPodBatchFailed("cancelled"), true);
  // 账务结算与生成结果分开：结算待处理不能锁死失败项重试。
  assert.equal(canRetryPodBatchFailed("settlement_pending"), false);
  assert.equal(canRetryPodBatchFailed("billing_auth_required"), false);
});

test("pause and cancel statuses render labels and keep pausing polling", () => {
  assert.equal(podBatchStatusLabel("pausing"), "暂停中");
  assert.equal(podBatchStatusLabel("paused"), "已暂停");
  assert.equal(podBatchStatusLabel("cancelling"), "取消中");
  assert.equal(podBatchStatusLabel("cancelled"), "已取消");
  assert.equal(isActiveBatchStatus("pausing"), true);
  assert.equal(isActiveBatchStatus("cancelling"), true);
  assert.equal(isActiveBatchStatus("paused"), false);
  assert.equal(podBatchStatusDetail("pausing"), "已提交的款正在完成，其余款不会继续发起。");
  assert.equal(podBatchStatusDetail("paused"), "已暂停，可继续剩余款式。");
});

test("active POD batches show the elapsed time since creation", () => {
  const createdAt = "2026-08-24T00:00:00.000Z";
  const now = Date.parse("2026-08-24T00:02:35.000Z");

  assert.equal(formatPodBatchWaitingTime(createdAt, now), "2分35秒");
  assert.equal(formatPodBatchWaitingTime(createdAt, Date.parse("2026-08-24T00:00:35.000Z")), "35秒");
  assert.equal(formatPodBatchWaitingTime(createdAt, Date.parse("2026-08-23T23:59:59.000Z")), "0秒");
  assert.equal(formatPodBatchWaitingTime("not-a-date", now), "0秒");
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

test("listing fields normalize every SKU together with its own dimensions and weight", () => {
  const result = listingFieldsForApi({
    title_mode: "long",
    declared_price: "18.5",
    suggested_price_usd: "29.99",
    category_name: " 家居收纳 > 洗衣篮 ",
    skus: [
      { name: "  米白 ", length_cm: "30", width_cm: "20", height_cm: "10", weight_g: "450" },
      { name: "深蓝  ", length_cm: "31", width_cm: "21", height_cm: "11", weight_g: "470" },
    ],
  });

  assert.deepEqual(result, {
    value: {
      title_mode: "long",
      declared_price: 18.5,
      suggested_price_usd: 29.99,
      category_name: "家居收纳 > 洗衣篮",
      skus: [
        { name: "米白", length_cm: 30, width_cm: 20, height_cm: 10, weight_g: 450 },
        { name: "深蓝", length_cm: 31, width_cm: 21, height_cm: 11, weight_g: 470 },
      ],
    },
  });
});

test("listing fields require at least one complete SKU", () => {
  const base = {
    title_mode: "long" as const,
    declared_price: "18.5",
    suggested_price_usd: "29.99",
    category_name: "家居收纳",
  };

  assert.deepEqual(listingFieldsForApi({ ...base, skus: [] }), { error: "请至少填写一个 SKU。" });
  assert.deepEqual(listingFieldsForApi({
    ...base,
    skus: [{ name: " ", length_cm: "30", width_cm: "20", height_cm: "10", weight_g: "450" }],
  }), { error: "SKU 名称不能为空。" });
  assert.deepEqual(listingFieldsForApi({
    ...base,
    skus: [{ name: "默认款", length_cm: "0", width_cm: "20", height_cm: "10", weight_g: "450" }],
  }), { error: "SKU「默认款」的长度必须是大于 0 的有效数字。" });
  assert.deepEqual(listingFieldsForApi({
    ...base,
    skus: [{ name: "默认款", length_cm: "30", width_cm: "20", height_cm: "10", weight_g: "0" }],
  }), { error: "SKU「默认款」的重量必须是大于 0 的有效数字。" });
});

test("listing fields reject more than 100 SKUs", () => {
  const result = listingFieldsForApi({
    title_mode: "long",
    declared_price: "18.5",
    suggested_price_usd: "29.99",
    category_name: "家居收纳",
    skus: Array.from({ length: 101 }, (_, index) => ({
      name: `SKU ${index + 1}`,
      length_cm: "30",
      width_cm: "20",
      height_cm: "10",
      weight_g: "450",
    })),
  });

  assert.deepEqual(result, { error: "SKU 最多可添加 100 个。" });
});

test("listing fields reject SKU names longer than 120 characters", () => {
  const result = listingFieldsForApi({
    title_mode: "long",
    declared_price: "18.5",
    suggested_price_usd: "29.99",
    category_name: "家居收纳",
    skus: [{ name: "款".repeat(121), length_cm: "30", width_cm: "20", height_cm: "10", weight_g: "450" }],
  });

  assert.deepEqual(result, { error: "SKU 名称不能超过 120 个字符。" });
});

test("successful listing-ready POD results can regenerate title and whole style outside billing interruption", () => {
  const publicResults = Array.from({ length: 4 }, () => ({
    status: "completed" as const,
    public_url: "https://images.example.com/result.png",
  }));
  assert.equal(canRegeneratePodStyle("completed", "completed", true), true);
  assert.equal(canRegeneratePodStyle("failed", "failed"), true);
  assert.equal(canRegeneratePodStyleTitle("completed", "completed", publicResults), true);
  assert.equal(canRegeneratePodStyleTitle("partial_failure", "failed", publicResults), true);
  assert.equal(canRegeneratePodStyle("settlement_pending", "completed", true), false);
  assert.equal(canRegeneratePodStyleTitle("billing_auth_required", "completed", publicResults), false);
  assert.equal(isBillingInterruptedPodBatch("settlement_pending"), true);
});

test("style rows preserve the backend export selection and default legacy rows to selected", () => {
  const base = {
    style_grid: true,
    business_fields: { product_name: "Laundry Hamper" },
    items: Array.from({ length: 4 }, (_, index) => ({
      id: `result-${index + 1}`,
      style_index: 1,
      variant_index: index + 1,
      status: "completed" as const,
      public_url: `https://images.example.com/${index + 1}.png`,
    })),
  };
  const unselected = groupPodStyleRows({
    ...base,
    style_titles: [{ style_index: 1, style_task_id: "title-1", status: "completed", title: "Selected title", listing_ready: true, export_selected: false, updated_at: "2026-09-02T00:00:00Z" }],
  });
  const legacy = groupPodStyleRows({ ...base, style_titles: [] });

  assert.equal(unselected[0].export_selected, false);
  assert.equal(legacy[0].export_selected, true);
});
