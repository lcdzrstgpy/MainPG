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
