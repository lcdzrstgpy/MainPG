import assert from "node:assert/strict";
import test from "node:test";

import {
  POD_BATCH_COUNTS,
  POD_STYLE_PLANNING_OPTIONS,
  EMPTY_POD_BUSINESS_FIELDS,
  buildPromptV1,
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
  isPodStylePlanning,
  isPristineCreativeEdit,
  listingFieldsForApi,
  normalizeStylePlanning,
  podBatchStatusLabel,
  podBatchStatusDetail,
} from "./podCustomizationModel.ts";

test("样式规划 fixed two-choice: unknown or legacy free text falls back to unselected", () => {
  assert.deepEqual([...POD_STYLE_PLANNING_OPTIONS], ["全覆盖", "半覆盖"]);

  assert.equal(normalizeStylePlanning("全覆盖"), "全覆盖");
  assert.equal(normalizeStylePlanning("半覆盖"), "半覆盖");

  // 旧草稿里的自由文本不再合法，视为未选择，交给用户重新选。
  assert.equal(normalizeStylePlanning("花纹铺满包身、提手处留白"), "");
  assert.equal(normalizeStylePlanning(""), "");
  assert.equal(normalizeStylePlanning(undefined), "");
  assert.equal(normalizeStylePlanning(null), "");

  assert.equal(isPodStylePlanning("全覆盖"), true);
  assert.equal(isPodStylePlanning("全铺满"), false);
});

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

test("settlement-pending status stays labeled separately from generation failure and is not an active generation state", () => {
  assert.equal(isActiveBatchStatus("settlement_pending"), false);
  assert.equal(podBatchStatusLabel("settlement_pending"), "等待计费结算");
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
  assert.equal(canRetryPodBatchFailed("settlement_pending"), true);
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
    style_planning: " 花纹铺满杯身 ",
    style_keywords: "复古, 粗线条",
    color_preferences: "松绿、砂岩黄",
    excluded_elements: "Logo",
  });
  assert.deepEqual(payload.core_selling_points, ["轻量", "防漏"]);
  assert.deepEqual(payload.style_keywords, ["复古", "粗线条"]);
  assert.equal(payload.style_planning, "花纹铺满杯身");
});

test("built-in v1 prompt carries the renamed batch-wide fields and never the element list", () => {
  const prompt = buildPromptV1({
    ...EMPTY_POD_BUSINESS_FIELDS,
    product_name: "绗缝手提托特包",
    design_theme: "美式西南复古牛仔荒野风",
    style_planning: "花纹铺满包身、提手处留白",
    style_keywords: "复古牛仔靴插画、沙漠仙人掌、绿松石配饰",
  });
  assert.ok(prompt.includes("主题整批统一风格：美式西南复古牛仔荒野风"));
  assert.ok(prompt.includes("样式规划：花纹铺满包身、提手处留白"));
  assert.ok(!prompt.includes("风格关键词"));
  assert.ok(!prompt.includes("复古牛仔靴插画"));
});

test("pristine v1 snapshot detection follows the renamed labels", () => {
  const prompt = buildPromptV1({ ...EMPTY_POD_BUSINESS_FIELDS, product_name: "包" });
  assert.equal(isPristineCreativeEdit(prompt), true);
  assert.equal(isPristineCreativeEdit("手写的自定义方向：加一只小狗"), false);
  // 旧版快照（含“设计主题/风格关键词”标签）不再视为 pristine，避免旧标签冻结。
  const legacy = prompt.replace("主题整批统一风格：", "设计主题：").replace("样式规划：", "风格关键词：");
  assert.equal(isPristineCreativeEdit(legacy), false);
});

