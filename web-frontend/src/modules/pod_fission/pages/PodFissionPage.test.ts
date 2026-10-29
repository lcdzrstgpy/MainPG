import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("./PodFissionPage.tsx", import.meta.url), "utf8");
const types = readFileSync(new URL("../types.ts", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/podFissionApi.ts", import.meta.url), "utf8");
const draft = readFileSync(new URL("../data/podFissionDraft.ts", import.meta.url), "utf8");
const sharedTypes = readFileSync(new URL("../../pod_customization/types/index.ts", import.meta.url), "utf8");

test("fission batch contract includes shared listing fields and editable SKUs", () => {
  assert.match(types, /FissionListingFields = PodListingFields/);
  assert.match(sharedTypes, /title_mode/);
  assert.match(sharedTypes, /category_name/);
  assert.match(sharedTypes, /skus/);
  assert.match(page, /SKU/);
  assert.match(page, /category_name/);
  assert.match(page, /onAddSku|addSku/);
});

test("restored fission assets are checked with an authenticated request", () => {
  assert.match(page, /assetExists/);
  assert.match(api, /getAuthToken/);
  assert.match(api, /[Aa]uthorization|authorization/);
});

test("fission draft persists listing fields with account and workspace scope", () => {
  assert.match(draft, /listingFields|listing_fields/);
  assert.match(page, /savePodFissionDraft/);
  assert.match(page, /loadPodFissionDraft/);
  assert.match(page, /getAuthAccount/);
});

test("fission results reuse gallery, lightbox, history, retry and spec-card components", () => {
  for (const component of ["PodBatchGallery", "PodResultLightbox", "PodBatchHistoryDrawer", "PodFailedRetryDialog", "SpecCardDrawer"]) assert.match(page, new RegExp(`<${component}`));
  assert.match(page, /scope="fission"/);
});

test("one click starts fission: create posts both assets and the requested count", () => {
  assert.doesNotMatch(page, /analyzeAssets/);
  assert.match(page, /source_asset_id: source\.id/);
  assert.match(page, /target_asset_id: target\.id/);
  assert.match(page, /requested_count: count/);
  assert.match(page, /开始裂变/);
  assert.match(page, /await podFissionApi\.retryStyle[\s\S]{0,200}refreshActiveBatch/);
});

test("legacy grid batches are read only in the UI", () => {
  assert.match(page, /generation_strategy === "grid"/);
  assert.match(page, /历史裂变批次只读/);
});

test("quantity presets use the shared sliding indicator so the active pill stays visible", () => {
  assert.match(page, /useSlidingIndicator/);
  assert.match(page, /pod-selection-thumb/);
  assert.match(page, /data-indicator-ready/);
});
