import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_RENDER_PRESET,
  RENDER_PRESETS,
  isRenderPreset,
  recommendPreset,
  resolveRenderProfile,
  safeEncodeParams,
} from "../compose-presets.ts";

test("render presets resolve to the documented encoders", () => {
  assert.deepEqual(resolveRenderProfile("fast"), { resolution: "720p", videoPreset: "veryfast", crf: 26 });
  assert.equal(resolveRenderProfile("standard").resolution, "1080p");
  assert.deepEqual(resolveRenderProfile("hd"), { resolution: "1080p", videoPreset: "slow", crf: 17 });
});

test("invalid or absent presets fall back to the default", () => {
  assert.deepEqual(resolveRenderProfile("bogus"), RENDER_PRESETS[DEFAULT_RENDER_PRESET]);
  assert.deepEqual(resolveRenderProfile(undefined), RENDER_PRESETS.standard);
});

test("quality is monotonic: hd crf < standard crf < fast crf", () => {
  assert.ok(RENDER_PRESETS.hd.crf < RENDER_PRESETS.standard.crf);
  assert.ok(RENDER_PRESETS.standard.crf < RENDER_PRESETS.fast.crf);
});

test("isRenderPreset distinguishes a real selection from an arbitrary string", () => {
  for (const id of ["fast", "standard", "hd"]) assert.equal(isRenderPreset(id), true);
  for (const value of ["ultra", "", undefined, null, 123]) assert.equal(isRenderPreset(value), false);
});

test("safeEncodeParams whitelists the encoder and clamps crf", () => {
  assert.deepEqual(safeEncodeParams("veryfast", 26), { videoPreset: "veryfast", crf: 26 });
  assert.equal(safeEncodeParams("evil; rm -rf", 20).videoPreset, "medium");
  assert.equal(safeEncodeParams(undefined, 20).videoPreset, "medium");
  assert.equal(safeEncodeParams("medium", 999).crf, 51);
  assert.equal(safeEncodeParams("medium", -5).crf, 0);
  assert.equal(safeEncodeParams("medium", 20.7).crf, 21);
  assert.equal(safeEncodeParams("medium", NaN).crf, 18);
  assert.equal(safeEncodeParams("medium", undefined).crf, 18);
});

test("recommendPreset adapts to content complexity", () => {
  assert.equal(recommendPreset({ shotCount: 2, totalDuration: 12 }).preset, "fast");
  assert.equal(recommendPreset({ shotCount: 3, totalDuration: 45 }).preset, "hd");
  assert.equal(recommendPreset({ shotCount: 7, totalDuration: 20 }).preset, "hd");
  assert.equal(recommendPreset({ shotCount: 3, totalDuration: 20, i2vCount: 3 }).preset, "hd");
  assert.equal(recommendPreset({ shotCount: 4, totalDuration: 25 }).preset, "standard");
  assert.equal(recommendPreset({ shotCount: 2, totalDuration: 12, i2vCount: 1 }).preset, "standard");
  assert.equal(recommendPreset({ shotCount: 2, totalDuration: 12 }).reason.length > 0, true);
});
