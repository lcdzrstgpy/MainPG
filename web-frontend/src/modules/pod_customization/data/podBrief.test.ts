import assert from "node:assert/strict";
import test from "node:test";

import {
  POD_BRIEF_HISTORY_LIMIT,
  briefFieldsToDraft,
  createBriefHistoryItem,
  isBriefRequestValid,
  mergeBusinessFields,
  normalizeBriefInput,
  recordBriefHistory,
} from "./podBrief.ts";
import type {
  PodBusinessFields,
  PodBusinessFieldsDraft,
  PodBriefFieldsDraft,
  PodBriefHistoryItem,
} from "../types";

const EMPTY_DRAFT: PodBusinessFieldsDraft = {
  product_name: "",
  product_category: "",
  target_market: "",
  target_audience: "",
  core_selling_points: "",
  design_theme: "",
  style_keywords: "",
  color_preferences: "",
  excluded_elements: "",
};

/** 前置层实际代填的 9 个字段。 */
const EMPTY_BRIEF_DRAFT: PodBriefFieldsDraft = {
  product_name: "",
  product_category: "",
  target_market: "",
  target_audience: "",
  core_selling_points: "",
  design_theme: "",
  style_keywords: "",
  color_preferences: "",
  excluded_elements: "",
};

function briefFields(overrides: Partial<PodBusinessFields> = {}): PodBusinessFields {
  return {
    product_name: "托特包",
    product_category: "女包",
    target_market: "美国",
    target_audience: "通勤女性",
    core_selling_points: ["耐用", "大容量"],
    design_theme: "美式西南复古",
    style_keywords: ["仙人掌", "纳瓦霍几何"],
    color_preferences: ["棕", "米白"],
    excluded_elements: ["品牌 logo"],
    ...overrides,
  };
}

test("brief fields convert array columns into 、-joined draft strings", () => {
  const draft = briefFieldsToDraft(briefFields());

  assert.equal(draft.core_selling_points, "耐用、大容量");
  assert.equal(draft.style_keywords, "仙人掌、纳瓦霍几何");
  assert.equal(draft.color_preferences, "棕、米白");
  assert.equal(draft.excluded_elements, "品牌 logo");
  assert.equal(draft.product_name, "托特包");
});

test("blank array columns become empty strings without leftover separators", () => {
  const draft = briefFieldsToDraft(briefFields({ core_selling_points: [], style_keywords: ["  ", ""] }));

  assert.equal(draft.core_selling_points, "");
  assert.equal(draft.style_keywords, "");
});

test("generated fields overwrite the same draft fields and keep untouched keys", () => {
  const current = { ...EMPTY_DRAFT, product_name: "旧名称", product_category: "旧品类", target_audience: "保留人群" };
  const merged = mergeBusinessFields(current, { ...EMPTY_BRIEF_DRAFT, product_name: "新名称", design_theme: "新主题" });

  assert.equal(merged.product_name, "新名称");
  assert.equal(merged.design_theme, "新主题");
  // 生成结果是 9 字段快照：同名字段（含空值）一律直接覆盖，不做逐字段取舍。
  assert.equal(merged.product_category, "");

  // 结果里没有的键保持用户当前值（spread 合并语义）。
  const partial = mergeBusinessFields(current, { product_name: "新名称" });
  assert.equal(partial.product_name, "新名称");
  assert.equal(partial.target_audience, "保留人群");
});

test("recording a brief history entry dedupes the same trimmed input and keeps the newest", () => {
  const first = createBriefHistoryItem(" 美式复古 ", EMPTY_BRIEF_DRAFT, "brief-1", "2026-09-12T01:00:00.000Z");
  const second = createBriefHistoryItem("通勤托特包", EMPTY_BRIEF_DRAFT, "brief-2", "2026-09-12T02:00:00.000Z");
  const history = recordBriefHistory(recordBriefHistory([], first), second);

  const replaced = recordBriefHistory(
    history,
    createBriefHistoryItem("美式复古", EMPTY_BRIEF_DRAFT, "brief-3", "2026-09-12T03:00:00.000Z"),
  );

  assert.deepEqual(replaced.map((item) => item.id), ["brief-3", "brief-2"]);
  assert.equal(replaced[0].input, "美式复古");
});

test("brief history keeps at most 20 newest entries and drops the oldest", () => {
  let history: PodBriefHistoryItem[] = [];
  for (let index = 0; index < POD_BRIEF_HISTORY_LIMIT + 5; index += 1) {
    history = recordBriefHistory(history, createBriefHistoryItem(`需求 ${index}`, EMPTY_BRIEF_DRAFT, `brief-${index}`));
  }

  assert.equal(history.length, POD_BRIEF_HISTORY_LIMIT);
  assert.equal(history[0].id, "brief-24");
  assert.equal(history[history.length - 1].id, "brief-5");
});

test("brief request validation normalizes whitespace and enforces the 1..500 window", () => {
  assert.equal(normalizeBriefInput("  美式\n\n复古  托特包 "), "美式 复古 托特包");
  assert.equal(normalizeBriefInput("   "), "");
  assert.equal(isBriefRequestValid("   "), false);
  assert.equal(isBriefRequestValid("a".repeat(500)), true);
  assert.equal(isBriefRequestValid("a".repeat(501)), false);
});
