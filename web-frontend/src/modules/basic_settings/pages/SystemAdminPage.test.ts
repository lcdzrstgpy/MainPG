import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const pageSource = readFileSync(new URL("./SystemAdminPage.tsx", import.meta.url), "utf8");
const apiSource = readFileSync(new URL("../api/systemAdminApi.ts", import.meta.url), "utf8");

test("system admin exposes independently versioned POD pricing controls", () => {
  assert.match(apiSource, /\/api\/admin\/billing\/pricing\/pod/);
  assert.match(pageSource, /POD AI 调用单价/);
  assert.match(pageSource, /pod\.title/);
  assert.match(pageSource, /pod\.image/);
  assert.match(pageSource, /updatePodPricingItems/);
});
