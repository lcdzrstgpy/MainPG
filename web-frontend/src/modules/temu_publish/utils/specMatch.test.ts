import assert from "node:assert/strict";
import test from "node:test";

import { matchPresetValue } from "./specMatch.ts";

// 假发类目 31035 的真实预设颜色（节选）
const COLORS = [
  "白色",
  "黑色",
  "粉红色",
  "酒红色",
  "金色",
  "灰色",
  "深棕色",
  "浅棕色",
  "天蓝色",
  "蓝色",
  "绿色",
  "抹茶色",
  "咖啡色",
  "透明",
];

test("规格对齐：完全相等直接命中", () => {
  assert.equal(matchPresetValue("黑色", COLORS), "黑色");
  assert.equal(matchPresetValue(" 白色 ", COLORS), "白色");
});

test("规格对齐：描述里包含预设值，取最长的那个", () => {
  // 草稿里就是这种「款式-颜色」拼起来的写法
  assert.equal(matchPresetValue("安卓通用款-黑色", COLORS), "黑色");
  assert.equal(matchPresetValue("安卓通用款-迷雾蓝", COLORS), "蓝色");
  assert.equal(matchPresetValue("抹茶绿色", COLORS), "绿色");
  // 同时包含「深棕色」和「棕色」时取最长
  assert.equal(matchPresetValue("自然深棕色", COLORS), "深棕色");
  assert.equal(matchPresetValue("浅棕色短发", COLORS), "浅棕色");
});

test("规格对齐：靠色系关键词兜底", () => {
  assert.equal(matchPresetValue("迷雾蓝", COLORS), "蓝色");
  assert.equal(matchPresetValue("自然黑", COLORS), "黑色");
  assert.equal(matchPresetValue("奶茶棕", COLORS), "深棕色");
  assert.equal(matchPresetValue("Matcha", COLORS), null, "纯英文匹配不到就交给人选");
});

test("规格对齐：匹不到返回 null，不瞎猜", () => {
  assert.equal(matchPresetValue("", COLORS), null);
  assert.equal(matchPresetValue("钢化玻璃", COLORS), null);
  assert.equal(matchPresetValue("黑色", []), null);
});

test("规格对齐：尺码场景同样可用", () => {
  const SIZES = ["6inch", "8inch", "10inch"];
  assert.equal(matchPresetValue("6inch", SIZES), "6inch");
  assert.equal(matchPresetValue("长度 8inch", SIZES), "8inch");
});
