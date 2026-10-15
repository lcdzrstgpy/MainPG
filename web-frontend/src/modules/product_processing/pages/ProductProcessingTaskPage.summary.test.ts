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

// 背景（2026-10-15）：任务是否继续跑，只该由**真实事件**决定。后端此前按「多久没心跳」
// 猜用户离开（90/180 秒），而浏览器会把隐藏页的定时器压到约 1 分钟一次、节能模式/休眠还会
// 直接冻结页面 —— 「工作台明明开着却被暂停」就是这么来的。现在的分工：
//   ① 页面开着（哪怕被节流/冻结）→ 照跑：暂停只看「离开信标之后是否连续静默」，
//      隐藏页 1 分钟一次的轮询也算还在看；
//   ② 页面真的关掉 → pagehide 发离开信标，连续静默后暂停（刷新/重开会被轮询撤销）。
test("关闭页面用 pagehide 信标通知后端，不再按静默时长猜", () => {
  assert.match(page, /window\.addEventListener\('pagehide', leave\)/);
  assert.ok(
    page.includes("navigator.sendBeacon?.(`${API_BASE}/tasks/${taskId}/leave?workspace="),
    "pagehide 必须发送 leave 信标（后端据此暂停；地址与后端路由对齐）",
  );
  // 「约 X 秒/分钟后自动暂停」这类猜测式文案不许回来
  assert.doesNotMatch(page, /约 \d+ 秒后任务将自动暂停/);
  assert.doesNotMatch(page, /约 \d+ 分钟后任务将自动暂停/);
});

// 最坏的一条路径：信标里的工作区与轮询用的工作区不一致 —— 信标会记下一个「没有对应心跳」
// 的标记，于是页面开着也会被判静默而暂停。两处必须取同一个值（ctx.workspaceId）。
test("信标与轮询用同一个 workspace 值（不一致会让页面开着也被暂停）", () => {
  const client = readFileSync(new URL("../api/client.ts", import.meta.url), "utf8");
  const context = readFileSync(new URL("../api/context.ts", import.meta.url), "utf8");
  // 轮询：请求头取 ctx.workspaceId
  assert.match(client, /"X-Workspace-ID": context\.workspaceId/);
  // 信标：查询参数取同一个 ctx.workspaceId
  assert.match(page, /const workspace = ctx\.workspaceId;/);
  assert.ok(
    page.includes("?workspace=${encodeURIComponent(workspace)}"),
    "信标必须把 workspace 带在查询串里（sendBeacon 带不了自定义头）",
  );
  // 两处都来自同一个上下文工厂，避免有人另起一套取值
  assert.match(context, /workspaceId:/);
});
