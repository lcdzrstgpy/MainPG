const DEFAULT_BASE_URL = "http://127.0.0.1:8010";
const FALLBACK_BASE_URLS = ["http://127.0.0.1:8010", "http://localhost:8010"];
const tenantContext = globalThis.WorkbenchTenantContext;

const baseUrlInput = document.getElementById("baseUrl");
const tokenInput = document.getElementById("token");
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
  await chrome.storage.local.remove("apiToken");
  showCompany("", false);
}

async function loadSettings() {
  const data = await chrome.storage.local.get([
    "baseUrl",
    "baseUrlMode",
    "connectionContext",
    "sessionId"
  ]);
  const storedBaseUrl = normalizeBaseUrl(data.baseUrl);
  tokenInput.value = "";
  await chrome.storage.local.remove("apiToken");

  if (data.connectionContext) {
    try {
      const connection = tenantContext.validateConnectionContext(data.connectionContext);
      baseUrlInput.value = connection.http_base;
      showCompany(connection.company_code, true);
      statusEl.textContent = `已保存公司 ${connection.company_code} 会话 ${connection.session_id}，点击“连接插件”向当前工作台重新确认`;
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
  if (data.sessionId) {
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
  await chrome.storage.local.remove("apiToken");
  const entry = tenantContext.canonicalEntryBaseUrl(baseUrl);
  showCompany(entry.companyCode, false);
  statusEl.textContent = `已保存公司 ${entry.companyCode} 入口`;
  return true;
}

async function connect() {
  const saved = await saveSettings();
  if (!saved) return;
  const apiToken = tokenInput.value.trim();
  if (!apiToken) {
    statusEl.textContent = "请填写插件连接码";
    return;
  }
  const preferredBaseUrl = normalizeBaseUrl(baseUrlInput.value);
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
          lastError = `公司 ${entry.companyCode} 的连接码无效或已过期，请回到该公司工作台重新获取连接码`;
        } else if (response.status === 401 && !tenantContext.isLoopbackHttpEntryUrl(baseUrl)) {
          lastError = "公司 001 根入口拒绝了连接码；如果这是其他公司的连接码，请先打开该公司的 /t/三位公司编号 页面，再重新打开插件";
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
      await chrome.storage.local.remove("apiToken");
      baseUrlInput.value = connectionContext.http_base;
      tokenInput.value = "";
      showCompany(connectionContext.company_code, true);
      const started = await chrome.runtime.sendMessage({ type: "START_WORKBENCH_SOCKET" });
      statusEl.textContent = started?.ok
        ? `公司 ${connectionContext.company_code} 已连接，会话 ${connectionContext.session_id}`
        : "租户会话已原子保存，后台连接待重试";
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
loadSettings();
