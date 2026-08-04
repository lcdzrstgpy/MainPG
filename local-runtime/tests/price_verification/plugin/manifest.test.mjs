import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

const extensionRoot = path.resolve(import.meta.dirname, "../../../wh_local/price_verification/plugin/extension");
const manifest = JSON.parse(
  fs.readFileSync(path.join(extensionRoot, "manifest.json"), "utf8"),
);

test("manifest is limited to sourcing hosts", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.permissions.sort(), [
    "alarms",
    "scripting",
    "storage",
    "tabs",
  ]);
  assert.ok(manifest.host_permissions.some((host) => host.includes("*.temu.com")));
  assert.ok(manifest.host_permissions.some((host) => host.includes("1688.com")));
  assert.deepEqual(manifest.host_permissions.sort(), [
    "http://127.0.0.1/*",
    "http://localhost/*",
    "https://*.1688.com/*",
    "https://*.temu.com/*",
    "https://1688.com/*",
  ]);
  assert.ok(!JSON.stringify(manifest).toLowerCase().includes("dianxiaomi"));
  assert.ok(!JSON.stringify(manifest).toLowerCase().includes("websocket"));
});

test("manifest exposes only the two read-only command capabilities", () => {
  assert.deepEqual(manifest.price_verification.capabilities, [
    "temu_price_quote_discovery",
    "source_browser_image_search",
  ]);
  assert.deepEqual(manifest.price_verification.bridge_paths, [
    "/plugin/connect",
    "/plugin/poll",
    "/plugin/result",
  ]);
});
