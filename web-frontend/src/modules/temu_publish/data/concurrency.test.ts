import assert from "node:assert/strict";
import test from "node:test";

import { mapWithConcurrency } from "./concurrency.ts";

test("mapWithConcurrency：结果顺序和输入一致，跟谁先完成无关", async () => {
  const delays = [60, 10, 30, 5];
  const out = await mapWithConcurrency([0, 1, 2, 3], 3, async (item) => {
    await new Promise((resolve) => setTimeout(resolve, delays[item]));
    return `r${item}`;
  });
  assert.deepEqual(out, ["r0", "r1", "r2", "r3"]);
});

test("mapWithConcurrency：并发数不超过 limit", async () => {
  let running = 0;
  let peak = 0;
  await mapWithConcurrency(Array.from({ length: 9 }, (_, i) => i), 3, async () => {
    running += 1;
    peak = Math.max(peak, running);
    await new Promise((resolve) => setTimeout(resolve, 15));
    running -= 1;
    return null;
  });
  assert.equal(peak, 3, `峰值并发应等于 3，实际 ${peak}`);
});

test("mapWithConcurrency：一个失败整体失败（Promise.all 语义）", async () => {
  await assert.rejects(
    mapWithConcurrency([1, 2, 3], 2, async (item) => {
      if (item === 2) throw new Error("boom");
      return item;
    }),
    /boom/,
  );
});

test("mapWithConcurrency：空数组直接返回空，不会卡住", async () => {
  const out = await mapWithConcurrency([], 3, async () => "never");
  assert.deepEqual(out, []);
});
