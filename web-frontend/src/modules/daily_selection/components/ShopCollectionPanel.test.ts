import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const styles = readFileSync(
  new URL("../styles/shop-collection.css", import.meta.url),
  "utf8",
);
const dailyStyles = readFileSync(
  new URL("../styles/daily-selection.css", import.meta.url),
  "utf8",
);
const panel = readFileSync(new URL("./ShopCollectionPanel.tsx", import.meta.url), "utf8");
const page = readFileSync(new URL("../pages/DailySelectionPage.tsx", import.meta.url), "utf8");

type MediaBlock = { query: string; body: string; start: number; end: number };
const headerMobileRule = /\.shop-collection-header,\s*\.shop-batch-summary\s*\{[^}]*align-items:\s*stretch;[^}]*flex-direction:\s*column;/;
const createMobileRule = /\.shop-collection-create\s*\{[^}]*grid-template-columns:\s*1fr\s*;/;

function extractMediaBlocks(css: string): MediaBlock[] {
  const blocks: MediaBlock[] = [];
  const marker = /@media\s*([^{}]+)\{/g;
  let searchFrom = 0;

  while (true) {
    const match = marker.exec(css);
    if (!match) break;
    const markerStart = match.index;
    const openBrace = marker.lastIndex - 1;

    if (markerStart < searchFrom) continue;

    let depth = 0;
    for (let index = openBrace; index < css.length; index += 1) {
      if (css[index] === "{") depth += 1;
      if (css[index] === "}") depth -= 1;
      if (depth === 0) {
        blocks.push({
          query: match[1].trim(),
          body: css.slice(openBrace + 1, index),
          start: markerStart,
          end: index + 1,
        });
        searchFrom = index + 1;
        break;
      }
      if (index === css.length - 1) throw new Error(`Missing closing brace for ${match[0]}`);
    }
  }
  return blocks;
}

function assertOnlyInMobileMedia(mediaBlocks: MediaBlock[], rule: RegExp, description: string) {
  const matchingBlocks = mediaBlocks.filter((block) => rule.test(block.body));
  assert.ok(matchingBlocks.length > 0, `expected ${description} in a media block`);
  assert.ok(
    matchingBlocks.every((block) => block.query === "(max-width: 760px)"),
    `${description} must only be in max-width 760px media blocks`,
  );
}

test("shop collection keeps its form fallback rules scoped to mobile", () => {
  const mediaBlocks = extractMediaBlocks(styles);
  const mobileBlocks = mediaBlocks.filter((block) => block.query === "(max-width: 760px)");
  assert.ok(mobileBlocks.length > 0, "expected a max-width 760px media block");
  const shopMobileBlock = mobileBlocks.find((block) => createMobileRule.test(block.body));
  assert.ok(shopMobileBlock, "expected shop collection mobile layout rules");
  assertOnlyInMobileMedia(mediaBlocks, headerMobileRule, "shop header/summary stacking rule");
  assertOnlyInMobileMedia(mediaBlocks, createMobileRule, "shop create-form one-column rule");
  assert.match(shopMobileBlock.body, headerMobileRule);
  assert.match(shopMobileBlock.body, createMobileRule);

  const pluginMobileBlock = mobileBlocks.find((block) =>
    /\.plugin-capture-stats\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*1fr\)/.test(block.body),
  );
  assert.ok(pluginMobileBlock, "expected plugin capture mobile layout rules");
  assert.match(pluginMobileBlock.body, /\.plugin-capture-item-links\s*\{[^}]*align-items:\s*flex-end;[^}]*flex-direction:\s*column;/);
});

test("shop collection uses a dashboard layout with a compact metric rail", () => {
  assert.match(styles, /\.shop-collection-panel\s*\{[^}]*max-width:\s*none;[^}]*margin:\s*0;[^}]*gap:\s*16px;/);
  assert.match(styles, /\.shop-collection-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\);/);
  assert.match(styles, /\.shop-batch-stats\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*repeat\(4,\s*minmax\(0,\s*1fr\)\);/);
  assert.match(styles, /\.shop-batch-stats\s+span\s*\{[^}]*display:\s*flex;[^}]*background:\s*transparent;/);
  assert.match(styles, /\.shop-collection-header\s*\{[^}]*border:\s*0;[^}]*background:\s*transparent;/);
  assert.match(styles, /\.shop-collection-create\s*\{[^}]*border:\s*0;[^}]*background:\s*transparent;/);
  assert.match(styles, /\.shop-batch-detail\s*\{[^}]*border:\s*0\s*!important;[^}]*background:\s*transparent\s*!important;/);
  assert.match(styles, /\.shop-batch-summary\s*\{[^}]*background:\s*linear-gradient\(135deg,\s*var\(--theme-module-surface-tint/);
  assert.match(styles, /\.shop-batch-summary\s*\{[^}]*margin:\s*0\s+16px;/);
  assert.match(styles, /\.shop-batch-stats\s*\{[^}]*margin:\s*10px\s+16px\s+0;/);
  assert.match(styles, /\.shop-items-list\s*\{[^}]*margin:\s*0\s+16px;/);
});

test("shop collection moves batch management into a POD-style side drawer", () => {
  assert.match(page, /shop-batch-manager-trigger/);
  assert.match(page, /shopCollectionPanelRef\.current\?\.openBatchManager/);
  assert.match(page, /onBatchCountChange=\{setShopBatchCount\}/);
  assert.match(panel, /useImperativeHandle\(ref, \(\) => \(\{ openBatchManager/);
  assert.match(panel, /shop-batch-drawer-layer/);
  assert.match(panel, /createPortal\(/);
  assert.doesNotMatch(panel, /<aside className="shop-batch-list"/);
  assert.match(styles, /\.shop-batch-drawer-layer\s*\{[^}]*position:\s*fixed;[^}]*z-index:\s*450;/);
  assert.match(styles, /\.shop-batch-drawer\s*\{[^}]*right:\s*0;[^}]*width:\s*min\(400px,\s*94vw\)/);
});

test("collection workspace surfaces inherit the active theme instead of a fixed white board", () => {
  assert.match(dailyStyles, /\.daily-collection-surface\s*\{[^}]*border:\s*1px solid var\(--theme-module-border/);
  assert.match(dailyStyles, /\.daily-collection-surface\s*\{[^}]*background:\s*linear-gradient\(135deg,\s*color-mix\(in srgb, var\(--theme-module-surface-tint/);
  assert.match(dailyStyles, /\.daily-panel\s*\{[^}]*background:\s*linear-gradient\(135deg,\s*color-mix\(in srgb, var\(--theme-module-surface-tint/);
  assert.match(dailyStyles, /\.daily-drawer-body\s*\{[^}]*background:\s*color-mix\(in srgb, var\(--theme-module-surface-soft/);
  assert.doesNotMatch(dailyStyles, /\.daily-collection-surface\s*\{[^}]*background:\s*#f4f9fc/);
});

test("sunset workspace reserves its warm tint for emphasis instead of full-panel fills", () => {
  assert.match(dailyStyles, /\[data-theme="sunset"\] \.daily-selection-page \.daily-collection-surface\s*\{[^}]*surface-tint\) 16%, transparent/);
  assert.match(dailyStyles, /\[data-theme="sunset"\] \.daily-selection-page \.daily-collection-surface \.daily-panel\s*\{[^}]*surface-tint\) 18%, var\(--theme-module-surface-raised\)/);
  assert.match(styles, /\[data-theme="sunset"\] \.daily-selection-page \.shop-batch-summary,[\s\S]*surface-tint\) 24%, var\(--theme-module-surface-soft\)/);
});
