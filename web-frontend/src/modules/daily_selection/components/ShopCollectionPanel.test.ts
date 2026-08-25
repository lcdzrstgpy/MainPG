import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const styles = readFileSync(
  new URL("../styles/shop-collection.css", import.meta.url),
  "utf8",
);

type MediaBlock = { query: string; body: string; start: number; end: number };
const gridMobileRule = /\.shop-collection-grid\s*\{[^}]*grid-template-columns:\s*1fr\s*;/;
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

test("shop collection keeps two columns on desktop and stacks only on mobile", () => {
  const mediaBlocks = extractMediaBlocks(styles);
  const topLevelStyles = mediaBlocks.reduceRight(
    (css, block) => `${css.slice(0, block.start)}${css.slice(block.end)}`,
    styles,
  );
  assert.match(
    topLevelStyles,
    /\.shop-collection-grid\s*\{[^}]*grid-template-columns:\s*minmax\(220px,\s*\.58fr\)\s+minmax\(0,\s*1\.42fr\);/,
  );
  assert.doesNotMatch(
    topLevelStyles,
    gridMobileRule,
  );
  assert.doesNotMatch(topLevelStyles, headerMobileRule);
  assert.doesNotMatch(topLevelStyles, createMobileRule);

  const mobileBlocks = mediaBlocks.filter((block) => block.query === "(max-width: 760px)");
  assert.ok(mobileBlocks.length > 0, "expected a max-width 760px media block");
  const shopMobileBlock = mobileBlocks.find((block) => gridMobileRule.test(block.body));
  assert.ok(shopMobileBlock, "expected shop collection mobile layout rules");
  assertOnlyInMobileMedia(mediaBlocks, gridMobileRule, "shop collection one-column rule");
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
