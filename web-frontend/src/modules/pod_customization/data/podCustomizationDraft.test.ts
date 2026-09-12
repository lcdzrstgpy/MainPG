import assert from "node:assert/strict";
import test from "node:test";

import {
  createEmptyPodCustomizationDraft,
  createPodSystemTemplate,
  loadPodCustomizationDraft,
  podCustomizationDraftStorageKey,
  removePodSystemTemplate,
  resolvePodSystemTemplate,
  savePodCustomizationDraft,
  type PodCustomizationStorage,
} from "./podCustomizationDraft.ts";
import type { PodTemplate } from "../types";

class MemoryStorage implements PodCustomizationStorage {
  readonly entries = new Map<string, string>();

  getItem(key: string): string | null {
    return this.entries.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.entries.set(key, value);
  }

  removeItem(key: string): void {
    this.entries.delete(key);
  }
}

function template(id: string, previewUrl = `https://assets.example/${id}-preview.png`): PodTemplate {
  return {
    id,
    name: `模板 ${id}`,
    source: "personal",
    preview_url: previewUrl,
    original_url: `https://assets.example/${id}-original.png`,
    width: 1200,
    height: 900,
    calibration_status: "ready",
    calibration: { mask: { x: 0.1, y: 0.2, width: 0.7, height: 0.6 }, anchor: { x: 0.5, y: 0.5 } },
    mask_preview_url: `https://assets.example/${id}-mask.png`,
    created_at: "2026-08-24T08:00:00.000Z",
    updated_at: "2026-08-24T08:00:00.000Z",
  };
}

test("POD drafts are isolated by account and workspace", () => {
  const storage = new MemoryStorage();
  const first = createEmptyPodCustomizationDraft();
  first.business_fields.product_name = "旅行杯";
  first.listing_fields.skus = [
    { name: "默认款", declared_price: "18.5", weight_g: "400" },
    { name: "礼盒款", declared_price: "19.9", weight_g: "450" },
  ];
  assert.deepEqual(savePodCustomizationDraft("account-a", "workspace-a", first, storage), { ok: true });

  assert.equal(podCustomizationDraftStorageKey("account-a", "workspace-a"), "mainpg:pod-customization:v4:account-a:workspace-a");
  assert.equal(loadPodCustomizationDraft("account-a", "workspace-a", storage).state.business_fields.product_name, "旅行杯");
  assert.deepEqual(loadPodCustomizationDraft("account-a", "workspace-a", storage).state.listing_fields.skus, first.listing_fields.skus);
  assert.equal(loadPodCustomizationDraft("account-b", "workspace-a", storage).state.business_fields.product_name, "");
  assert.equal(loadPodCustomizationDraft("account-a", "workspace-b", storage).state.business_fields.product_name, "");
});

test("new POD drafts start with one blank SKU carrying declared price and weight", () => {
  assert.deepEqual(createEmptyPodCustomizationDraft().listing_fields.skus, [
    { name: "", declared_price: "", weight_g: "" },
  ]);
});

test("v2 POD drafts move their global weight and declared price into each SKU and dimensions into the spec card", () => {
  const storage = new MemoryStorage();
  const legacyKey = "mainpg:pod-customization:v2:account-a:workspace-a";
  storage.setItem(legacyKey, JSON.stringify({
    ...createEmptyPodCustomizationDraft(),
    version: 2,
    listing_fields: {
      title_mode: "long",
      declared_price: "18.5",
      suggested_price_usd: "29.99",
      weight_g: "450",
      category_name: "收纳",
      skus: [{ name: "默认款", length_cm: "30", width_cm: "20", height_cm: "10" }],
    },
  }));

  const result = loadPodCustomizationDraft("account-a", "workspace-a", storage);

  assert.deepEqual(result.state.listing_fields.skus, [
    { name: "默认款", declared_price: "18.5", weight_g: "450" },
  ]);
  assert.deepEqual(result.state.spec_card.cells, [
    ["尺寸图", "长", "宽", "高"],
    ["默认款", "30", "20", "10"],
  ]);
});

