import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PodCustomizationPage.tsx", import.meta.url), "utf8");
const gallerySource = readFileSync(new URL("../components/PodBatchGallery.tsx", import.meta.url), "utf8");
const modelSource = readFileSync(new URL("../data/podCustomizationModel.ts", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/podCustomization.css", import.meta.url), "utf8");

test("POD page does not render the pending billing authorization banner", () => {
  assert.doesNotMatch(source, /待恢复的 POD 任务/);
  assert.doesNotMatch(source, /个任务需要重新授权/);
  assert.doesNotMatch(source, />继续任务</);
});

test("current template card follows the selected template and current draft", () => {
  assert.match(source, /const summaryTemplate = selectedTemplateSnapshot \?\? selectedTemplate;/);
  assert.match(source, /const summaryFields = businessFieldsForApi\(businessFields\);/);
  assert.doesNotMatch(source, /const summaryTemplate = activeBatch/);
  assert.doesNotMatch(source, /const summaryFields = activeBatch/);
});

test("saving a system template uses the image snapshot currently shown to the user", () => {
  const saveTemplate = source.slice(source.indexOf("const saveCurrentAsSystemTemplate"), source.indexOf("const applySystemTemplate"));
  assert.match(saveTemplate, /const templateSnapshot = selectedTemplateSnapshot \?\? selectedTemplate;/);
  assert.match(saveTemplate, /template: templateSnapshot/);
});

test("POD customer-facing copy does not mention four-grid generation", () => {
  assert.doesNotMatch(source, /四宫格/);
  assert.doesNotMatch(gallerySource, /四宫格/);
  assert.doesNotMatch(modelSource, /四宫格/);
  assert.doesNotMatch(source, /每款一次.*请求/);
  assert.doesNotMatch(source, /自动拆分为四张商品图/);
  assert.doesNotMatch(gallerySource, /每款一次.*请求/);
});

test("system-template save control is independent and immediately precedes generation", () => {
  const advancedStart = source.indexOf('<div className="pod-advanced-prompt">');
  const advancedEnd = source.indexOf('<div className="pod-volume-inline">', advancedStart);
  const saveControl = source.indexOf('className="pod-save-system-template-button"');
  const startControl = source.indexOf('className="pod-start-button"');

  assert.ok(saveControl >= 0, "expected a dedicated system-template save control");
  assert.ok(startControl > saveControl, "expected the save control above start generation");
  assert.ok(saveControl > advancedEnd, "expected the save control outside the advanced prompt editor");
  assert.doesNotMatch(source.slice(advancedStart, advancedEnd), /保存为系统模板/);
});

test("system-template save control has its own secondary action treatment", () => {
  assert.match(source, /className="pod-save-system-template-button"[\s\S]*?<span className="iconfont icon-save" aria-hidden="true" \/>[\s\S]*?className="pod-save-system-template-copy"[\s\S]*?<b>保存为系统模板<\/b>[\s\S]*?<small>保存当前提示词与模板图<\/small>/);
  assert.match(styles, /\.pod-save-system-template-button \{[\s\S]*?display: flex;[\s\S]*?border: 1px solid var\(--pod-border-strong\);/);
  assert.match(styles, /\.pod-save-system-template-copy small \{[\s\S]*?font-size:/);
});

test("listing editor supports independently adding and removing SKU names", () => {
  assert.match(source, /新增 SKU/);
  assert.match(source, /aria-label="SKU 名称"/);
  assert.match(source, /aria-label="删除 SKU"/);
  assert.match(source, /updateSkuName/);
  assert.match(source, /addSkuName/);
  assert.match(source, /removeSkuName/);
});

test("creating a batch keeps the current prompt draft for later refreshes", () => {
  const startBatch = source.slice(source.indexOf("const startBatch"), source.indexOf("const uploadTemplate"));
  assert.doesNotMatch(startBatch, /setCurrentBatchEdit\(null\)/);
});

test("a local draft recovery warning survives initial POD data loading", () => {
  const bootstrap = source.slice(source.indexOf("const bootstrap"), source.indexOf("useEffect(() => {\n    const updateVisibility"));
  assert.doesNotMatch(bootstrap, /setError\(""\)/);
});

test("POD local state fails closed when the signed-in account scope is incomplete", () => {
  assert.match(source, /const accountId = \(account\?\.account_id \|\| account\?\.customer_id\)\?\.trim\(\) \?\? "";/);
  assert.match(source, /const workspaceId = \(account\?\.workspace_id \|\| account\?\.workspace_code\)\?\.trim\(\) \?\? "";/);
  assert.match(source, /return accountId && workspaceId \? \{ accountId, workspaceId \} : null;/);
  assert.doesNotMatch(source, /account\?\.account_id \|\| "default"/);
});
