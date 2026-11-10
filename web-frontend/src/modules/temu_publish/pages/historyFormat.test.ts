import assert from "node:assert/strict";
import test from "node:test";

import {
  dayLabelOf,
  errorCodeOf,
  formatPriceRange,
  formatRelativeTime,
  sourceLabelOf,
  stripErrorCode,
} from "./historyFormat.ts";

test("价格区间：分转元，同价只显示一个，没记价格不显示 ¥0.00", () => {
  assert.equal(formatPriceRange(1250, 1899), "¥12.50–18.99");
  assert.equal(formatPriceRange(1250, 1250), "¥12.50");
  assert.equal(formatPriceRange(1250, 0), "¥12.50");
  assert.equal(formatPriceRange(0, 1899), "¥18.99");
  // 0 = 没记价格（老记录），列表里宁可不显示，也不能显示「¥0.00」
  assert.equal(formatPriceRange(0, 0), "");
  assert.equal(formatPriceRange(undefined, undefined), "");
  // 浮点脏数据不能带出三位小数
  assert.equal(formatPriceRange(1250.4, 1899.6), "¥12.50–19.00");
});

test("相对时间：刚刚 / 分钟 / 小时 / 昨天 / 天数 / 日期", () => {
  const now = new Date("2026-11-04T15:00:00").getTime();
  const at = (iso: string) => new Date(iso).getTime() / 1000;

  assert.equal(formatRelativeTime(at("2026-11-04T14:59:40"), now), "刚刚");
  assert.equal(formatRelativeTime(at("2026-11-04T14:48:00"), now), "12 分钟前");
  assert.equal(formatRelativeTime(at("2026-11-04T11:00:00"), now), "4 小时前");
  assert.equal(formatRelativeTime(at("2026-11-03T14:20:00"), now), "昨天 14:20");
  assert.equal(formatRelativeTime(at("2026-11-01T10:00:00"), now), "3 天前");
  assert.equal(formatRelativeTime(at("2026-10-20T09:05:00"), now), "10-20 09:05");
  // 没有时间戳（老记录）就不显示，而不是 Invalid Date
  assert.equal(formatRelativeTime(0, now), "");
});

test("日期分隔：今天 / 昨天 / 月日", () => {
  const now = new Date("2026-11-04T15:00:00").getTime();
  const at = (iso: string) => new Date(iso).getTime() / 1000;
  assert.equal(dayLabelOf(at("2026-11-04T09:00:00"), now), "今天");
  assert.equal(dayLabelOf(at("2026-11-03T23:30:00"), now), "昨天");
  assert.equal(dayLabelOf(at("2026-10-28T09:00:00"), now), "10 月 28 日");
});

test("来源标签按草稿 id 前缀判定", () => {
  assert.equal(sourceLabelOf("ai:task-1:item-2"), "AI 处理");
  assert.equal(sourceLabelOf("pod:batch-9:style-1"), "POD 定制");
  assert.equal(sourceLabelOf(""), "手动填写");
  assert.equal(sourceLabelOf(undefined), "手动填写");
});

test("错误码：优先用后端字段，老记录从错误文本里提取 7 位码", () => {
  assert.equal(errorCodeOf({ error_code: "6000002", error: "随便写" }), "6000002");
  assert.equal(
    errorCodeOf({ error: "TemuGatewayError: 6000002 货品类目属性更新失败：属性[颜色]校验错误" }),
    "6000002",
  );
  assert.equal(errorCodeOf({ error: "网络超时" }), "");
  assert.equal(errorCodeOf({}), "");
});

test("原因正文去掉开头的错误码（徽章里已经有了）", () => {
  assert.equal(
    stripErrorCode("6000002 货品类目属性更新失败：属性[颜色]校验错误", "6000002"),
    "货品类目属性更新失败：属性[颜色]校验错误",
  );
  assert.equal(stripErrorCode("6000002：属性[颜色]校验错误", "6000002"), "属性[颜色]校验错误");
  // 码不在开头 / 没有码：原文照旧，不能把正文啃掉
  assert.equal(stripErrorCode("失败原因 6000002 在后面", "6000002"), "失败原因 6000002 在后面");
  assert.equal(stripErrorCode("网络超时", ""), "网络超时");
  assert.equal(stripErrorCode("", "6000002"), "");
  // 只有码本身：退回原文，别显示成空
  assert.equal(stripErrorCode("6000002", "6000002"), "6000002");
});
