import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PodCustomizationPage.tsx", import.meta.url), "utf8");
const gallerySource = readFileSync(new URL("../components/PodBatchGallery.tsx", import.meta.url), "utf8");
const modelSource = readFileSync(new URL("../data/podCustomizationModel.ts", import.meta.url), "utf8");

test("POD page does not render the pending billing authorization banner", () => {
  assert.doesNotMatch(source, /待恢复的 POD 任务/);
  assert.doesNotMatch(source, /个任务需要重新授权/);
  assert.doesNotMatch(source, />继续任务</);
});

test("current template card follows the selected template and current draft", () => {
  assert.match(source, /const summaryTemplate = selectedTemplate;/);
  assert.match(source, /const summaryFields = businessFieldsForApi\(businessFields\);/);
  assert.doesNotMatch(source, /const summaryTemplate = activeBatch/);
  assert.doesNotMatch(source, /const summaryFields = activeBatch/);
});

test("POD customer-facing copy does not mention four-grid generation", () => {
  assert.doesNotMatch(source, /四宫格/);
  assert.doesNotMatch(gallerySource, /四宫格/);
  assert.doesNotMatch(modelSource, /四宫格/);
  assert.doesNotMatch(source, /每款一次.*请求/);
  assert.doesNotMatch(source, /自动拆分为四张商品图/);
  assert.doesNotMatch(gallerySource, /每款一次.*请求/);
});
