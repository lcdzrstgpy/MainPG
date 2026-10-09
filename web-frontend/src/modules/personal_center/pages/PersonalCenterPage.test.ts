import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PersonalCenterPage.tsx", import.meta.url), "utf8");

test("consumption history renders server timestamps in the user's local timezone", () => {
  assert.match(source, /function formatUsageDateTime\(/);
  assert.match(source, /new Date\(value\)\.toLocaleString\("zh-CN"/);
  assert.doesNotMatch(source, /created_at\.replace\("T", " "\)\.slice\(0, 19\)/);
});

test("consumption history distinguishes POD batches from product processing", () => {
  assert.match(source, /pod_customization\.batch/);
  assert.match(source, /POD 定制/);
});
