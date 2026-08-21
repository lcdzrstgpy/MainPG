import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PodCustomizationPage.tsx", import.meta.url), "utf8");

test("POD page does not render the pending billing authorization banner", () => {
  assert.doesNotMatch(source, /待恢复的 POD 任务/);
  assert.doesNotMatch(source, /个任务需要重新授权/);
  assert.doesNotMatch(source, />继续任务</);
});
