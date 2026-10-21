import assert from "node:assert/strict";
import test from "node:test";

import {
  PRODUCT_LIBRARY_STORAGE_KEY,
  readProductsFrom,
  sanitizeProducts,
  writeProductsTo,
  type ProductStorage,
} from "./productLibraryData.ts";

class MemoryStorage implements ProductStorage {
  readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

test("空 / 损坏的存储返回空数组，不抛异常", () => {
  assert.deepEqual(readProductsFrom(null), []);
  assert.deepEqual(readProductsFrom(new MemoryStorage()), []);
  const broken = new MemoryStorage();
  broken.setItem(PRODUCT_LIBRARY_STORAGE_KEY, "{not json");
  assert.deepEqual(readProductsFrom(broken), []);
});

test("兼容 zustand 包装与裸数组，坏条目直接丢弃", () => {
  const fromWrapped = sanitizeProducts({
    state: {
      products: [
        { id: "p1", name: "茶", category: "food", images: ["/a.png"], videoCount: 2 },
        { id: "bad" },
        { id: "p1", name: "重复" },
      ],
    },
    version: 0,
  });
  assert.equal(fromWrapped.length, 1);
  assert.equal(fromWrapped[0].id, "p1");

  const fromArray = sanitizeProducts([
    { id: "p2", name: "杯子", category: "unknown", images: ["1", "2", "3", "4", "5", "6"], videoCount: -3 },
  ]);
  assert.equal(fromArray[0].category, "other");
  assert.equal(fromArray[0].images.length, 5);
  assert.equal(fromArray[0].videoCount, 0);
});

test("写盘后能原样读回（zustand persist 结构）", () => {
  const storage = new MemoryStorage();
  writeProductsTo(storage, [{
    id: "p1",
    name: "桂花乌龙",
    category: "food",
    images: ["/api/files/products/p1/a.png"],
    videoCount: 3,
    createdAt: new Date("2026-10-20T00:00:00.000Z"),
    description: "0 糖",
  }]);

  const stored = storage.getItem(PRODUCT_LIBRARY_STORAGE_KEY) ?? "";
  assert.match(stored, /"products"/);
  const [product] = readProductsFrom(storage);
  assert.equal(product.name, "桂花乌龙");
  assert.equal(product.videoCount, 3);
  assert.equal(product.description, "0 糖");
  assert.equal(product.createdAt.toISOString(), "2026-10-20T00:00:00.000Z");
});
