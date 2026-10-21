import assert from "node:assert/strict";
import test from "node:test";

import {
  BRAND_STORAGE_KEY,
  defaultBrand,
  readBrandFrom,
  sanitizeBrand,
  writeBrandTo,
  type BrandStorage,
} from "./brandData.ts";

class MemoryStorage implements BrandStorage {
  readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

test("空 / 损坏的存储回落到默认品牌", () => {
  const defaults = defaultBrand();
  const normalize = (brand: ReturnType<typeof readBrandFrom>) => ({ ...brand, id: defaults.id });

  assert.deepEqual(normalize(readBrandFrom(null)), defaults);
  assert.deepEqual(normalize(readBrandFrom(new MemoryStorage())), defaults);
  const broken = new MemoryStorage();
  broken.setItem(BRAND_STORAGE_KEY, "{bad json");
  assert.deepEqual(normalize(readBrandFrom(broken)), defaults);
});

test("水印与颜色按白名单/区间清洗", () => {
  const brand = sanitizeBrand({
    name: "小店",
    primaryColor: "not-a-color",
    secondaryColor: "#112233",
    watermark: { enabled: true, position: "middle", opacity: 5, scale: 0 },
  });

  assert.equal(brand.name, "小店");
  assert.equal(brand.primaryColor, defaultBrand().primaryColor);
  assert.equal(brand.secondaryColor, "#112233");
  assert.equal(brand.watermark.enabled, true);
  assert.equal(brand.watermark.position, "bottom-right");
  assert.equal(brand.watermark.opacity, 1);
  assert.equal(brand.watermark.scale, 0.1);
});

test("写盘后读回（zustand persist 结构），片尾文案可选", () => {
  const storage = new MemoryStorage();
  const brand = {
    ...defaultBrand(),
    name: "桂花铺",
    watermark: { enabled: true, position: "top-left" as const, opacity: 0.5, scale: 0.2 },
    outroEnabled: true,
    outroText: "关注我们",
  };
  writeBrandTo(storage, brand);

  assert.match(storage.getItem(BRAND_STORAGE_KEY) ?? "", /"brand"/);
  const restored = readBrandFrom(storage);
  assert.equal(restored.name, "桂花铺");
  assert.equal(restored.watermark.position, "top-left");
  assert.equal(restored.watermark.opacity, 0.5);
  assert.equal(restored.outroText, "关注我们");
});