test("v1 POD drafts migrate SKU names and product dimensions into per-SKU listing fields and spec-card rows", () => {
  const storage = new MemoryStorage();
  const legacyKey = "mainpg:pod-customization:v1:account-a:workspace-a";
  const legacy = {
    ...createEmptyPodCustomizationDraft(),
    version: 1,
    listing_fields: {
      title_mode: "long",
      declared_price: "18.5",
      suggested_price_usd: "29.99",
      length_cm: "30",
      width_cm: "20",
      height_cm: "10",
      weight_g: "450",
      category_name: "收纳",
      sku_names: ["  米白 ", "深蓝"],
    },
  };
  storage.setItem(legacyKey, JSON.stringify(legacy));

  const result = loadPodCustomizationDraft("account-a", "workspace-a", storage);

  assert.equal(result.error, undefined);
  assert.deepEqual(result.state.listing_fields.skus, [
    { name: "  米白 ", declared_price: "18.5", weight_g: "450" },
    { name: "深蓝", declared_price: "18.5", weight_g: "450" },
  ]);
  assert.deepEqual(result.state.spec_card.cells, [
    ["尺寸图", "长", "宽", "高"],
    ["  米白 ", "30", "20", "10"],
    ["深蓝", "30", "20", "10"],
  ]);
});

test("v1 POD drafts without SKU names migrate to the default SKU", () => {
  const storage = new MemoryStorage();
  const legacyKey = "mainpg:pod-customization:v1:account-a:workspace-a";
  storage.setItem(legacyKey, JSON.stringify({
    ...createEmptyPodCustomizationDraft(),
    version: 1,
    listing_fields: {
      title_mode: "long",
      declared_price: "",
      suggested_price_usd: "",
      length_cm: "30",
      width_cm: "20",
      height_cm: "10",
      weight_g: "",
      category_name: "",
      sku_names: [],
    },
  }));

  const result = loadPodCustomizationDraft("account-a", "workspace-a", storage);

  assert.deepEqual(result.state.listing_fields.skus, [
    { name: "默认款", declared_price: "", weight_g: "" },
  ]);
  assert.deepEqual(result.state.spec_card.cells, [
    ["尺寸图", "长", "宽", "高"],
    ["默认款", "30", "20", "10"],
  ]);
});

test("malformed v1 POD drafts are safely removed from the legacy key", () => {
  const storage = new MemoryStorage();
  const legacyKey = "mainpg:pod-customization:v1:account-a:workspace-a";
  storage.setItem(legacyKey, "not-json");

  const result = loadPodCustomizationDraft("account-a", "workspace-a", storage);

  assert.equal(result.error, "POD 草稿数据已损坏，已清除当前账号的本地草稿。");
  assert.equal(storage.getItem(legacyKey), null);
});

test("malformed POD draft payload is removed only from its own scope", () => {
  const storage = new MemoryStorage();
  const brokenKey = podCustomizationDraftStorageKey("account-a", "workspace-a");
  const intactKey = podCustomizationDraftStorageKey("account-b", "workspace-a");
  storage.setItem(brokenKey, "not-json");
  storage.setItem(intactKey, JSON.stringify(createEmptyPodCustomizationDraft()));

  const result = loadPodCustomizationDraft("account-a", "workspace-a", storage);

  assert.equal(result.state.business_fields.product_name, "");
  assert.equal(result.error, "POD 草稿数据已损坏，已清除当前账号的本地草稿。");
  assert.equal(storage.getItem(brokenKey), null);
  assert.notEqual(storage.getItem(intactKey), null);
});

test("POD draft save returns a safe error when storage is unavailable", () => {
  const storage: PodCustomizationStorage = {
    getItem: () => { throw new Error("blocked"); },
    setItem: () => { throw new Error("blocked"); },
    removeItem: () => { throw new Error("blocked"); },
  };

  const result = savePodCustomizationDraft("account-a", "workspace-a", createEmptyPodCustomizationDraft(), storage);

  assert.deepEqual(result, { ok: false, error: "无法保存 POD 草稿：浏览器本地存储不可用。" });
});

