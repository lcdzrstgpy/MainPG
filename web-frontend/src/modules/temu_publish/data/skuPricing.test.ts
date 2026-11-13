import assert from "node:assert/strict";
import test from "node:test";

import {
  resolveSkuField,
  resolveSkuPrice,
  resolveSkuPriceCents,
  skuKeyOf,
  variantKeysOf,
  variantLabel,
} from "./skuPricing.ts";

/**
 * 变种级定价的全部规则都在这里，穷举一遍。
 *
 * 价格算错 = 直接亏钱，所以「变种价 → 整条价 → 空」这个顺序必须钉死：
 * 任何一处取价偏离这个顺序，测试就该红。
 */

test("skuKeyOf：颜色 / 颜色|尺码，值里的 | 会被替换避免撞键", () => {
  assert.equal(skuKeyOf("黑色"), "黑色");
  assert.equal(skuKeyOf("黑色", "M"), "黑色|M");
  assert.equal(skuKeyOf("黑色", ""), "黑色");
  assert.equal(skuKeyOf("  黑色  ", " M "), "黑色|M");
  assert.equal(skuKeyOf("黑|白", "M"), "黑/白|M");
  assert.equal(skuKeyOf(undefined, undefined), "");
});

test("resolveSkuField：变种值优先，空/未设回落整条值（价/库存/尺寸同一套规则）", () => {
  const overrides = {
    "黑色": { declared_price: "169", stock: "7", weight_g: "620" },
    "棕色": { declared_price: "158.5" },
  };
  assert.equal(resolveSkuPrice("150", overrides, "黑色"), "169");
  assert.equal(resolveSkuPrice("150", overrides, "棕色"), "158.5");
  // 同一套规则对库存/重量也成立
  assert.equal(resolveSkuField(overrides["黑色"], "stock", "1"), "7");
  assert.equal(resolveSkuField(overrides["黑色"], "weight_g", "500"), "620");
  assert.equal(resolveSkuField(overrides["棕色"], "stock", "1"), "1", "没设的字段回落整条");
  // 没设过的变种
  assert.equal(resolveSkuPrice("150", overrides, "泥色"), "150");
  // 整条值也没有 → 空（交给后面的校验报错，不能悄悄变成 0）
  assert.equal(resolveSkuPrice(undefined, overrides, "泥色"), "");
  // 覆盖值是空串 = 取消覆盖
  assert.equal(resolveSkuPrice("150", { "黑色": { declared_price: "  " } }, "黑色"), "150");
  // 尺寸这类数字字段也能按变种设
  assert.equal(
    resolveSkuField({ "黑色|M": { length_cm: "42" } }["黑色|M"], "length_cm", "35"),
    "42",
  );
  // 前后空格要裁掉（用户粘贴常见）
  assert.equal(resolveSkuPrice(" 150 ", overrides, "泥色"), "150");
  // 没有 override map 也不能炸
  assert.equal(resolveSkuPrice("150", undefined, "黑色"), "150");
});

test("resolveSkuPriceCents：换算成分，非法值直接抛（不许静默成 0）", () => {
  const overrides = { "黑色": { declared_price: "169" } };
  assert.equal(resolveSkuPriceCents("150", overrides, "黑色"), 16900);
  assert.equal(resolveSkuPriceCents("150", overrides, "泥色"), 15000);
  assert.throws(() => resolveSkuPriceCents("", overrides, "泥色"), /申报价格必须填/);
  assert.throws(() => resolveSkuPriceCents("150", { "黑色": { declared_price: "abc" } }, "黑色"), /申报价格请按/);
  assert.throws(() => resolveSkuPriceCents("150", { "黑色": { declared_price: "0" } }, "黑色"), /大于 0/);
});

test("variantLabel：有尺码才拼竖线", () => {
  assert.equal(variantLabel("黑色", "M"), "黑色 | M");
  assert.equal(variantLabel("黑色", ""), "黑色");
  assert.equal(variantLabel("", "M"), "M");
  assert.equal(variantLabel("", ""), "单 SKU");
});

test("variantKeysOf：variants 优先、规格值笛卡尔积、单值兜底、空也能出单 SKU", () => {
  const fromVariants = variantKeysOf({
    variants: [
      { color: "黑色", size: "M" },
      { color: "黑色", size: "L" },
      { color: "红色", size: "M" },
      { color: "黑色", size: "M" },
    ],
  });
  assert.deepEqual(
    fromVariants.map((item) => item.key),
    ["黑色|M", "黑色|L", "红色|M"],
    "同一变种只出现一次",
  );

  const cartesian = variantKeysOf({
    spec_values_1: ["黑色", "白色"],
    spec_values_2: ["M", "L"],
  });
  assert.deepEqual(
    cartesian.map((item) => item.key),
    ["黑色|M", "黑色|L", "白色|M", "白色|L"],
  );

  const colorOnly = variantKeysOf({ spec_values_1: ["黑色", "白色", ""] });
  assert.deepEqual(colorOnly.map((item) => item.key), ["黑色", "白色"]);

  const single = variantKeysOf({ spec_value_1: "黑色", spec_value_2: "" });
  assert.deepEqual(single.map((item) => item.key), ["黑色"]);

  const nothing = variantKeysOf({});
  assert.equal(nothing.length, 1);
  assert.equal(nothing[0].key, "", "没有任何变种值 → 一个空 key 的单 SKU");
});
