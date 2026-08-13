import assert from "node:assert/strict";
import test from "node:test";

import { DimensionNotificationRefreshFence } from "./dimensionNotificationRefresh.ts";

test("an older late success cannot overwrite a newer successful refresh", () => {
  const fence = new DimensionNotificationRefreshFence<string[]>();
  let notifications: string[] = [];
  const oldGeneration = fence.begin();
  assert.equal(typeof oldGeneration, "number");

  fence.setVisible(false);
  fence.setVisible(true);
  const newGeneration = fence.begin();
  assert.equal(typeof newGeneration, "number");
  assert.equal(fence.succeed(newGeneration!, ["new"], (value) => { notifications = value; }), true);
  assert.equal(fence.succeed(oldGeneration!, ["old"], (value) => { notifications = value; }), false);
  assert.deepEqual(notifications, ["new"]);
});

test("a newer failure still prevents an older late success from restoring stale notifications", () => {
  const fence = new DimensionNotificationRefreshFence<string[]>();
  let notifications = ["baseline"];
  const oldGeneration = fence.begin()!;

  fence.setVisible(false);
  fence.setVisible(true);
  const newGeneration = fence.begin()!;
  assert.equal(fence.fail(newGeneration), true);
  assert.equal(fence.succeed(oldGeneration, ["stale"], (value) => { notifications = value; }), false);
  assert.deepEqual(notifications, ["baseline"]);
});

test("only one visible request may be logically in flight", () => {
  const fence = new DimensionNotificationRefreshFence<string[]>();
  const generation = fence.begin()!;
  assert.equal(fence.begin(), null);
  assert.equal(fence.succeed(generation, [], () => undefined), true);
  assert.equal(typeof fence.begin(), "number");
});

test("hidden and stopped controllers reject response callbacks", () => {
  const hiddenFence = new DimensionNotificationRefreshFence<string[]>();
  const hiddenGeneration = hiddenFence.begin()!;
  hiddenFence.setVisible(false);
  assert.equal(hiddenFence.succeed(hiddenGeneration, [], () => assert.fail("must not apply")), false);

  const stoppedFence = new DimensionNotificationRefreshFence<string[]>();
  const stoppedGeneration = stoppedFence.begin()!;
  stoppedFence.stop();
  assert.equal(stoppedFence.succeed(stoppedGeneration, [], () => assert.fail("must not apply")), false);
  assert.equal(stoppedFence.begin(), null);
});
