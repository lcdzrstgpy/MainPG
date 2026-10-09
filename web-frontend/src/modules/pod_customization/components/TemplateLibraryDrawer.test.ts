import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./TemplateLibraryDrawer.tsx", import.meta.url), "utf8");

test("system template tab is backed by local snapshots and not server system templates", () => {
  assert.match(source, /systemTemplates\?: PodSystemTemplate\[\]/);
  assert.match(source, /onApplySystemTemplate\?: \(template: PodSystemTemplate\) => void/);
  assert.match(source, /onDeleteSystemTemplate\?: \(templateId: string\) => void/);
  assert.match(source, /activeSystemTemplate/);
  assert.match(source, /activeSystemTemplate\?\.template/);
  assert.match(source, /activeSystemTemplate\.templateId/);
  assert.match(source, /template\.createdAt/);
  assert.match(source, /activeSystemTemplate\.creativePrompt/);
  assert.match(source, /系统模板绑定的图片已不可用/);
  assert.match(source, /onApplySystemTemplate\(activeSystemTemplate\)/);
  assert.match(source, /onDeleteSystemTemplate\?\.\(activeSystemTemplate\.id\)/);
  assert.doesNotMatch(source, /templates\.filter\(\(template\) => template\.source === scope\)/);
});

test("switching saved system templates refreshes the read-only calibration snapshot", () => {
  assert.match(source, /\[activeTemplate\?\.id, activeTemplate\?\.updated_at, activeSystemTemplate\?\.id\]/);
});
