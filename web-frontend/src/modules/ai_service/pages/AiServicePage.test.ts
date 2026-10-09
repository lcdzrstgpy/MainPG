import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("./AiServicePage.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/aiServiceApi.ts", import.meta.url), "utf8");
const types = readFileSync(new URL("../types/index.ts", import.meta.url), "utf8");

test("legacy AI service no longer contains a second POD mode or API client", () => {
  assert.doesNotMatch(page, /mode\s*===\s*["']pod["']/);
  assert.doesNotMatch(page, /PodJob|POD 出图|POD 供应商/);
  assert.doesNotMatch(api, /pod-creations|latestPodCreation|retryPodGroup/);
  assert.doesNotMatch(types, /AiPod|["']pod["']/);
});
