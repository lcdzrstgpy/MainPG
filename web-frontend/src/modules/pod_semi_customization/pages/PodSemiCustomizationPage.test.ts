import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PodSemiCustomizationPage.tsx", import.meta.url), "utf8");

test("a newly created semi-custom batch becomes the polling request target", () => {
  const startBatch = source.match(/const startBatch = async \(\) => \{[\s\S]*?\n  \};/)?.[0];

  assert.ok(startBatch, "startBatch should exist");
  assert.match(
    startBatch,
    /batchRequestRef\.current = created\.id;\s*setActiveBatch\(created\);/,
  );
});

test("semi-custom batches are named after the generated style", () => {
  const startBatch = source.match(/const startBatch = async \(\) => \{[\s\S]*?\n  \};/)?.[0];
  const titleHelper = source.match(/function semiBatchTitle\([\s\S]*?\n\}/)?.[0];

  assert.ok(startBatch, "startBatch should exist");
  assert.ok(titleHelper, "semiBatchTitle should exist");
  // 批次标题必须随创建请求一起提交，且以「整批统一风格」为主。
  assert.match(startBatch, /title: semiBatchTitle\(fields, input\)/);
  assert.match(titleHelper, /fields\.design_theme\.trim\(\)/);
});
