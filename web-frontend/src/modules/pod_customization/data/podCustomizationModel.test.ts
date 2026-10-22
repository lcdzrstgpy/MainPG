import assert from "node:assert/strict";
import test from "node:test";

import {
  POD_BATCH_COUNTS,
  EMPTY_SPEC_CARD,
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
  isPodColumnBatch,
  isPodColumnBatchCount,
  podBatchRoundProgress,
  isPristineCreativeEdit,
  resolveCreativePrompt,
  stripExcludedElementsLine,
  listingFieldsForApi,
  cloneSpecCardConfig,
  isSpecCardConfig,
  podBatchStatusLabel,
  podBatchStatusDetail,
} from "./podCustomizationModel.ts";

test("spec-card display unit defaults to centimetres and survives cloning", () => {
  assert.equal(EMPTY_SPEC_CARD.display_unit, "cm");
  const inches = { ...EMPTY_SPEC_CARD, display_unit: "in" as const };
  assert.equal(cloneSpecCardConfig(inches).display_unit, "in");

  const legacy = {
    enabled: true,
    style: "light",
    corner: "bottom-right",
    cells: [["SKU", "Length", "Width", "Height"]],
  };
  assert.equal(isSpecCardConfig(legacy), true);
  assert.equal(cloneSpecCardConfig(legacy as typeof EMPTY_SPEC_CARD).display_unit, "cm");
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

test("POD counts are preset multiples of four", () => {
  assert.deepEqual(POD_BATCH_COUNTS, [4, 8, 20, 40, 60, 100]);
  assert.equal(isPodBatchCount(1), true);
  assert.equal(isPodBatchCount(200), true);
  assert.equal(isPodBatchCount(0), false);
  assert.equal(isPodBatchCount(201), false);
  assert.equal(isPodBatchCount(1.5), false);
  // 列式生成只接受 4 的倍数，且至少 4 款。
  assert.equal(isPodColumnBatchCount(4), true);
  assert.equal(isPodColumnBatchCount(8), true);
  assert.equal(isPodColumnBatchCount(10), false);
  assert.equal(isPodColumnBatchCount(2), false);
  assert.equal(isPodColumnBatchCount(202), false);
});

test("column batches report progress per round instead of per style", () => {
  const progress = podBatchRoundProgress({
    count: 4,
    generation_layout: "column_v1",
    items: [
      { id: "a", style_index: 1, variant_index: 1, role: "lifestyle", status: "completed", public_url: "https://x/1.png" },
      { id: "b", style_index: 2, variant_index: 1, role: "lifestyle", status: "completed", public_url: "https://x/2.png" },
      { id: "c", style_index: 1, variant_index: 2, role: "detail_a", status: "queued", public_url: "" },
    ] as never,
  });

  assert.deepEqual(progress.map((round) => `${round.label} ${round.done}/${round.total}`), [
    "主图 2/4",
    "细节 A 0/4",
    "细节 B 0/4",
    "素材 0/4",
  ]);
  assert.equal(isPodColumnBatch({ generation_layout: "column_v1" }), true);
  assert.equal(isPodColumnBatch({ generation_layout: "legacy" }), false);
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
    fabric_craft: "  绗缝 ",
    scene_mood: "暖光木质厨房",
    style_keywords: "复古, 粗线条",
    color_preferences: "松绿、砂岩黄",
    excluded_elements: "Logo",
    copy_restrictions: " 标题不要出现刺绣、务必带上 2D Flat ",
  });
  assert.deepEqual(payload.core_selling_points, ["轻量", "防漏"]);
  assert.deepEqual(payload.style_keywords, ["复古", "粗线条"]);
  // 上架文案限制是整段自然语言：只去首尾空白，不按分隔符切分。
  assert.equal(payload.copy_restrictions, "标题不要出现刺绣、务必带上 2D Flat");
  // 批级全局字段同样整段透传，只去首尾空白。
  assert.equal(payload.fabric_craft, "绗缝");
  assert.equal(payload.scene_mood, "暖光木质厨房");
});

test("creative prompt is only the user's subject anchor", () => {
  assert.equal(resolveCreativePrompt("  产品主体：绗缝化妆包，正面拉链，菱形格  "), "产品主体：绗缝化妆包，正面拉链，菱形格");
  assert.equal(resolveCreativePrompt(""), "");
});

test("legacy creative edit drops only the excluded-elements line", () => {
  const saved = [
    "产品名称：圣诞主题麻将推尺",
    "目标市场：美国",
    "禁用元素：品牌 logo、毒品或吸毒工具、纳粹标志",
    "硬性规则：每款生成一次。",
  ].join("\n");

  assert.equal(
    stripExcludedElementsLine(saved),
    "产品名称：圣诞主题麻将推尺\n目标市场：美国\n硬性规则：每款生成一次。",
  );
});

test("legacy v1 snapshot is detected so it can be discarded", () => {
  const legacy = [
    "[POD DIRECT LISTING PROMPT v1]",
    "产品名称：包",
    "产品品类：袋",
    "目标市场：美国",
    "主题整批统一风格：圣诞",
    "硬性规则：每款生成一次。",
  ].join("\n");
  assert.equal(isPristineCreativeEdit(legacy), true);
  assert.equal(isPristineCreativeEdit("产品主体：绗缝化妆包，正面拉链，菱形格"), false);
  // 旧版快照（含“设计主题”标签）不再视为可丢弃的 v1 快照。
  const older = legacy.replace("主题整批统一风格：", "设计主题：");
  assert.equal(isPristineCreativeEdit(older), false);
});

test("listing fields normalize every SKU with its declared price and weight, and resolve dimensions from the spec card", () => {
  const specCard = {
    enabled: true,
    style: "light" as const,
    corner: "bottom-right" as const,
    display_unit: "cm" as const,
    cells: [
      ["SKU", "Length", "Width", "Height"],
      ["米白", "30", "20", "10"],
      ["深蓝", "31", "21", "11"],
    ],
  };
  const result = listingFieldsForApi({
    title_mode: "long",
    category_name: " 家居收纳 > 洗衣篮 ",
    skus: [
      { name: "  米白 ", declared_price: "18.5", weight_g: "450" },
      { name: "深蓝  ", declared_price: "19.9", weight_g: "470" },
    ],
  }, specCard);

  assert.deepEqual(result, {
    value: {
      title_mode: "long",
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
    category_name: "家居收纳",
  };
  const specCard = {
    enabled: true,
    style: "light" as const,
    corner: "bottom-right" as const,
    display_unit: "cm" as const,
    cells: [
      ["SKU", "Length", "Width", "Height"],
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
      ["SKU", "Length", "Width", "Height"],
      ["默认款", "0", "20", "10"],
    ],
  }), { error: "SKU「默认款」的长（cm）必须是大于 0 的有效数字。" });
});

test("listing fields reject more than 100 SKUs", () => {
  const result = listingFieldsForApi({
    title_mode: "long",
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
