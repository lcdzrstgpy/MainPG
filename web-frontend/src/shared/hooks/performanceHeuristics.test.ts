import assert from "node:assert/strict";
import test from "node:test";

import {
  computeFrameRate,
  deviceLooksStrong,
  deviceLooksWeak,
  isFrameRateWeak,
  WEAK_FRAME_RATE_THRESHOLD,
} from "./performanceHeuristics.ts";

test("deviceLooksWeak: 2 核，或 4 核且内存 ≤4G 判弱；信息不足不判", () => {
  assert.equal(deviceLooksWeak({ cores: 1 }), true);
  assert.equal(deviceLooksWeak({ cores: 2 }), true);
  assert.equal(deviceLooksWeak({ cores: 4, memoryGb: 2 }), true);
  assert.equal(deviceLooksWeak({ cores: 4, memoryGb: 4 }), true);
  assert.equal(deviceLooksWeak({ cores: 4, memoryGb: 8 }), false);
  assert.equal(deviceLooksWeak({ cores: 4 }), false); // 拿不到内存档位：不判弱
  assert.equal(deviceLooksWeak({ cores: 8, memoryGb: 4 }), false);
  assert.equal(deviceLooksWeak({ cores: 0 }), false); // 探测不到核心数：不判弱
});

test("deviceLooksStrong: ≥8 核视为明显不弱", () => {
  assert.equal(deviceLooksStrong({ cores: 8 }), true);
  assert.equal(deviceLooksStrong({ cores: 16 }), true);
  assert.equal(deviceLooksStrong({ cores: 4 }), false);
  assert.equal(deviceLooksStrong({ cores: 0 }), false);
});

test("isFrameRateWeak: 低于阈值判弱，null（采样不可信）不判断", () => {
  assert.equal(isFrameRateWeak(null), false);
  assert.equal(isFrameRateWeak(WEAK_FRAME_RATE_THRESHOLD), false);
  assert.equal(isFrameRateWeak(WEAK_FRAME_RATE_THRESHOLD - 0.1), true);
  assert.equal(isFrameRateWeak(60), false);
});

test("computeFrameRate: 帧数折算与不可信采样", () => {
  assert.equal(computeFrameRate(121, 2000), 60);
  assert.equal(computeFrameRate(61, 2000), 30);
  assert.equal(computeFrameRate(5, 2000), null); // 帧数太少，不可信
  assert.equal(computeFrameRate(100, 500), null); // 时间太短，不可信
  assert.equal(computeFrameRate(0, 0), null);
});
