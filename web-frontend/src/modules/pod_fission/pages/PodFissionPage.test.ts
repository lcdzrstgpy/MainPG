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

test("replacing inputs invalidates analysis before uploading and retry refetches full batch", () => {
  const upload = page.slice(page.indexOf("const upload ="), page.indexOf("const analyze ="));
  assert.ok(upload.indexOf("setAnalysis(undefined)") < upload.indexOf("podFissionApi.uploadImage"));
  assert.match(page, /await podFissionApi\.retryStyle[\s\S]{0,200}refreshActiveBatch/);
});
