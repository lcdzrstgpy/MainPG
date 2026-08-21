import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { readFile } from "node:fs/promises";
import test from "node:test";

const require = createRequire(import.meta.url);
const { dispatchPolledCommands, normalizePolledCommands } = require("./plugin_command_contract.js");

const source = await readFile(new URL("./background.js", import.meta.url), "utf8");

test("advertises and dispatches both shop-adjacent command types", () => {
  assert.match(source, /temu_link_capture:\s*true/);
  assert.match(source, /temu_flux_accel:\s*true/);
  assert.match(source, /command\.command_type === "temu_link_capture"/);
  assert.match(source, /command\.command_type === "temu_flux_accel"/);
  assert.match(source, /runFluxApiBySpuCommand\(baseUrl, sessionToken, command\)/);
});

test("poll accepts both the legacy command list and the envelope response", () => {
  assert.match(source, /dispatchPolledCommands\(payload/);
  assert.match(source, /payload\.tenant_context !== undefined/);
});

test("poll normalizes command_id before execute and result", async () => {
  const events = ["poll"];
  const payload = { commands: [{ command_id: "command-42", command_type: "temu_link_capture" }] };

  await dispatchPolledCommands(payload, async (command) => {
    events.push(`execute:${command.id}`);
    events.push(`result:${command.id}`);
  });

  assert.deepEqual(events, ["poll", "execute:command-42", "result:command-42"]);
  assert.deepEqual(normalizePolledCommands([{ id: "legacy", command_id: "new" }]), [
    { id: "legacy", command_id: "new" },
  ]);
});
