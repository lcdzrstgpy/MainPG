import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./DailySelectionPage.tsx", import.meta.url), "utf8");

test("daily selection keeps its existing collection flow and adds a shop intake tab", () => {
  assert.match(source, /ShopCollectionPanel/);
  assert.match(source, /每日选品/);
  assert.match(source, /整店采集/);
  assert.match(source, /collectionWorkspaceMode === "shop"/);
  assert.match(source, /<ShopCollectionPanel isActive=\{isActive\}/);
  assert.match(source, /startCollectionTask/);
  assert.match(source, /startSkuRepull/);
  assert.match(source, /getCollectionRetryState/);
});
