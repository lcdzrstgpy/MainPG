import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./background.js", import.meta.url), "utf8");

test("advertises and dispatches both shop-adjacent command types", () => {
  assert.match(source, /temu_link_capture:\s*true/);
  assert.match(source, /temu_flux_accel:\s*true/);
  assert.match(source, /command\.command_type === "temu_link_capture"/);
  assert.match(source, /command\.command_type === "temu_flux_accel"/);
  assert.match(source, /runFluxApiBySpuCommand\(baseUrl, sessionToken, command\)/);
});

test("poll accepts both the legacy command list and the envelope response", () => {
  assert.match(source, /Array\.isArray\(payload\)/);
  assert.match(source, /payload\.commands/);
  assert.match(source, /payload\.tenant_context !== undefined/);
});
