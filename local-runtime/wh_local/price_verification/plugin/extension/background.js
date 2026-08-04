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
    const result = command.command_type === "temu_price_quote_discovery" ? await collectTemuQuotes() : await collectSources(command.payload);
    await postResult(session, command.command_id, "succeeded", result);
  } catch (error) {
    await postResult(session, command.command_id, "failed", { error: String(error && error.message || "read-only collection failed") });
  } finally { clearInterval(keepAlive); }
}

async function collectTemuQuotes() {
  const tab = await activeTabFor("temu.com");
  const evidence = await runPageProbe(tab, () => globalThis.PriceVerificationPageProbe.quoteEvidenceFromPage({ popupConfirmed: false }));
  return PriceVerificationNetworkProbeUtils.sanitizeResult(evidence);
}

async function collectSources(payload) {
  const tasks = Array.isArray(payload && payload.tasks) ? payload.tasks : [];
  const tab = await activeTabFor("1688.com");
  const evidence = await runPageProbe(tab, () => globalThis.PriceVerificationPageProbe.sourceEvidenceFromPage());
  return PriceVerificationNetworkProbeUtils.sanitizeResult({ items: tasks.map((task) => ({ task_key: String(task.task_key || ""), source_quote_keys: task.source_quote_keys, status: "succeeded", candidates: evidence.candidates || [] })) });
}

async function runPageProbe(tab, func) {
  const target = { tabId: tab.id };
  await chrome.scripting.executeScript({ target, files: ["network_probe_utils.js", "page_probe.js"] });
  const [result] = await chrome.scripting.executeScript({ target, func });
  return result && result.result ? result.result : {};
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
function validBridgeBase(value) { const url = new URL(String(value || "")); if (!PriceVerificationNetworkProbeUtils.isLocalBridgeUrl(url.toString())) throw new Error("Bridge URL must be local HTTP loopback"); url.pathname = ""; url.search = ""; url.hash = ""; return url.toString().replace(/\/$/, ""); }
async function bridgeFetch(base, path, body, headers) { if (!BRIDGE_PATHS.has(path)) throw new Error("Unsupported bridge path"); const response = await fetch(`${base}${path}`, { method: "POST", headers: { "Content-Type": "application/json", ...(headers || {}) }, body: JSON.stringify(body) }); if (!response.ok) throw new Error(`Bridge request failed (${response.status})`); return response.json(); }
