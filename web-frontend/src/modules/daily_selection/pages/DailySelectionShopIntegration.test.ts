import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./DailySelectionPage.tsx", import.meta.url), "utf8");
const workspaceSource = readFileSync(new URL("../../../app/layout/WorkspaceShell.tsx", import.meta.url), "utf8");

test("daily selection keeps its existing collection flow and adds shop and plugin intake tabs", () => {
  assert.match(source, /ShopCollectionPanel/);
  assert.match(source, /PluginOneboundCapturePanel/);
  assert.match(source, /每日选品/);
  assert.match(source, /整店采集/);
  assert.match(source, /插件采集/);
  assert.match(source, /collectionWorkspaceMode === "shop"/);
  assert.match(source, /collectionWorkspaceMode === "plugin"/);
  assert.match(source, /<ShopCollectionPanel isActive=\{isActive\}/);
  assert.match(source, /<PluginOneboundCapturePanel isActive=\{isActive\} onOpenDraft=\{onOpenProductProcessingDraft\}/);
  assert.match(source, /startCollectionTask/);
  assert.match(source, /startSkuRepull/);
  assert.match(source, /getCollectionRetryState/);
});

test("plugin draft entry delegates to the existing product-processing workspace navigation", () => {
  assert.match(source, /onOpenProductProcessingDraft\?: \(draftId: number\) => void/);
  assert.match(
    workspaceSource,
    /<DailySelectionPage[\s\S]{0,300}onOpenProductProcessingDraft=\{\(\) => openModule\("product_processing"\)\}/,
  );
});
