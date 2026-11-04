import assert from "node:assert/strict";
import test from "node:test";

import {
  captureStatusChip,
  isActiveCaptureStatus,
  resolveSyncKind,
  summarizeCaptureResults,
  FULL_SYNC_MAX_AGE_MS,
} from "./captureTaskModel.ts";

const DAY = 24 * 60 * 60 * 1000;

test("没有全量记录时第一次同步走全量", () => {
  assert.equal(resolveSyncKind({ now: 1_000_000, lastFullAt: 0 }), "full");
});

test("7 天内走增量，超过 7 天回到全量", () => {
  const now = 10 * DAY;
  assert.equal(resolveSyncKind({ now, lastFullAt: now - 3 * DAY }), "incremental");
  assert.equal(resolveSyncKind({ now, lastFullAt: now - FULL_SYNC_MAX_AGE_MS - 1 }), "full");
});

test("采集结果按成功/跳过/失败汇总", () => {
  const summary = summarizeCaptureResults([
    { key: "store", label: "店铺首页", state: "ok" },
    { key: "traffic", label: "商品流量", state: "ok" },
    { key: "finance", label: "对账中心", state: "skipped" },
    { key: "orders", label: "订单列表", state: "failed" },
  ]);
  assert.deepEqual(summary, { total: 4, ok: 2, skipped: 1, failed: 1, running: 0, partial: true });
});

test("只成功一项时不能显示为已完成", () => {
  const chip = captureStatusChip("succeeded", true, [
    { key: "store", label: "店铺首页", state: "ok" },
    { key: "orders", label: "订单列表", state: "failed" },
  ]);
  assert.equal(chip.label, "部分完成");
  assert.equal(chip.tone, "is-warn");
});

test("全部成功才显示已完成", () => {
  const chip = captureStatusChip("succeeded", true, [
    { key: "store", label: "店铺首页", state: "ok" },
    { key: "orders", label: "订单列表", state: "ok" },
  ]);
  assert.equal(chip.label, "已完成");
  assert.equal(chip.tone, "is-ok");
});

test("全部失败显示未采到数据", () => {
  const chip = captureStatusChip("succeeded", true, [
    { key: "store", label: "店铺首页", state: "failed" },
    { key: "orders", label: "订单列表", state: "failed" },
  ]);
  assert.equal(chip.label, "未采到数据");
  assert.equal(chip.tone, "is-error");
});

test("排队、下发、运行都算进行中", () => {
  assert.equal(isActiveCaptureStatus("queued"), true);
  assert.equal(isActiveCaptureStatus("sent"), true);
  assert.equal(isActiveCaptureStatus("running"), true);
  assert.equal(isActiveCaptureStatus("succeeded"), false);
  assert.equal(isActiveCaptureStatus("failed"), false);
});
