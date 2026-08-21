import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const root = new URL("./", import.meta.url);

test("background restores page controls on already-open 1688 tabs without duplicate live injection", async () => {
  const background = await readFile(new URL("background.js", root), "utf8");

  assert.match(background, /async function ensure1688PageCaptureControls\(tab\)/);
  assert.match(background, /createSingleflight\(\)/);
  assert.match(background, /pageControlRepairSingleflight\.run\(tab\.id,/);
  assert.match(
    background,
    /chrome\.tabs\.sendMessage\(tab\.id, \{ type: "READ_PAGE_CONTEXT" \}\)/,
    "a live content script must be detected before attempting reinjection"
  );
  assert.match(
    background,
    /delete window\.__temuWorkbenchConnector/,
    "a dead content-script marker must not block reinjection"
  );
  for (const controlId of [
    "temu-workbench-connector-badge",
    "temu-workbench-product-capture",
    "temu-workbench-product-list-capture",
    "temu-workbench-product-list-cancel",
    "temu-workbench-page-capture-status"
  ]) {
    assert.match(background, new RegExp(controlId));
  }
  assert.match(
    background,
    /files: \["tenant_context\.js", "content\.js"\]/,
    "late recovery must inject only the isolated-world page controls"
  );
  assert.doesNotMatch(
    background.slice(
      background.indexOf("async function ensure1688PageCaptureControls"),
      background.indexOf("async function startConnection")
    ),
    /network_probe_utils\.js|page_probe\.js/,
    "document_start MAIN-world probes must never be late-injected"
  );
});

test("install, startup, and successful workbench connection all repair open 1688 pages", async () => {
  const background = await readFile(new URL("background.js", root), "utf8");
  const installed = background.slice(
    background.indexOf("chrome.runtime.onInstalled.addListener"),
    background.indexOf("chrome.runtime.onStartup.addListener")
  );
  const startup = background.slice(
    background.indexOf("chrome.runtime.onStartup.addListener"),
    background.indexOf("chrome.alarms.onAlarm.addListener")
  );
  const connection = background.slice(
    background.indexOf("async function startConnection"),
    background.indexOf("async function restoreConnection")
  );

  assert.match(installed, /ensureOpen1688PageCaptureControls/);
  assert.match(startup, /ensureOpen1688PageCaptureControls/);
  assert.match(connection, /ensureOpen1688PageCaptureControls/);
  assert.match(
    background,
    /\nrestoreConnection\(\);\nvoid ensureOpen1688PageCaptureControls\(\);\n\nasync function startConnection/,
    "manual unpacked-extension reload must repair already-open pages when the service worker boots"
  );
});

test("1688 page button remains at the existing lower-left position with an explicit OneBound label", async () => {
  const content = await readFile(new URL("content.js", root), "utf8");
  const renderStart = content.indexOf("function renderProductListCaptureButton");
  const renderEnd = content.indexOf("function renderProductListCancelButton", renderStart);
  const renderBranch = content.slice(renderStart, renderEnd);

  assert.match(renderBranch, /"left:14px"/);
  assert.match(renderBranch, /"bottom:96px"/);
  assert.match(renderBranch, /"整页采集（万邦）"/);
  assert.match(content, /connected \? \(is1688ProductListPage\(\) \? "整页采集（万邦）"/);
});

test("extension release version advances for the page-control recovery fix", async () => {
  const manifest = JSON.parse(await readFile(new URL("manifest.json", root), "utf8"));
  assert.equal(manifest.version, "0.1.124");
});
