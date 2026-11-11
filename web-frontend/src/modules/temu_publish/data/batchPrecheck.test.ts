import assert from "node:assert/strict";
import test from "node:test";

import { precheckBatchItem } from "./batchPrecheck.ts";
import type { ProductTemplate } from "./localStore";
import type { TemuPublishPrefill } from "../types.ts";

/** 一份「能发出去」的基准：各条测试只改自己关心的那几项。 */
function makeTemplate(overrides: Partial<ProductTemplate> = {}): ProductTemplate {
  return {
    name: "基准模板",
    categoryPath: [
      { catId: 1, catName: "家居" },
      { catId: 29163, catName: "抱枕" },
    ],
    attrs: {},
    freightId: "freight-1",
    warehouseId: "wh-1",
    shipmentDays: "9",
    origin: "CN",
    sizeTemplates: "",
    isCustom: false,
    isSensitive: false,
    savedAt: "2026-01-01T00:00:00.000Z",
    ...overrides,
  };
}

function makePrefill(overrides: Partial<TemuPublishPrefill> = {}): TemuPublishPrefill {
  return {
    title: "抱枕套 45x45",
    images: ["/media/a.png", "/media/b.png", "/media/c.png"],
    detailImages: [],
    sku: "PILLOW-1",
    weightKg: "0.3",
    lengthCm: "20",
    widthCm: "20",
    heightCm: "10",
    supplierPrice: "12.5",
    stock: "5",
    specValue1: "米白",
    specValue2: "45x45",
    ...overrides,
  };
}

test("预检：正常的草稿不该报任何阻塞项", () => {
  const result = precheckBatchItem(makePrefill(), makeTemplate(), null);
  assert.deepEqual(result.blocked, []);
});

test("预检：轮播不足 3 张要说清当前几张（Temu 硬性要求）", () => {
  const result = precheckBatchItem(makePrefill({ images: ["/media/a.png"] }), makeTemplate(), null);
  assert.equal(result.blocked.length, 1);
  assert.match(result.blocked[0], /轮播图只有 1 张/);
  assert.match(result.blocked[0], /至少 3 张/);
});

test("预检：模板缺类目/仓库/运费都要拦，而不是等网关拒", () => {
  const result = precheckBatchItem(
    makePrefill(),
    makeTemplate({ categoryPath: [], warehouseId: "", freightId: "" }),
    null,
  );
  assert.equal(result.blocked.length, 3);
  assert.ok(result.blocked.some((item) => item.includes("没有类目")));
  assert.ok(result.blocked.some((item) => item.includes("默认发货仓库")));
  assert.ok(result.blocked.some((item) => item.includes("运费模板")));
});

test("预检：价格/重量/三边缺一个都要指名道姓", () => {
  const result = precheckBatchItem(
    makePrefill({ supplierPrice: "", weightKg: "0", lengthCm: "", widthCm: "", heightCm: "" }),
    makeTemplate(),
    null,
  );
  assert.ok(result.blocked.some((item) => item.includes("申报价格")));
  assert.ok(result.blocked.some((item) => item.includes("重量")));
  assert.ok(result.blocked.some((item) => item.includes("长度")));
  assert.ok(result.blocked.some((item) => item.includes("宽度")));
  assert.ok(result.blocked.some((item) => item.includes("高度")));
});

test("预检：变种模板的值会覆盖草稿自带的（批量套模板时不能报假错）", () => {
  // 草稿本身没有尺码值，但套了变种模板就有 —— 预检必须跟着模板走
  const result = precheckBatchItem(
    makePrefill({ specValue1: "", specValue2: "" }),
    makeTemplate(),
    {
      name: "变种模板",
      colorsText: "米白,灰",
      sizesText: "45x45,50x50",
      batch: {
        supplierPrice: "19.9",
        suggestedPrice: "",
        stock: "10",
        weightG: "400",
        lengthCm: "22",
        widthCm: "22",
        heightCm: "12",
      },
      savedAt: "2026-01-01T00:00:00.000Z",
    },
  );
  assert.deepEqual(result.blocked, []);
});

test("预检：库存 0 / 没有变种值只算提醒，不拦发布", () => {
  const result = precheckBatchItem(
    makePrefill({ stock: "0", specValue1: "", specValue2: "", specValues1: [], specValues2: [] }),
    makeTemplate(),
    null,
  );
  assert.deepEqual(result.blocked, []);
  assert.ok(result.warnings.some((item) => item.includes("库存 0")));
  assert.ok(result.warnings.some((item) => item.includes("1 个 SKU")));
});

test("预检：标题超过 500 字符要拦（Temu 官方上限）", () => {
  const result = precheckBatchItem(makePrefill({ title: "x".repeat(501) }), makeTemplate(), null);
  assert.equal(result.blocked.length, 1);
  assert.match(result.blocked[0], /标题 501 字符，超过 Temu 上限 500/);
});

test("预检：重量超出 2~1000000g 要拦（实测回执里的边界）", () => {
  const tooLight = precheckBatchItem(makePrefill({ weightKg: "0.001" }), makeTemplate(), null);
  assert.ok(tooLight.blocked.some((item) => /重量.*超出 Temu 允许范围 2~1000000g/.test(item)));

  const tooHeavy = precheckBatchItem(makePrefill({ weightKg: "1001" }), makeTemplate(), null);
  assert.ok(tooHeavy.blocked.some((item) => /重量.*超出 Temu 允许范围 2~1000000g/.test(item)));

  // 边界值 2g / 1000000g 合法
  const ok = precheckBatchItem(makePrefill({ weightKg: "0.002" }), makeTemplate(), null);
  assert.deepEqual(ok.blocked, []);
});
