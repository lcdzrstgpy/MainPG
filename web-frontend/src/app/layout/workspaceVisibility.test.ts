import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const sources = [
  "../../modules/daily_selection/pages/DailySelectionPage.tsx",
  "../../modules/profit_activity/pages/ProfitActivityTestPage.tsx",
  "../../modules/profit_activity/pages/ProfitActivityProductsPage.tsx",
  "../../modules/price_verification/components/SourcingPanel.tsx",
  "../../modules/product_processing/pages/ProductProcessingVerifyPage.tsx",
  "../../modules/product_processing/pages/ProductProcessingPrecheckPage.tsx",
  "../../modules/product_processing/pages/DimensionCanvasPage.tsx",
].map((path) => readFileSync(new URL(path, import.meta.url), "utf8"));

test("workspace-aware pages close transient layers when their tab becomes inactive", () => {
  for (const source of sources) {
    assert.match(source, /isActive\??:\s*boolean/);
    assert.match(source, /\[isActive\]/);
  }

  assert.match(sources[0], /setHistoryDrawerOpen\(false\)/);
  assert.match(sources[1], /setSettingsDialogOpen\(false\)/);
  assert.match(sources[2], /setPreviewImage\(null\)/);
  assert.match(sources[3], /setManualLookupSkcId\(""\)/);
  assert.match(sources[4], /setSkuDrawerDraftId\(null\)/);
  assert.match(sources[5], /setActiveImage\(null\)/);
  assert.match(sources[6], /setImportOpen\(false\)/);
});
