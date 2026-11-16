import assert from "node:assert/strict";
import test from "node:test";

import {
  categoryAnchorTexts,
  categoryQueryPlan,
  categorySearchTexts,
  decideCategoryPath,
  dedupeCategoryPaths,
  type CategoryQueryResult,
} from "./categoryMatch.ts";
import type { TemuCategory } from "../types.ts";

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

/* ---------------- 属性锚定 + 多查询互验（2026-11 类目配错修复） ---------------- */

function cat(catId: number, catName: string, options: Partial<TemuCategory> = {}): TemuCategory {
  return { catId, catName, isLeaf: true, isHidden: false, catLevel: 4, ...options } as TemuCategory;
}

test("品类锚定词：只取「款式/品类/品名/种类」属性的值", () => {
  const anchors = categoryAnchorTexts([
    { name: "箱包潮流款式", value: "单肩斜挎包" },
    { name: "材质类型", value: "PU" }, // 「类型」不是品类信号，PU 不是品类词
    { name: "箱包大小", value: "中" }, // 单字尺寸词不算
    { name: "风格", value: "简约风" }, // 风格不是品类
    { name: "产品种类", value: "默认项" }, // 占位值不算
  ]);
  assert.deepEqual(anchors, ["单肩斜挎包"]);
});

test("查询计划：锚定词优先，标题兜底，去重且最多 3 个", () => {
  const plan = categoryQueryPlan({
    title: "Women's Soft PU Leather Tote Shoulder Bag, Large Capacity",
    sourceAttributes: [{ name: "箱包潮流款式", value: "单肩斜挎包" }],
  });
  assert.equal(plan[0]?.kind, "anchor");
  assert.equal(plan[0]?.text, "单肩斜挎包");
  assert.ok(plan.filter((item) => item.kind === "title").length >= 1);
  assert.ok(plan.length <= 3);
});

test("裁决：同一叶子 ≥2 个查询命中就是共识", () => {
  const bag = [cat(29164, "女士手提包")];
  const results: CategoryQueryResult[] = [
    { kind: "anchor", paths: [bag] },
    { kind: "title", paths: [bag] },
    { kind: "title", paths: [[cat(30988, "工具包")]] },
  ];
  assert.equal(decideCategoryPath(results)?.[0]?.catId, 29164);
});

test("裁决：锚定结果压过标题猜测（托特包实测：标题猜到木材类目）", () => {
  const bag = [cat(29164, "女士手提包")];
  const wood = [cat(29158, "木制品")];
  const results: CategoryQueryResult[] = [
    { kind: "anchor", paths: [bag] },
    { kind: "title", paths: [wood] },
    { kind: "title", paths: [wood] },
  ];
  assert.equal(decideCategoryPath(results)?.[0]?.catId, 29164);
});

test("裁决：隐藏类目不当候选（29158 就是这么混进来的）", () => {
  const hidden = [cat(29158, "隐藏类目", { isHidden: true })];
  const results: CategoryQueryResult[] = [{ kind: "title", paths: [hidden] }];
  assert.equal(decideCategoryPath(results), null);
});

test("裁决：多个标题查询互相冲突且没有锚定 → 不认领", () => {
  const results: CategoryQueryResult[] = [
    { kind: "title", paths: [[cat(1, "桌布")]] },
    { kind: "title", paths: [[cat(2, "化妆包")]] },
  ];
  assert.equal(decideCategoryPath(results), null);
});

test("裁决：只有一个查询有结果时认领（维持原有覆盖率）", () => {
  const results: CategoryQueryResult[] = [
    { kind: "title", paths: [[cat(1, "桌布")]] },
    { kind: "title", paths: [] },
  ];
  assert.equal(decideCategoryPath(results)?.[0]?.catId, 1);
});

test("查询计划：词典品类词插在锚定和标题之间", () => {
  const plan = categoryQueryPlan({ title: "Clear Magnetic Protective Phone Case for iPhone 18" });
  assert.equal(plan[0]?.kind, "dict");
  assert.equal(plan[0]?.text, "手机壳");
  assert.ok(plan.some((item) => item.kind === "title"));
});

test("裁决：词典 top1 压过标题原文的字符串猜测（磁吸手机壳实测场景）", () => {
  const phone = [cat(301, "手机壳")];
  const jacket = [cat(302, "女士休闲夹克")];
  const results: CategoryQueryResult[] = [
    { kind: "dict", paths: [phone] },
    { kind: "title", paths: [jacket] },
    { kind: "title", paths: [jacket] },
  ];
  assert.equal(decideCategoryPath(results)?.[0]?.catId, 301);
});

test("裁决：词典与标题互证是跨证据源共识", () => {
  const phone = [cat(301, "手机壳")];
  const results: CategoryQueryResult[] = [
    { kind: "dict", paths: [phone] },
    { kind: "title", paths: [phone] },
    { kind: "title", paths: [[cat(303, "夹克")]] },
  ];
  assert.equal(decideCategoryPath(results)?.[0]?.catId, 301);
});

test("裁决：锚定压过词典（真实采集属性 > 标题翻译）", () => {
  const bag = [cat(29164, "手提包")];
  const results: CategoryQueryResult[] = [
    { kind: "anchor", paths: [bag] },
    { kind: "dict", paths: [[cat(304, "化妆包")]] },
    { kind: "title", paths: [[cat(305, "工具包")]] },
  ];
  assert.equal(decideCategoryPath(results)?.[0]?.catId, 29164);
});
