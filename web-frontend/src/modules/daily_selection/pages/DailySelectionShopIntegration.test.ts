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

test("daily selection blocks inconsistent filter ranges before calling the backend", () => {
  const submit = source.slice(
    source.indexOf("async function submitCollection"),
    source.indexOf("async function cancelCollection"),
  );
  const guard = source.slice(
    source.indexOf("function collectionFilterError"),
    source.indexOf("function formatDate"),
  );

  // 区间上下限、正整数、非负价格的检查口径与后端模型级校验一致。
  for (const pair of ["最低价", "最高价", "SKU 最低价", "SKU 最高价", "SKU 数量下限", "SKU 数量上限", "SKU 最低库存", "SKU 最高库存"]) {
    assert.ok(guard.includes(pair), `筛选区间守卫缺少 ${pair}`);
  }
  assert.match(guard, /Number\.isInteger\(value\) \|\| value < 1/);
  assert.match(guard, /value < 0/);

  // 守卫必须在发起采集任务之前执行，否则仍会打到后端。
  assert.match(submit, /const filterError = collectionFilterError\(\{/);
  assert.match(submit, /setError\(`筛选条件有误：\$\{filterError\}`\)/);
  assert.ok(
    submit.indexOf("collectionFilterError(") < submit.indexOf("startCollectionTask"),
    "expected the filter guard before the create-task request",
  );
});
