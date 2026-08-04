/* global chrome, importScripts */
importScripts("network_probe_utils.js", "page_probe.js");

const BRIDGE_PATHS = new Set(["/plugin/connect", "/plugin/poll", "/plugin/result"]);
const COMMAND_TYPES = new Set(["temu_price_quote_discovery", "source_browser_image_search"]);
const SESSION_KEY = "priceVerificationSession";
const POLL_ALARM = "priceVerificationPoll";
const RUNNING_INTERVAL_MS = 30_000;

chrome.runtime.onInstalled.addListener(() => chrome.alarms.create(POLL_ALARM, { periodInMinutes: 0.5 }));
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === POLL_ALARM) pollAndExecute().catch(() => undefined); });
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message && message.type === "connect") connect(message).then(sendResponse).catch((error) => sendResponse({ ok: false, error: error.message }));
  if (message && message.type === "status") getSession().then((session) => sendResponse({ ok: true, connected: Boolean(session) }));
  return true;
});

async function connect(message) {
  const bridgeBaseUrl = validBridgeBase(message.bridgeBaseUrl);
  const pairingCode = String(message.pairingCode || "").trim();
  if (!pairingCode) throw new Error("Pairing code is required");
  const response = await bridgeFetch(bridgeBaseUrl, "/plugin/connect", {
    browser_name: "Chromium MV3",
    capabilities: { temu_price_quote_discovery: true, source_browser_image_search: true },
    plugin_version: chrome.runtime.getManifest().version,
  }, { Authorization: `Bearer ${pairingCode}` });
  if (!response.session_token) throw new Error("Bridge did not return a session token");
  await chrome.storage.local.set({ [SESSION_KEY]: { bridgeBaseUrl, sessionToken: response.session_token } });
  await pollAndExecute();
  return { ok: true };
}

async function pollAndExecute() {
  const session = await getSession();
  if (!session) return;
  const commands = await bridgeFetch(session.bridgeBaseUrl, "/plugin/poll", { session_token: session.sessionToken, limit: 10 });
  if (!Array.isArray(commands)) return;
  for (const command of commands) if (command && COMMAND_TYPES.has(command.command_type)) await executeCommand(session, command);
}

async function executeCommand(session, command) {
  const running = () => postResult(session, command.command_id, "running", { progress: 0, message: "Read-only browser evidence collection is running" });
  await running();
  const keepAlive = setInterval(() => running().catch(() => undefined), RUNNING_INTERVAL_MS);
  try {
    const result = command.command_type === "temu_price_quote_discovery" ? await collectTemuQuotes(command.payload) : await collectSources(command.payload);
    await postResult(session, command.command_id, "succeeded", result);
  } catch (error) {
    await postResult(session, command.command_id, "failed", { error: String(error && error.message || "read-only collection failed") });
  } finally { clearInterval(keepAlive); }
}

async function collectTemuQuotes(payload) {
  const tab = await activeTabFor("temu.com");
  const waitMs = boundedWait(payload && payload.wait_ms, 3_000);
  const [capture] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, world: "MAIN", func: captureTemuQuoteResponses, args: [waitMs] });
  const captured = capture && Array.isArray(capture.result) ? capture.result : [];
  const evidence = await runPageProbe(tab, () => globalThis.PriceVerificationPageProbe.quoteEvidenceFromPage({ popupConfirmed: false }));
  return PriceVerificationNetworkProbeUtils.sanitizeResult({ ...evidence, records: PriceVerificationNetworkProbeUtils.collectAllowedQuoteRecords(captured) });
}

async function collectSources(payload) {
  const tasks = Array.isArray(payload && payload.tasks) ? payload.tasks : [];
  const tab = await activeTabFor("1688.com");
  const items = [];
  for (const task of tasks) {
    const boundTask = {
      task_key: String(task && task.task_key || ""),
      skc_id: String(task && task.skc_id || ""),
      main_image_url: String(task && task.main_image_url || ""),
      source_quote_keys: Array.isArray(task && task.source_quote_keys) ? task.source_quote_keys : [],
    };
    const evidence = await runPageProbe(tab, (sourceTask) => globalThis.PriceVerificationPageProbe.sourceEvidenceFromPage(sourceTask), [boundTask]);
    items.push({ ...boundTask, status: "succeeded", candidates: evidence.candidates || [], sku_verification: evidence.sku_verification || [] });
  }
  return PriceVerificationNetworkProbeUtils.sanitizeResult({ items });
}

