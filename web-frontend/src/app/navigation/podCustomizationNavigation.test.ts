import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const modulesSource = readFileSync(new URL("./modules.ts", import.meta.url), "utf8");
const shellSource = readFileSync(new URL("../layout/WorkspaceShell.tsx", import.meta.url), "utf8");

test("workspace exposes only the new POD customization page", () => {
  assert.match(modulesSource, /"pod_customization"/);
  assert.match(modulesSource, /label: "POD定制"/);
  assert.match(shellSource, /PodCustomizationPage/);
  assert.match(shellSource, /case "pod_customization":/);
  assert.doesNotMatch(shellSource, /case "ai_service":/);
});
