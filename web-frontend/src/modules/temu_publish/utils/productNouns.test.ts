import assert from "node:assert/strict";
import test from "node:test";

import { productNounTexts } from "./productNouns.ts";

test("提词：phone case 命中「手机壳」，for 用途尾巴不捣乱", () => {
  assert.deepEqual(
    productNounTexts("Clear Magnetic Protective Phone Case for iPhone 18 Duo Foldable Smartphone"),
    ["手机壳"],
  );
});

test("提词：最长词组优先，suitcase 不会被 case 命中（词典无 suitcase 就该空）", () => {
  assert.deepEqual(productNounTexts("Vintage Leather Suitcase, Large"), []);
});

test("提词：词边界，cot 不命中 cotton", () => {
  assert.deepEqual(productNounTexts("Cotton Tote Bag"), ["手提包"]);
});

test("提词：多个品类词最多取 2 个", () => {
  const nouns = productNounTexts("Women PU Leather Tote Shoulder Bag with Scarf");
  assert.ok(nouns.length <= 2);
  assert.ok(nouns.includes("单肩包") || nouns.includes("手提包"));
});

test("提词：只看主体段，逗号后的卖点不算", () => {
  assert.deepEqual(productNounTexts("Quilted Cosmetic Pouch, waterproof tablecloth pattern"), [
    "化妆包",
  ]);
});

test("提词：Cable Protective Sleeve 进数据线（cable 词组不连续时退单词）", () => {
  assert.deepEqual(productNounTexts("Cable Protective Sleeve"), ["数据线"]);
});
