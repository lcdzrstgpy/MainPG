import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PersonalCenterPage.tsx", import.meta.url), "utf8");

test("consumption history renders server timestamps in the user's local timezone", () => {
  // 服务端流水时间是 UTC，必须经 Date 转成本地时区再格式化，
  // 而不是把原始 ISO 字符串直接截断显示（那样会显示成 UTC）。
  assert.match(source, /function formatUsageTime\(/);
  assert.match(source, /const date = new Date\(iso\);/);
  assert.match(source, /date\.getFullYear\(\)/);
  assert.doesNotMatch(source, /created_at\.replace\("T", " "\)\.slice\(0, 19\)/);
});

test("consumption history distinguishes POD batches from product processing", () => {
  assert.match(source, /pod_customization\.batch/);
  assert.match(source, /POD 定制/);
});
