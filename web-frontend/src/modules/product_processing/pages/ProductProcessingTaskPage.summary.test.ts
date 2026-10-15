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

// 背景（2026-10-15）：任务页 1 秒轮询 /outputs 就是「任务心跳」，而浏览器会把隐藏页的
// 定时器压到约 1 分钟一次（Chrome/Edge 隐藏满 5 分钟）/在节能模式下冻结后台页 —— 用户只是
// 切到别的界面或最小化窗口，任务就被误判成「页面关了」自动暂停（工单来源）。
// 修复：任务运行期间持有 Web Lock（浏览器认可的「后台仍要干活」信号，可豁免上述压制）。
test("任务运行期间持 Web Lock 保活，避免隐藏页被节流/冻结导致误停", () => {
  assert.match(
    page,
    /useTaskHeartbeatKeepAlive\(batch\?\.task\.status === 'queued' \|\| batch\?\.task\.status === 'running'\)/,
  );
  const keepAlive = readFileSync(new URL("../hooks/useTaskHeartbeatKeepAlive.ts", import.meta.url), "utf8");
  assert.match(keepAlive, /navigator\.locks/);
  // shared：多个处理页签可以同时持有（exclusive 会让后来的页签排队等锁）
  assert.match(keepAlive, /mode: "shared"/);
  // 任务结束/页面卸载时必须释放，否则等于永远拦着浏览器省电
  assert.match(keepAlive, /controller\.abort\(\)/);
});
