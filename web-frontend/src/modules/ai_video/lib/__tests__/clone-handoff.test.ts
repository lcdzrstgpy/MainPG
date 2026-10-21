import assert from "node:assert/strict";
import test from "node:test";

import {
  AI_VIDEO_CLONE_PREFILL_KEY,
  clearCloneHandoff,
  consumeCloneHandoff,
  writeCloneHandoff,
  type CloneHandoffStorage,
} from "../clone-handoff.ts";

class MemoryStorage implements CloneHandoffStorage {
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

const HANDOFF = {
  version: 1 as const,
  productName: "桂花乌龙",
  sellingPoints: "0 糖 0 卡",
  brief: { inputMode: "clone" as const, styleType: "scenario", styleSource: "explicit" as const, targetDuration: 30 as const },
  referenceStructure: "1:0-3s,2:3-8s",
  referenceVideoUrl: "https://example.com/video",
  productImages: ["/api/files/clone-prefill-x/a.png"],
};

test("写一次、消费一次：商品名/卖点/节奏骨架都能带过去", () => {
  const storage = new MemoryStorage();
  writeCloneHandoff(storage, HANDOFF);

  const consumed = consumeCloneHandoff(storage);
  assert.equal(consumed?.productName, "桂花乌龙");
  assert.equal(consumed?.sellingPoints, "0 糖 0 卡");
  assert.equal(consumed?.brief.styleType, "scenario");
  assert.equal(consumed?.brief.targetDuration, 30);
  assert.equal(consumed?.referenceStructure, "1:0-3s,2:3-8s");
  assert.deepEqual(consumed?.productImages, ["/api/files/clone-prefill-x/a.png"]);
  assert.equal(storage.getItem(AI_VIDEO_CLONE_PREFILL_KEY), null);
});

test("坏数据 / 非法时长按缺失处理，不抛异常", () => {
  const storage = new MemoryStorage();
  storage.setItem(AI_VIDEO_CLONE_PREFILL_KEY, "{bad json");
  assert.equal(consumeCloneHandoff(storage), null);

  writeCloneHandoff(storage, { ...HANDOFF, brief: { inputMode: "clone", targetDuration: 45 as never } });
  const consumed = consumeCloneHandoff(storage);
  assert.equal(consumed?.brief.targetDuration, undefined);
});

test("clear 可以主动丢弃交接", () => {
  const storage = new MemoryStorage();
  writeCloneHandoff(storage, HANDOFF);
  clearCloneHandoff(storage);
  assert.equal(consumeCloneHandoff(storage), null);
});
