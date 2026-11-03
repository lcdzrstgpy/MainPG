import assert from "node:assert/strict";
import test from "node:test";

import { findParentSpec, pickSpecPair } from "./specPair.ts";
import type { TemuParentSpec } from "../types.ts";

function spec(id: number, name: string): TemuParentSpec {
  return { parentSpecId: id, parentSpecName: name };
}

/**
 * 这些是**行为测试**：不同品类的父规格叫法完全不一样，写死一个名字就换品类必错。
 * 错位的表现是「生成变种失败」或「发布被 Temu 判不合法的规格值」，很难从报错反推。
 */

test("双属性：颜色 + 尺码（最常见的服饰类目）", () => {
  const pair = pickSpecPair([spec(1, "颜色"), spec(2, "尺码")]);
  assert.deepEqual(pair, { color: 1, size: 2 });
});

test("双属性：颜色 + 型号（手机壳 / 手机配件）", () => {
  const pair = pickSpecPair([spec(11, "颜色"), spec(12, "型号")]);
  assert.deepEqual(pair, { color: 11, size: 12 });
});

test("顺序颠倒时两列不能落到同一个属性上", () => {
  // Temu 返回顺序不保证。按名字找「第一列」会命中「颜色」，
  // 如果第二列还按下标取 1，又会拿到「颜色」——生成变种直接崩。
  const pair = pickSpecPair([spec(21, "型号"), spec(22, "颜色")]);
  assert.equal(pair.color, 22, "颜色应作为第一列");
  assert.equal(pair.size, 21, "型号作为第二列（不能等于第一列）");
  assert.notEqual(pair.color, pair.size);
});

test("单属性：只有颜色 → 第二列为 0（调用方据此隐藏第二列）", () => {
  // 毛巾、浴巾这类只有颜色一列。以前会硬要求填尺码，
  // 抛「Temu 父规格列表为空，无法创建规格」，等于这类商品永远发不出去。
  assert.deepEqual(pickSpecPair([spec(31, "颜色")]), { color: 31, size: 0 });
});

test("零属性：空列表不崩", () => {
  assert.deepEqual(pickSpecPair([]), { color: 0, size: 0 });
});

test("三属性：尺码优先于容量（取两个就够，Temu 变种最多两维）", () => {
  const pair = pickSpecPair([spec(41, "颜色"), spec(42, "容量"), spec(43, "尺码")]);
  assert.equal(pair.color, 41);
  assert.equal(pair.size, 43, "「尺码」命中 SECOND_SPEC_WORDS 且排在「容量」后返回时仍应选中它");
  assert.notEqual(pair.color, pair.size);
});

test("不常见叫法也能命中：款式 / 花色 / 尺寸", () => {
  assert.equal(pickSpecPair([spec(51, "款式"), spec(52, "尺寸")]).color, 51);
  assert.equal(pickSpecPair([spec(53, "花色"), spec(54, "规格")]).color, 53);
});

test("findParentSpec：只给一个关键字也照常工作（兼容旧调用）", () => {
  const hit = findParentSpec([spec(61, "颜色"), spec(62, "尺码")], "尺码", 0);
  assert.equal(hit?.parentSpecId, 62);
});
