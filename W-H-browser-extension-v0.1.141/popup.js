const DEFAULT_BASE_URL = "http://127.0.0.1:8010";
const FALLBACK_BASE_URLS = ["http://127.0.0.1:8010", "http://localhost:8010"];
// 工作台登录令牌：由前端登录成功后写入 127.0.0.1:8010 的 localStorage。
// 插件不持有任何固定口令，连接时从已登录的工作台页面读取当前登录态。
const WORKBENCH_TAB_URLS = ["http://127.0.0.1:8010/*", "http://localhost:8010/*"];
const WORKBENCH_TOKEN_KEY = "wh_demo_token";
const tenantContext = globalThis.WorkbenchTenantContext;

const baseUrlInput = document.getElementById("baseUrl");
const statusEl = document.getElementById("status");
const companyEl = document.getElementById("company");

function normalizeBaseUrl(value) {
  try {
    return tenantContext.normalizeEntryBaseUrl(value || DEFAULT_BASE_URL);
  } catch (_error) {
    return String(value || "").trim().replace(/\/$/, "");
  }
}

function isAllowedWorkbenchUrl(value) {
  try {
    return tenantContext.normalizeEntryBaseUrl(value) === DEFAULT_BASE_URL;
  } catch (_error) {
    return false;
  }
}

function candidateBaseUrls(preferred, allowLoopbackFallback = false) {
  return tenantContext
    .connectionCandidateBaseUrls(
      preferred || DEFAULT_BASE_URL,
      FALLBACK_BASE_URLS,
      { allowLoopbackFallback }
    )
    .filter(isAllowedWorkbenchUrl);
}

function showCompany(companyCode, connected = false) {
  const safeCode = /^[0-9]{3}$/.test(String(companyCode || "")) ? String(companyCode) : "";
  companyEl.textContent = safeCode ? `公司 ${safeCode}${connected ? " · 已连接" : ""}` : "公司未确认";
  companyEl.dataset.connected = connected ? "true" : "false";
}

async function clearPluginSession() {
  await chrome.storage.local.remove(["connectionContext", "sessionId", "sessionToken", "workbenchRuntimeConfig"]);
  showCompany("", false);
}

async function loadSettings() {
  const data = await chrome.storage.local.get([
    "baseUrl",
    "baseUrlMode",
    "connectionContext",
    "sessionId",
    "connectionStatus"
  ]);
  const storedBaseUrl = normalizeBaseUrl(data.baseUrl);
  const lastStatus = data.connectionStatus;

  if (data.connectionContext) {
    try {
      const connection = tenantContext.validateConnectionContext(data.connectionContext);
      baseUrlInput.value = connection.http_base;
      if (lastStatus?.state === "connected") {
        showCompany(connection.company_code, true);
        statusEl.textContent = `公司 ${connection.company_code} 已连接，会话 ${connection.session_id}`;
        return;
      }
      showCompany(connection.company_code, false);
      statusEl.textContent = lastStatus?.state === "unreachable" && lastStatus.detail
        ? `会话 ${connection.session_id} 已保存，但${lastStatus.detail}，插件会自动重试`
        : `已保存公司 ${connection.company_code} 会话 ${connection.session_id}，点击“连接插件”向当前工作台重新确认`;
      return;
    } catch (_error) {
      await clearPluginSession();
    }
  }

  baseUrlInput.value = DEFAULT_BASE_URL;
  if (storedBaseUrl !== DEFAULT_BASE_URL) {
    await chrome.storage.local.set({ baseUrl: DEFAULT_BASE_URL, baseUrlMode: "default" });
  }
  showCompany("", false);
  // 后台已把连接结果写进 storage：会话被拒时不能再显示“已连接”或含糊的“待连接”。
  if (lastStatus?.state === "session_rejected") {
    statusEl.textContent = `已断开：${lastStatus.detail || "工作台拒绝了插件会话，请重新登录后点“连接插件”"}`;
  } else if (data.sessionId) {
    statusEl.textContent = `已保存旧会话 ${data.sessionId}，后台会仅按公司 001 根入口迁移`;
  }
}

async function saveSettings() {
  const baseUrl = normalizeBaseUrl(baseUrlInput.value);
  if (!isAllowedWorkbenchUrl(baseUrl)) {
    statusEl.textContent = "工作台地址仅支持本机 http://127.0.0.1:8010";
    return false;
  }
  const current = await chrome.storage.local.get(["connectionContext"]);
  if (current.connectionContext) {
    try {
      const connection = tenantContext.validateConnectionContext(current.connectionContext);
      if (connection.http_base !== baseUrl) await clearPluginSession();
    } catch (_error) {
      await clearPluginSession();
    }
  }
  await chrome.storage.local.set({ baseUrl, baseUrlMode: "explicit" });
  const entry = tenantContext.canonicalEntryBaseUrl(baseUrl);
  showCompany(entry.companyCode, false);
  statusEl.textContent = `已保存公司 ${entry.companyCode} 入口`;
  return true;
}

