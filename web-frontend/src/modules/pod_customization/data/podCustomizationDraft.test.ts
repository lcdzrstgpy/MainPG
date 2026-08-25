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
    { name: "默认款", length_cm: "20", width_cm: "10", height_cm: "8", weight_g: "400" },
    { name: "礼盒款", length_cm: "22", width_cm: "12", height_cm: "9", weight_g: "450" },
  ];
  assert.deepEqual(savePodCustomizationDraft("account-a", "workspace-a", first, storage), { ok: true });

  assert.equal(podCustomizationDraftStorageKey("account-a", "workspace-a"), "mainpg:pod-customization:v3:account-a:workspace-a");
  assert.equal(loadPodCustomizationDraft("account-a", "workspace-a", storage).state.business_fields.product_name, "旅行杯");
  assert.deepEqual(loadPodCustomizationDraft("account-a", "workspace-a", storage).state.listing_fields.skus, first.listing_fields.skus);
  assert.equal(loadPodCustomizationDraft("account-b", "workspace-a", storage).state.business_fields.product_name, "");
  assert.equal(loadPodCustomizationDraft("account-a", "workspace-b", storage).state.business_fields.product_name, "");
});

test("new POD drafts start with one blank SKU including weight", () => {
  assert.deepEqual(createEmptyPodCustomizationDraft().listing_fields.skus, [
    { name: "", length_cm: "", width_cm: "", height_cm: "", weight_g: "" },
  ]);
});

test("v2 POD drafts move their global weight into each SKU", () => {
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

  assert.deepEqual(loadPodCustomizationDraft("account-a", "workspace-a", storage).state.listing_fields.skus, [
    { name: "默认款", length_cm: "30", width_cm: "20", height_cm: "10", weight_g: "450" },
  ]);
});

test("v1 POD drafts migrate SKU names and product dimensions into per-SKU rows", () => {
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
    { name: "  米白 ", length_cm: "30", width_cm: "20", height_cm: "10", weight_g: "450" },
    { name: "深蓝", length_cm: "30", width_cm: "20", height_cm: "10", weight_g: "450" },
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

  assert.deepEqual(loadPodCustomizationDraft("account-a", "workspace-a", storage).state.listing_fields.skus, [
    { name: "默认款", length_cm: "30", width_cm: "20", height_cm: "10", weight_g: "" },
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
