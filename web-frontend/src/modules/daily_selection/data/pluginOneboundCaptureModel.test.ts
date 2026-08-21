import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  canRetryPluginCaptureFailures,
  isActivePluginCaptureStatus,
  isTerminalPluginCaptureStatus,
  pluginCaptureProgress,
  pluginCaptureStatusLabel,
  type PluginOneboundCaptureBatch,
} from "./pluginOneboundCaptureModel.ts";

const modelSource = readFileSync(new URL("./pluginOneboundCaptureModel.ts", import.meta.url), "utf8");

function batch(overrides: Partial<PluginOneboundCaptureBatch> = {}): PluginOneboundCaptureBatch {
  return {
    batch_id: "batch-1",
    parent_batch_id: "",
    page_url: "https://detail.1688.com/offer/1.html",
    status: "started",
    cancelled: false,
    created_count: 2,
    refreshed_count: 1,
    skipped_count: 1,
    failed_count: 1,
    unprocessed_count: 5,
    total_count: 10,
    error_code: "",
    error_message: "",
    created_at: "2026-08-21T00:00:00Z",
    updated_at: "2026-08-21T00:01:00Z",
    completed_at: null,
    ...overrides,
  };
}

test("maps the complete plugin capture lifecycle and polls only active batches", () => {
  assert.equal(pluginCaptureStatusLabel("prepared"), "等待启动万邦");
  assert.equal(pluginCaptureStatusLabel("queued"), "排队中");
  assert.equal(pluginCaptureStatusLabel("running"), "采集中");
  assert.equal(pluginCaptureStatusLabel("completed"), "采集完成");
  assert.equal(pluginCaptureStatusLabel("partial"), "部分完成");
  assert.equal(pluginCaptureStatusLabel("cancelled"), "已取消");
  assert.equal(pluginCaptureStatusLabel("failed"), "采集失败");
  assert.equal(pluginCaptureStatusLabel("started"), "采集中");
  assert.equal(pluginCaptureStatusLabel("finished"), "采集完成");
  assert.equal(pluginCaptureStatusLabel("expired"), "批次已过期");
  assert.equal(isActivePluginCaptureStatus("prepared"), true);
  assert.equal(isActivePluginCaptureStatus("queued"), true);
  assert.equal(isActivePluginCaptureStatus("running"), true);
  assert.equal(isActivePluginCaptureStatus("started"), true);
  for (const status of ["completed", "partial", "cancelled", "failed", "expired", "finished"] as const) {
    assert.equal(isActivePluginCaptureStatus(status), false);
    assert.equal(isTerminalPluginCaptureStatus(status), true);
  }
  assert.equal(isTerminalPluginCaptureStatus("finished"), true);
  assert.equal(isTerminalPluginCaptureStatus("expired"), true);
});

test("uses authoritative total count for progress and clamps the percentage", () => {
  assert.deepEqual(pluginCaptureProgress(batch()), { completed: 5, total: 10, percent: 50 });
  assert.deepEqual(pluginCaptureProgress(batch({ total_count: 20 })), { completed: 5, total: 20, percent: 25 });
  assert.deepEqual(pluginCaptureProgress(batch({ total_count: 3 })), { completed: 5, total: 3, percent: 100 });
  assert.deepEqual(pluginCaptureProgress(batch({ total_count: Number.NaN })), { completed: 5, total: 10, percent: 50 });
  assert.deepEqual(pluginCaptureProgress(batch({ created_count: 0, refreshed_count: 0, skipped_count: 0, failed_count: 0, unprocessed_count: 0, total_count: 0 })), { completed: 0, total: 0, percent: 0 });
});

test("models the complete persisted batch and item contract", () => {
  for (const field of ["page_url", "total_count", "error_message", "source_title", "attempts"]) {
    assert.match(modelSource, new RegExp(`${field}:`));
  }
  assert.match(modelSource, /PluginOneboundCaptureItemStatus[^;]+"unprocessed"/s);
  assert.match(modelSource, /outcome:[^;]+"unprocessed"/s);
});

test("allows retry only for terminal batches with failed items", () => {
  assert.equal(canRetryPluginCaptureFailures(batch({ status: "running" })), false);
  assert.equal(canRetryPluginCaptureFailures(batch({ status: "partial", failed_count: 1 })), true);
  assert.equal(canRetryPluginCaptureFailures(batch({ status: "failed", failed_count: 1 })), true);
  assert.equal(canRetryPluginCaptureFailures(batch({ status: "finished", failed_count: 1 })), true);
  assert.equal(canRetryPluginCaptureFailures(batch({ status: "expired", failed_count: 1 })), true);
  assert.equal(canRetryPluginCaptureFailures(batch({ status: "finished", failed_count: 0 })), false);
});
