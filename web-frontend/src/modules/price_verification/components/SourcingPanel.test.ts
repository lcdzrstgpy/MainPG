import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const styles = readFileSync(new URL("../styles/priceVerificationSource.css", import.meta.url), "utf8");
const panel = readFileSync(new URL("./SourcingPanel.tsx", import.meta.url), "utf8");

test("source search statistics have space between each metric and its value", () => {
  assert.match(styles, /\.pv-source-inline-stats\s*\{[^}]*gap:\s*18px/);
  assert.match(styles, /\.pv-source-inline-stats\s+span\s*\{[^}]*gap:\s*6px/);
  assert.match(styles, /\.pv-source-inline-stats\s*\{[^}]*flex-wrap:\s*wrap/);
});

test("each SKC exposes the manual 1688 lookup dialog", () => {
  assert.match(panel, /手动查1688/);
  assert.match(panel, /请输入1688商品详情链接/);
  assert.match(panel, /onManualLookup/);
});

test("reference product images expand toward the content area and support full-size preview", () => {
  assert.match(panel, /pv-source-temu-image-trigger/);
  assert.match(panel, /setImagePreviewUrl\(mainImageUrl\)/);
  assert.match(panel, /pv-source-image-preview-backdrop/);
  assert.match(styles, /\.pv-source-temu-image:hover\s*\{[^}]*transform-origin:\s*left center/);
  assert.match(styles, /\.pv-source-temu-image-trigger:hover,[\s\S]*?z-index:\s*20/);
  assert.match(styles, /\.pv-source-image-preview-backdrop\s*\{/);
});
