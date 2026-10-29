import assert from "node:assert/strict";
import { test } from "node:test";

import { centsToYuanText, formatYuan, yuanToCents } from "./price.ts";

test("元 → 分：整数、一位小数、两位小数都按分精确换算", () => {
  assert.equal(yuanToCents("150", "申报价格"), 15000);
  assert.equal(yuanToCents("12.99", "申报价格"), 1299);
  assert.equal(yuanToCents("12.9", "申报价格"), 1290);
  assert.equal(yuanToCents("0.01", "申报价格"), 1);
  // 前后空格不该影响
  assert.equal(yuanToCents(" 12.99 ", "申报价格"), 1299);
  // 浮点误差不能漏进来（12.29 * 100 = 1228.9999...）
  assert.equal(yuanToCents("12.29", "申报价格"), 1229);
  assert.equal(yuanToCents("3.35", "申报价格"), 335);
});

test("元 → 分：三位小数/非数字/0 一律报错，不静默四舍五入", () => {
  // 多填一位如果被悄悄取整，定价就偏了，必须当场拦
  assert.throws(() => yuanToCents("12.345", "申报价"), /最多两位小数/);
  assert.throws(() => yuanToCents("12.", "申报价"), /最多两位小数/);
  assert.throws(() => yuanToCents("abc", "申报价"), /最多两位小数/);
  assert.throws(() => yuanToCents("-5", "申报价"), /最多两位小数/);
  assert.throws(() => yuanToCents("", "申报价"), /必须填（单位：元）/);
  assert.throws(() => yuanToCents("0", "申报价"), /必须大于 0/);
  assert.throws(() => yuanToCents("0.00", "申报价"), /必须大于 0/);
});

test("分 → 元（老数据迁移）：1299 变 12.99，脏值原样留着", () => {
  assert.equal(centsToYuanText("1299"), "12.99");
  assert.equal(centsToYuanText(15000), "150.00");
  assert.equal(centsToYuanText("50"), "0.50");
  // 空值与脏值不能变成 "0.00"，否则界面会显示一个假的 0 元价
  assert.equal(centsToYuanText(""), "");
  assert.equal(centsToYuanText(undefined), "");
  assert.equal(centsToYuanText("abc"), "abc");
  assert.equal(centsToYuanText("0"), "0");
});

test("展示：元金额统一两位小数", () => {
  assert.equal(formatYuan("150"), "¥150.00");
  assert.equal(formatYuan("12.9"), "¥12.90");
  // 空/0 原样返回，由调用方显示「—」
  assert.equal(formatYuan(""), "");
  assert.equal(formatYuan("0"), "0");
});