test("listing fields normalize every SKU with its declared price and weight, and resolve dimensions from the spec card", () => {
  const specCard = {
    enabled: true,
    style: "light" as const,
    corner: "bottom-right" as const,
    cells: [
      ["尺寸图", "长", "宽", "高"],
      ["米白", "30", "20", "10"],
      ["深蓝", "31", "21", "11"],
    ],
  };
  const result = listingFieldsForApi({
    title_mode: "long",
    suggested_price_usd: "29.99",
    category_name: " 家居收纳 > 洗衣篮 ",
    skus: [
      { name: "  米白 ", declared_price: "18.5", weight_g: "450" },
      { name: "深蓝  ", declared_price: "19.9", weight_g: "470" },
    ],
  }, specCard);

  assert.deepEqual(result, {
    value: {
      title_mode: "long",
      suggested_price_usd: 29.99,
      category_name: "家居收纳 > 洗衣篮",
      skus: [
        { name: "米白", declared_price: 18.5, weight_g: 450 },
        { name: "深蓝", declared_price: 19.9, weight_g: 470 },
      ],
      spec_card: specCard,
    },
  });
});

test("listing fields require one complete SKU with its dimensions", () => {
  const base = {
    title_mode: "long" as const,
    suggested_price_usd: "29.99",
    category_name: "家居收纳",
  };
  const specCard = {
    enabled: true,
    style: "light" as const,
    corner: "bottom-right" as const,
    cells: [
      ["尺寸图", "长", "宽", "高"],
      ["默认款", "30", "20", "10"],
    ],
  };

  assert.deepEqual(listingFieldsForApi({ ...base, skus: [] }, specCard), { error: "请至少填写一个 SKU。" });
  assert.deepEqual(listingFieldsForApi({
    ...base,
    skus: [{ name: " ", declared_price: "18.5", weight_g: "450" }],
  }, specCard), { error: "SKU 名称不能为空。" });
  assert.deepEqual(listingFieldsForApi({
    ...base,
    skus: [{ name: "默认款", declared_price: "0", weight_g: "450" }],
  }, specCard), { error: "SKU「默认款」的申报价必须是大于 0 的有效数字。" });
  assert.deepEqual(listingFieldsForApi({
    ...base,
    skus: [{ name: "默认款", declared_price: "18.5", weight_g: "0" }],
  }, specCard), { error: "SKU「默认款」的重量必须是大于 0 的有效数字。" });
  // 尺寸不再挂在 SKU 上，而是从尺寸详情表格按 SKU 反查校验。
  assert.deepEqual(listingFieldsForApi({
    ...base,
    skus: [{ name: "默认款", declared_price: "18.5", weight_g: "450" }],
  }, {
    ...specCard,
    cells: [
      ["尺寸图", "长", "宽", "高"],
      ["默认款", "0", "20", "10"],
    ],
  }), { error: "SKU「默认款」的长（cm）必须是大于 0 的有效数字。" });
});

test("listing fields reject more than 100 SKUs", () => {
  const result = listingFieldsForApi({
    title_mode: "long",
    suggested_price_usd: "29.99",
    category_name: "家居收纳",
    skus: Array.from({ length: 101 }, (_, index) => ({
      name: `SKU ${index + 1}`,
      declared_price: "18.5",
      weight_g: "450",
    })),
  });

  assert.deepEqual(result, { error: "SKU 最多可添加 100 个。" });
});

test("listing fields reject SKU names longer than 120 characters", () => {
  const result = listingFieldsForApi({
    title_mode: "long",
    suggested_price_usd: "29.99",
    category_name: "家居收纳",
    skus: [{ name: "款".repeat(121), declared_price: "18.5", weight_g: "450" }],
  });

  assert.deepEqual(result, { error: "SKU 名称不能超过 120 个字符。" });
});

test("successful listing-ready POD results can regenerate title and whole style for settled batches", () => {
  const publicResults = Array.from({ length: 4 }, () => ({
    status: "completed" as const,
    public_url: "https://images.example.com/result.png",
  }));
  assert.equal(canRegeneratePodStyle("completed", "completed", true), true);
  assert.equal(canRegeneratePodStyle("failed", "failed"), true);
  assert.equal(canRegeneratePodStyleTitle("completed", "completed", publicResults), true);
  assert.equal(canRegeneratePodStyleTitle("partial_failure", "failed", publicResults), true);
  // 计费中断已不再是生成/重试的拦截条件：结算待处理也允许重新生成。
  assert.equal(canRegeneratePodStyle("settlement_pending", "completed", true), true);
  assert.equal(isBillingInterruptedPodBatch("settlement_pending"), false);
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
