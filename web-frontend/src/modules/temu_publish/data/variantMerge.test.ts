import assert from "node:assert/strict";
import test from "node:test";

import { mergeGeneratedRows, variantCoverage } from "./variantMerge.ts";

type Row = { colorName: string; sizeName: string; supplierPrice: string; stock: string };

const row = (colorName: string, sizeName: string, supplierPrice: string, stock = "0"): Row => ({
  colorName,
  sizeName,
  supplierPrice,
  stock,
});

const makeRow = (color: string, size: string) => row(color, size, "150", "0");

test("首次生成：按 颜色 × 尺码 铺出所有组合", () => {
  const out = mergeGeneratedRows<Row>([], ["黑", "白"], ["S", "M"], makeRow);
  assert.deepEqual(
    out.map((item) => `${item.colorName}/${item.sizeName}`),
    ["黑/S", "黑/M", "白/S", "白/M"],
  );
  assert.ok(out.every((item) => item.supplierPrice === "150"));
});

test("再点一次生成：已经改过价的旧行原样保留，只补新组合", () => {
  // 用户把「黑/S」的价格改成了 199，「黑/M」库存改成 5
  const previous = [row("黑", "S", "199", "0"), row("黑", "M", "150", "5")];
  const out = mergeGeneratedRows<Row>(previous, ["黑"], ["S", "M", "L"], makeRow);

  assert.equal(out.length, 3);
  assert.equal(out[0], previous[0], "旧行要原对象复用，不能被新默认值覆盖");
  assert.equal(out[1]  , previous[1]);
  assert.equal(out[0].supplierPrice, "199");
  assert.equal(out[1].stock, "5");
  // 新加的尺码才用默认值
  assert.equal(out[2].sizeName, "L");
  assert.equal(out[2].supplierPrice, "150");
});

test("减少尺码：多余行消失，留下的仍然保留各自的值", () => {
  const previous = [row("黑", "S", "199"), row("黑", "M", "188"), row("黑", "L", "177")];
  const out = mergeGeneratedRows<Row>(previous, ["黑"], ["S", "L"], makeRow);
  assert.deepEqual(out.map((item) => item.sizeName), ["S", "L"]);
  assert.equal(out[0].supplierPrice, "199");
  assert.equal(out[1].supplierPrice, "177");
});

test("颜色和尺码同名也不会串行（用分隔键而不是拼字符串）", () => {
  // 颜色叫「s」、尺码也叫「s」：拼成 "ss" 会和 颜色"s"+尺码"s" 之外的情况撞车
  const previous = [row("s", "s", "999"), row("s", "m", "150")];
  const out = mergeGeneratedRows<Row>(previous, ["s"], ["s", "m"], makeRow);
  assert.equal(out[0], previous[0]);
  assert.equal(out[0].supplierPrice, "999");
  assert.equal(out[1]  , previous[1]);
});

test("换颜色后旧行的价不会平移到同名尺码上", () => {
  const previous = [row("黑", "S", "199")];
  const out = mergeGeneratedRows<Row>(previous, ["白"], ["S"], makeRow);
  assert.equal(out.length, 1);
  assert.equal(out[0].colorName, "白");
  // 这是一条全新的行，不该带着「黑/S」的价格
  assert.equal(out[0].supplierPrice, "150");
});


test("组合对齐时按钮该置灰：表格和 颜色×尺码 一一对应", () => {
  const rows = [
    row("黑", "S", "150"),
    row("黑", "M", "150"),
    row("白", "S", "150"),
    row("白", "M", "150"),
  ];
  const full = variantCoverage(rows, ["黑", "白"], ["S", "M"]);
  assert.deepEqual(full, { expected: 4, missing: 0, extra: 0, inSync: true });
});

test("改了颜色但没点生成：算得出还缺几个组合", () => {
  const rows = [row("黑", "S", "150"), row("黑", "M", "150")];
  const cov = variantCoverage(rows, ["黑", "白", "灰"], ["S", "M"]);
  assert.equal(cov.expected, 6);
  assert.equal(cov.missing, 4);
  assert.equal(cov.extra, 0);
  assert.equal(cov.inSync, false);
});

test("删了尺码但行还在：多余行也算没对齐", () => {
  const rows = [row("黑", "S", "150"), row("黑", "L", "150")];
  const cov = variantCoverage(rows, ["黑"], ["S"]);
  assert.equal(cov.missing, 0);
  assert.equal(cov.extra, 1);
  assert.equal(cov.inSync, false);
});

test("空表格 / 空取值都不算对齐（按钮要能点）", () => {
  assert.equal(variantCoverage([], ["黑"], ["S"]).inSync, false);
  assert.equal(variantCoverage([], [], []).inSync, false);
});