test("system template creation trims its name and preserves the saved template image snapshot", () => {
  const source = template("template-1", "https://assets.example/original-choice.png");
  const result = createPodSystemTemplate({
    name: "  露营收纳篮  ",
    creativePrompt: "保存时的完整提示词",
    template: source,
    id: "system-template-1",
    createdAt: "2026-08-24T09:00:00.000Z",
  });

  assert.equal(result.ok, true);
  if (!result.ok) return;
  source.preview_url = "https://assets.example/latest-upload.png";
  source.calibration!.mask.width = 0.1;
  assert.equal(result.template.name, "露营收纳篮");
  assert.equal(result.template.templateId, "template-1");
  assert.equal(result.template.template.preview_url, "https://assets.example/original-choice.png");
  assert.equal(result.template.template.calibration?.mask.width, 0.7);
});

test("system templates can be deleted and retain their saved snapshot when their linked template remains available", () => {
  const created = createPodSystemTemplate({
    name: "模板",
    creativePrompt: "提示词",
    template: template("template-1"),
    id: "system-template-1",
    createdAt: "2026-08-24T09:00:00.000Z",
  });
  assert.equal(created.ok, true);
  if (!created.ok) return;

  assert.deepEqual(resolvePodSystemTemplate(created.template, [template("template-1", "https://assets.example/newest.png")]), {
    valid: true,
    template: created.template.template,
  });
  assert.deepEqual(resolvePodSystemTemplate(created.template, []), {
    valid: false,
    reason: "关联的图片模板已不可用，无法用于本批次。",
  });

  const next = removePodSystemTemplate([created.template], created.template.id);
  assert.deepEqual(next, []);
});

test("blank system template names are rejected", () => {
  const result = createPodSystemTemplate({ name: "  ", creativePrompt: "提示词", template: template("template-1") });
  assert.deepEqual(result, { ok: false, error: "请填写系统模板名称。" });
});

test("old drafts without style_planning load with it backfilled as empty string", () => {
  const storage = new MemoryStorage();
  const state = createEmptyPodCustomizationDraft();
  const legacy = JSON.parse(JSON.stringify(state)) as Record<string, unknown>;
  const fields = legacy["business_fields"] as Record<string, unknown>;
  delete fields["style_planning"];
  storage.setItem(podCustomizationDraftStorageKey("account-a", "workspace-a"), JSON.stringify(legacy));

  const loaded = loadPodCustomizationDraft("account-a", "workspace-a", storage);
  assert.equal(loaded.state.business_fields.style_planning, "");
});

test("v3 drafts saved before brief history load with an empty brief_history instead of being discarded", () => {
  const storage = new MemoryStorage();
  const state = createEmptyPodCustomizationDraft();
  const legacy = JSON.parse(JSON.stringify(state)) as Record<string, unknown>;
  delete legacy["brief_history"];
  (legacy["business_fields"] as Record<string, unknown>)["product_name"] = "保留商品";
  storage.setItem(podCustomizationDraftStorageKey("account-a", "workspace-a"), JSON.stringify(legacy));

  const loaded = loadPodCustomizationDraft("account-a", "workspace-a", storage);

  assert.equal(loaded.error, undefined);
  assert.equal(loaded.state.business_fields.product_name, "保留商品");
  assert.deepEqual(loaded.state.brief_history, []);
});

test("brief history written into a draft is read back unchanged", () => {
  const storage = new MemoryStorage();
  const state = createEmptyPodCustomizationDraft();
  state.brief_history = [{
    id: "brief-1",
    input: "美式复古托特包",
    fields: { ...state.business_fields, product_name: "托特包", style_keywords: "仙人掌、纳瓦霍几何" },
    created_at: "2026-09-12T01:00:00.000Z",
  }];

  assert.deepEqual(savePodCustomizationDraft("account-a", "workspace-a", state, storage), { ok: true });
  assert.deepEqual(loadPodCustomizationDraft("account-a", "workspace-a", storage).state.brief_history, state.brief_history);
});
