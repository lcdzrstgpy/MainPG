import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const page = readFileSync(new URL("./ProductProcessingTaskPage.tsx", import.meta.url), "utf8");

// 背景（2026-10-14）：任务收尾竞态曾把仍有未处理条目的任务误判「完成」，
// 而后端已算好的 not_processed_count 未在 UI 展示，「剩下的商品」在汇总里
// 完全隐形（线上实例：总数 55、成功 27，其余全部为 0）。这组断言锁定修复：
// 终态汇总区必须给出「未处理」条目数。
test("结果汇总展示「未处理」条目数（存在未处理项时）", () => {
  assert.match(page, /\(batch\.not_processed_count \?\? 0\) > 0/);
  assert.match(page, /未处理 <b>\{batch\.not_processed_count\}<\/b>/);
});

test("未处理格子位于终态汇总区内（处理中/已暂停不展示）", () => {
  const summaryAt = page.indexOf('className="verify-summary"');
  const notProcessedAt = page.indexOf("未处理 <b>{batch.not_processed_count}</b>");
  assert.ok(summaryAt > 0, "结果汇总区应存在");
  assert.ok(notProcessedAt > summaryAt, "未处理格子应位于结果汇总区内");
  // 汇总区仅在任务非活跃（终态）时渲染
  assert.match(page, /\{!taskActive && \(\s*<div className="verify-summary">/);
});
