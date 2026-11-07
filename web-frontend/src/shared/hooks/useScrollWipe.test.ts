import assert from "node:assert/strict";
import test from "node:test";

import { wipePercent } from "./useScrollWipe.ts";

/**
 * 「滚动侵蚀」进度的口径：
 * 卡片一动就开始啃，滑过约一张卡的高度就啃满，全程 0..100 不越界。
 */

test("停在原位是 0，滑过一整程是 100", () => {
  assert.equal(wipePercent(100, 100, 142), 0);
  // 一程 = 高 142 + 80 = 222
  assert.equal(wipePercent(100, 100 - 222, 142), 100);
});

test("滑到一半是 50", () => {
  assert.equal(wipePercent(0, -111, 142), 50);
});

test("越界要夹住：往回滚不给负数，滚太远不超 100", () => {
  assert.equal(wipePercent(100, 160, 142), 0);
  assert.equal(wipePercent(100, -500, 142), 100);
});

test("元素高度为 0 时也不会除出 NaN", () => {
  assert.equal(Number.isFinite(wipePercent(0, -80, 0)), true);
  assert.equal(wipePercent(0, -80, 0), 100);
});
