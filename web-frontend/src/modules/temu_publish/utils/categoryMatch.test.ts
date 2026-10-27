import assert from "node:assert/strict";
import test from "node:test";

import { categorySearchTexts, dedupeCategoryPaths } from "./categoryMatch.ts";

test("标题查询词：截到第一个逗号前 + 去掉 for 用途说明", () => {
  const title =
    "Clear Magnetic Protective Phone Case for iPhone 18 Duo Foldable Smartphone, 2mm TPU Slim Fit Cover with Built-in Magnetic Alignment, Reinforced Corners";
  const queries = categorySearchTexts(title);

  // 逗号后的卖点描述会把匹配带偏（实测会匹配出儿童安全座椅、维生素）
  assert.ok(queries.length >= 1);
  for (const query of queries) {
    assert.ok(!query.includes("TPU"), `查询词不该带逗号后的描述：${query}`);
  }
  // 商品名在 for 前面，不能把 for 之后的机型/用途带进去
  assert.deepEqual(queries, ["Clear Magnetic Protective Phone Case"]);
});

test("标题查询词：主体只剩一个词时改用整段逗号前标题兜底", () => {
  assert.deepEqual(categorySearchTexts("Case for iPhone 15, shockproof TPU"), [
    "Case for iPhone 15",
  ]);
});

test("标题查询词：POD 长标题取到商品主体，不能只剩开头的颜色/花纹", () => {
  const title =
    "Sage Green Hydrangea Daisy Print Natural Rubber Waterproof Square Tablecloth for Cozy Country Dining Room Table Settings";
  assert.deepEqual(categorySearchTexts(title), [
    "Sage Green Hydrangea Daisy Print Natural Rubber Waterproof Square Tablecloth",
  ]);
});

test("标题查询词：中文标题整句直接查", () => {
  assert.deepEqual(categorySearchTexts("女士整顶假发 中分大波浪"), ["女士整顶假发 中分大波浪"]);
});

test("标题查询词：太短的标题不硬拆", () => {
  assert.deepEqual(categorySearchTexts("phone case"), ["phone case"]);
  assert.deepEqual(categorySearchTexts("   "), []);
});

test("类目路径去重：同一条路径只留一次", () => {
  const a = {
    cat1DTO: { catId: 1, catName: "电子" },
    cat2DTO: { catId: 2, catName: "手机壳" },
  };
  const same = {
    cat1DTO: { catId: 1, catName: "电子" },
    cat2DTO: { catId: 2, catName: "手机壳" },
  };
  const other = {
    cat1DTO: { catId: 9, catName: "家居" },
    cat2DTO: { catId: 10, catName: "收纳" },
  };

  const merged = dedupeCategoryPaths([a, same, other]);
  assert.equal(merged.length, 2);
  assert.equal(merged[0].cat2DTO.catId, 2);
  assert.equal(merged[1].cat2DTO.catId, 10);
});