// 插件不再携带固定连接码：后端打包版的管理员口令是随机值，写死的口令必然被拒。
// 改为向已登录的工作台页面索取前端登录成功后写入的登录令牌。
async function readWorkbenchApiToken() {
  let tabs = [];
  try {
    tabs = await chrome.tabs.query({ url: WORKBENCH_TAB_URLS });
  } catch (_error) {
    return "";
  }
  for (const tab of tabs) {
    if (typeof tab?.id !== "number") continue;
    try {
      const [result] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (key) => {
          try {
            return window.localStorage.getItem(key) || "";
          } catch (_error) {
            return "";
          }
        },
        args: [WORKBENCH_TOKEN_KEY]
      });
      const token = String(result?.result || "").trim();
      if (token) return token;
    } catch (_error) {
      // 页面可能在查询与注入之间关闭、休眠或跳转到受限地址，换下一个标签页。
    }
  }
  return "";
}

async function connect() {
  const saved = await saveSettings();
  if (!saved) return;
  const preferredBaseUrl = normalizeBaseUrl(baseUrlInput.value);
  const apiToken = await readWorkbenchApiToken();
  if (!apiToken) {
    statusEl.textContent = "连接失败：请先用浏览器打开并登录工作台 http://127.0.0.1:8010，再点“连接插件”";
    return;
  }
  const allowLoopbackFallback = tenantContext.isLoopbackHttpEntryUrl(preferredBaseUrl);
  const candidates = candidateBaseUrls(preferredBaseUrl, allowLoopbackFallback);
  if (!candidates.length) {
    statusEl.textContent = "连接失败：工作台入口不受信任";
    return;
  }
  await clearPluginSession();

  const manifest = chrome.runtime.getManifest();
  let lastError = "";
  for (const baseUrl of candidates) {
    try {
      const entry = tenantContext.canonicalEntryBaseUrl(baseUrl);
      const response = await fetch(tenantContext.buildEntryHttpUrl(baseUrl, "/plugin/connect"), {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "authorization": `Bearer ${apiToken}`
        },
        body: JSON.stringify({
          browser_name: "Edge",
          capabilities: {
            read_page_context: true,
            temu_y2: true,
            dxm_import_assist: true,
            product_capture_to_draft: true,
            product_batch_capture_to_draft: true,
            product_batch_capture_command: true,
            temu_price_quote_discovery: true,
            temu_price_quote_dom_image_fix: true,
            temu_flux_by_spu: true,
            temu_sales_manage_snapshot: true,
            source_browser_image_search: true,
            active_1688_assistant_sidebar: true,
            auto_1688_assistant_sidebar: true,
            temaishuju_background_image_search: true,
            source_detail_sku_validation: true,
            employee_action_validation: true,
            command_capability_model: true,
            runtime_config_poll: true,
            tenant_context_schema: 1,
            company_code: entry.companyCode,
            base_url: baseUrl,
            extension_version: manifest.version
          }
        })
      });
      if (!response.ok) {
        if (response.status === 401 && entry.mode === "tenant_capsule") {
          lastError = `公司 ${entry.companyCode} 的登录状态无效或已过期，请回到该公司工作台重新登录后再连接`;
        } else if (response.status === 401) {
          lastError = "工作台拒绝了当前登录状态，请确认已在 http://127.0.0.1:8010 登录（不是停在登录页），然后重新打开插件再连接";
        } else {
          lastError = `${baseUrl} 返回 ${response.status}`;
        }
        if (!allowLoopbackFallback) break;
        continue;
      }
      const payload = await response.json();
      const trustedTenant = tenantContext.resolveServerTenantContext(payload.tenant_context, baseUrl);
      const connectionContext = tenantContext.createConnectionContext(
        trustedTenant,
        payload.session_id,
        payload.session_token
      );
      await chrome.storage.local.set({
        baseUrl: connectionContext.http_base,
        connectionContext,
        ...(payload.runtime_config && typeof payload.runtime_config === "object"
          ? { workbenchRuntimeConfig: payload.runtime_config }
          : {})
      });
      await chrome.storage.local.remove(["sessionId", "sessionToken"]);
      baseUrlInput.value = connectionContext.http_base;
      showCompany(connectionContext.company_code, true);
      const started = await chrome.runtime.sendMessage({ type: "START_WORKBENCH_SOCKET" });
      if (started?.ok) {
        statusEl.textContent = `公司 ${connectionContext.company_code} 已连接，会话 ${connectionContext.session_id}`;
        return;
      }
      // 后台已用首轮轮询验证这次会话；失败时必须如实回报，不能显示“已连接”。
      await clearPluginSession();
      statusEl.textContent = `连接失败：${started?.statusText || "工作台未接受本次插件会话，请重新登录工作台后再试"}`;
      return;
    } catch (error) {
      lastError = `${baseUrl} ${error?.code || error?.message || error}`;
      if (!allowLoopbackFallback) break;
    }
  }
  await clearPluginSession();
  statusEl.textContent = `连接失败：${lastError || "工作台后端不可用"}`;
}

document.getElementById("save").addEventListener("click", saveSettings);
document.getElementById("connect").addEventListener("click", connect);
// 面板打开期间后台可能把连接结果写进 storage（会话过期、工作台重启等），
// 监听后立即刷新，避免面板停留在过期的“已连接”。
chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local") return;
  if (changes.connectionStatus || changes.connectionContext) void loadSettings();
});
loadSettings();
