import assert from "node:assert/strict";
import test from "node:test";

import {
  PRODUCTION_PROFILE_IDS,
  PRODUCTION_PROFILES,
  isProductionProfileId,
  productionProfilePatch,
} from "../production-profiles.ts";

test("every production profile is internally consistent", () => {
  for (const id of PRODUCTION_PROFILE_IDS) {
    const profile = PRODUCTION_PROFILES[id];
    assert.equal(profile.id, id);
    assert.ok(profile.motionStrength >= 0 && profile.motionStrength <= 1);
    assert.ok(profile.duration > 0);
  }
});

test("applying a profile preserves user-owned optional parameters", () => {
  const patch = productionProfilePatch("rapid", {
    imageParams: { aspectRatio: "16:9", count: 3, seed: 42, negativePrompt: "blur" },
    videoParams: { aspectRatio: "16:9", resolution: "1080p", seed: 7, fps: 30 },
  });

  assert.equal(patch.defaultResolution, "720p");
  assert.deepEqual(patch.imageParams, {
    aspectRatio: "16:9",
    count: 1,
    seed: 42,
    negativePrompt: "blur",
  });
  assert.deepEqual(patch.videoParams, {
    aspectRatio: "16:9",
    resolution: "720p",
    duration: 4,
    seed: 7,
    fps: 30,
    motionStrength: 0.35,
  });
  assert.equal(patch.activeProductionProfile, "rapid");
  assert.equal(patch.motionIntensity, "subtle");
  assert.equal(patch.chainMode, "off");
});

test("cinematic opts into 1080p while keeping the current aspect ratio", () => {
  const patch = productionProfilePatch("cinematic", {
    imageParams: { aspectRatio: "9:16", count: 2 },
    videoParams: { aspectRatio: "9:16", resolution: "720p", duration: 5 },
  });
  assert.equal(patch.defaultResolution, "1080p");
  assert.equal(patch.videoParams.resolution, "1080p");
  assert.equal(patch.videoParams.duration, 8);
  assert.equal(patch.videoParams.aspectRatio, "9:16");
  assert.equal(patch.imageParams.count, 1);
});

test("stale or unknown persisted ids are rejected", () => {
  assert.equal(isProductionProfileId("balanced"), true);
  assert.equal(isProductionProfileId("ultra"), false);
  assert.equal(isProductionProfileId(null), false);
});
