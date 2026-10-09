import assert from "node:assert/strict";
import test from "node:test";

import { canEmbedClipForge, clipForgeInstanceKey, pollDelayForState } from "./clipforgeServiceState.ts";

test("polls starting quickly and ready periodically", () => {
  assert.equal(pollDelayForState("starting"), 800);
  assert.equal(pollDelayForState("ready"), 5000);
  assert.equal(pollDelayForState("stopped"), null);
  assert.equal(pollDelayForState("failed"), null);
});

test("polls while the sidecar is shutting down and never polls terminal states", () => {
  assert.equal(pollDelayForState("stopping"), 800);
  assert.equal(pollDelayForState("unavailable"), null);
});

test("embeds only a ready instance with url and instance id", () => {
  assert.equal(canEmbedClipForge({ state: "ready", url: "http://127.0.0.1:1", instanceId: "a" }), true);
  assert.equal(canEmbedClipForge({ state: "ready", url: "http://127.0.0.1:1", instanceId: null }), false);
  assert.equal(canEmbedClipForge({ state: "failed", url: "http://127.0.0.1:1", instanceId: "a" }), false);
});

test("never embeds an unknown or stopped sidecar", () => {
  assert.equal(canEmbedClipForge(null), false);
  assert.equal(canEmbedClipForge({ state: "starting", url: "http://127.0.0.1:1", instanceId: "a" }), false);
  assert.equal(canEmbedClipForge({ state: "ready", url: null, instanceId: "a" }), false);
});

test("instance key changes on url or instance id", () => {
  assert.notEqual(
    clipForgeInstanceKey({ url: "http://127.0.0.1:1", instanceId: "a" }),
    clipForgeInstanceKey({ url: "http://127.0.0.1:2", instanceId: "a" }),
  );
  assert.notEqual(
    clipForgeInstanceKey({ url: "http://127.0.0.1:1", instanceId: "a" }),
    clipForgeInstanceKey({ url: "http://127.0.0.1:1", instanceId: "b" }),
  );
  assert.equal(clipForgeInstanceKey(null), null);
  assert.equal(clipForgeInstanceKey({ url: "http://127.0.0.1:1", instanceId: null }), null);
});
