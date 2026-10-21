import assert from "node:assert/strict";
import test from "node:test";

import {
  AI_VIDEO_START_PREFILL_KEY,
  consumeStartPrefill,
  writeStartPrefill,
  type PrefillStorage,
} from "../start-prefill.ts";

class MemoryStorage implements PrefillStorage {
  readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  removeItem(key: string): void {
    this.values.delete(key);
  }
}

test("写一次、消费一次：读完立即清除，刷新不会重放", () => {
  const storage = new MemoryStorage();
  writeStartPrefill(storage, { productId: "p1" });

  assert.deepEqual(consumeStartPrefill(storage), { productId: "p1" });
  assert.equal(storage.getItem(AI_VIDEO_START_PREFILL_KEY), null);
  assert.equal(consumeStartPrefill(storage), null);
});

test("坏数据 / 空 id 一律当作没有交接", () => {
  const storage = new MemoryStorage();
  storage.setItem(AI_VIDEO_START_PREFILL_KEY, "{not json");
  assert.equal(consumeStartPrefill(storage), null);

  storage.setItem(AI_VIDEO_START_PREFILL_KEY, JSON.stringify({ productId: "  " }));
  assert.equal(consumeStartPrefill(storage), null);

  assert.equal(consumeStartPrefill(null), null);
});
