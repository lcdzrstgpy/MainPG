import assert from "node:assert/strict";
import test from "node:test";

import {
  preserveVerifiedRelease,
  shouldPollUpdateStatus,
  shouldShowDeferAction,
  toUpdateDialogState,
  type AppUpdateStatus,
} from "./updateState.ts";

function status(state: AppUpdateStatus["state"]): AppUpdateStatus {
  return {
    state,
    current_version: "1.2.0",
    release: null,
    progress: null,
    error: null,
  };
}

test("available optional release renders version, notes, and defer action", () => {
  const state = toUpdateDialogState({
    state: "available",
    current_version: "1.2.0",
    release: {
      version: "1.3.0",
      mandatory: false,
      release_notes: "修复同步问题\n提升启动速度",
      published_at: "2026-08-17T00:00:00Z",
    },
  });

  assert.deepEqual(state, {
    visible: true,
    mandatory: false,
    currentVersion: "1.2.0",
    targetVersion: "1.3.0",
    notes: ["修复同步问题", "提升启动速度"],
    phase: "ready",
    progress: null,
    message: "发现新版本，可立即更新。",
    error: "",
  });
});

test("mandatory release cannot be deferred", () => {
  const state = toUpdateDialogState({
    state: "available",
    current_version: "1.2.0",
    release: {
      version: "2.0.0",
      mandatory: true,
      release_notes: "需要立即升级",
      published_at: "2026-08-17T00:00:00Z",
    },
  });

  assert.equal(state.visible, true);
  assert.equal(state.mandatory, true);
  assert.equal(state.phase, "ready");
});

test("retry check failure keeps a verified mandatory release visible and non-deferrable", () => {
  const available: AppUpdateStatus = {
    state: "available",
    current_version: "1.2.0",
    release: {
      version: "2.0.0",
      mandatory: true,
      release_notes: "需要立即升级",
      published_at: "2026-08-17T00:00:00Z",
    },
    progress: null,
    error: null,
  };
  const checking = preserveVerifiedRelease(available, {
    ...status("checking"),
    current_version: available.current_version,
  });
  const failed = preserveVerifiedRelease(checking, {
    ...status("failed"),
    current_version: available.current_version,
    error: "更新服务暂时不可用",
  });
  const view = toUpdateDialogState(failed);

  assert.equal(checking.release?.mandatory, true);
  assert.equal(failed.release, available.release);
  assert.equal(view.visible, true);
  assert.equal(view.mandatory, true);
  assert.equal(view.phase, "failed");
  assert.equal(shouldShowDeferAction(view), false);
});

test("download progress and failure have distinct public dialog states", () => {
  const downloading = toUpdateDialogState({
    state: "downloading",
    current_version: "1.2.0",
    release: { version: "1.3.0", mandatory: false, release_notes: "", published_at: "" },
    progress: { downloaded_bytes: 42, total_bytes: 100, percent: 42 },
  });
  const failed = toUpdateDialogState({
    state: "failed",
    current_version: "1.2.0",
    release: { version: "1.3.0", mandatory: false, release_notes: "", published_at: "" },
    error: "下载包校验失败",
  });

  assert.deepEqual(
    { phase: downloading.phase, progress: downloading.progress, message: downloading.message },
    { phase: "downloading", progress: 42, message: "正在下载更新包…" },
  );
  assert.deepEqual(
    { phase: failed.phase, error: failed.error, message: failed.message },
    { phase: "failed", error: "下载包校验失败", message: "更新失败，请检查网络后重试。" },
  );
});

test("no update response does not render a dialog", () => {
  assert.equal(toUpdateDialogState({ state: "idle" }).visible, false);
});

test("checking status continues polling until it reaches a terminal state", () => {
  assert.equal(shouldPollUpdateStatus(status("checking")), true);
  assert.equal(shouldPollUpdateStatus(status("available")), false);
  assert.equal(shouldPollUpdateStatus(status("unavailable")), false);
  assert.equal(shouldPollUpdateStatus(status("failed")), false);
});

test("optional releases keep the defer action after an update failure", () => {
  assert.equal(shouldShowDeferAction({ mandatory: false, phase: "failed" }), true);
  assert.equal(shouldShowDeferAction({ mandatory: true, phase: "failed" }), false);
});
