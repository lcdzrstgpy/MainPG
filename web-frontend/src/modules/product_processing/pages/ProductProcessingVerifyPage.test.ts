import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("./ProductProcessingVerifyPage.tsx", import.meta.url), "utf8");
const pageCss = readFileSync(new URL("../styles/ProductProcessingVerifyPage.css", import.meta.url), "utf8");

test("draft toolbar sticks via CSS sticky instead of a JS fixed layer", () => {
  // 曾经由 JS 量视口坐标切成 position:fixed，并用 spacer 占位；这种独立浮层会横向撑开、
  // 还会被 section 的入场 transform 当成包含块。现在只靠 CSS sticky，不应再出现这些写法。
  assert.doesNotMatch(page, /verify-sticky-toolbar-spacer/);
  assert.doesNotMatch(page, /stickyToolbarRef|stickySpacerRef|isStickyToolbar/);
  assert.doesNotMatch(page, /position = 'fixed'/);
  assert.match(pageCss, /\.verify-sticky-toolbar\s*\{[^}]*position:\s*sticky/);
  assert.match(pageCss, /\.verify-sticky-toolbar\s*\{[^}]*top:\s*calc\(var\(--workspace-topbar-height/);
});

test("SKU editor renders captured SKU images and keeps each variant attribute visible", () => {
  assert.match(page, /variantPresentation\(variant,\s*legacyTemuCurrency,/);
  assert.match(page, /className="verify-sku-image"/);
  assert.match(page, /referrerPolicy="no-referrer"/);
  assert.match(page, /className="verify-variant-attributes"/);
  assert.match(page, /presentation\.attributes\.map/);
  assert.match(page, /className="verify-variant-attribute"/);
});