async function runPageProbe(tab, func, args) {
  const target = { tabId: tab.id };
  await chrome.scripting.executeScript({ target, files: ["network_probe_utils.js", "page_probe.js"] });
  const [result] = await chrome.scripting.executeScript({ target, func, args: args || [] });
  return result && result.result ? result.result : {};
}

function boundedWait(value, fallback) { const parsed = Number(value); return Number.isFinite(parsed) ? Math.max(500, Math.min(Math.trunc(parsed), 15_000)) : fallback; }

async function captureTemuQuoteResponses(waitMs) {
  const records = [];
  const parse = (text) => { try { return JSON.parse(text); } catch (_) { return null; } };
  const record = (url, method, status, text) => { const responseJson = parse(text); if (responseJson !== null) records.push({ url: String(url || ""), method: String(method || "GET"), status: Number(status || 0), capturedAt: new Date().toISOString(), responseJson }); };
  const originalFetch = globalThis.fetch;
  const wrappedFetch = async function (...args) { const response = await originalFetch.apply(this, args); response.clone().text().then((text) => record(response.url || args[0], args[1] && args[1].method, response.status, text)).catch(() => undefined); return response; };
  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  function wrappedOpen(method, url, ...rest) { this.__priceVerificationReadOnly = { method, url }; return originalOpen.call(this, method, url, ...rest); }
  function wrappedSend(...args) { this.addEventListener("loadend", () => { const request = this.__priceVerificationReadOnly || {}; record(this.responseURL || request.url, request.method, this.status, this.responseText); }, { once: true }); return originalSend.apply(this, args); }
  globalThis.fetch = wrappedFetch;
  XMLHttpRequest.prototype.open = wrappedOpen;
  XMLHttpRequest.prototype.send = wrappedSend;
  await new Promise((resolve) => setTimeout(resolve, waitMs));
  if (globalThis.fetch === wrappedFetch) globalThis.fetch = originalFetch;
  if (XMLHttpRequest.prototype.open === wrappedOpen) XMLHttpRequest.prototype.open = originalOpen;
  if (XMLHttpRequest.prototype.send === wrappedSend) XMLHttpRequest.prototype.send = originalSend;
  return records;
}

async function activeTabFor(hostSuffix) {
  const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const tab = tabs.find((candidate) => { try { return new URL(candidate.url).hostname.endsWith(hostSuffix); } catch (_) { return false; } });
  if (!tab || !tab.id) throw new Error(`Open a logged-in ${hostSuffix} tab before running this read-only task`);
  return tab;
}

async function postResult(session, commandId, status, result) {
  return bridgeFetch(session.bridgeBaseUrl, "/plugin/result", { session_token: session.sessionToken, command_id: commandId, status, result: PriceVerificationNetworkProbeUtils.sanitizeResult(result) });
}

async function getSession() { return (await chrome.storage.local.get(SESSION_KEY))[SESSION_KEY] || null; }
function validBridgeBase(value) { const url = new URL(String(value || "")); if (!PriceVerificationNetworkProbeUtils.isLocalBridgeUrl(url.toString())) throw new Error("Bridge URL must be local HTTPS loopback"); url.pathname = ""; url.search = ""; url.hash = ""; return url.toString().replace(/\/$/, ""); }
async function bridgeFetch(base, path, body, headers) { if (!BRIDGE_PATHS.has(path)) throw new Error("Unsupported bridge path"); const response = await fetch(`${base}${path}`, { method: "POST", headers: { "Content-Type": "application/json", ...(headers || {}) }, body: JSON.stringify(body) }); if (!response.ok) throw new Error(`Bridge request failed (${response.status})`); return response.json(); }
