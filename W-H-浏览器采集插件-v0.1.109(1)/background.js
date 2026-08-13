importScripts("tenant_context.js");

const DEFAULT_BASE_URL = "http://127.0.0.1:8010";
const tenantContext = self.WorkbenchTenantContext;
const BUSINESS_HOST_RE = /(^|\.)((temu)|(dianxiaomi))\.com$/i;
const PRODUCT_CAPTURE_HOST_RE = /(^|\.)(temu|1688|alibaba|pinduoduo|yangkeduo|amazon)\.com$/i;
const CAPTURE_COMMANDS = new Set(["capture_temu_goods", "capture_temu_flux", "capture_temu_orders"]);
const FLUX_PAGE_URL = "https://agentseller-us.temu.com/main/flux-analysis";
const GOODS_PAGE_URL = "https://agentseller.temu.com/goods/list";
const ORDERS_PAGE_URL = "https://agentseller-us.temu.com/mmsos/orders.html";
const SALES_MANAGE_PAGE_URL = "https://agentseller.temu.com/stock/fully-mgt/sale-manage/main";
const SOURCE_IMAGE_SEARCH_PAGE_URL = "https://s.1688.com/youyuan/index.htm?tab=imageSearch";
const TEMAISHUJU_IMAGE_SEARCH_PAGE_URL = "https://www.temaishuju.com/plugin/search-image";
const POLL_ALARM_NAME = "workbench-command-poll";
const POLL_FALLBACK_DELAY_MS = 12000;
const POLL_FALLBACK_MAX_DELAY_MS = 60000;
const POLL_REQUEST_TIMEOUT_MS = 8000;
const PRODUCT_BATCH_DETAIL_CAPTURE_LIMIT = 40;
const PRODUCT_BATCH_DETAIL_LOAD_DELAY_MS = 900;
const PRODUCT_BATCH_DETAIL_RETRY_COUNT = 3;
const PRODUCT_BATCH_DETAIL_RETRY_DELAY_MS = 1500;
const PRODUCT_BATCH_DETAIL_READY_TIMEOUT_MS = 18000;
const PRODUCT_BATCH_DETAIL_WORKER_COUNT = 2;
const PRODUCT_BATCH_DETAIL_WORKER_MAX_COUNT = 2;
const PRODUCT_BATCH_ACTIVE_JOB_LIMIT = 2;
const PRODUCT_BATCH_DETAIL_WORKER_STAGGER_MS = 3200;
const PRODUCT_BATCH_DETAIL_SAFE_PACE_MIN_MS = 1200;
const PRODUCT_BATCH_DETAIL_SAFE_PACE_JITTER_MS = 1800;
const PRODUCT_BATCH_MANUAL_CHALLENGE_TIMEOUT_MS = 120000;
const PRODUCT_BATCH_LIST_SCAN_LIMIT = 200;
const PRODUCT_BATCH_LIST_SCROLL_MAX_PASSES = 12;
const PRODUCT_BATCH_LIST_SCROLL_WAIT_MS = 650;
const SOURCE_IMAGE_SEARCH_TASK_LIMIT = 50;
const PRICE_QUOTE_DOM_ROW_LIMIT = 500;
const SOURCE_IMAGE_SEARCH_CANDIDATE_LIMIT = 20;
const SOURCE_IMAGE_SEARCH_WAIT_MS = 4200;
const SOURCE_IMAGE_SEARCH_UPLOAD_WAIT_MS = 5200;
const SOURCE_IMAGE_SEARCH_IMAGE_MAX_BYTES = 6 * 1024 * 1024;
const SOURCE_ASSISTANT_SIDEBAR_WAIT_MS = 45000;
const SOURCE_ASSISTANT_SIDEBAR_POLL_MS = 1500;
const SOURCE_ASSISTANT_TRIGGER_TIMEOUT_MS = 9000;
const SOURCE_ASSISTANT_TAB_SCRIPT_TIMEOUT_MS = 3500;
const SOURCE_ASSISTANT_COMMAND_TIMEOUT_MS = 70000;
const SOURCE_TEMAISHUJU_SINGLE_TASK_TIMEOUT_MS = 240000;
const SOURCE_BROWSER_COMMAND_TIMEOUT_MS = 900000;
const SOURCE_BROWSER_COMMAND_MAX_TIMEOUT_MS = 3600000;
const SOURCE_TEMAISHUJU_TASK_WORKER_DEFAULT = 5;
const SOURCE_TEMAISHUJU_TASK_WORKER_MAX = 8;
const SOURCE_TEMAISHUJU_TASK_WORKER_STAGGER_MS = 1800;
const SOURCE_CAPTURE_CONTRACT_VERSION = "2026-06-23.1";
const WORKBENCH_RESULT_POST_TIMEOUT_MS = 8000;
const DEFAULT_PLUGIN_COMMAND_TIMEOUT_MS = 20 * 60 * 1000;
const PLUGIN_COMMAND_TIMEOUT_MS = {
  read_page_context: 30000,
  product_capture_current_page: 180000,
  product_batch_capture_current_page: 45 * 60 * 1000,
  capture_temu_goods: 15 * 60 * 1000,
  capture_temu_flux: 15 * 60 * 1000,
  capture_temu_orders: 15 * 60 * 1000,
  old_product_health_check: 30 * 60 * 1000,
  temu_orders_by_sku: 20 * 60 * 1000,
  temu_flux_by_spu: 20 * 60 * 1000,
  temu_sales_manage_snapshot: 10 * 60 * 1000,
  temu_price_quote_discovery: 180000,
  dxm_accessory_lookup: 30000,
  dxm_product_video_backfill: 45 * 60 * 1000,
  temu_batch_delist_prepare: 10 * 60 * 1000,
  temu_batch_delist_execute: 30000
};
const DXM_ACCESSORY_LOOKUP_ENDPOINT = "/api/pddkjCategory/searchByPlatformAndValue.json";
const DXM_ACCESSORY_LOOKUP_PLATFORM = "popTemu";
const DXM_ACCESSORY_LOOKUP_QUERY_LIMIT = 50;
const DXM_ACCESSORY_LOOKUP_NAME_MAX_LENGTH = 80;
const DXM_ACCESSORY_LOOKUP_UNIT_MAX_LENGTH = 32;
const DXM_ACCESSORY_LOOKUP_REQUEST_ID_MAX_LENGTH = 128;
const DXM_ACCESSORY_LOOKUP_CANDIDATE_LIMIT = 20;
const DXM_ACCESSORY_LOOKUP_CONCURRENCY = 5;
const DXM_ACCESSORY_LOOKUP_REQUEST_TIMEOUT_MS = 2000;
const DXM_ACCESSORY_LOOKUP_RESPONSE_MAX_CHARS = 256 * 1024;
const TEMAISHUJU_IMAGE_SEARCH_WAIT_MS = 6500;
const TEMAISHUJU_IMAGE_SEARCH_RESULT_WAIT_MS = 90000;
const TEMAISHUJU_DETAIL_CAPTURE_LIMIT = 6;
const TEMAISHUJU_DETAIL_LOAD_WAIT_MS = 2600;
const TEMAISHUJU_DETAIL_READY_TIMEOUT_MS = 26000;
const TEMAISHUJU_BACKGROUND_WINDOW_WIDTH = 1280;
const TEMAISHUJU_BACKGROUND_WINDOW_HEIGHT = 900;
const DEFAULT_MAIN_WORLD_SCRIPT_TIMEOUT_MS = 60000;
const PRODUCT_BATCH_RISK_CONTROL_RE = /访问被拒绝|親，访问被拒绝|亲，访问被拒绝/i;
const PRODUCT_BATCH_RISK_AUXILIARY_RE = /验证码|安全验证|滑块|访问受限|请求过于频繁|操作频繁|稍后再试|扫码验证/i;
let socket = null;
let pollTimer = null;
let pollInFlight = null;
let pollRequestController = null;
let pollFailureCount = 0;
let activeConnectionContext = null;
const productBatchCaptureJobs = new Map();
let productBatchCaptureJobSequence = 0;
const sourceBrowserPartialItems = new Map();
const sourceBrowserActiveResults = new Map();
const sourceBrowserCancelledCommands = new Set();

function normalizeBaseUrl(value) {
  try {
    return tenantContext.normalizeEntryBaseUrl(value || DEFAULT_BASE_URL);
  } catch (_error) {
    return String(value || DEFAULT_BASE_URL).trim().replace(/\/$/, "");
  }
}

function isAllowedWorkbenchUrl(value) {
  try {
    return tenantContext.normalizeEntryBaseUrl(value) === DEFAULT_BASE_URL;
  } catch (_error) {
    return false;
  }
}

function redactSensitiveForLog(value) {
  if (value instanceof Error) {
    return {
      name: value.name,
      message: redactSensitiveForLog(value.message)
    };
  }
  if (typeof value !== "string") return value;
  return value
    .replace(/(token=)[^&\s)]+/gi, "$1[REDACTED]")
    .replace(/(session[_-]?token["']?\s*[:=]\s*["']?)[^"',&\s}]+/gi, "$1[REDACTED]")
    .replace(/(api[_-]?token["']?\s*[:=]\s*["']?)[^"',&\s}]+/gi, "$1[REDACTED]")
    .replace(/(authorization["']?\s*[:=]\s*["']?\s*Bearer\s+)[^"',&\s}]+/gi, "$1[REDACTED]")
    .replace(/(Bearer\s+)[A-Za-z0-9._~+/-]+=*/gi, "$1[REDACTED]");
}

function warnWorkbench(message, ...details) {
  console.warn(message, ...details.map(redactSensitiveForLog));
}

function disconnectActiveConnection() {
  activeConnectionContext = null;
  if (socket) {
    try {
      socket.close();
    } catch (_error) {
      // The browser may already have closed the socket.
    }
    socket = null;
  }
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  if (pollRequestController) {
    try {
      pollRequestController.abort();
    } catch (_error) {
      // The request may already have completed.
    }
    pollRequestController = null;
  }
  pollInFlight = null;
  pollFailureCount = 0;
}

async function clearConnectionState() {
  disconnectActiveConnection();
  await chrome.storage.local.remove([
    "connectionContext",
    "sessionId",
    "sessionToken",
    "apiToken",
    "workbenchRuntimeConfig"
  ]);
}

async function readConnectionContext(options = {}) {
  const allowLegacyMigration = options.allowLegacyMigration !== false;
  const settings = await chrome.storage.local.get([
    "connectionContext",
    "baseUrl",
    "baseUrlMode",
    "sessionId",
    "sessionToken"
  ]);
  if (settings.connectionContext) {
    try {
      const connection = tenantContext.validateConnectionContext(settings.connectionContext);
      activeConnectionContext = connection;
      return connection;
    } catch (_error) {
      await clearConnectionState();
      return null;
    }
  }
  if (!allowLegacyMigration || !settings.sessionId || !settings.sessionToken) return null;

  if (
    normalizeBaseUrl(settings.baseUrl) !== DEFAULT_BASE_URL
  ) {
    await clearConnectionState();
    await chrome.storage.local.set({ baseUrl: DEFAULT_BASE_URL, baseUrlMode: "default" });
    return null;
  }

  const migrated = tenantContext.migrateLegacyConnectionSettings({
    ...settings,
    baseUrl: settings.baseUrl || DEFAULT_BASE_URL
  });
  if (!migrated) {
    await clearConnectionState();
    return null;
  }
  await chrome.storage.local.set({
    baseUrl: migrated.http_base,
    connectionContext: migrated
  });
  await chrome.storage.local.remove(["sessionId", "sessionToken"]);
  activeConnectionContext = migrated;
  return migrated;
}

function trustedConnectionForBase(baseUrl) {
  const connection = tenantContext.validateConnectionContext(activeConnectionContext);
  if (connection.http_base !== normalizeBaseUrl(baseUrl)) {
    throw new tenantContext.TenantContextError(
      "connection_context_mismatch",
      "Request base does not match the active tenant connection."
    );
  }
  return connection;
}

function publicTenantContext(connectionContext) {
  const connection = tenantContext.validateConnectionContext(connectionContext);
  return Object.freeze({
    schema_version: connection.schema_version,
    mode: connection.mode,
    tenant_id: connection.tenant_id,
    company_code: connection.company_code,
    base_path: connection.base_path,
    http_base: connection.http_base,
    ws_base: connection.ws_base
  });
}

function connectionMatchesActive(connectionContext) {
  try {
    const expected = tenantContext.validateConnectionContext(connectionContext);
    const active = tenantContext.validateConnectionContext(activeConnectionContext);
    return expected.session_id === active.session_id
      && expected.session_token === active.session_token
      && expected.tenant_id === active.tenant_id
      && expected.company_code === active.company_code
      && expected.base_path === active.base_path
      && expected.http_base === active.http_base
      && expected.ws_base === active.ws_base;
  } catch (_error) {
    return false;
  }
}

function workbenchHttpUrl(baseUrl, endpoint) {
  return tenantContext.buildHttpUrl(trustedConnectionForBase(baseUrl), endpoint);
}

function cleanCapturedProductTitleForDraft(value) {
  let title = String(value || "").replace(/\s+/g, " ").trim();
  if (!title) return "";
  title = title
    .replace(/\{[^{}]*region[^{}]*\}/gi, " ")
    .replace(/\bhome\s+kitchen\s*[-–—]\s*(?:Canada|United States|United Kingdom|Australia|Germany|France|Spain|Italy|Japan|Korea|Mexico)\b/gi, " ")
    .replace(/\s*[-–—]\s*(?:Canada|United States|United Kingdom|Australia|Germany|France|Spain|Italy|Japan|Korea|Mexico)\s*$/gi, "")
    .replace(/\b(?:brand|品牌)\s*[:：]\s*[A-Za-z0-9 _.-]{2,48}\b/gi, " ")
    .replace(/\b(?:CA\$|US\$|\$)\s*\d+(?:\.\d+)?(?:\s*\d+)?/gi, " ")
    .replace(/(?:已售|Sold)\s*[\d,.万kK]+(?:\s*件|pcs|piece|pieces)?/gi, " ")
    .replace(/(?:仅剩|Only\s*\d+\s*left|剩余)\s*[\d,.万kK]+(?:\s*件|pcs|piece|pieces)?/gi, " ")
    .replace(/跨境\s*(?:亚马逊|amazon|temu|tiktok|tik\s*tok|shein|ebay|eBay|速卖通|aliexpress|wish)?/gi, "")
    .replace(/\b(?:Amazon|TEMU|TikTok|Tik\s*Tok|SHEIN|eBay|AliExpress|Wish)\b/gi, "")
    .replace(/(?:亚马逊|特姆|拼多多|抖音|速卖通|希音|虾皮|Shopee|Lazada)/gi, "")
    .replace(/(?:厂家直销|工厂直销|源头厂家|源头工厂|源头直供|源头供应|厂家批发|工厂批发|厂家供应|实力厂家|实力工厂|超级工厂|一件代发|支持代发|跨境专供|外贸专供|外贸爆款|电商爆款)/gi, "")
    .replace(/(?:现货批发|批发定制|来图定制|支持定制|支持ODM|支持OEM|免费贴标|免费拿样|一件起批|混批)/gi, "")
    .replace(/[【】\[\]（）()]/g, " ")
    .replace(/\s+/g, " ")
    .replace(/^[,，、;；:：\s-]+|[,，、;；:：\s-]+$/g, "")
    .trim();
  return title || String(value || "").replace(/\s+/g, " ").trim();
}

function cleanCapturedPageTitleFallback(value) {
  let title = String(value || "").replace(/\s+/g, " ").trim();
  if (!title) return "";
  title = title
    .replace(/\s*[-_]\s*(?:1688|阿里巴巴|Alibaba).*$/i, "")
    .replace(/\s*[-_].*(?:1688|阿里巴巴|Alibaba).*$/i, "")
    .replace(/^\s*商品标题[:：]\s*/, "")
    .trim();
  return cleanCapturedProductTitleForDraft(title);
}

function capturedProductTitleIsUsable(value) {
  const title = String(value || "").replace(/\s+/g, " ").trim();
  if (title.length < 4) return false;
  if (/^(?:1688(?:\.com)?|阿里巴巴|Alibaba|Alibaba\.com|商品详情|商品详情页|详情页)$/i.test(title)) return false;
  if (/^(?:https?:\/\/)?(?:www\.)?(?:1688|alibaba)\.com(?:[/?#].*)?$/i.test(title)) return false;
  if (/搜索|找本店|首页|购物车|我的订单|客服|官方服务|下载插件|采购车|消息/.test(title) && title.length <= 80) return false;
  if (/有限公司|有限责任公司|经营部|商行|旺铺|诚信通|供应商/.test(title) && title.length <= 60) return false;
  if (/^[\u4e00-\u9fa5]{2,8}$/.test(title)) return false;
  return true;
}

function captureTitleLooksLikePackagingProduct(value) {
  return /pack(?:ag|ing)\s+bag|shipping\s+bag|mail(?:er|ing)\s+bag|poly\s+mailer|courier\s+bag|opp\s+bag|ziplock\s+bag|self\s*seal\s+bag|快递袋|包装袋|打包袋|邮寄袋|物流袋|封口袋|自封袋|opp袋/i.test(String(value || ""));
}

function capturedImageQualityFlags(image, title) {
  const flags = [];
  const nearby = String(image?.nearbyText || "");
  const source = String(image?.source || "");
  const url = String(image?.url || "");
  const evidence = `${nearby} ${source} ${url}`;
  if (/logo|avatar|sprite|icon|service|guarantee|refund|return|客服|保障|退货|运费险/i.test(evidence)) {
    flags.push("page_service_or_icon_image");
  }
  if (/packaging\s+only|packing\s+only|poly\s+mailer|courier\s+bag|仅包装|只有包装|仅展示包装|只展示包装|包装袋图/i.test(evidence) && !captureTitleLooksLikePackagingProduct(title)) {
    flags.push("packaging_only_not_product");
  }
  return Array.from(new Set(flags));
}

function isExcludedProductCaptureUrl(value) {
  try {
    const parsed = new URL(String(value || ""));
    const host = parsed.hostname.toLowerCase();
    const href = parsed.href.toLowerCase();
    let decodedHref = href;
    try {
      decodedHref = decodeURIComponent(href);
    } catch (_error) {
      decodedHref = href;
    }
    if (/(^|\.)air\.1688\.com$/i.test(host)) return true;
    if (/(^|\.)(amos|im|chat|wangwang)\.(1688|alibaba)\.com$/i.test(host)) return true;
    return /ocms-fusion|web_im|aliim|wangwang|旺旺|聊天|客服|messenger|instant-message/i.test(decodedHref);
  } catch (_error) {
    return false;
  }
}

function productIdFromCaptureUrl(value) {
  const raw = String(value || "");
  const patterns = [
    /\/(?:dp|gp\/product|product)\/([A-Z0-9]{10})(?:[/?#]|$)/i,
    /[?&](?:asin|ASIN)=([A-Z0-9]{10})(?:&|$)/,
    /\/offer\/(\d+)\.html/i,
    /-g-(\d+)(?:\.html|[/?#]|$)/i,
    /[?&](?:offerId|offerid|offer_id|productId|productid|product_id|goods_id|goodsId|item_id|itemId|spu_id|spuId)=(\d+)/i,
    /\/(\d{8,})(?:\.html|[/?#]|$)/i
  ];
  for (const pattern of patterns) {
    const match = raw.match(pattern);
    if (match) return match[1];
  }
  return "";
}

function canonicalProductCaptureUrl(value, fallbackProductId = "") {
  const raw = String(value || "").trim();
  const productId = String(fallbackProductId || productIdFromCaptureUrl(raw) || "").trim();
  if (!raw && productId && /^\d{8,}$/.test(productId)) {
    return `https://detail.1688.com/offer/${productId}.html`;
  }
  if (!raw) return "";
  try {
    const parsed = new URL(raw);
    const host = parsed.hostname.toLowerCase();
    if (/amazon\.com$/.test(host) && /^[A-Z0-9]{10}$/i.test(productId)) {
      return `https://www.amazon.com/dp/${productId.toUpperCase()}`;
    }
    if (/1688\.com$/.test(host) && productId) {
      return `https://detail.1688.com/offer/${productId}.html`;
    }
    if (/alibaba\.com$/.test(host)) {
      parsed.search = "";
      parsed.hash = "";
      return parsed.href;
    }
    if (/temu\.com$/.test(host)) {
      parsed.hash = "";
      const keepKeys = new Set([
        "goods_id",
        "goodsId",
        "product_id",
        "productId",
        "item_id",
        "itemId",
        "spu_id",
        "spuId",
        "offerId",
        "offer_id"
      ]);
      for (const key of Array.from(parsed.searchParams.keys())) {
        if (!keepKeys.has(key)) parsed.searchParams.delete(key);
      }
      return parsed.href;
    }
  } catch (_error) {
    // Fall through and use the raw value for non-standard but still useful URLs.
  }
  return raw;
}

function productCaptureIdsMatch(expectedId, actualId) {
  const expected = String(expectedId || "").trim();
  const actual = String(actualId || "").trim();
  return !expected
    || !actual
    || expected === actual
    || (/^[A-Z0-9]{10}$/i.test(expected) && expected.toUpperCase() === actual.toUpperCase());
}

function normalizeBatchListProduct(product) {
  const source = product && typeof product === "object" ? product : {};
  const productId = String(source.product_id || productIdFromCaptureUrl(source.product_link || source.link || source.source_ref) || "").trim();
  const canonicalLink = canonicalProductCaptureUrl(
    source.product_link || source.link || source.url || source.source_ref || "",
    productId
  );
  return {
    ...source,
    product_id: productId || String(source.product_id || "").trim(),
    product_link: canonicalLink || String(source.product_link || source.link || "").trim(),
    link: canonicalLink || String(source.link || source.product_link || "").trim(),
    source_ref: canonicalLink || String(source.source_ref || productId || "").trim()
  };
}

function productCaptureSourceRef(product) {
  const normalized = normalizeBatchListProduct(product);
  return String(normalized.source_ref || normalized.product_link || normalized.product_id || "").trim();
}

function resolveProductBatchDetailWorkerCount(value, candidateCount) {
  const requested = Number(value || PRODUCT_BATCH_DETAIL_WORKER_COUNT);
  const safeRequested = Number.isFinite(requested) && requested > 0 ? requested : PRODUCT_BATCH_DETAIL_WORKER_COUNT;
  const clamped = Math.max(1, Math.min(PRODUCT_BATCH_DETAIL_WORKER_MAX_COUNT, Math.floor(safeRequested)));
  return Math.max(0, Math.min(clamped, Math.max(0, Number(candidateCount || 0))));
}

function activeProductBatchCaptureJobCount() {
  let count = 0;
  for (const job of productBatchCaptureJobs.values()) {
    if (!job.finished && !job.cancelled) count += 1;
  }
  return count;
}

function createProductBatchCaptureJob(sourceTab) {
  if (activeProductBatchCaptureJobCount() >= PRODUCT_BATCH_ACTIVE_JOB_LIMIT) return null;
  const job = {
    id: ++productBatchCaptureJobSequence,
    source_tab_id: sourceTab?.id || null,
    source_url: sourceTab?.url || "",
    started_at: new Date().toISOString(),
    cancelled: false,
    finished: false
  };
  productBatchCaptureJobs.set(job.id, job);
  return job;
}

function finishProductBatchCaptureJob(job) {
  if (!job) return;
  job.finished = true;
  productBatchCaptureJobs.delete(job.id);
}

function isProductBatchCaptureCancelled(job) {
  return Boolean(job?.cancelled);
}

async function cancelProductBatchCapture(sourceTab) {
  const sourceTabId = sourceTab?.id || null;
  const jobs = Array.from(productBatchCaptureJobs.values())
    .filter((job) => !job.finished && !job.cancelled)
    .filter((job) => sourceTabId == null || job.source_tab_id === sourceTabId);
  const candidates = jobs.length ? jobs : Array.from(productBatchCaptureJobs.values()).filter((job) => !job.finished && !job.cancelled);
  if (!candidates.length) {
    return { ok: false, error: "no_active_batch_capture", statusText: "当前没有正在运行的批量采集" };
  }
  for (const job of candidates) {
    job.cancelled = true;
    job.cancelled_at = new Date().toISOString();
  }
  return {
    ok: true,
    cancelled_count: candidates.length,
    statusText: candidates.length > 1 ? `已请求中断 ${candidates.length} 个批量采集` : "已请求中断批量采集"
  };
}

async function createProductBatchCaptureWindow(workerCount) {
  if (workerCount <= 0) return { window: null, tabs: [] };
  const captureWindow = await chrome.windows.create({
    url: "about:blank",
    focused: false,
    type: "popup",
    width: 1120,
    height: 900,
    left: 80,
    top: 60
  });
  const tabs = [];
  const firstTab = captureWindow?.tabs?.[0] || (await chrome.tabs.query({ windowId: captureWindow.id }))[0];
  if (firstTab) tabs.push(firstTab);
  for (let workerIndex = tabs.length; workerIndex < workerCount; workerIndex += 1) {
    tabs.push(await chrome.tabs.create({
      windowId: captureWindow.id,
      url: "about:blank",
      active: false
    }));
  }
  return { window: captureWindow, tabs };
}

async function focusProductBatchCaptureTab(tabId) {
  try {
    const tab = await chrome.tabs.update(tabId, { active: true });
    if (tab?.windowId != null) {
      await chrome.windows.update(tab.windowId, { focused: true });
    }
  } catch (_error) {
    // The tab or its capture window may already be closed.
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  const settings = await chrome.storage.local.get(["baseUrl", "baseUrlMode", "connectionContext"]);
  if (settings.connectionContext) {
    try {
      const connection = tenantContext.validateConnectionContext(settings.connectionContext);
      if (normalizeBaseUrl(connection.http_base) === DEFAULT_BASE_URL) {
        await chrome.storage.local.set({
          baseUrl: connection.http_base,
          baseUrlMode: settings.baseUrlMode || "connected"
        });
        schedulePollAlarm();
        await restoreConnection();
        return;
      }
    } catch (_error) {
      await clearConnectionState();
    }
  }
  await clearConnectionState();
  await chrome.storage.local.set({ baseUrl: DEFAULT_BASE_URL, baseUrlMode: "default" });
  schedulePollAlarm();
  await restoreConnection();
});

chrome.runtime.onStartup.addListener(() => {
  schedulePollAlarm();
  restoreConnection();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === POLL_ALARM_NAME) {
    pollOnceFromStorage();
  }
});

chrome.storage.onChanged.addListener((changes, areaName) => {
  if (areaName !== "local" || !changes.connectionContext) return;
  disconnectActiveConnection();
  const nextValue = changes.connectionContext.newValue;
  if (!nextValue) return;
  try {
    activeConnectionContext = tenantContext.validateConnectionContext(nextValue);
  } catch (_error) {
    void clearConnectionState();
  }
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type === "PING") {
    sendResponse({ ok: true, tabId: sender.tab?.id || null });
    return true;
  }
  if (message?.type === "CAPTURE_PRODUCT_TO_WORKBENCH") {
    captureProductToWorkbench(sender.tab).then(sendResponse);
    return true;
  }
  if (message?.type === "CAPTURE_VISIBLE_PRODUCTS_TO_WORKBENCH") {
    captureVisibleProductsToWorkbench(sender.tab).then(sendResponse);
    return true;
  }
  if (message?.type === "CAPTURE_TEMU_PRICE_QUOTE_PAGE") {
    captureCurrentTemuPriceQuotePage(sender.tab).then(sendResponse);
    return true;
  }
  if (message?.type === "CANCEL_PRODUCT_BATCH_CAPTURE") {
    cancelProductBatchCapture(sender.tab).then(sendResponse);
    return true;
  }
  if (message?.type === "START_WORKBENCH_SOCKET") {
    startConnection().then(sendResponse);
    return true;
  }
  return false;
});

restoreConnection();

async function startConnection() {
  const connection = await readConnectionContext();
  if (!connection) {
    return { ok: false, error: "missing plugin session" };
  }
  if (!isAllowedWorkbenchUrl(connection.http_base)) {
    await clearConnectionState();
    return { ok: false, error: "workbench URL must be W-H HTTPS or localhost HTTP" };
  }
  schedulePollAlarm();
  connectWebSocket(connection);
  startPollFallback(connection);
  await pollOnce(connection);
  return { ok: true };
}

async function restoreConnection() {
  try {
    const connection = await readConnectionContext();
    if (!connection) return;
    if (!isAllowedWorkbenchUrl(connection.http_base)) {
      await clearConnectionState();
      return;
    }
    schedulePollAlarm();
    connectWebSocket(connection);
    startPollFallback(connection);
    await pollOnce(connection);
  } catch (error) {
    warnWorkbench("workbench restore failed", error);
  }
}

function schedulePollAlarm() {
  chrome.alarms.create(POLL_ALARM_NAME, { periodInMinutes: 1 });
}

function currentPluginCapabilities(baseUrl) {
  const manifest = chrome.runtime.getManifest();
  const activeTenant = activeConnectionContext ? publicTenantContext(activeConnectionContext) : null;
  return {
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
    dxm_accessory_lookup: true,
    dxm_product_video_backfill: true,
    dxm_product_video_backfill_identity_fix: true,
    dxm_product_video_backfill_dropdown_fix: true,
    dxm_product_video_backfill_textarea_fix: true,
    dxm_product_video_backfill_confirm_fix: true,
    dxm_product_video_backfill_input_state_fix: true,
    dxm_product_video_backfill_preview_success_fix: true,
    dxm_product_video_backfill_persist_verify_fix: true,
    source_browser_image_search: true,
    active_1688_assistant_sidebar: true,
    auto_1688_assistant_sidebar: true,
    temaishuju_background_image_search: true,
    source_detail_sku_validation: true,
    employee_action_validation: true,
    command_capability_model: true,
    runtime_config_poll: true,
    tenant_context_schema: 1,
    tenant_context: activeTenant,
    base_url: normalizeBaseUrl(baseUrl),
    extension_version: manifest.version
  };
}

function connectWebSocket(connectionContext) {
  try {
    const connection = tenantContext.validateConnectionContext(connectionContext);
    if (!isAllowedWorkbenchUrl(connection.http_base)) return;
    if (socket) {
      socket.close();
    }
    const tenantQuery = `&tenant_context=${encodeURIComponent(JSON.stringify(publicTenantContext(connection)))}`;
    const endpoint = `/plugin/session/${encodeURIComponent(String(connection.session_id))}?token=${encodeURIComponent(connection.session_token)}${tenantQuery}`;
    const currentSocket = new WebSocket(tenantContext.buildWebSocketUrl(connection, endpoint));
    socket = currentSocket;
    currentSocket.onmessage = async (event) => {
      try {
        if (socket !== currentSocket || !connectionMatchesActive(connection)) return;
        const message = JSON.parse(String(event.data || "{}"));
        try {
          tenantContext.assertServerTenantContext(connection, message.tenant_context);
        } catch (tenantError) {
          await clearConnectionState();
          warnWorkbench("workbench websocket tenant context rejected", tenantError);
          return;
        }
        if (message.type === "commands") {
          for (const command of message.commands || []) {
            try {
              await executeCommand(connection.http_base, connection.session_token, command);
            } catch (commandError) {
              warnWorkbench("workbench websocket command failed", command?.id || command?.command_id, commandError);
            }
          }
        }
      } catch (messageError) {
        warnWorkbench("workbench websocket message ignored", messageError);
      }
    };
    currentSocket.onclose = () => {
      if (socket === currentSocket) socket = null;
    };
  } catch (error) {
    warnWorkbench("workbench websocket failed", error);
  }
}

function pollFallbackDelayMs() {
  if (pollFailureCount <= 0) return POLL_FALLBACK_DELAY_MS;
  return Math.min(
    POLL_FALLBACK_DELAY_MS * (2 ** Math.min(pollFailureCount, 3)),
    POLL_FALLBACK_MAX_DELAY_MS
  );
}

function scheduleNextPollFallback(connectionContext) {
  const connection = tenantContext.validateConnectionContext(connectionContext);
  if (!connectionMatchesActive(connection) || !isAllowedWorkbenchUrl(connection.http_base)) return;
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = setTimeout(() => {
    pollTimer = null;
    void pollOnce(connection);
  }, pollFallbackDelayMs());
}

function startPollFallback(connectionContext) {
  const connection = tenantContext.validateConnectionContext(connectionContext);
  if (!connectionMatchesActive(connection) || !isAllowedWorkbenchUrl(connection.http_base)) return;
  pollFailureCount = 0;
  scheduleNextPollFallback(connection);
}

async function pollOnceFromStorage() {
  try {
    const connection = await readConnectionContext();
    if (!connection || !isAllowedWorkbenchUrl(connection.http_base)) return;
    await pollOnce(connection);
  } catch (error) {
    warnWorkbench("workbench alarm poll failed", error);
  }
}

async function performPluginPoll(connection) {
  const controller = new AbortController();
  pollRequestController = controller;
  let timeoutTimer = setTimeout(() => controller.abort(), POLL_REQUEST_TIMEOUT_MS);
  try {
    const response = await fetch(tenantContext.buildHttpUrl(connection, "/plugin/poll"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        session_token: connection.session_token,
        tenant_context: publicTenantContext(connection),
        limit: 10,
        capabilities: currentPluginCapabilities(connection.http_base)
      })
    });
    clearTimeout(timeoutTimer);
    timeoutTimer = null;
    if (pollRequestController === controller) pollRequestController = null;
    if (!response.ok) {
      if ([401, 403, 404].includes(response.status)) await clearConnectionState();
      return false;
    }
    const payload = await response.json();
    if (!connectionMatchesActive(connection)) return false;
    tenantContext.assertServerTenantContext(connection, payload.tenant_context);
    if (payload.runtime_config && typeof payload.runtime_config === "object") {
      await chrome.storage.local.set({ workbenchRuntimeConfig: payload.runtime_config });
    }
    for (const command of payload.commands || []) {
      if (!connectionMatchesActive(connection)) return false;
      await executeCommand(connection.http_base, connection.session_token, command);
    }
    return true;
  } catch (error) {
    if (error instanceof tenantContext.TenantContextError && connectionMatchesActive(connection)) {
      await clearConnectionState();
    }
    if (connectionMatchesActive(connection)) warnWorkbench("workbench poll failed", error);
    return false;
  } finally {
    if (timeoutTimer !== null) clearTimeout(timeoutTimer);
    if (pollRequestController === controller) pollRequestController = null;
  }
}

function pollOnce(connectionContext) {
  let connection;
  try {
    connection = tenantContext.validateConnectionContext(connectionContext);
  } catch (error) {
    if (error instanceof tenantContext.TenantContextError) void clearConnectionState();
    warnWorkbench("workbench poll rejected", error);
    return Promise.resolve(false);
  }
  if (!connectionMatchesActive(connection) || !isAllowedWorkbenchUrl(connection.http_base)) {
    return Promise.resolve(false);
  }
  if (pollInFlight) return pollInFlight;

  const request = performPluginPoll(connection)
    .then((succeeded) => {
      if (connectionMatchesActive(connection)) {
        pollFailureCount = succeeded ? 0 : Math.min(pollFailureCount + 1, 4);
        scheduleNextPollFallback(connection);
      }
      return succeeded;
    })
    .finally(() => {
      if (pollInFlight === request) pollInFlight = null;
    });
  pollInFlight = request;
  return request;
}

async function executeCommand(baseUrl, sessionToken, command) {
  try {
    if (command?.command_type === "source_browser_image_search") {
      sourceBrowserCancelledCommands.delete(command.id);
    }
    const timeoutMs = resolvePluginCommandTimeoutMs(command);
    const result = await withTimeout(
      runCommand(baseUrl, sessionToken, command),
      timeoutMs,
      `${command?.command_type || "plugin"}_command_timeout`
    );
    await postResult(baseUrl, sessionToken, command.id, result?.error ? "failed" : "succeeded", result);
    if (command?.command_type === "source_browser_image_search") {
      sourceBrowserActiveResults.delete(command.id);
      sourceBrowserCancelledCommands.delete(command.id);
    }
  } catch (error) {
    if (command?.command_type === "dxm_accessory_lookup") {
      await postResult(baseUrl, sessionToken, command.id, "failed", dxmAccessoryLookupFatalResult(command, error));
      return;
    }
    if (command?.command_type === "source_browser_image_search" && isSourceSearchTimeoutError(error)) {
      sourceBrowserCancelledCommands.add(command.id);
      await postResult(baseUrl, sessionToken, command.id, "failed", sourceBrowserCommandTimeoutResult(command, error, sourceBrowserActiveResults.get(command.id)));
      sourceBrowserActiveResults.delete(command.id);
      sourceBrowserCancelledCommands.delete(command.id);
      return;
    }
    if (command?.command_type === "source_browser_image_search" && sourceBrowserActiveResults.has(command.id)) {
      sourceBrowserCancelledCommands.add(command.id);
      await postResult(baseUrl, sessionToken, command.id, "failed", sourceBrowserCommandAbortedResult(command, error, sourceBrowserActiveResults.get(command.id)));
      sourceBrowserActiveResults.delete(command.id);
      sourceBrowserCancelledCommands.delete(command.id);
      return;
    }
    await postResult(baseUrl, sessionToken, command.id, "failed", await describeError(error));
    if (command?.command_type === "source_browser_image_search") {
      sourceBrowserActiveResults.delete(command.id);
      sourceBrowserCancelledCommands.delete(command.id);
    }
  }
}

function resolveSourceBrowserCommandTimeoutMs(command) {
  const payload = command?.payload || {};
  const tasks = Array.isArray(payload.tasks) ? payload.tasks.slice(0, SOURCE_IMAGE_SEARCH_TASK_LIMIT) : [];
  const detailSkuValidation = payload.capture_strategy === "source_detail_sku_validation";
  const temaishujuBackground = !detailSkuValidation && (payload.capture_strategy === "temaishuju_background_image_search" || payload.provider === "temaishuju");
  if (!tasks.length || (!temaishujuBackground && !detailSkuValidation)) {
    return SOURCE_BROWSER_COMMAND_TIMEOUT_MS;
  }
  const workerCount = resolveSourceTaskWorkerCount(payload, tasks.length, true);
  const waves = Math.max(1, Math.ceil(tasks.length / Math.max(1, workerCount)));
  const waveBudget = SOURCE_TEMAISHUJU_SINGLE_TASK_TIMEOUT_MS + 30000;
  const startupBudget = 90000 + (workerCount * SOURCE_TEMAISHUJU_TASK_WORKER_STAGGER_MS);
  return Math.min(
    SOURCE_BROWSER_COMMAND_MAX_TIMEOUT_MS,
    Math.max(SOURCE_BROWSER_COMMAND_TIMEOUT_MS, startupBudget + waves * waveBudget)
  );
}

function resolvePluginCommandTimeoutMs(command) {
  if (command?.command_type === "source_browser_image_search") {
    return resolveSourceBrowserCommandTimeoutMs(command);
  }
  const configured = PLUGIN_COMMAND_TIMEOUT_MS[command?.command_type];
  return Number(configured || DEFAULT_PLUGIN_COMMAND_TIMEOUT_MS);
}

async function runCommand(baseUrl, sessionToken, command) {
  if (command.command_type === "temu_link_capture") {
    return runTemuLinkCapture(command);
  }
  if (command.command_type === "read_page_context") {
    return readActivePageContext();
  }
  if (command.command_type === "product_capture_current_page") {
    return captureCurrentProductPage();
  }
  if (command.command_type === "product_batch_capture_current_page") {
    return runProductBatchCaptureCommand(baseUrl, sessionToken, command);
  }
  if (CAPTURE_COMMANDS.has(command.command_type)) {
    return captureNetworkResponses(command);
  }
  if (command.command_type === "old_product_health_check") {
    return runOldProductHealthCheck(baseUrl, sessionToken, command);
  }
  if (command.command_type === "temu_orders_by_sku") {
    return runIdQueryCommand(baseUrl, sessionToken, command, {
      pageUrl: ORDERS_PAGE_URL,
      captureType: "temu_orders_by_sku",
      label: "订单页 SKU 查询",
      idLabel: "SKU",
      targetPageSize: 500,
      preQueryStatusTab: "\u5168\u90e8",
      requiredTexts: ["订单列表", "商品ID查询", "查询"],
      inputHelp: "请确认当前页面在“订单管理 > 订单列表”，商品 ID 查询条件为 SKU ID。插件会按 SKU 输入查询，并尽量把订单列表每页切到 500，避免批量订单漏页。"
    });
  }
  if (command.command_type === "temu_flux_by_spu") {
    return runIdQueryCommand(baseUrl, sessionToken, command, {
      pageUrl: FLUX_PAGE_URL,
      captureType: "temu_flux_by_spu",
      label: "流量页 SPU 查询",
      idLabel: "SPU",
      targetPageSize: 100,
      requiredTexts: ["商品流量", "商品ID查询", "查询"],
      inputHelp: "请确认当前页面在“经营分析 > 流量分析 > 商品流量”，右上角站点为美国，商品 ID 查询条件为 SPU。插件会先把每页条数切到 100，再切换近7日/近30日并点击查询。"
    });
  }
  if (command.command_type === "temu_sales_manage_snapshot") {
    return runTemuSalesManageSnapshotCommand(baseUrl, sessionToken, command);
  }
  if (command.command_type === "temu_price_quote_discovery") {
    return runTemuPriceQuoteDiscoveryCommand(command);
  }
  if (command.command_type === "source_browser_image_search") {
    return runSourceBrowserImageSearchCommand(baseUrl, sessionToken, command);
  }
  if (command.command_type === "dxm_accessory_lookup") {
    return runDxmAccessoryLookupCommand(command);
  }
  if (command.command_type === "dxm_product_video_backfill") {
    return runDxmProductVideoBackfillCommand(baseUrl, sessionToken, command);
  }
  if (command.command_type === "temu_batch_delist_prepare") {
    return runBatchDelistCommand(command, { execute: false });
  }
  if (command.command_type === "temu_batch_delist_execute") {
    return manualDelistOnlyResult(command);
  }
  return {
    supported: false,
    command_type: command.command_type,
    reason: "未知或未实现的插件指令"
  };
}

function manualDelistOnlyResult(command) {
  return {
    command_type: command.command_type,
    statusText: "当前 V1 是人工确认闭环：插件可以辅助填写到下架确认前，但不会点击最终提交",
    error: "manual_delist_only",
    help: "请使用工作台的“AI 引导到确认前”准备页面；最终“开始下架/提交”必须由员工在浏览器里手动点击。",
    capturedAt: new Date().toISOString()
  };
}

async function runTemuLinkCapture(command) {
  const sourceUrl = String(command?.payload?.source_url || "").trim();
  let parsed;
  try {
    parsed = new URL(sourceUrl);
  } catch (_error) {
    return { command_type: "temu_link_capture", error: "invalid_source_url" };
  }
  if (parsed.protocol !== "https:" || !/(^|\.)temu\.com$/i.test(parsed.hostname)) {
    return { command_type: "temu_link_capture", error: "source_url_must_be_temu_https" };
  }
  const tab = await chrome.tabs.create({ url: parsed.href, active: true });
  await waitForTabReady(tab.id, 30000);
  const readyTab = await chrome.tabs.get(tab.id);
  const captured = await captureProductFromTab(readyTab, { commandType: "temu_link_capture" });
  return {
    command_type: "temu_link_capture",
    source_url: parsed.href,
    capturedAt: new Date().toISOString(),
    ...captured
  };
}

function normalizeDxmAccessoryLookupText(value) {
  if (value == null || (typeof value !== "string" && typeof value !== "number")) return "";
  try {
    return String(value).normalize("NFKC").replace(/\s+/g, " ").trim();
  } catch (_error) {
    return String(value).replace(/\s+/g, " ").trim();
  }
}

function foldDxmAccessoryLookupText(value) {
  return normalizeDxmAccessoryLookupText(value).replace(/\s+/g, "").toLocaleLowerCase("en-US");
}

function foldDxmAccessoryLookupUnit(value) {
  const unit = foldDxmAccessoryLookupText(value);
  const aliases = {
    "1": "件",
    pc: "件",
    pcs: "件",
    piece: "件",
    pieces: "件",
    "2": "双",
    pair: "双",
    pairs: "双",
    "3": "包",
    pack: "包",
    packs: "包"
  };
  return aliases[unit] || unit;
}

function projectDxmAccessoryLookupCandidate(rawCandidate) {
  if (!rawCandidate || typeof rawCandidate !== "object" || Array.isArray(rawCandidate)) return null;
  const rawVid = rawCandidate.vid;
  const vid = typeof rawVid === "number" && Number.isSafeInteger(rawVid)
    ? rawVid
    : (typeof rawVid === "string" && /^\d+$/.test(normalizeDxmAccessoryLookupText(rawVid))
      ? Number(normalizeDxmAccessoryLookupText(rawVid))
      : 0);
  const value = normalizeDxmAccessoryLookupText(rawCandidate.value);
  const unitName = normalizeDxmAccessoryLookupText(rawCandidate.unitName);
  const rawUnitCode = rawCandidate.unitCode;
  let unitCode = null;
  if (typeof rawUnitCode === "number" && Number.isSafeInteger(rawUnitCode) && rawUnitCode > 0) {
    unitCode = rawUnitCode;
  } else if (typeof rawUnitCode === "string") {
    const normalizedUnitCode = normalizeDxmAccessoryLookupText(rawUnitCode);
    if (/^\d+$/.test(normalizedUnitCode) && Number.isSafeInteger(Number(normalizedUnitCode)) && Number(normalizedUnitCode) > 0) {
      unitCode = Number(normalizedUnitCode);
    }
  }
  if (!Number.isSafeInteger(vid) || vid <= 0) return null;
  if (!value || value.length > DXM_ACCESSORY_LOOKUP_NAME_MAX_LENGTH) return null;
  if (unitCode == null || !unitName || unitName.length > DXM_ACCESSORY_LOOKUP_UNIT_MAX_LENGTH) return null;
  return { vid, value, unitCode, unitName };
}

function dxmAccessoryLookupBusinessResult(requestId, query, status, candidates = []) {
  const allowedStatuses = ["resolved", "ambiguous", "not_found", "auth_required", "error"];
  const requestIdText = normalizeDxmAccessoryLookupText(requestId).slice(0, DXM_ACCESSORY_LOOKUP_REQUEST_ID_MAX_LENGTH);
  const queryText = normalizeDxmAccessoryLookupText(query).slice(0, DXM_ACCESSORY_LOOKUP_NAME_MAX_LENGTH);
  const projectedCandidates = [];
  const seen = new Set();
  for (const rawCandidate of Array.isArray(candidates) ? candidates.slice(0, DXM_ACCESSORY_LOOKUP_CANDIDATE_LIMIT) : []) {
    const candidate = projectDxmAccessoryLookupCandidate(rawCandidate);
    if (!candidate) continue;
    const key = JSON.stringify([candidate.vid, candidate.value, candidate.unitCode, candidate.unitName]);
    if (seen.has(key)) continue;
    seen.add(key);
    projectedCandidates.push(candidate);
  }
  return {
    request_id: requestIdText,
    query: queryText,
    status: allowedStatuses.includes(status) ? status : "error",
    candidates: projectedCandidates
  };
}

function normalizeDxmAccessoryLookupQueries(payload = {}) {
  const rawQueries = payload && typeof payload === "object" && !Array.isArray(payload) && Array.isArray(payload.queries)
    ? payload.queries
    : null;
  if (!rawQueries) {
    return { ok: false, error: "dxm_accessory_lookup_queries_required", requests: [], unique_queries: [], rejected: [] };
  }
  if (!rawQueries.length) {
    return { ok: false, error: "dxm_accessory_lookup_queries_empty", requests: [], unique_queries: [], rejected: [] };
  }
  if (rawQueries.length > DXM_ACCESSORY_LOOKUP_QUERY_LIMIT) {
    return { ok: false, error: "dxm_accessory_lookup_query_limit_exceeded", requests: [], unique_queries: [], rejected: [] };
  }

  const requests = [];
  const uniqueQueries = [];
  const rejected = [];
  const uniqueByKey = new Map();
  rawQueries.forEach((rawQuery, sourceIndex) => {
    const item = rawQuery && typeof rawQuery === "object" && !Array.isArray(rawQuery) ? rawQuery : {};
    const requestId = normalizeDxmAccessoryLookupText(item.request_id);
    const query = normalizeDxmAccessoryLookupText(item.name);
    const unit = item.unit == null ? "" : normalizeDxmAccessoryLookupText(item.unit);
    const safeRequestId = requestId.slice(0, DXM_ACCESSORY_LOOKUP_REQUEST_ID_MAX_LENGTH);
    const safeQuery = query.slice(0, DXM_ACCESSORY_LOOKUP_NAME_MAX_LENGTH);
    const invalid = !requestId
      || requestId.length > DXM_ACCESSORY_LOOKUP_REQUEST_ID_MAX_LENGTH
      || !query
      || query.length > DXM_ACCESSORY_LOOKUP_NAME_MAX_LENGTH
      || unit.length > DXM_ACCESSORY_LOOKUP_UNIT_MAX_LENGTH;
    if (invalid) {
      rejected.push({
        source_index: sourceIndex,
        result: dxmAccessoryLookupBusinessResult(safeRequestId, safeQuery, "error", [])
      });
      return;
    }
    const key = `${foldDxmAccessoryLookupText(query)}\u0000${foldDxmAccessoryLookupUnit(unit)}`;
    if (!uniqueByKey.has(key)) {
      uniqueByKey.set(key, uniqueQueries.length);
      uniqueQueries.push({ key, query, unit });
    }
    requests.push({
      source_index: sourceIndex,
      request_id: requestId,
      query,
      unit,
      key
    });
  });
  return { ok: true, error: "", requests, unique_queries: uniqueQueries, rejected };
}

function resolveDxmAccessoryLookupResponse(query, unit, pageResponse = {}) {
  const statusHint = String(pageResponse?.status_hint || "error");
  if (statusHint === "auth_required") return { status: "auth_required", candidates: [] };
  if (statusHint !== "ok") return { status: "error", candidates: [] };

  const candidates = dxmAccessoryLookupBusinessResult("", query, "ambiguous", pageResponse?.candidates || []).candidates;
  if (Number(pageResponse?.invalid_candidate_count || 0) > 0) {
    return { status: "error", candidates };
  }
  if (pageResponse?.truncated === true) {
    return { status: candidates.length ? "ambiguous" : "error", candidates };
  }
  if (!candidates.length) {
    return { status: pageResponse?.had_candidate_items === true ? "error" : "not_found", candidates: [] };
  }

  const foldedQuery = foldDxmAccessoryLookupText(query);
  const exactNameCandidates = candidates.filter((candidate) => foldDxmAccessoryLookupText(candidate.value) === foldedQuery);
  const foldedUnit = foldDxmAccessoryLookupUnit(unit);
  const exactCandidates = foldedUnit
    ? exactNameCandidates.filter((candidate) => (
      foldDxmAccessoryLookupUnit(candidate.unitName) === foldedUnit
      || foldDxmAccessoryLookupUnit(candidate.unitCode) === foldedUnit
    ))
    : exactNameCandidates;
  if (exactCandidates.length === 1) {
    return { status: "resolved", candidates: exactCandidates };
  }
  return { status: "ambiguous", candidates };
}

function buildDxmAccessoryLookupResults(normalized, pageResponses = []) {
  const responseByKey = new Map();
  normalized.unique_queries.forEach((query, index) => {
    responseByKey.set(query.key, pageResponses[index] || { status_hint: "error", candidates: [] });
  });
  const ordered = normalized.rejected.map((item) => ({ ...item }));
  for (const request of normalized.requests) {
    const resolved = resolveDxmAccessoryLookupResponse(request.query, request.unit, responseByKey.get(request.key));
    ordered.push({
      source_index: request.source_index,
      result: dxmAccessoryLookupBusinessResult(request.request_id, request.query, resolved.status, resolved.candidates)
    });
  }
  ordered.sort((left, right) => left.source_index - right.source_index);
  return ordered.map((item) => item.result);
}

function dxmAccessoryLookupFatalResult(command, error) {
  const normalized = normalizeDxmAccessoryLookupQueries(command?.payload || {});
  const errorCode = /timeout/i.test(String(error?.message || error || ""))
    ? "dxm_accessory_lookup_command_timeout"
    : "dxm_accessory_lookup_command_error";
  if (!normalized.ok) {
    return { command_type: "dxm_accessory_lookup", error: normalized.error, results: [] };
  }
  const pageResponses = normalized.unique_queries.map(() => ({ status_hint: "error", candidates: [] }));
  return {
    command_type: "dxm_accessory_lookup",
    error: errorCode,
    results: buildDxmAccessoryLookupResults(normalized, pageResponses)
  };
}

async function runDxmAccessoryLookupCommand(command) {
  const normalized = normalizeDxmAccessoryLookupQueries(command?.payload || {});
  if (!normalized.ok) {
    return { command_type: "dxm_accessory_lookup", error: normalized.error, results: [] };
  }
  if (!normalized.unique_queries.length) {
    return { command_type: "dxm_accessory_lookup", results: buildDxmAccessoryLookupResults(normalized, []) };
  }

  let pageResponses = [];
  try {
    const tab = await findDxmAccessoryLookupTab();
    if (!tab?.id) {
      pageResponses = normalized.unique_queries.map(() => ({ status_hint: "auth_required", candidates: [] }));
    } else {
      await waitForTabReady(tab.id, 1500);
      pageResponses = await fetchDxmAccessoryLookupInPage(tab.id, normalized.unique_queries);
      if (!Array.isArray(pageResponses) || pageResponses.length !== normalized.unique_queries.length) {
        pageResponses = normalized.unique_queries.map(() => ({ status_hint: "error", candidates: [] }));
      }
    }
  } catch (_error) {
    pageResponses = normalized.unique_queries.map(() => ({ status_hint: "error", candidates: [] }));
  }
  return {
    command_type: "dxm_accessory_lookup",
    results: buildDxmAccessoryLookupResults(normalized, pageResponses)
  };
}

function dxmAccessoryLookupTabScore(tab, currentWindowIds) {
  try {
    const parsed = new URL(tab?.url || "");
    if (parsed.protocol !== "https:" || !/(^|\.)dianxiaomi\.com$/i.test(parsed.hostname)) return -1;
    let score = 0;
    if (tab.active) score += 100;
    if (currentWindowIds.has(tab.id)) score += 50;
    if (parsed.hostname.toLowerCase() === "www.dianxiaomi.com") score += 20;
    if (/(^|\/)(login|signin)(\/|$)/i.test(parsed.pathname)) return -1;
    score += 10;
    if (tab.status === "complete") score += 5;
    return score;
  } catch (_error) {
    return -1;
  }
}

async function findDxmAccessoryLookupTab() {
  const currentWindowTabs = await chrome.tabs.query({ currentWindow: true });
  const currentWindowIds = new Set(currentWindowTabs.map((tab) => tab.id).filter(Number.isInteger));
  const allTabs = await chrome.tabs.query({});
  const candidates = allTabs
    .filter((tab) => tab?.id && dxmAccessoryLookupTabScore(tab, currentWindowIds) >= 0)
    .sort((left, right) => dxmAccessoryLookupTabScore(right, currentWindowIds) - dxmAccessoryLookupTabScore(left, currentWindowIds));
  return candidates[0] || null;
}

async function fetchDxmAccessoryLookupInPage(tabId, uniqueQueries) {
  const pageQueries = uniqueQueries.map((item) => ({ query: item.query, unit: item.unit }));
  const waves = Math.max(1, Math.ceil(pageQueries.length / DXM_ACCESSORY_LOOKUP_CONCURRENCY));
  const scriptTimeoutMs = Math.min(
    28000,
    3000 + waves * DXM_ACCESSORY_LOOKUP_REQUEST_TIMEOUT_MS
  );
  return executeMainWorld(tabId, [pageQueries, {
    endpoint: DXM_ACCESSORY_LOOKUP_ENDPOINT,
    platform: DXM_ACCESSORY_LOOKUP_PLATFORM,
    concurrency: DXM_ACCESSORY_LOOKUP_CONCURRENCY,
    request_timeout_ms: DXM_ACCESSORY_LOOKUP_REQUEST_TIMEOUT_MS,
    candidate_limit: DXM_ACCESSORY_LOOKUP_CANDIDATE_LIMIT,
    name_max_length: DXM_ACCESSORY_LOOKUP_NAME_MAX_LENGTH,
    unit_max_length: DXM_ACCESSORY_LOOKUP_UNIT_MAX_LENGTH,
    response_max_chars: DXM_ACCESSORY_LOOKUP_RESPONSE_MAX_CHARS
  }], async (queries, config) => {
    const blankResult = (statusHint) => ({
      status_hint: statusHint,
      candidates: [],
      truncated: false,
      had_candidate_items: false,
      invalid_candidate_count: 0
    });
    const normalizeText = (value) => {
      if (value == null || (typeof value !== "string" && typeof value !== "number")) return "";
      try {
        return String(value).normalize("NFKC").replace(/\s+/g, " ").trim();
      } catch (_error) {
        return String(value).replace(/\s+/g, " ").trim();
      }
    };
    const projectCandidate = (rawCandidate) => {
      if (!rawCandidate || typeof rawCandidate !== "object" || Array.isArray(rawCandidate)) return null;
      const rawVid = rawCandidate.vid;
      const normalizedVid = normalizeText(rawVid);
      const vid = typeof rawVid === "number" && Number.isSafeInteger(rawVid)
        ? rawVid
        : (typeof rawVid === "string" && /^\d+$/.test(normalizedVid) ? Number(normalizedVid) : 0);
      const value = normalizeText(rawCandidate.value);
      const unitName = normalizeText(rawCandidate.unitName);
      const rawUnitCode = rawCandidate.unitCode;
      let unitCode = null;
      if (typeof rawUnitCode === "number" && Number.isSafeInteger(rawUnitCode) && rawUnitCode > 0) {
        unitCode = rawUnitCode;
      } else if (typeof rawUnitCode === "string") {
        const normalizedUnitCode = normalizeText(rawUnitCode);
        if (/^\d+$/.test(normalizedUnitCode) && Number.isSafeInteger(Number(normalizedUnitCode)) && Number(normalizedUnitCode) > 0) {
          unitCode = Number(normalizedUnitCode);
        }
      }
      if (!Number.isSafeInteger(vid) || vid <= 0) return null;
      if (!value || value.length > Number(config.name_max_length || 120)) return null;
      if (unitCode == null || !unitName || unitName.length > Number(config.unit_max_length || 32)) return null;
      return { vid, value, unitCode, unitName };
    };
    const extractCandidateItems = (json) => {
      if (Array.isArray(json)) return json;
      if (!json || typeof json !== "object") return [];
      const containers = [
        json,
        json.data,
        json.result,
        json.rows,
        json.list,
        json.items,
        json.values,
        json.records,
        json.data?.data,
        json.data?.result,
        json.data?.rows,
        json.data?.list,
        json.data?.items,
        json.data?.values,
        json.data?.records,
        json.result?.data,
        json.result?.rows,
        json.result?.list,
        json.result?.items,
        json.result?.values,
        json.result?.records
      ];
      const list = containers.find((item) => Array.isArray(item));
      if (list) return list;
      const singleton = containers.find((item) => item && typeof item === "object" && !Array.isArray(item) && "vid" in item);
      return singleton ? [singleton] : [];
    };
    const authRequiredByJson = (json) => {
      if (!json || typeof json !== "object") return false;
      const codeValues = [json.code, json.errorCode, json.status].map((value) => normalizeText(value));
      if (codeValues.some((value) => ["401", "403", "1001", "1002", "2001", "-401"].includes(value))) return true;
      const message = [json.msg, json.message, json.errorMsg, json.resultMsg]
        .map((value) => normalizeText(value))
        .filter(Boolean)
        .join(" ")
        .slice(0, 300);
      return /未登录|登录(?:已)?过期|请(?:先)?登录|验证失败|login|unauthorized|forbidden/i.test(message);
    };
    const lookupOne = async (item) => {
      if (location.protocol !== "https:" || !/(^|\.)dianxiaomi\.com$/i.test(location.hostname)) {
        return blankResult("error");
      }
      const endpoint = new URL(String(config.endpoint || ""), location.origin);
      if (endpoint.origin !== location.origin || endpoint.pathname !== config.endpoint || endpoint.search || endpoint.hash) {
        return blankResult("error");
      }
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), Math.max(1000, Number(config.request_timeout_ms || 9000)));
      try {
        const form = new URLSearchParams();
        form.set("value", normalizeText(item.query).slice(0, Number(config.name_max_length || 120)));
        form.set("platform", String(config.platform || "popTemu"));
        const response = await fetch(endpoint.pathname, {
          method: "POST",
          credentials: "include",
          headers: { "content-type": "application/x-www-form-urlencoded; charset=UTF-8" },
          body: form.toString(),
          signal: controller.signal
        });
        if (response.status === 401 || response.status === 403 || response.redirected) {
          return blankResult("auth_required");
        }
        if (!response.ok) return blankResult("error");
        const contentType = String(response.headers.get("content-type") || "").toLowerCase();
        const contentLength = Number(response.headers.get("content-length") || 0);
        if (contentLength > Number(config.response_max_chars || 262144)) return blankResult("error");
        const responseText = await response.text();
        if (responseText.length > Number(config.response_max_chars || 262144)) return blankResult("error");
        if (contentType.includes("text/html") || /^\s*</.test(responseText)) {
          return blankResult("auth_required");
        }
        let responseJson = null;
        try {
          responseJson = JSON.parse(responseText);
        } catch (_error) {
          return blankResult("error");
        }
        if (authRequiredByJson(responseJson)) return blankResult("auth_required");
        const candidateItems = extractCandidateItems(responseJson);
        const responseCode = normalizeText(responseJson?.code ?? responseJson?.errorCode ?? responseJson?.status).toLocaleLowerCase("en-US");
        const responseCodeIsSuccess = !responseCode || ["0", "200", "1000000", "ok", "success"].includes(responseCode);
        if (responseJson?.success === false || responseJson?.ok === false || !responseCodeIsSuccess) {
          return blankResult("error");
        }
        const candidateLimit = Math.max(1, Math.min(Number(config.candidate_limit || 20), 20));
        const candidates = [];
        let invalidCandidateCount = 0;
        for (const rawCandidate of candidateItems.slice(0, candidateLimit)) {
          const candidate = projectCandidate(rawCandidate);
          if (candidate) candidates.push(candidate);
          else invalidCandidateCount += 1;
        }
        return {
          status_hint: "ok",
          candidates,
          truncated: candidateItems.length > candidateLimit,
          had_candidate_items: candidateItems.length > 0,
          invalid_candidate_count: invalidCandidateCount
        };
      } catch (_error) {
        return blankResult("error");
      } finally {
        clearTimeout(timer);
      }
    };

    const safeQueries = Array.isArray(queries) ? queries.slice(0, 50) : [];
    const results = new Array(safeQueries.length);
    let cursor = 0;
    const worker = async () => {
      while (cursor < safeQueries.length) {
        const index = cursor;
        cursor += 1;
        results[index] = await lookupOne(safeQueries[index]);
      }
    };
    const workerCount = Math.max(1, Math.min(Number(config.concurrency || 1), 5, safeQueries.length || 1));
    await Promise.all(Array.from({ length: workerCount }, () => worker()));
    return results.map((item) => item || blankResult("error"));
  }, { attempts: 1, timeoutMs: scriptTimeoutMs });
}

function normalizeDxmProductVideoBackfillItems(payload = {}) {
  const rawItems = Array.isArray(payload.items) ? payload.items
    : Array.isArray(payload.videos) ? payload.videos
      : Array.isArray(payload.video_items) ? payload.video_items
        : (payload.video_url ? [payload] : []);
  return rawItems.map((raw, index) => {
    const item = raw && typeof raw === "object" ? raw : {};
    const rawPayload = item.raw_payload && typeof item.raw_payload === "object" ? item.raw_payload : {};
    const pick = (...keys) => {
      for (const key of keys) {
        const value = key in item ? item[key] : rawPayload[key];
        const normalized = String(value ?? "").replace(/\s+/g, " ").trim();
        if (normalized) return normalized;
      }
      return "";
    };
    const skuRaw = item.sku_values ?? item.sku_value ?? item.sku ?? item.sku_text ?? rawPayload.sku_values ?? rawPayload.sku;
    const skuValues = Array.isArray(skuRaw)
      ? skuRaw.map((value) => String(value ?? "").replace(/\s+/g, " ").trim()).filter(Boolean)
      : String(skuRaw ?? "").split(/[,，;\n\r|/]+/).map((value) => value.replace(/\s+/g, " ").trim()).filter(Boolean);
    const productNo = pick("product_no", "product_id", "source_product_id", "source_product_no", "source_id", "1688_id");
    const productLink = pick("product_link", "external_link", "source_link", "source_url", "source_ref", "url", "1688_url");
    const videoUrl = pick("video_url", "product_video_url", "public_video_url", "videoUrl", "mp4_url");
    const title = pick("title", "product_title", "name");
    const listRowIndexRaw = Number(item.list_row_index ?? item.list_index ?? item.row_index ?? item.sequence_index);
    return {
      item_id: pick("item_id", "task_item_id", "id") || String(index + 1),
      row_index: Number.isFinite(listRowIndexRaw) ? listRowIndexRaw : null,
      product_no: productNo,
      product_link: productLink,
      product_offer_id: extractOfferId(productLink) || extractOfferId(productNo),
      title,
      sku_values: skuValues,
      video_url: videoUrl,
      _source_index: index
    };
  });
}

function extractOfferId(value) {
  const text = String(value || "");
  const decoded = (() => {
    try {
      return decodeURIComponent(text);
    } catch (_error) {
      return text;
    }
  })();
  const patterns = [
    /\/offer\/(\d{8,})(?:\.html|[/?#]|$)/i,
    /offer[=/](\d{8,})/i,
    /\b(\d{10,})\b/
  ];
  for (const source of [text, decoded]) {
    for (const pattern of patterns) {
      const match = source.match(pattern);
      if (match) return match[1];
    }
  }
  return "";
}

function dxmProductVideoUrlIsSupported(value) {
  const url = String(value || "").trim();
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" && /\.mp4$/i.test(parsed.pathname || "");
  } catch (_error) {
    return false;
  }
}

function validateDxmProductVideoBackfillItem(item) {
  if (!item.video_url) return "missing_video_url";
  if (!dxmProductVideoUrlIsSupported(item.video_url)) return "video_url_must_be_https_mp4";
  if (!item.product_no && !item.product_offer_id && !item.product_link) return "missing_product_identity";
  return "";
}

function dxmProductVideoBackfillCounts(results, total) {
  const safeResults = Array.isArray(results) ? results : [];
  return {
    total_items: total,
    processed_items: safeResults.length,
    succeeded_items: safeResults.filter((item) => item.status === "succeeded" || item.status === "prepared").length,
    failed_items: safeResults.filter((item) => item.status === "failed").length,
    skipped_items: safeResults.filter((item) => item.status === "skipped").length
  };
}

async function runDxmProductVideoBackfillCommand(baseUrl, sessionToken, command) {
  const payload = command.payload || {};
  const execute = payload.execute === true && payload.dry_run !== true;
  const items = normalizeDxmProductVideoBackfillItems(payload);
  const itemResults = [];
  if (!items.length) {
    return {
      command_type: command.command_type,
      statusText: "店小秘补视频失败：没有可补传的视频清单",
      error: "empty_video_items",
      item_results: [],
      counts: dxmProductVideoBackfillCounts([], 0),
      safety: dxmProductVideoBackfillSafety(execute),
      capturedAt: new Date().toISOString()
    };
  }

  const listTab = await findDxmProductVideoListTab(payload.list_url || payload.page_url || "");
  await waitForTabReady(listTab.id, 20000);
  const progress = {
    command_type: command.command_type,
    mode: execute ? "network_upload" : "dry_run",
    statusText: `店小秘补视频已打开列表页，准备处理 0/${items.length}`,
    counts: dxmProductVideoBackfillCounts(itemResults, items.length),
    item_results: itemResults,
    safety: dxmProductVideoBackfillSafety(execute),
    capturedAt: new Date().toISOString()
  };
  const publishProgress = async (statusText) => {
    progress.statusText = statusText;
    progress.counts = dxmProductVideoBackfillCounts(itemResults, items.length);
    progress.capturedAt = new Date().toISOString();
    if (command.id) {
      await postResult(baseUrl, sessionToken, command.id, "running", progress);
    }
  };

  await publishProgress(`店小秘补视频正在准备：共 ${items.length} 个商品，模式 ${execute ? "执行上传" : "dry-run 校验"}`);
  for (let index = 0; index < items.length; index += 1) {
    const item = items[index];
    const validationError = validateDxmProductVideoBackfillItem(item);
    if (validationError) {
      itemResults.push({
        item_id: item.item_id,
        product_no: item.product_no,
        product_link: item.product_link,
        video_url: item.video_url,
        status: "failed",
        error: validationError
      });
      await publishProgress(`店小秘补视频跳过无效清单：${index + 1}/${items.length}`);
      continue;
    }

    await publishProgress(`店小秘补视频处理中：${index + 1}/${items.length}，${item.product_no || item.product_offer_id || item.title || item.item_id}`);
    try {
      const result = await processDxmProductVideoBackfillItem(listTab.id, item, {
        execute,
        fallbackIndex: index
      });
      itemResults.push(result);
    } catch (error) {
      itemResults.push({
        item_id: item.item_id,
        product_no: item.product_no,
        product_link: item.product_link,
        video_url: item.video_url,
        status: "failed",
        error: String(error?.message || error || "店小秘页面动作失败")
      });
    }
    await publishProgress(`店小秘补视频已处理：${index + 1}/${items.length}`);
    await delay(900);
  }

  const counts = dxmProductVideoBackfillCounts(itemResults, items.length);
  const failed = itemResults.filter((item) => item.status === "failed");
  return {
    command_type: command.command_type,
    mode: execute ? "network_upload" : "dry_run",
    statusText: failed.length
      ? `店小秘补视频完成：成功/预检 ${counts.succeeded_items}，失败 ${failed.length}`
      : `店小秘补视频完成：${execute ? "已上传" : "已完成 dry-run 校验"} ${counts.succeeded_items} 个商品`,
    error: failed.length ? "dxm_product_video_backfill_partial_failed" : undefined,
    item_results: itemResults,
    counts,
    safety: dxmProductVideoBackfillSafety(execute),
    capturedAt: new Date().toISOString()
  };
}

function dxmProductVideoBackfillSafety(execute) {
  return {
    serial_execution: true,
    verifies_product_no_before_upload: true,
    verifies_external_link_before_upload: true,
    no_publish_click: true,
    dry_run: !execute
  };
}

async function findDxmProductVideoListTab(listUrl) {
  const targetUrl = String(listUrl || "").trim();
  if (targetUrl) {
    try {
      const parsed = new URL(targetUrl);
      if (/(^|\.)dianxiaomi\.com$/i.test(parsed.hostname)) {
        return findOrOpenBusinessTab(targetUrl);
      }
    } catch (_error) {
      // Fall back to the currently open Dianxiaomi tab.
    }
  }
  const tabs = await chrome.tabs.query({ currentWindow: true });
  const activeDxm = tabs.find((tab) => tab.active && tab.id && dxmTabLooksUsable(tab));
  if (activeDxm?.id) {
    await chrome.tabs.update(activeDxm.id, { active: true });
    return { id: activeDxm.id };
  }
  const anyDxm = tabs.find((tab) => tab.id && dxmTabLooksUsable(tab));
  if (anyDxm?.id) {
    await chrome.tabs.update(anyDxm.id, { active: true });
    return { id: anyDxm.id };
  }
  throw new Error("没有找到已登录的店小秘页面，请先打开待发布产品列表页");
}

function dxmTabLooksUsable(tab) {
  try {
    const parsed = new URL(tab.url || "");
    return /(^|\.)dianxiaomi\.com$/i.test(parsed.hostname);
  } catch (_error) {
    return false;
  }
}

async function processDxmProductVideoBackfillItem(listTabId, item, options) {
  const beforeTabs = await chrome.tabs.query({ currentWindow: true });
  const beforeTabIds = beforeTabs.map((tab) => tab.id).filter(Boolean);
  await chrome.tabs.update(listTabId, { active: true });
  const listAction = await clickDxmProductListEditForVideoBackfill(listTabId, item, options.fallbackIndex);
  if (!listAction?.ok) {
    return dxmProductVideoBackfillItemResult(item, "failed", listAction?.error || "没有在列表页找到可编辑商品", {
      list_action: listAction || {}
    });
  }

  const editTab = await waitForDxmProductEditTab(listTabId, beforeTabIds, 25000);
  if (!editTab?.id) {
    return dxmProductVideoBackfillItemResult(item, "failed", "打开编辑页后没有识别到产品货号/站外链接区域", {
      list_action: listAction
    });
  }

  let pageAction = null;
  try {
    await chrome.tabs.update(editTab.id, { active: true });
    await waitForTabReady(editTab.id, 15000);
    pageAction = await verifyAndMaybeUploadDxmProductVideo(editTab.id, item, { execute: options.execute });
    if (options.execute && pageAction?.save_clicked) {
      await delay(Number(pageAction.post_save_wait_ms || 7000));
    }
  } finally {
    await returnFromDxmProductEditTab(editTab.id, listTabId, editTab.id !== listTabId);
  }

  let persistVerify = null;
  if (options.execute && pageAction?.ok && pageAction?.needs_persist_verify) {
    persistVerify = await reopenAndVerifyDxmProductVideo(listTabId, item, options);
    if (!persistVerify?.ok) {
      return dxmProductVideoBackfillItemResult(item, "failed", persistVerify?.error || "视频上传后重新打开未看到产品视频，已判定未成功", {
        list_action: listAction,
        page_action: pageAction || {},
        persist_verify: persistVerify || {}
      });
    }
  }

  const status = pageAction?.ok ? (options.execute ? "succeeded" : "prepared") : "failed";
  return dxmProductVideoBackfillItemResult(item, status, pageAction?.ok ? "" : (pageAction?.error || "编辑页补视频动作失败"), {
    list_action: listAction,
    page_action: pageAction || {},
    persist_verify: persistVerify || undefined
  });
}

async function reopenAndVerifyDxmProductVideo(listTabId, item, options) {
  const beforeTabs = await chrome.tabs.query({ currentWindow: true });
  const beforeTabIds = beforeTabs.map((tab) => tab.id).filter(Boolean);
  await chrome.tabs.update(listTabId, { active: true });
  await delay(1000);
  const listAction = await clickDxmProductListEditForVideoBackfill(listTabId, item, options.fallbackIndex);
  if (!listAction?.ok) {
    return {
      ok: false,
      error: listAction?.error || "保存后回到列表页失败，无法重新打开商品验证视频",
      list_action: listAction || {}
    };
  }
  const editTab = await waitForDxmProductEditTab(listTabId, beforeTabIds, 25000);
  if (!editTab?.id) {
    return {
      ok: false,
      error: "保存后重新打开编辑页失败，无法验证视频是否持久化",
      list_action: listAction
    };
  }
  let verifyAction = null;
  try {
    await chrome.tabs.update(editTab.id, { active: true });
    await waitForTabReady(editTab.id, 15000);
    verifyAction = await verifyAndMaybeUploadDxmProductVideo(editTab.id, item, { verifyOnly: true });
  } finally {
    await returnFromDxmProductEditTab(editTab.id, listTabId, editTab.id !== listTabId);
  }
  return {
    ok: Boolean(verifyAction?.ok),
    error: verifyAction?.ok ? "" : (verifyAction?.error || "重新打开后没有看到产品视频预览"),
    list_action: listAction,
    page_action: verifyAction || {}
  };
}

function dxmProductVideoBackfillItemResult(item, status, error, detail = {}) {
  return {
    item_id: item.item_id,
    product_no: item.product_no,
    product_offer_id: item.product_offer_id,
    product_link: item.product_link,
    title: item.title,
    sku_values: item.sku_values,
    video_url: item.video_url,
    status,
    error: error || "",
    ...detail
  };
}

async function waitForDxmProductEditTab(listTabId, beforeTabIds, timeoutMs) {
  const before = new Set(beforeTabIds || []);
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const tabs = await chrome.tabs.query({ currentWindow: true });
    const orderedTabs = [
      ...tabs.filter((tab) => tab.id && !before.has(tab.id)),
      ...tabs.filter((tab) => tab.id === listTabId),
      ...tabs.filter((tab) => tab.active && tab.id && tab.id !== listTabId)
    ];
    const seen = new Set();
    for (const tab of orderedTabs) {
      if (!tab.id || seen.has(tab.id) || !dxmTabLooksUsable(tab)) continue;
      seen.add(tab.id);
      const summary = await readDxmProductEditPageSummary(tab.id);
      if (summary?.ok && summary.is_edit_page) {
        return { id: tab.id, summary };
      }
    }
    await delay(700);
  }
  return null;
}

async function readDxmProductEditPageSummary(tabId) {
  try {
    return await executeMainWorld(tabId, [], async () => {
      const text = (document.body?.innerText || document.documentElement?.innerText || "").replace(/\s+/g, " ").trim();
      return {
        ok: true,
        is_edit_page: text.includes("产品货号") && text.includes("站外产品链接"),
        url: location.href,
        title: document.title,
        textSample: text.slice(0, 300)
      };
    }, { timeoutMs: 5000, attempts: 1 });
  } catch (error) {
    return { ok: false, error: String(error?.message || error) };
  }
}

async function returnFromDxmProductEditTab(editTabId, listTabId, openedInNewTab) {
  try {
    if (openedInNewTab && editTabId !== listTabId) {
      await chrome.tabs.remove(editTabId);
      await chrome.tabs.update(listTabId, { active: true });
      await delay(800);
      return;
    }
    await chrome.tabs.update(listTabId, { active: true });
    const summary = await readDxmProductEditPageSummary(listTabId);
    if (!summary?.is_edit_page) {
      await delay(800);
      return;
    }
    try {
      await chrome.tabs.goBack(listTabId);
    } catch (_error) {
      await executeMainWorld(listTabId, [], async () => {
        history.back();
        return { ok: true };
      }, { timeoutMs: 3000, attempts: 1 });
    }
    await delay(1200);
  } catch (error) {
    warnWorkbench("dxm product video return failed", error);
  }
}

async function clickDxmProductListEditForVideoBackfill(tabId, item, fallbackIndex) {
  return executeMainWorld(tabId, [item, fallbackIndex], async (targetItem, targetFallbackIndex) => {
    function visible(element) {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
    }
    function textOf(element) {
      return (element?.innerText || element?.textContent || "").replace(/\s+/g, " ").trim();
    }
    function clickElement(element) {
      const target = element.closest("button, a") || element;
      target.scrollIntoView({ block: "center", inline: "center" });
      for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
        target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
      }
    }
    function findEditAction(row) {
      const candidates = Array.from(row.querySelectorAll("a, button, span, div"))
        .filter((element) => visible(element) && textOf(element));
      return candidates.find((element) => textOf(element) === "编辑")
        || candidates.find((element) => textOf(element).startsWith("编辑"));
    }
    function scoreRow(rowText, rowIndex) {
      let score = 0;
      const normalized = rowText.toLowerCase();
      const title = String(targetItem.title || "").replace(/\s+/g, " ").trim().toLowerCase();
      if (title && normalized.includes(title)) score += 800;
      if (title && title.length > 20 && normalized.includes(title.slice(0, 28))) score += 320;
      for (const sku of targetItem.sku_values || []) {
        const normalizedSku = String(sku || "").replace(/\s+/g, " ").trim().toLowerCase();
        if (normalizedSku && normalized.includes(normalizedSku)) score += 450;
      }
      if (targetItem.product_no && normalized.includes(String(targetItem.product_no).toLowerCase())) score += 1000;
      const expectedRowIndex = Number(targetItem.row_index);
      if (Number.isFinite(expectedRowIndex) && expectedRowIndex >= 0 && (expectedRowIndex === rowIndex || expectedRowIndex - 1 === rowIndex)) score += 280;
      if (rowIndex === Number(targetFallbackIndex)) score += 120;
      return score;
    }

    const rows = Array.from(document.querySelectorAll("tr, .el-table__row, .ant-table-row, .vxe-body--row"))
      .filter((row) => visible(row) && findEditAction(row));
    const candidates = rows.map((row, index) => {
      const rowText = textOf(row);
      return {
        row,
        index,
        rowText,
        edit: findEditAction(row),
        score: scoreRow(rowText, index)
      };
    });
    candidates.sort((left, right) => right.score - left.score);
    let selected = candidates.find((candidate) => candidate.score > 0);
    if (!selected && Number.isFinite(Number(targetFallbackIndex)) && candidates[Number(targetFallbackIndex)]) {
      selected = candidates[Number(targetFallbackIndex)];
    }
    if (!selected && candidates.length === 1) {
      selected = candidates[0];
    }
    if (!selected) {
      return {
        ok: false,
        error: "列表页没有找到可匹配的编辑行",
        visible_edit_rows: candidates.length,
        first_rows: candidates.slice(0, 5).map((candidate) => candidate.rowText.slice(0, 220))
      };
    }
    clickElement(selected.edit);
    return {
      ok: true,
      selected_index: selected.index,
      selected_score: selected.score,
      selected_row_text: selected.rowText.slice(0, 300),
      visible_edit_rows: candidates.length,
      url: location.href,
      title: document.title
    };
  }, { timeoutMs: 10000, attempts: 1 });
}

async function verifyAndMaybeUploadDxmProductVideo(tabId, item, options) {
  return executeMainWorld(tabId, [item, options], async (targetItem, actionOptions) => {
    function delay(ms) {
      return new Promise((resolve) => setTimeout(resolve, ms));
    }
    function visible(element) {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
    }
    function textOf(element) {
      return (element?.innerText || element?.textContent || "").replace(/\s+/g, " ").trim();
    }
    function clickElement(element) {
      const target = interactiveTargetFor(element);
      target.scrollIntoView({ block: "center", inline: "center" });
      const rect = target.getBoundingClientRect();
      const clientX = Math.max(1, Math.min(window.innerWidth - 1, rect.left + rect.width / 2));
      const clientY = Math.max(1, Math.min(window.innerHeight - 1, rect.top + rect.height / 2));
      const pointTarget = document.elementFromPoint?.(clientX, clientY);
      const eventTarget = pointTarget && (pointTarget === target || target.contains?.(pointTarget)) ? pointTarget : target;
      const baseEvent = { bubbles: true, cancelable: true, view: window, clientX, clientY, button: 0 };
      eventTarget.focus?.();
      for (const type of ["pointerover", "mouseover", "mouseenter", "pointermove", "mousemove", "pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
        const isDown = type === "pointerdown" || type === "mousedown";
        const EventCtor = type.startsWith("pointer") && typeof PointerEvent !== "undefined" ? PointerEvent : MouseEvent;
        try {
          eventTarget.dispatchEvent(new EventCtor(type, { ...baseEvent, buttons: isDown ? 1 : 0 }));
        } catch (_error) {
          eventTarget.dispatchEvent(new MouseEvent(type.replace(/^pointer/, "mouse"), { ...baseEvent, buttons: isDown ? 1 : 0 }));
        }
      }
      if (typeof eventTarget.click === "function") eventTarget.click();
      if (target !== eventTarget && typeof target.click === "function") target.click();
    }
    function setControlValue(element, value) {
      element.focus();
      const previous = controlValue(element);
      const descriptors = [
        Object.getOwnPropertyDescriptor(Object.getPrototypeOf(element), "value"),
        typeof HTMLTextAreaElement !== "undefined" ? Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value") : null,
        typeof HTMLInputElement !== "undefined" ? Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value") : null,
      ].filter(Boolean);
      const descriptor = descriptors.find((item) => typeof item?.set === "function");
      if (descriptor?.set) descriptor.set.call(element, value);
      else element.value = value;
      if (element.value !== value) element.value = value;
      element.setAttribute?.("value", value);
      if (element._valueTracker?.setValue) element._valueTracker.setValue(previous);
      try {
        element.dispatchEvent(new InputEvent("beforeinput", { bubbles: true, cancelable: true, inputType: "insertText", data: value }));
      } catch (_error) {
        element.dispatchEvent(new Event("beforeinput", { bubbles: true, cancelable: true }));
      }
      try {
        element.dispatchEvent(new InputEvent("input", { bubbles: true, cancelable: true, inputType: "insertText", data: value }));
      } catch (_error) {
        element.dispatchEvent(new Event("input", { bubbles: true }));
      }
      element.dispatchEvent(new Event("change", { bubbles: true }));
      element.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, cancelable: true, key: "Unidentified" }));
      notifyFrameworkInput(element, previous, value);
    }
    function reactPropsForElement(element) {
      if (!element) return null;
      const key = Object.keys(element).find((name) => /^__react(Props|EventHandlers)\$/.test(name));
      return key ? element[key] : null;
    }
    function notifyFrameworkInput(element, previous, value) {
      const props = reactPropsForElement(element);
      if (!props) return 0;
      const event = {
        bubbles: true,
        cancelable: true,
        defaultPrevented: false,
        isTrusted: false,
        nativeEvent: { isTrusted: false },
        target: element,
        currentTarget: element,
        type: "change",
        preventDefault() { this.defaultPrevented = true; },
        stopPropagation() {},
      };
      let called = 0;
      if (element._valueTracker?.setValue) element._valueTracker.setValue(previous);
      for (const handlerName of ["onBeforeInput", "onInput", "onChange"]) {
        const handler = props?.[handlerName];
        if (typeof handler === "function") {
          try {
            handler({ ...event, type: handlerName === "onChange" ? "change" : "input", data: value });
            called += 1;
          } catch (_error) {
            // Browser page handlers should not break the plugin command loop.
          }
        }
      }
      return called;
    }
    async function waitFor(condition, timeoutMs = 10000, intervalMs = 250) {
      const deadline = Date.now() + timeoutMs;
      let last = null;
      while (Date.now() < deadline) {
        last = condition();
        if (last) return last;
        await delay(intervalMs);
      }
      return last;
    }
    function normalizedLabel(value) {
      return String(value || "").replace(/\s+/g, "");
    }
    function controlValue(control) {
      return String(control?.value || control?.getAttribute?.("value") || "").replace(/\s+/g, " ").trim();
    }
    function labelElementLooksScoped(element, expected) {
      const ownText = Array.from(element.childNodes || [])
        .filter((node) => node.nodeType === 3)
        .map((node) => node.textContent || "")
        .join(" ");
      const normalizedOwn = normalizedLabel(ownText);
      if (normalizedOwn && normalizedOwn.includes(expected)) return true;
      const fullText = textOf(element);
      return fullText.length <= 120 && normalizedLabel(fullText).includes(expected);
    }
    function closestFieldRoot(label) {
      return label.closest?.(".ant-form-item, .el-form-item, .layui-form-item, .form-group, .form-item, .form-row, tr")
        || label.parentElement;
    }
    function nearestValuedControl(label, root) {
      if (!root) return null;
      const labelRect = label.getBoundingClientRect();
      const labelCenterY = labelRect.top + labelRect.height / 2;
      const controls = Array.from(root.querySelectorAll("input, textarea, select"))
        .filter((control) => control !== label && !label.contains(control) && (visible(control) || controlValue(control)));
      const ranked = controls
        .map((control) => {
          const rect = control.getBoundingClientRect();
          const value = controlValue(control);
          if (!value) return null;
          const centerY = rect.top + rect.height / 2;
          const verticalDistance = Math.abs(centerY - labelCenterY);
          const rightOfLabelPenalty = rect.left >= labelRect.left - 8 ? 0 : 500;
          const horizontalDistance = Math.abs(rect.left - labelRect.right);
          return {
            control,
            value,
            score: verticalDistance * 5 + horizontalDistance + rightOfLabelPenalty,
          };
        })
        .filter(Boolean)
        .sort((left, right) => left.score - right.score);
      return ranked[0] || null;
    }
    function findControlValueByLabel(labelText) {
      const expected = normalizedLabel(labelText);
      const labels = Array.from(document.querySelectorAll("label, span, div, td, th, p"))
        .filter((element) => visible(element) && labelElementLooksScoped(element, expected))
        .sort((left, right) => textOf(left).length - textOf(right).length);
      for (const label of labels) {
        const labelFor = label.getAttribute?.("for");
        if (labelFor) {
          const byFor = document.getElementById?.(labelFor);
          const value = controlValue(byFor);
          if (value) return { value, labelText: textOf(label), controlTag: byFor.tagName, method: "for" };
        }
        const roots = [
          closestFieldRoot(label),
          label.parentElement,
          label.parentElement?.parentElement,
          label.closest?.("tr"),
        ].filter(Boolean);
        const seenRoots = new Set();
        for (const root of roots) {
          if (seenRoots.has(root)) continue;
          seenRoots.add(root);
          const nearest = nearestValuedControl(label, root);
          if (nearest?.value) {
            return { value: nearest.value, labelText: textOf(label), controlTag: nearest.control.tagName, method: "near_label" };
          }
        }
        const nearby = nearestValuedControl(label, document.body);
        if (nearby?.value && nearby.score < 260) {
          return { value: nearby.value, labelText: textOf(label), controlTag: nearby.control.tagName, method: "nearby_body" };
        }
      }
      return { value: "", labelText: "", controlTag: "" };
    }
    function findVisibleControlValueMatching(predicate, method) {
      const controls = Array.from(document.querySelectorAll("input, textarea, select"))
        .filter((control) => visible(control) || controlValue(control));
      for (const control of controls) {
        const value = controlValue(control);
        if (value && predicate(value)) {
          return { value, labelText: "", controlTag: control.tagName, method };
        }
      }
      return { value: "", labelText: "", controlTag: "", method };
    }
    function extractOfferId(value) {
      const text = String(value || "");
      let decoded = text;
      try {
        decoded = decodeURIComponent(text);
      } catch (_error) {
        decoded = text;
      }
      const patterns = [/\/offer\/(\d{8,})(?:\.html|[/?#]|$)/i, /offer[=/](\d{8,})/i, /\b(\d{10,})\b/];
      for (const source of [text, decoded]) {
        for (const pattern of patterns) {
          const match = source.match(pattern);
          if (match) return match[1];
        }
      }
      return "";
    }
    function productLinkMatches(expected, actual) {
      const expectedId = extractOfferId(expected);
      const actualId = extractOfferId(actual);
      if (expectedId && actualId) return expectedId === actualId;
      if (!expected) return true;
      return String(actual || "").trim() === String(expected || "").trim();
    }
    function findVisibleByText(pattern, selector = "button, a, span, div, li") {
      const candidates = Array.from(document.querySelectorAll(selector))
        .filter((element) => visible(element) && pattern.test(textOf(element)));
      return candidates[0] || null;
    }
    function interactiveTargetFor(element) {
      const selector = [
        "button",
        "a",
        "label",
        "li",
        "[role='button']",
        "[aria-haspopup='true']",
        "[onclick]",
        "input[type='button']",
        "input[type='submit']",
        ".ant-btn",
        ".el-button",
        ".ivu-btn",
        ".layui-layer-btn0",
        ".layui-layer-btn1",
        ".btn",
        ".button",
      ].join(", ");
      return element.closest?.(selector)
        || element.querySelector?.(selector)
        || element;
    }
    function elementClassText(element) {
      if (!element) return "";
      if (typeof element.className === "string") return element.className;
      return String(element.getAttribute?.("class") || "");
    }
    function isButtonLikeElement(element) {
      if (!element) return false;
      const tag = String(element.tagName || "").toUpperCase();
      const role = String(element.getAttribute?.("role") || "").toLowerCase();
      const className = elementClassText(element);
      return tag === "BUTTON"
        || tag === "A"
        || tag === "LI"
        || tag === "LABEL"
        || (tag === "INPUT" && /^(button|submit)$/i.test(String(element.getAttribute?.("type") || "")))
        || role === "button"
        || element.getAttribute?.("onclick") !== null
        || element.getAttribute?.("aria-haspopup")
        || /\b(ant-btn|el-button|ivu-btn|layui-layer-btn\d*|btn|button|primary|confirm|sure)\b/i.test(className);
    }
    function elementDebugInfo(element) {
      if (!element) return {};
      const target = interactiveTargetFor(element);
      return {
        text: textOf(element).slice(0, 80),
        tag: element.tagName || "",
        class_name: elementClassText(element).slice(0, 160),
        click_target_text: textOf(target).slice(0, 80),
        click_target_tag: target?.tagName || "",
        click_target_class_name: elementClassText(target).slice(0, 160),
      };
    }
    function isDisabledElement(element) {
      return Boolean(
        element?.disabled
        || element?.getAttribute?.("disabled") !== null
        || element?.getAttribute?.("aria-disabled") === "true"
        || /\bdisabled\b/i.test(String(element?.className || ""))
      );
    }
    function findProductVideoLabel() {
      const exact = Array.from(document.querySelectorAll("label, span, div, td, th"))
        .filter((element) => visible(element))
        .filter((element) => /^产品视频[:：]?$/.test(textOf(element)))
        .sort((left, right) => textOf(left).length - textOf(right).length);
      if (exact.length) return exact[0];
      return findVisibleByText(/产品视频/, "label, span, div, td, th");
    }
    function productVideoSectionRoots() {
      const roots = [];
      const label = findProductVideoLabel();
      let current = label || null;
      for (let depth = 0; current && depth < 8; depth += 1) {
        roots.push(current);
        current = current.parentElement || null;
      }
      return Array.from(new Set(roots)).filter(Boolean);
    }
    function productVideoPreviewEvidence() {
      for (const root of productVideoSectionRoots()) {
        const text = textOf(root);
        if (!text || !/产品视频/.test(text)) continue;
        const hasPreviewText = (/播放/.test(text) && /删除/.test(text)) || /重新上传/.test(text);
        const hasMediaNode = Boolean(root.querySelector?.("video, source"));
        if (hasPreviewText || hasMediaNode) {
          return {
            found: true,
            text: text.slice(0, 500),
            has_preview_text: hasPreviewText,
            has_media_node: hasMediaNode,
          };
        }
      }
      return { found: false };
    }
    function findAddVideoAction() {
      const videoLabel = findProductVideoLabel();
      if (videoLabel) videoLabel.scrollIntoView({ block: "center", inline: "center" });
      const candidates = Array.from(document.querySelectorAll("button, a, [role='button'], [aria-haspopup='true'], span, div, li"))
        .map((element) => {
          const elementText = textOf(element);
          if (!visible(element) || !/添加视频/.test(elementText)) return null;
          const target = interactiveTargetFor(element);
          if (!target || !visible(target) || isDisabledElement(target)) return null;
          const targetText = textOf(target);
          const isInteractive = /^(BUTTON|A|LI)$/i.test(String(target.tagName || ""))
            || String(target.getAttribute?.("role") || "").toLowerCase() === "button"
            || target.getAttribute?.("aria-haspopup");
          if (!isInteractive && elementText.length > 40) return null;
          const rect = element.getBoundingClientRect();
          const labelRect = videoLabel?.getBoundingClientRect?.() || rect;
          const normalized = elementText.replace(/\s+/g, "");
          let score = Math.abs(rect.top - labelRect.top) + Math.abs(rect.left - labelRect.left) * 0.15;
          if (normalized === "添加视频" || normalized === "添加视频▾" || normalized === "添加视频▼") score -= 600;
          else if (normalized.startsWith("添加视频")) score -= 320;
          score += Math.min(elementText.length, 120);
          if (/^(BUTTON|A)$/i.test(String(target.tagName || ""))) score -= 240;
          if (String(target.getAttribute?.("aria-haspopup") || "").toLowerCase() === "true") score -= 120;
          if (target !== element) score -= 80;
          return {
            element: target,
            score,
            text: targetText || elementText,
            tag: target.tagName || element.tagName || "",
          };
        })
        .filter(Boolean)
        .sort((left, right) => left.score - right.score);
      return candidates[0]?.element || null;
    }
    function findNetworkUploadAction() {
      const candidates = Array.from(document.querySelectorAll("button, a, span, div, li, [role='menuitem'], [role='option']"))
        .filter((element) => visible(element) && /网络上传/.test(textOf(element)))
        .map((element) => {
          const text = textOf(element);
          const target = interactiveTargetFor(element);
          const rect = element.getBoundingClientRect();
          let score = Math.abs(rect.top - window.innerHeight * 0.62) * 0.05 + Math.min(text.length, 120);
          if (/^网络上传$/.test(text)) score -= 500;
          if (/^(LI|BUTTON|A)$/i.test(String(target?.tagName || element.tagName || ""))) score -= 160;
          return { element: target || element, score, text };
        })
        .filter((item) => item.element && visible(item.element))
        .sort((left, right) => left.score - right.score);
      return candidates[0]?.element || null;
    }
    async function openAddVideoDropdown(addVideo) {
      const attempts = [
        () => clickElement(addVideo),
        () => {
          addVideo.focus?.();
          addVideo.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true, cancelable: true }));
          addVideo.dispatchEvent(new KeyboardEvent("keyup", { key: "ArrowDown", bubbles: true, cancelable: true }));
        },
        () => {
          addVideo.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, cancelable: true, view: window }));
          addVideo.dispatchEvent(new MouseEvent("mouseenter", { bubbles: true, cancelable: true, view: window }));
        },
        () => clickElement(addVideo),
      ];
      for (const attempt of attempts) {
        attempt();
        const action = await waitFor(() => findNetworkUploadAction(), 1800, 180);
        if (action) return action;
      }
      return null;
    }
    function uploadDialogRootFor(element) {
      return element?.closest?.(".ant-modal, .el-dialog, .modal, .modal-dialog, [role='dialog'], .layui-layer, .ui-dialog, .bootbox, .ivu-modal");
    }
    function videoUploadContextFor(element) {
      const roots = [];
      const dialog = uploadDialogRootFor(element);
      if (dialog) roots.push(dialog);
      let current = element?.parentElement || null;
      for (let depth = 0; current && depth < 6; depth += 1) {
        roots.push(current);
        current = current.parentElement;
      }
      return Array.from(new Set(roots))
        .map((root) => textOf(root))
        .filter(Boolean)
        .join(" ")
        .slice(0, 1200);
    }
    function videoUploadInputLooksScoped(element) {
      const fieldText = [
        element?.getAttribute?.("placeholder") || "",
        element?.getAttribute?.("aria-label") || "",
        element?.getAttribute?.("title") || "",
        videoUploadContextFor(element),
      ].join(" ");
      return /视频地址|文件格式后缀|支持\s*mp4|mp4等|优酷|爱奇艺|网络链接|视频链接/i.test(fieldText);
    }
    function findVisibleTextarea() {
      const candidates = Array.from(document.querySelectorAll("textarea, input[type='text'], input:not([type])"))
        .filter((element) => visible(element) && !isDisabledElement(element) && videoUploadInputLooksScoped(element))
        .map((element) => {
          const rect = element.getBoundingClientRect();
          let score = rect.top + Math.abs(rect.left - window.innerWidth * 0.45) * 0.05;
          if (uploadDialogRootFor(element)) score -= 1000;
          if (String(element.tagName || "").toUpperCase() === "TEXTAREA") score -= 160;
          if (rect.width >= 300) score -= 80;
          if (rect.height >= 80) score -= 80;
          if (/视频地址|视频链接/i.test(element.getAttribute?.("placeholder") || "")) score -= 180;
          return { element, score };
        })
        .sort((left, right) => left.score - right.score);
      return candidates[0]?.element || null;
    }
    function findConfirmButton(anchor) {
      const root = uploadDialogRootFor(anchor) || anchor?.closest?.(".ant-modal, .el-dialog, .modal, [role='dialog'], .layui-layer, body") || document.body;
      const selectors = "button, a, span, div, [role='button'], input[type='button'], input[type='submit'], .layui-layer-btn0, .ant-btn, .el-button, .ivu-btn, .btn";
      const candidates = Array.from(root.querySelectorAll(selectors))
        .filter((element) => visible(element) && /^(确定|确认)$/.test(textOf(element)))
        .map((element) => {
          const target = interactiveTargetFor(element);
          if (!target || !visible(target) || isDisabledElement(target)) return null;
          const targetText = textOf(target);
          if (/取消/.test(targetText) && !/^(确定|确认)$/.test(targetText)) return null;
          const rect = target.getBoundingClientRect();
          let score = Math.min(targetText.length || textOf(element).length, 120);
          if (/^(确定|确认)$/.test(targetText)) score -= 500;
          if (/^(确定|确认)$/.test(textOf(element))) score -= 260;
          if (isButtonLikeElement(target)) score -= 320;
          if (target !== element) score -= 120;
          score += Math.abs(rect.left - window.innerWidth * 0.58) * 0.02;
          score += rect.top * 0.005;
          return { element: target, score };
        })
        .filter(Boolean)
        .sort((left, right) => left.score - right.score);
      const fallback = findVisibleByText(/^(确定|确认)$/, selectors);
      return candidates[0]?.element || (fallback ? interactiveTargetFor(fallback) : null);
    }
    function findUploadDialogCloseButton(anchor) {
      const root = uploadDialogRootFor(anchor);
      if (!root) return null;
      const selectors = ".ant-modal-close, .ant-modal-close-x, button, a, span";
      const candidates = Array.from(root.querySelectorAll(selectors))
        .filter((element) => visible(element))
        .map((element) => {
          const text = textOf(element);
          const className = elementClassText(element);
          const aria = String(element.getAttribute?.("aria-label") || "");
          const rect = element.getBoundingClientRect();
          let score = rect.top * 0.02 + Math.max(0, window.innerWidth - rect.left) * 0.001 + Math.min(text.length, 120);
          if (/ant-modal-close|modal-close|close/i.test(className) || /close|关闭/i.test(aria)) score -= 500;
          if (/^(取消|关闭|×|x)$/i.test(text)) score -= 420;
          if (/^(确定|确认)$/.test(text)) score += 800;
          return { element: interactiveTargetFor(element), score, text, className };
        })
        .filter((item) => item.element && visible(item.element))
        .sort((left, right) => left.score - right.score);
      return candidates[0]?.element || null;
    }
    async function closeUploadDialogIfOpen(anchor) {
      if (!findVisibleTextarea()) return true;
      const root = uploadDialogRootFor(anchor);
      const attempts = [
        () => findUploadDialogCloseButton(anchor),
        () => {
          if (!root) return null;
          return Array.from(root.querySelectorAll("button, a, span, div, [role='button'], .ant-btn, .el-button, .ivu-btn, .btn"))
            .filter((element) => visible(element) && /^(取消|关闭)$/.test(textOf(element)))
            .map((element) => interactiveTargetFor(element))
            .find((element) => element && visible(element));
        },
        () => {
          document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
          document.dispatchEvent(new KeyboardEvent("keyup", { key: "Escape", bubbles: true, cancelable: true }));
          return null;
        },
      ];
      for (const attempt of attempts) {
        const close = attempt();
        if (close) clickElement(close);
        if (await waitFor(() => !findVisibleTextarea(), 3500, 200)) return true;
      }
      return false;
    }
    function productEditSaveText(element) {
      const text = textOf(element);
      return text || String(element?.value || element?.getAttribute?.("aria-label") || element?.getAttribute?.("title") || "").replace(/\s+/g, " ").trim();
    }
    function findProductEditSaveAction() {
      const selectors = "button, a, [role='button'], input[type='button'], input[type='submit'], span, div, .ant-btn, .el-button, .ivu-btn, .layui-btn, .btn";
      const forbidden = /发布|提交|上架|刊登|审核|报名|删除|确认发布|立即发布|保存并发布|保存并提交|保存并上架|保存并刊登/;
      const candidates = Array.from(document.querySelectorAll(selectors))
        .map((element) => {
          if (!visible(element) || isDisabledElement(element) || uploadDialogRootFor(element)) return null;
          const text = productEditSaveText(element);
          const normalized = normalizedLabel(text);
          if (!normalized || !/保存/.test(normalized)) return null;
          if (forbidden.test(normalized)) return null;
          const target = interactiveTargetFor(element);
          if (!target || !visible(target) || isDisabledElement(target) || uploadDialogRootFor(target)) return null;
          const targetText = normalizedLabel(productEditSaveText(target) || text);
          if (forbidden.test(targetText)) return null;
          const rect = target.getBoundingClientRect();
          let score = Math.min(normalized.length, 80);
          if (/^保存$/.test(normalized) || /^保存$/.test(targetText)) score -= 900;
          else if (/^(保存草稿|保存修改|保存商品|保存并返回|保存并关闭|保存并继续)$/.test(normalized)) score -= 650;
          else score -= 240;
          if (isButtonLikeElement(target)) score -= 240;
          if (/^(BUTTON|A|INPUT)$/i.test(String(target.tagName || ""))) score -= 180;
          if (target !== element) score -= 80;
          score += Math.abs(rect.left - window.innerWidth * 0.5) * 0.01;
          score += Math.max(0, window.innerHeight - rect.top) * 0.002;
          return { element: target, score, text, targetText: productEditSaveText(target) };
        })
        .filter(Boolean)
        .sort((left, right) => left.score - right.score);
      return candidates[0]?.element || null;
    }
    async function saveProductEditPageAfterVideo(previewEvidence, uploadAnchor) {
      const dialogClosed = await closeUploadDialogIfOpen(uploadAnchor || document.activeElement);
      if (!dialogClosed) {
        return {
          ok: false,
          error: "视频已添加到编辑页，但网络上传弹窗未关闭，未执行保存",
          upload_dialog_closed: false,
          preview_evidence: previewEvidence,
        };
      }
      window.scrollTo(0, document.body?.scrollHeight || document.documentElement?.scrollHeight || 0);
      await delay(500);
      const save = findProductEditSaveAction();
      if (!save) {
        return {
          ok: false,
          error: "视频已添加到编辑页，但没有找到安全保存按钮；未点击发布/提交/上架类按钮",
          upload_dialog_closed: true,
          preview_evidence: previewEvidence,
          textSample: (document.body?.innerText || "").replace(/\s+/g, " ").slice(-900),
        };
      }
      const saveAction = elementDebugInfo(save);
      clickElement(save);
      return {
        ok: true,
        save_clicked: true,
        save_action: saveAction,
        upload_dialog_closed: true,
        post_save_wait_ms: 7000,
      };
    }
    async function setVideoUploadValue(element, value) {
      const expected = String(value || "").trim();
      const attempts = [
        "native_setter",
        "exec_command_insert_text",
        "direct_assignment",
      ];
      const methods = [];
      let frameworkHandlers = 0;
      for (const method of attempts) {
        if (method === "exec_command_insert_text") {
          element.focus();
          element.click?.();
          try {
            element.select?.();
          } catch (_error) {
            // Not every input-like element supports select.
          }
          try {
            document.execCommand?.("insertText", false, expected);
          } catch (_error) {
            setControlValue(element, expected);
          }
          element.dispatchEvent(new Event("input", { bubbles: true }));
          element.dispatchEvent(new Event("change", { bubbles: true }));
          frameworkHandlers += notifyFrameworkInput(element, "", expected);
        } else if (method === "direct_assignment") {
          element.focus();
          const previous = controlValue(element);
          element.value = expected;
          element.setAttribute?.("value", expected);
          element.dispatchEvent(new Event("input", { bubbles: true }));
          element.dispatchEvent(new Event("change", { bubbles: true }));
          frameworkHandlers += notifyFrameworkInput(element, previous, expected);
        } else {
          setControlValue(element, expected);
          frameworkHandlers += notifyFrameworkInput(element, "", expected);
        }
        methods.push(method);
        await delay(300);
      }
      const actual = controlValue(element);
      if (actual === expected) {
        return { ok: true, method: methods.join("+"), methods, actual, framework_handler_calls: frameworkHandlers };
      }
      return {
        ok: false,
        actual: controlValue(element),
        methods,
        framework_handler_calls: frameworkHandlers,
        tag: element?.tagName || "",
        placeholder: element?.getAttribute?.("placeholder") || "",
        context: videoUploadContextFor(element).slice(0, 500),
      };
    }

    window.scrollTo(0, Math.max(0, Math.floor(document.body.scrollHeight * 0.45)));
    await delay(500);
    let productNo = findControlValueByLabel("产品货号");
    let productLink = findControlValueByLabel("站外产品链接");
    const expectedNo = String(targetItem.product_no || "").trim();
    if (expectedNo && productNo.value !== expectedNo) {
      const exactProductNo = findVisibleControlValueMatching((value) => value === expectedNo, "expected_product_no_exact");
      if (exactProductNo.value) productNo = exactProductNo;
    }
    const expectedOfferId = extractOfferId(targetItem.product_link || targetItem.product_offer_id || "");
    if (expectedOfferId && !productLinkMatches(targetItem.product_link, productLink.value)) {
      const exactProductLink = findVisibleControlValueMatching((value) => extractOfferId(value) === expectedOfferId, "expected_offer_id_exact");
      if (exactProductLink.value) productLink = exactProductLink;
    }
    if (expectedNo && !productNo.value) {
      return {
        ok: false,
        error: "没有读取到产品货号，已阻止上传",
        expected_product_no: expectedNo,
        actual_product_no: productNo.value,
        actual_product_no_source: productNo.method || "",
        actual_product_link: productLink.value,
        actual_product_link_source: productLink.method || "",
        url: location.href,
        title: document.title
      };
    }
    if (expectedNo && productNo.value !== expectedNo) {
      return {
        ok: false,
        error: "产品货号不匹配，已阻止上传",
        expected_product_no: expectedNo,
        actual_product_no: productNo.value,
        actual_product_no_source: productNo.method || "",
        actual_product_link: productLink.value,
        actual_product_link_source: productLink.method || "",
        url: location.href,
        title: document.title
      };
    }
    if (String(targetItem.product_link || "").trim() && !productLinkMatches(targetItem.product_link, productLink.value)) {
      return {
        ok: false,
        error: "站外产品链接不匹配，已阻止上传",
        expected_product_link: targetItem.product_link,
        actual_product_link: productLink.value,
        actual_product_link_source: productLink.method || "",
        expected_offer_id: extractOfferId(targetItem.product_link),
        actual_offer_id: extractOfferId(productLink.value),
        actual_product_no: productNo.value,
        actual_product_no_source: productNo.method || "",
        url: location.href,
        title: document.title
      };
    }

    const existingPreview = productVideoPreviewEvidence();
    if (actionOptions?.verifyOnly) {
      if (existingPreview.found) {
        return {
          ok: true,
          persisted: true,
          verify_only: true,
          actual_product_no: productNo.value,
          actual_product_link: productLink.value,
          preview_evidence: existingPreview,
          video_url: targetItem.video_url,
          url: location.href,
          title: document.title
        };
      }
      return {
        ok: false,
        verify_only: true,
        error: "重新打开商品后没有看到产品视频预览",
        actual_product_no: productNo.value,
        actual_product_link: productLink.value,
        expected_video_url: targetItem.video_url,
        url: location.href,
        title: document.title
      };
    }
    if (actionOptions?.execute && existingPreview.found) {
      return {
        ok: true,
        uploaded: true,
        already_uploaded: true,
        persisted: true,
        actual_product_no: productNo.value,
        actual_product_link: productLink.value,
        preview_evidence: existingPreview,
        video_url: targetItem.video_url,
        url: location.href,
        title: document.title
      };
    }

    const addVideo = findAddVideoAction();
    if (!addVideo) {
      return {
        ok: false,
        error: "没有找到产品视频的添加视频按钮",
        actual_product_no: productNo.value,
        actual_product_link: productLink.value,
        textSample: (document.body?.innerText || "").replace(/\s+/g, " ").slice(0, 500),
        url: location.href,
        title: document.title
      };
    }
    if (!actionOptions?.execute) {
      return {
        ok: true,
        prepared: true,
        dry_run: true,
        actual_product_no: productNo.value,
        actual_product_link: productLink.value,
        add_video_text: textOf(addVideo),
        url: location.href,
        title: document.title
      };
    }

    const networkUpload = await openAddVideoDropdown(addVideo);
    if (!networkUpload) {
      return {
        ok: false,
        error: "点击添加视频后没有找到网络上传入口",
        actual_product_no: productNo.value,
        actual_product_link: productLink.value,
        add_video_text: textOf(addVideo),
        textSample: (document.body?.innerText || "").replace(/\s+/g, " ").slice(0, 700),
        url: location.href,
        title: document.title
      };
    }
    clickElement(networkUpload);
    const textarea = await waitFor(() => findVisibleTextarea(), 8000);
    if (!textarea) {
      return {
        ok: false,
        error: "网络上传弹窗没有出现视频地址输入框",
        actual_product_no: productNo.value,
        actual_product_link: productLink.value,
        url: location.href,
        title: document.title
      };
    }
    const videoUrl = String(targetItem.video_url || "").trim();
    const inputResult = await setVideoUploadValue(textarea, videoUrl);
    if (!inputResult.ok) {
      return {
        ok: false,
        error: "视频地址输入框未写入视频链接，已停止确认",
        actual_product_no: productNo.value,
        actual_product_link: productLink.value,
        video_url: videoUrl,
        textarea_value: inputResult.actual,
        textarea_tag: inputResult.tag,
        textarea_placeholder: inputResult.placeholder,
        textarea_context: inputResult.context,
        url: location.href,
        title: document.title
      };
    }
    await delay(500);
    const confirm = findConfirmButton(textarea);
    if (!confirm) {
      return {
        ok: false,
        error: "网络上传弹窗没有找到确定按钮",
        actual_product_no: productNo.value,
        actual_product_link: productLink.value,
        url: location.href,
        title: document.title
      };
    }
    const confirm_click = elementDebugInfo(confirm);
    clickElement(confirm);
    let closed = await waitFor(() => !findVisibleTextarea(), 5000, 250);
    let previewEvidence = productVideoPreviewEvidence();
    if (!closed && !previewEvidence.found) {
      const confirmTarget = interactiveTargetFor(confirm);
      confirmTarget.focus?.();
      for (const key of ["Enter", " "]) {
        confirmTarget.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
        confirmTarget.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true, cancelable: true }));
      }
      clickElement(confirmTarget);
      previewEvidence = await waitFor(() => {
        const evidence = productVideoPreviewEvidence();
        return evidence.found ? evidence : null;
      }, 6000, 250) || productVideoPreviewEvidence();
      if (!previewEvidence.found) {
        closed = await waitFor(() => !findVisibleTextarea(), 10000, 250);
      }
    }
    if (previewEvidence.found) {
      const saveResult = await saveProductEditPageAfterVideo(previewEvidence, textarea);
      if (!saveResult.ok) {
        return {
          ok: false,
          uploaded: false,
          actual_product_no: productNo.value,
          actual_product_link: productLink.value,
          video_url: targetItem.video_url,
          preview_evidence: previewEvidence,
          confirm_click,
          input_result: inputResult,
          upload_dialog_closed: saveResult.upload_dialog_closed === true,
          error: saveResult.error || "视频已添加到编辑页但保存失败",
          save_result: saveResult,
          url: location.href,
          title: document.title
        };
      }
      return {
        ok: true,
        uploaded: true,
        save_clicked: true,
        needs_persist_verify: true,
        actual_product_no: productNo.value,
        actual_product_link: productLink.value,
        video_url: targetItem.video_url,
        preview_evidence: previewEvidence,
        confirm_click,
        input_result: inputResult,
        upload_dialog_closed: saveResult.upload_dialog_closed === true,
        save_result: saveResult,
        post_save_wait_ms: saveResult.post_save_wait_ms || 7000,
        url: location.href,
        title: document.title
      };
    }
    if (!closed) {
      return {
        ok: false,
        error: "视频地址确认后弹窗未关闭，可能被店小秘拒绝",
        actual_product_no: productNo.value,
        actual_product_link: productLink.value,
        video_url: videoUrl,
        input_result: inputResult,
        confirm_click,
        textarea_value_after_confirm: controlValue(textarea),
        textarea_placeholder: textarea.getAttribute?.("placeholder") || "",
        modal_text: videoUploadContextFor(textarea).slice(0, 700),
        url: location.href,
        title: document.title
      };
    }
    return {
      ok: false,
      uploaded: false,
      error: "视频地址确认后没有看到产品视频预览，未执行保存",
      actual_product_no: productNo.value,
      actual_product_link: productLink.value,
      video_url: targetItem.video_url,
      url: location.href,
      title: document.title
    };
  }, { timeoutMs: 45000, attempts: 1 });
}

async function runTemuSalesManageSnapshotCommand(baseUrl, sessionToken, command) {
  const label = "TEMU sales-management snapshot";
  const pageUrl = String(command.payload?.page_url || SALES_MANAGE_PAGE_URL);
  const pageSize = Math.max(10, Math.min(Number(command.payload?.page_size || 100), 100));
  const maxPages = Math.max(1, Math.min(Number(command.payload?.max_pages || 20), 100));
  const tab = await findOrOpenBusinessTab(pageUrl);
  await waitForTabReady(tab.id, 20000);
  await injectNetworkProbe(tab.id);
  await postResult(baseUrl, sessionToken, command.id, "running", {
    command_type: command.command_type,
    statusText: "正在采集 TEMU 销售管理近30天销量信号",
    page_url: pageUrl,
    page_size: pageSize,
    max_pages: maxPages,
    capturedAt: new Date().toISOString()
  });

  const result = await fetchSalesManageSnapshotInPage(tab.id, { pageSize, maxPages });
  if (!result?.ok) {
    return {
      command_type: command.command_type,
      statusText: `销售管理采集失败：${result?.error || "TEMU 接口没有返回成功业务数据"}`,
      error: "sales_manage_snapshot_failed",
      help: "请确认 TEMU 销售管理页面已登录且可打开；刷新页面后可以重试。",
      detail: result || {},
      records: result?.records || [],
      matched_count: 0,
      capturedAt: new Date().toISOString()
    };
  }
  return {
    command_type: command.command_type,
    statusText: `销售管理采集完成：${result.items_count || 0} 条商品，${result.sku_count || 0} 个 SKU`,
    mode: "dxm_inspired_sales_manage_fetch",
    page_url: pageUrl,
    page_size: pageSize,
    max_pages: maxPages,
    matched_count: result.records?.length || 0,
    items_count: result.items_count || 0,
    sku_count: result.sku_count || 0,
    records: result.records || [],
    evidence: result.evidence || {},
    capturedAt: new Date().toISOString()
  };
}

async function fetchSalesManageSnapshotInPage(tabId, options) {
  return executeMainWorld(tabId, [options], async (opts) => {
    const pageSize = Math.max(10, Math.min(Number(opts?.pageSize || 100), 100));
    const maxPages = Math.max(1, Math.min(Number(opts?.maxPages || 20), 100));
    const listEndpoints = [
      "/mms/venom/api/supplier/sales/management/listWarehouse",
      "/mms/venom/api/supplier/sales/management/list"
    ];
    const salesNumberEndpoint = "/mms/venom/api/supplier/sales/management/querySkuSalesNumber";
    const records = [];
    const allItems = [];
    const endpointEvidence = [];
    const errors = [];
    const extractItems = (json) => {
      const result = json?.result && typeof json.result === "object" ? json.result : {};
      const candidates = [
        result.pageItems,
        result.items,
        result.list,
        result.records,
        json?.pageItems,
        json?.items,
        json?.list,
        json?.records
      ];
      for (const candidate of candidates) {
        if (Array.isArray(candidate)) return candidate.filter((item) => item && typeof item === "object");
      }
      return [];
    };
    const businessOk = (response, json) => {
      if (!response?.ok) return false;
      if (!json || typeof json !== "object") return false;
      if (json.success === false) return false;
      if (json.errorCode && Number(json.errorCode) !== 1000000) return false;
      return true;
    };
    const request = async (endpoint, body) => {
      const capturedAt = new Date().toISOString();
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          credentials: "include",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(body)
        });
        const contentType = response.headers.get("content-type") || "";
        const text = await response.text();
        let responseJson = null;
        try {
          responseJson = JSON.parse(text);
        } catch (_error) {
          responseJson = null;
        }
        return {
          ok: businessOk(response, responseJson),
          url: new URL(endpoint, location.origin).href,
          endpoint,
          method: "POST",
          status: response.status,
          contentType,
          requestJson: body,
          responseJson,
          responseText: responseJson ? "" : text.slice(0, 1024 * 1024),
          capturedAt
        };
      } catch (error) {
        return {
          ok: false,
          url: new URL(endpoint, location.origin).href,
          endpoint,
          method: "POST",
          status: 0,
          contentType: "",
          requestJson: body,
          responseText: String(error?.message || error),
          capturedAt
        };
      }
    };

    let selectedEndpoint = "";
    for (const endpoint of listEndpoints) {
      const body = {
        pageNumber: 1,
        pageSize,
        isLack: 0,
        priceAdjustRecentDays: 7,
        selectStatusList: []
      };
      const record = await request(endpoint, body);
      const items = extractItems(record.responseJson);
      records.push({ ...record, captureType: "temu_sales_manage_snapshot" });
      endpointEvidence.push({ endpoint, status: record.status, ok: record.ok, returned_count: items.length });
      if (record.ok) {
        selectedEndpoint = endpoint;
        allItems.push(...items);
        break;
      }
      errors.push(`${endpoint}: HTTP ${record.status}`);
    }
    if (!selectedEndpoint) {
      return {
        ok: false,
        error: errors.join("; ") || "sales management endpoint failed",
        records,
        evidence: { endpointEvidence }
      };
    }

    for (let pageNumber = 2; pageNumber <= maxPages; pageNumber += 1) {
      const body = {
        pageNumber,
        pageSize,
        isLack: 0,
        priceAdjustRecentDays: 7,
        selectStatusList: []
      };
      const record = await request(selectedEndpoint, body);
      const items = extractItems(record.responseJson);
      records.push({ ...record, captureType: "temu_sales_manage_snapshot" });
      if (!record.ok) break;
      allItems.push(...items);
      if (items.length < pageSize) break;
    }

    const skuIds = [];
    const addSku = (value) => {
      const text = String(value ?? "").trim();
      if (text && !skuIds.includes(text)) skuIds.push(text);
    };
    for (const item of allItems) {
      addSku(item.productSkuId || item.skuId || item.sku);
      const details = item.skuQuantityDetailList || item.skuList || item.skuSalesList || [];
      if (Array.isArray(details)) {
        details.forEach((detail) => addSku(detail?.productSkuId || detail?.skuId || detail?.sku));
      }
    }

    const now = new Date();
    const endDate = new Date(now.getTime() - 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const startDate = new Date(now.getTime() - 31 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    for (let start = 0; start < skuIds.length; start += 100) {
      const productSkuIds = skuIds.slice(start, start + 100);
      const record = await request(salesNumberEndpoint, { productSkuIds, startDate, endDate });
      records.push({ ...record, captureType: "temu_sales_manage_snapshot", queryIds: productSkuIds });
      if (!record.ok && record.status === 403) break;
    }

    return {
      ok: allItems.length > 0,
      items_count: allItems.length,
      sku_count: skuIds.length,
      records,
      evidence: {
        endpoint: selectedEndpoint,
        endpointEvidence,
        pageSize,
        maxPages,
        sku_count: skuIds.length
      }
    };
  }, { attempts: 2 });
}

async function runFluxApiBySpuCommand(baseUrl, sessionToken, command) {
  const ids = normalizeIds(command.payload?.ids || command.payload?.query_ids || []);
  const chunkSize = Math.max(1, Math.min(Number(command.payload?.chunk_size || command.payload?.max_ids || 100), 100));
  const periods = normalizePeriods(command.payload?.periods || command.payload?.period || []);
  const effectivePeriods = periods.length > 0 ? periods : ["近7日"];
  const maxRetries = Math.max(0, Math.min(Number(command.payload?.max_retries ?? 2), 3));
  const batchDelayMs = Math.max(600, Math.min(Number(command.payload?.direct_delay_ms || 1200), 10000));
  const label = "流量接口 SPU 查询";

  if (ids.length === 0) {
    return {
      command_type: command.command_type,
      statusText: `${label}失败：没有可查询的 SPU ID`,
      error: "empty_ids",
      matched_count: 0,
      records: [],
      capturedAt: new Date().toISOString()
    };
  }

  const tab = await findOrOpenBusinessTab(FLUX_PAGE_URL);
  await waitForTabReady(tab.id, 20000);
  const ready = await waitForPageText(tab.id, ["商品流量"], 20000);
  if (!ready.ok) {
    return {
      command_type: command.command_type,
      statusText: `${label}失败：商品流量页没有加载完成`,
      error: "flux_page_not_ready",
      help: "请确认 TEMU 已登录，并且当前浏览器能打开“经营分析 > 流量分析 > 商品流量”。",
      textSample: ready.textSample || "",
      matched_count: 0,
      records: [],
      capturedAt: new Date().toISOString()
    };
  }
  await injectNetworkProbe(tab.id);

  const progress = {
    command_type: command.command_type,
    statusText: `${label}已进入商品流量页，准备按接口批量查询`,
    total_ids: ids.length,
    queried_ids: 0,
    chunk_size: chunkSize,
    periods: effectivePeriods,
    matched_count: 0,
    records: [],
    batches: [],
    mode: "direct_page_fetch",
    capturedAt: new Date().toISOString()
  };
  const publishProgress = async (statusText) => {
    progress.statusText = statusText;
    progress.capturedAt = new Date().toISOString();
    await postResult(baseUrl, sessionToken, command.id, "running", progress);
  };
  await publishProgress(`${label}正在准备查询`);

  const records = [];
  const batches = [];
  let queriedCount = 0;
  let failedBatch = null;
  let partialMissingCount = 0;

  for (const periodText of effectivePeriods) {
    const timeDimension = periodToTimeDimension(periodText);
    const periodDiscoverySince = new Date(Date.now() - 3 * 60 * 1000).toISOString();
    if (!timeDimension) {
      failedBatch = {
        period: periodText,
        error: `不支持的流量周期：${periodText}`,
        help: "当前只支持近7日和近30日。"
      };
      break;
    }

    let requestTemplate = await getLatestFluxRequestTemplate(tab.id, periodText, timeDimension);
    if (!requestTemplate) {
      await publishProgress(`${label}未读取到 ${periodText} 的页面真实请求模板，正在让 TEMU 页面自然查询一次用于学习模板`);
      requestTemplate = await learnFluxRequestTemplate(tab.id, periodText, timeDimension);
    }
    if (!requestTemplate) {
      const endpointDiagnostics = await getRecentEndpointDiagnostics(tab.id, periodDiscoverySince, 30);
      failedBatch = {
        period: periodText,
        error: `未学习到 ${periodText} 的 TEMU 流量真实请求模板`,
        help: endpointDiagnostics.length > 0
          ? `已捕获到 ${endpointDiagnostics.length} 个其他 TEMU 业务接口，但没有 /api/flow/analysis/list。请刷新 TEMU 商品流量页，确认商品明细区有返回数据后重试。`
          : `请刷新 TEMU 商品流量页，手动选择 ${periodText} 并点击一次查询，看到页面有结果后回到工作台重试。系统不会把缺失数据当作 0 流量。`,
        endpoint_diagnostics: endpointDiagnostics
      };
      break;
    }
    await publishProgress(requestTemplate
      ? `${label}已读取到 ${periodText} 的页面真实请求模板，开始按模板批量查询`
      : `${label}未读取到 ${periodText} 的页面真实请求模板`);
    await publishProgress(`${label}正在按 ${periodText} 全量分页读取商品流量，再按在售表 SPU 本地匹配`);
    const fullScanResult = await fetchFluxAllPagesInPage(tab.id, timeDimension, requestTemplate?.requestJson || null);
    const fullScanRecord = buildFluxApiRecord(fullScanResult, ids, periodText, timeDimension);
    const fullScanRequestVerification = verifyCapturedRequestBatch([fullScanRecord], {
      captureType: "temu_flux_by_spu",
      periodText,
      batchSize: Math.min(ids.length, 100)
    });
    const fullScanBusinessOk = isSuccessfulBusinessRecord(fullScanRecord);
    const fullScanCoverage = verifyFluxBatchCoverage(fullScanRecord, ids);
    const fullScanEvidence = buildFluxBatchEvidence({
      periodText,
      batchNumber: batches.length + 1,
      batchIds: ids,
      record: fullScanRecord,
      retryCount: 0,
      requestVerification: fullScanRequestVerification,
      coverage: fullScanCoverage
    });
    if (fullScanRequestVerification.ok && fullScanBusinessOk) {
      records.push(fullScanRecord);
      batches.push({
        ...fullScanEvidence,
        status: fullScanCoverage.ok ? "succeeded_full_scan" : "partial_missing_full_scan",
        matched_count: 1,
        mode: "period_full_scan",
        page_count: fullScanResult?.page_count || null,
        total_returned_rows: responsePageItems(fullScanRecord.responseJson).length
      });
      queriedCount += ids.length;
      progress.queried_ids = queriedCount;
      progress.matched_count = records.length;
      progress.records = records;
      progress.batches = batches;
      if (!fullScanCoverage.ok) {
        partialMissingCount += fullScanCoverage.missing_count;
        await publishProgress(`${label}${periodText} 全量分页完成，但缺失 ${fullScanCoverage.missing_count} 个 SPU，缺失项会标记为数据缺失`);
      } else {
        await publishProgress(`${label}${periodText} 全量分页完成，已覆盖 ${ids.length} 个 SPU`);
      }
      continue;
    }
    batches.push({
      ...fullScanEvidence,
      status: "full_scan_fallback_to_id_query",
      error: fullScanRequestVerification.error || recordBusinessError(fullScanRecord) || "全量分页读取失败，改用 ID 分批兜底"
    });
    await publishProgress(`${label}${periodText} 全量分页读取失败，改用 ID 分批兜底：${recordBusinessError(fullScanRecord) || fullScanRequestVerification.error || "未知错误"}`);
    const pendingBatches = chunkIds(ids, chunkSize);
    while (pendingBatches.length > 0) {
      const batchIds = pendingBatches.shift();
      const batchNumber = batches.length + 1;
      const totalBatches = batches.length + pendingBatches.length + 1;
      let record = null;
      let retryCount = 0;
      for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
        retryCount = attempt;
        const attemptText = attempt === 0 ? "" : `，第 ${attempt + 1} 次尝试`;
        await publishProgress(`${label}第 ${batchNumber}/${totalBatches} 批查询中：${batchIds.length} 个 SPU（${periodText}）${attemptText}`);
        const apiResult = await fetchFluxAnalysisInPage(tab.id, batchIds, timeDimension, requestTemplate?.requestJson || null);
        record = buildFluxApiRecord(apiResult, batchIds, periodText, timeDimension);
        const hasSuccess = isSuccessfulBusinessRecord(record);
        const hasRateLimit = isRateLimitedRecord(record);
        if (hasSuccess || !hasRateLimit || attempt >= maxRetries) {
          break;
        }
        await publishProgress(`${label}第 ${batchNumber}/${totalBatches} 批触发平台限流，稍后自动重试`);
        await delay(Math.max(2500, batchDelayMs * 3));
      }

      const requestVerification = verifyCapturedRequestBatch([record], {
        captureType: "temu_flux_by_spu",
        periodText,
        batchSize: batchIds.length
      });
      const businessOk = isSuccessfulBusinessRecord(record);
      const coverage = verifyFluxBatchCoverage(record, batchIds);
      const evidence = buildFluxBatchEvidence({
        periodText,
        batchNumber,
        batchIds,
        record,
        retryCount,
        requestVerification,
        coverage
      });
      const fallbackSize = nextFluxFallbackSize(batchIds.length);
      if ((!requestVerification.ok || !businessOk || !coverage.ok) && fallbackSize) {
        const retryChunks = chunkIds(batchIds, fallbackSize);
        pendingBatches.unshift(...retryChunks);
        batches.push({
          ...evidence,
          status: "fallback_split",
          next_chunk_size: fallbackSize,
          error: requestVerification.error || coverage.error || recordBusinessError(record) || "批次查询异常，已自动降批重试"
        });
        await publishProgress(`${label}第 ${batchNumber}/${totalBatches} 批不完整，已自动降到每批 ${fallbackSize} 个 SPU 重试`);
        continue;
      }
      if (!requestVerification.ok) {
        failedBatch = {
          period: periodText,
          batch_index: batchNumber,
          ids: batchIds,
          failed_ids_preview: batchIds.slice(0, 20),
          request_verification: requestVerification,
          response_status: record?.status,
          response_error: recordBusinessError(record),
          error: requestVerification.error
        };
        break;
      }
      if (!businessOk) {
        failedBatch = {
          period: periodText,
          batch_index: batchNumber,
          ids: batchIds,
          failed_ids_preview: batchIds.slice(0, 20),
          response_status: record?.status,
          response_error: recordBusinessError(record),
          error: "TEMU 流量接口没有返回成功业务数据"
        };
        failedBatch.error = recordBusinessError(record) || failedBatch.error;
        break;
      }
      records.push(record);
      if (!coverage.ok) {
        partialMissingCount += coverage.missing_count;
        batches.push({
          ...evidence,
          status: "partial_missing",
          matched_count: 1,
          mode: "direct_page_fetch"
        });
        queriedCount += batchIds.length;
        progress.queried_ids = queriedCount;
        progress.matched_count = records.length;
        progress.records = records;
        progress.batches = batches;
        await publishProgress(`${label}第 ${batchNumber}/${totalBatches} 批完成但缺失 ${coverage.missing_count} 个 SPU，缺失项会标记为数据缺失`);
        await delay(batchDelayMs);
        continue;
      }

      batches.push({
        ...evidence,
        status: "succeeded",
        matched_count: 1,
        mode: "direct_page_fetch"
      });
      queriedCount += batchIds.length;
      progress.queried_ids = queriedCount;
      progress.matched_count = records.length;
      progress.records = records;
      progress.batches = batches;
      await publishProgress(`${label}第 ${batchNumber}/${totalBatches} 批完成：累计查询 ${queriedCount}/${ids.length}，接口响应 ${records.length} 条`);
      await delay(batchDelayMs);
    }
    if (failedBatch) break;
  }

  if (failedBatch) {
    return {
      command_type: command.command_type,
      statusText: `${label}中断：${failedBatch.error}`,
      error: "batch_query_failed",
      failed_batch: failedBatch,
      help: "请确认 TEMU 已登录且商品流量页可正常访问；如果提示平台限流，稍后重试。",
      total_ids: ids.length,
      queried_ids: queriedCount,
      chunk_size: chunkSize,
      periods: effectivePeriods,
      matched_count: records.length,
      records,
      batches,
      mode: "direct_page_fetch",
      capturedAt: new Date().toISOString()
    };
  }

  return {
    command_type: command.command_type,
    statusText: partialMissingCount > 0
      ? `${label}完成但有缺失：已按接口查询 ${ids.length} 个 SPU（${effectivePeriods.join("、")}），缺失 ${partialMissingCount} 个返回行，缺失项不会进入建议下架`
      : `${label}完成：已按接口查询 ${ids.length} 个 SPU（${effectivePeriods.join("、")}），响应 ${records.length} 条`,
    total_ids: ids.length,
    queried_ids: queriedCount,
    chunk_size: chunkSize,
    periods: effectivePeriods,
    matched_count: records.length,
    partial_missing_count: partialMissingCount,
    records,
    batches,
    mode: "direct_page_fetch",
    capturedAt: new Date().toISOString()
  };
}

function periodToTimeDimension(periodText) {
  if (periodText === "近7日") return 4;
  if (periodText === "近30日") return 5;
  return null;
}

function chunkIds(ids, size) {
  const safeSize = Math.max(1, Number(size || 1));
  const chunks = [];
  for (let start = 0; start < ids.length; start += safeSize) {
    chunks.push(ids.slice(start, start + safeSize));
  }
  return chunks;
}

function nextFluxFallbackSize(currentSize) {
  if (currentSize > 50) return 50;
  if (currentSize > 20) return 20;
  return 0;
}

async function fetchFluxAnalysisInPage(tabId, ids, timeDimension, requestTemplate = null) {
  return executeMainWorld(tabId, [ids, timeDimension, requestTemplate], async (queryIds, queryTimeDimension, templateJson) => {
    const endpoint = "/api/flow/analysis/list";
    const productIdList = queryIds.map((item) => {
      const text = String(item || "").trim();
      return /^\d+$/.test(text) ? Number(text) : text;
    });
    const utils = window.WorkbenchNetworkProbeUtils;
    const requestJson = utils?.buildSafeFlowAnalysisRequest
      ? utils.buildSafeFlowAnalysisRequest(templateJson || {}, {
        pageNumber: 1,
        pageSize: 100,
        productIdList,
        timeDimension: Number(queryTimeDimension)
      })
      : {
        ...(templateJson && typeof templateJson === "object" && !Array.isArray(templateJson) ? templateJson : {}),
        pageNumber: 1,
        pageSize: 100,
        productIdList,
        timeDimension: Number(queryTimeDimension)
      };
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(requestJson)
      });
      const contentType = response.headers.get("content-type") || "";
      const text = await response.text();
      let responseJson = null;
      try {
        responseJson = JSON.parse(text);
      } catch (_error) {
        responseJson = null;
      }
      return {
        ok: true,
        url: new URL(endpoint, location.origin).href,
        method: "POST",
        status: response.status,
        contentType,
        requestJson,
        responseJson,
        responseText: responseJson ? "" : text.slice(0, 1024 * 1024),
        capturedAt: new Date().toISOString()
      };
    } catch (error) {
      return {
        ok: false,
        url: new URL(endpoint, location.origin).href,
        method: "POST",
        status: 0,
        contentType: "",
        requestJson,
        responseText: String(error?.message || error),
        capturedAt: new Date().toISOString()
      };
    }
  }, { attempts: 2 });
}

async function fetchFluxAllPagesInPage(tabId, timeDimension, requestTemplate = null) {
  return executeMainWorld(tabId, [timeDimension, requestTemplate], async (queryTimeDimension, templateJson) => {
    const endpoint = "/api/flow/analysis/list";
    const utils = window.WorkbenchNetworkProbeUtils;
    const pageSize = 100;
    const maxPages = 100;
    const allItems = [];
    let firstRequestJson = null;
    let total = null;
    let pageCount = 0;

    const extractItems = (responseJson) => {
      const result = responseJson?.result && typeof responseJson.result === "object" ? responseJson.result : {};
      const candidates = [
        result.pageItems,
        result.items,
        result.list,
        result.records,
        responseJson?.pageItems,
        responseJson?.items,
        responseJson?.list,
        responseJson?.records
      ];
      for (const candidate of candidates) {
        if (Array.isArray(candidate)) {
          return candidate.filter((item) => item && typeof item === "object");
        }
      }
      return [];
    };

    for (let pageNumber = 1; pageNumber <= maxPages; pageNumber += 1) {
      const requestJson = utils?.buildSafeFlowAnalysisRequest
        ? utils.buildSafeFlowAnalysisRequest(templateJson || {}, {
          pageNumber,
          pageSize,
          productIdList: [],
          timeDimension: Number(queryTimeDimension)
        })
        : {
          ...(templateJson && typeof templateJson === "object" && !Array.isArray(templateJson) ? templateJson : {}),
          pageNumber,
          pageSize,
          timeDimension: Number(queryTimeDimension)
        };
      delete requestJson.productIdList;
      if (!firstRequestJson) firstRequestJson = requestJson;
      try {
        const response = await fetch(endpoint, {
          method: "POST",
          credentials: "include",
          headers: { "content-type": "application/json" },
          body: JSON.stringify(requestJson)
        });
        const contentType = response.headers.get("content-type") || "";
        const text = await response.text();
        let responseJson = null;
        try {
          responseJson = JSON.parse(text);
        } catch (_error) {
          responseJson = null;
        }
        if (!response.ok) {
          return {
            ok: false,
            url: new URL(endpoint, location.origin).href,
            method: "POST",
            status: response.status,
            contentType,
            requestJson,
            responseJson,
            responseText: responseJson ? "" : text.slice(0, 1024 * 1024),
            page_count: pageCount,
            mode: "period_full_scan",
            capturedAt: new Date().toISOString()
          };
        }
        if (!responseJson || (responseJson.errorCode && Number(responseJson.errorCode) !== 1000000)) {
          return {
            ok: false,
            url: new URL(endpoint, location.origin).href,
            method: "POST",
            status: response.status,
            contentType,
            requestJson,
            responseJson,
            responseText: responseJson ? "" : text.slice(0, 1024 * 1024),
            page_count: pageCount,
            mode: "period_full_scan",
            capturedAt: new Date().toISOString()
          };
        }
        const items = extractItems(responseJson);
        allItems.push(...items);
        pageCount = pageNumber;
        const result = responseJson.result && typeof responseJson.result === "object" ? responseJson.result : {};
        const possibleTotal = Number(result.total ?? result.totalCount ?? result.totalRecords ?? responseJson.total ?? 0);
        if (Number.isFinite(possibleTotal) && possibleTotal > 0) total = possibleTotal;
        if (items.length === 0 || items.length < pageSize || (total && allItems.length >= total)) {
          return {
            ok: true,
            url: new URL(endpoint, location.origin).href,
            method: "POST",
            status: response.status,
            contentType,
            requestJson: firstRequestJson || requestJson,
            responseJson: {
              errorCode: 1000000,
              errorMsg: null,
              result: {
                pageItems: allItems,
                total: total || allItems.length
              }
            },
            page_count: pageCount,
            mode: "period_full_scan",
            capturedAt: new Date().toISOString()
          };
        }
      } catch (error) {
        return {
          ok: false,
          url: new URL(endpoint, location.origin).href,
          method: "POST",
          status: 0,
          contentType: "",
          requestJson: firstRequestJson,
          responseText: String(error?.message || error),
          page_count: pageCount,
          mode: "period_full_scan",
          capturedAt: new Date().toISOString()
        };
      }
    }
    return {
      ok: true,
      url: new URL(endpoint, location.origin).href,
      method: "POST",
      status: 200,
      contentType: "application/json",
      requestJson: firstRequestJson,
      responseJson: {
        errorCode: 1000000,
        errorMsg: null,
        result: {
          pageItems: allItems,
          total: total || allItems.length
        }
      },
      page_count: pageCount,
      mode: "period_full_scan",
      capturedAt: new Date().toISOString()
    };
  }, { attempts: 2 });
}

function buildFluxApiRecord(apiResult, batchIds, periodText, timeDimension) {
  const url = apiResult?.url || `${FLUX_PAGE_URL.replace(/\/main\/.*$/, "")}/api/flow/analysis/list`;
  const record = {
    captureType: "temu_flux_by_spu",
    url,
    endpoint: "/api/flow/analysis/list",
    method: apiResult?.method || "POST",
    status: Number(apiResult?.status || 0),
    contentType: apiResult?.contentType || "application/json",
    requestJson: apiResult?.requestJson || {
      pageNumber: 1,
      pageSize: 100,
      productIdList: batchIds,
      timeDimension
    },
    responseJson: apiResult?.responseJson || undefined,
    responseText: apiResult?.responseText || undefined,
    capturedAt: apiResult?.capturedAt || new Date().toISOString(),
    queryPeriod: periodText,
    queryIds: batchIds
  };
  record.requestVerification = verifyCapturedRequest(record, periodText, batchIds.length);
  return record;
}

async function runBatchDelistCommand(command, { execute }) {
  const items = Array.isArray(command.payload?.items) ? command.payload.items : [];
  const skcs = normalizeIds(command.payload?.skcs || items.map((item) => item?.skc));
  if (skcs.length === 0) {
    return {
      command_type: command.command_type,
      statusText: "批量下架失败：没有已审批的 SKC",
      error: "empty_skcs",
      item_results: [],
      capturedAt: new Date().toISOString()
    };
  }

  const tab = await findOrOpenBusinessTab(GOODS_PAGE_URL);
  await waitForTabReady(tab.id, 20000);
  const ready = await waitForPageText(tab.id, ["商品列表"], 20000);
  if (!ready.ok) {
    return {
      command_type: command.command_type,
      statusText: "批量下架失败：商品列表页未加载完成",
      error: "goods_page_not_ready",
      textSample: ready.textSample || "",
      screenshot: await captureTabScreenshot(),
      item_results: items.map((item) => failedItemResult(item, "商品列表页未加载完成")),
      capturedAt: new Date().toISOString()
    };
  }

  const action = await fillGoodsSkcQueryAndMaybeDelist(tab.id, skcs, { execute });
  const screenshot = await captureTabScreenshot();
  const itemResults = items.map((item) => ({
    item_id: item.item_id,
    skc: item.skc,
    status: action.ok ? (execute ? "succeeded" : "prepared") : "failed",
    error: action.ok ? "" : (action.error || "批量下架页面动作失败")
  }));

  return {
    command_type: command.command_type,
    statusText: action.ok
      ? (execute ? `批量下架已提交：${skcs.length} 个 SKC` : `已打开批量下架确认前页面：${skcs.length} 个 SKC，等待员工人工核对并提交`)
      : `批量下架失败：${action.error || "页面动作失败"}`,
    error: action.ok ? undefined : "batch_delist_failed",
    task_id: command.payload?.task_id,
    total_items: skcs.length,
    skcs,
    action,
    item_results: itemResults,
    screenshot,
    capturedAt: new Date().toISOString()
  };
}

async function runIdQueryCommand(baseUrl, sessionToken, command, options) {
  const ids = normalizeIds(command.payload?.ids || command.payload?.query_ids || []);
  const chunkSize = Math.max(1, Math.min(Number(command.payload?.chunk_size || command.payload?.max_ids || 80), 100));
  const waitMs = Math.max(6000, Math.min(Number(command.payload?.wait_ms || 10000), 30000));
  const batchDelayMs = Math.max(800, Math.min(Number(command.payload?.batch_delay_ms || 2200), 10000));
  const periods = normalizePeriods(command.payload?.periods || command.payload?.period || []);
  const effectivePeriods = periods.length > 0 ? periods : [""];
  if (ids.length === 0) {
    return {
      command_type: command.command_type,
      statusText: `${options.label}失败：没有可查询的 ${options.idLabel} ID`,
      error: "empty_ids",
      matched_count: 0,
      records: [],
      capturedAt: new Date().toISOString()
    };
  }

  const tab = await findOrOpenBusinessTab(options.pageUrl);
  await waitForTabReady(tab.id, 20000);
  await injectNetworkProbe(tab.id);
  const progress = {
    command_type: command.command_type,
    statusText: `${options.label}已打开页面，正在等待查询区加载`,
    total_ids: ids.length,
    queried_ids: 0,
    chunk_size: chunkSize,
    batch_delay_ms: batchDelayMs,
    periods: effectivePeriods.filter(Boolean),
    matched_count: 0,
    mode: "native_page_query",
    batches: [],
    capturedAt: new Date().toISOString()
  };
  const publishProgress = async (statusText) => {
    progress.statusText = statusText;
    progress.capturedAt = new Date().toISOString();
    await postResult(baseUrl, sessionToken, command.id, "running", progress);
  };
  await publishProgress(`${options.label}正在加载页面`);
  const readyTexts = Array.isArray(options.requiredTexts) && options.requiredTexts.length
    ? options.requiredTexts
    : ["查询"];
  const ready = await waitForPageText(tab.id, readyTexts, 20000);
  if (!ready.ok) {
    return {
      command_type: command.command_type,
      statusText: `${options.label}失败：页面没有加载出查询区`,
      error: "query_button_not_ready",
      help: options.inputHelp,
      requiredTexts: readyTexts,
      textSample: ready.textSample || "",
      matched_count: 0,
      records: [],
      capturedAt: new Date().toISOString()
    };
  }

  const batches = [];
    const records = [];
    let queriedCount = 0;
    let failedBatch = null;
    const maxRetries = Math.max(0, Math.min(Number(command.payload?.max_retries ?? 2), 3));

    for (const periodText of effectivePeriods) {
    await publishProgress(periodText ? `${options.label}正在等待${periodText}时间范围和查询按钮` : `${options.label}正在准备分批查询`);
    if (periodText) {
      const periodReady = await waitForPageText(tab.id, [periodText, "查询"], 20000);
      if (!periodReady.ok) {
        failedBatch = {
          ok: false,
          error: `没有找到 ${periodText} 或查询按钮`,
          help: options.inputHelp,
          textSample: periodReady.textSample || ""
        };
        break;
      }
    }

    const pendingBatches = chunkIds(ids, chunkSize);
    while (pendingBatches.length > 0) {
      const batchIds = pendingBatches.shift();
      const batchNumber = batches.length + 1;
      const totalBatches = batches.length + pendingBatches.length + 1;
      let action = null;
      let batchRecords = [];
      let retryCount = 0;
      for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
        retryCount = attempt;
        const attemptText = attempt === 0 ? "" : `，第 ${attempt + 1} 次尝试`;
        await publishProgress(`${options.label}第 ${batchNumber}/${totalBatches} 批查询中：${batchIds.length} 个 ${options.idLabel}${periodText ? `（${periodText}）` : ""}${attemptText}`);
        const since = new Date(Date.now() - 1000).toISOString();
        action = await fillDefaultIdQueryAndClick(tab.id, batchIds, periodText, options.idLabel, options.targetPageSize || 0, options.preQueryStatusTab || "");
        if (!action.ok) {
          const actionError = describePageActionError(action, "没有找到默认查询输入框");
          failedBatch = {
            period: periodText,
            batch_index: batches.length + 1,
            ids: batchIds,
            action,
            error: actionError
          };
          break;
        }
        if (periodText && action.periodSelection && action.periodSelection.selected === false) {
          failedBatch = {
            period: periodText,
            batch_index: batches.length + 1,
            ids: batchIds,
            action,
            error: `页面没有确认选中 ${periodText}`
          };
          break;
        }
        if (Number(options.targetPageSize || 0) > 0 && batchIds.length > 30 && action.pageSizeSelection?.ok === false) {
          failedBatch = {
            period: periodText,
            batch_index: batches.length + 1,
            ids: batchIds,
            action,
            error: `没有把页面每页条数切换到 ${options.targetPageSize}：${action.pageSizeSelection.error || "未知原因"}`
          };
          break;
        }
        await delay(waitMs);
        const rawBatchRecords = await getProbeCaptures(tab.id, options.captureType, since, 80);
        batchRecords = filterBatchRecordsForCommand(rawBatchRecords, {
          captureType: options.captureType,
          periodText,
          batchIds
        });
        const hasSuccess = batchRecords.some((record) => isSuccessfulBusinessRecord(record));
        const hasRateLimit = batchRecords.some((record) => isRateLimitedRecord(record));
        const hasForbidden = batchRecords.some((record) => Number(record?.status || 0) === 403);
        if (hasForbidden && attempt < maxRetries) {
          await publishProgress(`${options.label}第 ${batchNumber}/${totalBatches} 批返回 HTTP 403，正在刷新 TEMU 流量页并重试`);
          await chrome.tabs.reload(tab.id);
          await waitForTabReady(tab.id, 25000);
          await injectNetworkProbe(tab.id);
          await delay(Math.max(5000, waitMs));
          continue;
        }
        if (hasSuccess || !hasRateLimit || attempt >= maxRetries) {
          break;
        }
        await publishProgress(`${options.label}第 ${batchNumber}/${totalBatches} 批触发平台限流，等待后自动重试`);
        await delay(Math.max(15000, waitMs));
      }
      if (failedBatch) break;
      const requestVerification = verifyCapturedRequestBatch(batchRecords, {
        captureType: options.captureType,
        periodText,
        batchSize: batchIds.length
      });
      if (!requestVerification.ok) {
        failedBatch = {
          period: periodText,
          batch_index: batches.length + 1,
          ids: batchIds,
          action,
          request_verification: requestVerification,
          error: requestVerification.error
        };
        break;
      }
      const successfulRecords = isFlowCaptureType(options.captureType)
        ? batchRecords.filter((record) => isSuccessfulBusinessRecord(record))
        : batchRecords;
      if (isFlowCaptureType(options.captureType)) {
        const coverage = verifyFluxRecordsCoverage(batchRecords, batchIds);
        const fallbackSize = nextFluxFallbackSize(batchIds.length);
        if (!coverage.ok && fallbackSize) {
          pendingBatches.unshift(...chunkIds(coverage.missing_ids, fallbackSize));
          for (const record of successfulRecords) {
            records.push({
              ...record,
              queryPeriod: periodText || undefined,
              queryIds: batchIds,
              requestVerification: verifyCapturedRequest(record, periodText, batchIds.length)
            });
          }
          batches.push({
            period: periodText || undefined,
            batch_index: batchNumber,
            queried_ids: batchIds.length,
            matched_count: batchRecords.length,
            retry_count: retryCount,
            rate_limited: batchRecords.some((record) => isRateLimitedRecord(record)),
            request_verification: requestVerification,
            coverage,
            status: "partial_missing_retry_split",
            next_chunk_size: fallbackSize,
            missing_ids_preview: coverage.missing_ids.slice(0, 20),
            error: coverage.error || firstBusinessError(batchRecords) || "partial traffic response; retrying missing SPU in smaller batches",
            action
          });
          queriedCount += batchIds.length;
          progress.queried_ids = queriedCount;
          progress.matched_count = records.length;
          progress.batches = batches;
          await publishProgress(`${options.label}第 ${batchNumber}/${totalBatches} 批只返回部分数据，正在用每批 ${fallbackSize} 个 SPU 重试缺失的 ${coverage.missing_count} 个`);
          await delay(batchDelayMs);
          continue;
        }
      }
      queriedCount += batchIds.length;
      progress.queried_ids = queriedCount;
      for (const record of successfulRecords) {
        records.push({
          ...record,
          queryPeriod: periodText || undefined,
          queryIds: batchIds,
          requestVerification: verifyCapturedRequest(record, periodText, batchIds.length)
        });
      }
      batches.push({
        period: periodText || undefined,
        batch_index: batchNumber,
        queried_ids: batchIds.length,
        matched_count: batchRecords.length,
        retry_count: retryCount,
        rate_limited: batchRecords.some((record) => isRateLimitedRecord(record)),
        request_verification: requestVerification,
        coverage: isFlowCaptureType(options.captureType) ? verifyFluxRecordsCoverage(batchRecords, batchIds) : undefined,
        action
      });
      progress.queried_ids = queriedCount;
      progress.matched_count = records.length;
      progress.batches = batches;
      await publishProgress(`${options.label}第 ${batchNumber}/${totalBatches} 批完成：累计查询 ${queriedCount}/${ids.length}，捕获 ${records.length} 条接口响应`);
      await delay(batchDelayMs);
    }
    if (failedBatch) break;
  }

  if (failedBatch) {
    return {
      command_type: command.command_type,
      statusText: `${options.label}中断：${failedBatch.error}`,
      error: "batch_query_failed",
      failed_batch: failedBatch,
      help: options.inputHelp,
      total_ids: ids.length,
      queried_ids: queriedCount,
      chunk_size: chunkSize,
      batch_delay_ms: batchDelayMs,
      periods: effectivePeriods.filter(Boolean),
      matched_count: records.length,
      mode: "native_page_query",
      records,
      batches,
      capturedAt: new Date().toISOString()
    };
  }

  return {
    command_type: command.command_type,
    statusText: records.length > 0
      ? `${options.label}完成：已分批查询 ${ids.length} 个 ${options.idLabel}${periods.length ? `（${periods.join("、")}）` : ""}，捕获 ${records.length} 条接口响应`
      : `${options.label}已点击查询，但没有捕获到接口响应`,
    total_ids: ids.length,
    queried_ids: queriedCount,
    chunk_size: chunkSize,
    batch_delay_ms: batchDelayMs,
    periods: effectivePeriods.filter(Boolean),
    matched_count: records.length,
    mode: "native_page_query",
    records,
    batches,
    capturedAt: new Date().toISOString()
  };
}

async function runOldProductHealthCheck(baseUrl, sessionToken, command) {
  const steps = [
    createStep("open_flux", "打开商品流量页", "running", "正在打开 TEMU 商品流量页"),
    createStep("flux_7d", "采集近7日流量", "pending", "等待执行"),
    createStep("flux_30d", "采集近30日流量", "pending", "等待执行"),
    createStep("goods", "采集商品列表", "pending", "后续自动化入口待固化"),
    createStep("orders", "采集订单列表", "pending", "后续自动化入口待固化")
  ];
  const result = {
    command_type: command.command_type,
    statusText: "旧品体检已开始",
    steps,
    captures: {},
    safe_mode: "readonly",
    capturedAt: new Date().toISOString()
  };

  const publish = async () => {
    result.capturedAt = new Date().toISOString();
    await postResult(baseUrl, sessionToken, command.id, "running", result);
  };

  await publish();
  const fluxTab = await findOrOpenBusinessTab(FLUX_PAGE_URL);
  await waitForTabReady(fluxTab.id, 20000);
  await injectNetworkProbe(fluxTab.id);
  steps[0] = createStep("open_flux", "打开商品流量页", "succeeded", "已进入商品流量页");
  result.statusText = "商品流量页已打开";
  await publish();

  result.captures.flux_7d = await collectFluxPeriod({
    tabId: fluxTab.id,
    stepId: "flux_7d",
    periodText: "近7日",
    stepIndex: 1,
    steps,
    result,
    publish
  });

  result.captures.flux_30d = await collectFluxPeriod({
    tabId: fluxTab.id,
    stepId: "flux_30d",
    periodText: "近30日",
    stepIndex: 2,
    steps,
    result,
    publish
  });

  result.captures.goods = await collectGoodsList({ stepIndex: 3, steps, result, publish });

  steps[4] = createStep(
    "orders",
    "采集订单列表",
    "blocked",
    "订单页真实入口和接口仍需确认",
    "后续会自动打开订单列表页并采集订单商品信号。现在不会误判为已完成。"
  );
  result.statusText = "旧品体检采集已完成，订单自动入口待固化";
  result.summary = summarizeHealthCheck(result);
  result.capturedAt = new Date().toISOString();
  return result;
}

async function collectGoodsList({ stepIndex, steps, result, publish }) {
  steps[stepIndex] = createStep("goods", "采集商品列表", "running", "正在打开商品列表页并点击查询");
  result.statusText = "正在采集商品列表";
  await publish();

  const goodsTab = await findOrOpenBusinessTab(GOODS_PAGE_URL);
  await waitForTabReady(goodsTab.id, 20000);
  await injectNetworkProbe(goodsTab.id);
  const since = new Date(Date.now() - 120000).toISOString();
  const action = await clickQuery(goodsTab.id);
  if (!action.ok) {
    steps[stepIndex] = createStep(
      "goods",
      "采集商品列表",
      "failed",
      action.error || "没有找到商品列表查询按钮",
      "请确认 TEMU 已登录、站点为美国站，并且当前页面在“商品管理 > 商品列表”。"
    );
    await publish();
    return { matched_count: 0, records: [], action };
  }

  await delay(10000);
  const records = await getProbeCaptures(goodsTab.id, "capture_temu_goods", since, 50);
  const matchedCount = records.length;
  steps[stepIndex] = createStep(
    "goods",
    "采集商品列表",
    matchedCount > 0 ? "succeeded" : "blocked",
    matchedCount > 0 ? `已采集 ${matchedCount} 条商品列表接口响应` : "已点击查询，但没有捕获到商品列表接口响应",
    matchedCount > 0 ? "" : "请确认商品列表有内容并已刷新；如页面要求重新登录或切换美国站，请处理后重试。"
  );
  await publish();
  return { matched_count: matchedCount, records, action, capturedAt: new Date().toISOString() };
}

async function collectFluxPeriod({ tabId, stepId, periodText, stepIndex, steps, result, publish }) {
  steps[stepIndex] = createStep(stepId, `采集${periodText}流量`, "running", `正在切换到${periodText}并点击查询`);
  result.statusText = `正在采集${periodText}流量`;
  await publish();

  const ready = await waitForPageText(tabId, [periodText, "查询"], 20000);
  if (!ready.ok) {
    steps[stepIndex] = createStep(
      stepId,
      `采集${periodText}流量`,
      "failed",
      `商品流量页未加载出 ${periodText} 或查询按钮`,
      `请确认 TEMU 页面已登录且没有验证码/弹窗。页面文本片段：${ready.textSample || "空"}`
    );
    await publish();
    return { matched_count: 0, records: [], action: ready };
  }

  const since = new Date(Date.now() - 15000).toISOString();
  const action = await clickFluxPeriodAndQuery(tabId, periodText);
  if (!action.ok) {
    steps[stepIndex] = createStep(
      stepId,
      `采集${periodText}流量`,
      "failed",
      action.error || `没有找到${periodText}或查询按钮`,
      "请确认当前店铺站点是美国站，并且页面在“经营分析 > 流量分析 > 商品流量”。"
    );
    await publish();
    return { matched_count: 0, records: [], action };
  }

  await delay(10000);
  const records = await getProbeCaptures(tabId, "capture_temu_flux", since, 50);
  const matchedCount = records.length;
  steps[stepIndex] = createStep(
    stepId,
    `采集${periodText}流量`,
    matchedCount > 0 ? "succeeded" : "blocked",
    matchedCount > 0 ? `已采集 ${matchedCount} 条接口响应` : "已点击查询，但没有捕获到流量接口响应",
    matchedCount > 0 ? "" : "请在页面确认商品流量表格已刷新；如果页面要求重新登录或验证码，请处理后重试。"
  );
  await publish();
  return { matched_count: matchedCount, records, action, capturedAt: new Date().toISOString() };
}

function createStep(id, label, status, message, help = "") {
  return { id, label, status, message, help, updatedAt: new Date().toISOString() };
}

function describePageActionError(action, fallback) {
  const base = action?.error || fallback;
  const diagnostic = action?.diagnostic || {};
  const parts = [];
  if (diagnostic.title) parts.push(`标题：${diagnostic.title}`);
  if (diagnostic.url) parts.push(`地址：${diagnostic.url}`);
  if (diagnostic.readyState) parts.push(`加载状态：${diagnostic.readyState}`);
  return parts.length ? `${base}（${parts.join("，")}）` : base;
}

function summarizeHealthCheck(result) {
  const steps = result.steps || [];
  return {
    succeeded: steps.filter((step) => step.status === "succeeded").length,
    blocked: steps.filter((step) => step.status === "blocked").length,
    failed: steps.filter((step) => step.status === "failed").length,
    flux_7d_records: result.captures?.flux_7d?.matched_count || 0,
    flux_30d_records: result.captures?.flux_30d?.matched_count || 0,
    goods_records: result.captures?.goods?.matched_count || 0
  };
}

function responseContainsPriceReviewItems(value, depth = 0) {
  if (!value || depth > 8) return false;
  if (Array.isArray(value)) {
    return value.some((item) => responseContainsPriceReviewItems(item, depth + 1));
  }
  if (typeof value !== "object") return false;
  if (Array.isArray(value.priceReviewItemList) && value.priceReviewItemList.length > 0) return true;
  return Object.values(value).some((item) => responseContainsPriceReviewItems(item, depth + 1));
}

function isPriceQuoteDiscoveryNetworkRecord(record) {
  const endpointText = `${record?.endpoint || ""} ${record?.url || ""}`.toLowerCase();
  if (/price\/review|price\/re-price-review|bargain-no-bom\/batch\/info\/query|priceorder\/query|recommendedprice\.query/.test(endpointText)) return true;
  if (/action=bg\.(?:semi\.price\.review\.page\.query\.order|local\.goods\.priceorder\.query|glo\.product\.search)/.test(endpointText)) return true;
  return responseContainsPriceReviewItems(record?.responseJson);
}

async function runTemuPriceQuoteDiscoveryCommand(command) {
  const tab = await findPriceQuoteBusinessTab();
  await waitForTabReady(tab.id, 20000);
  await injectNetworkProbe(tab.id);
  const lookbackMs = Math.max(0, Math.min(Number(command.payload?.lookback_ms || 15000), 10 * 60 * 1000));
  const waitMs = Math.max(0, Math.min(Number(command.payload?.wait_ms || 3000), 30000));
  const limit = Math.max(10, Math.min(Number(command.payload?.limit || 80), 120));
  const targetPageSize = Math.max(0, Math.min(Number(command.payload?.target_page_size || 100), 100));
  const since = new Date(Date.now() - lookbackMs).toISOString();
  const batchPopupAction = command.payload?.auto_open_batch_popup === false
    ? { ok: true, skipped: true, statusText: "已跳过自动打开批量核价弹窗" }
    : await openPriceQuoteBatchDialog(tab.id, { targetPageSize });
  if (waitMs > 0) await delay(waitMs);
  const rawRecords = await getProbeCaptures(tab.id, "temu_price_quote_discovery", since, limit);
  const records = rawRecords.filter(isPriceQuoteDiscoveryNetworkRecord);
  const allowLifecycleDomFallback = command.payload?.allow_lifecycle_dom_fallback !== false
    && batchPopupAction?.ok === false
    && records.length === 0;
  const requirePriceDialog = command.payload?.auto_open_batch_popup !== false && !allowLifecycleDomFallback;
  const dom = await extractPriceQuoteDomSnapshot(tab.id, { requirePriceDialog, rowLimit: PRICE_QUOTE_DOM_ROW_LIMIT });
  if (dom.row_truncated) {
    return {
      ok: false,
      error: "price_quote_dom_row_limit_exceeded",
      statusText: dom.truncation_error,
      dom,
    };
  }
  const matchedCount = records.length + (dom.rows?.length || 0);
  return {
    ok: true,
    command_type: "temu_price_quote_discovery",
    statusText: matchedCount > 0
      ? `核价只读采集完成：接口 ${records.length} 条，Seller Central 页面/弹窗表格 ${dom.rows?.length || 0} 行${batchPopupAction?.pageSizeSelection?.ok === false ? "（每页100未确认，平台可能只返回当前页部分商品）" : ""}${allowLifecycleDomFallback ? "（已使用列表页基础信息兜底）" : ""}`
      : "核价只读采集完成，但没有识别到核价接口或 Seller Central 页面/弹窗表格",
    matched_count: matchedCount,
    page: {
      url: tab.url || "",
      title: tab.title || "",
      selected_reason: tab.selectedReason || ""
    },
    captures: {
      network: {
        matched_count: records.length,
        records
      }
    },
    dom,
    actions: {
      batch_price_popup: {
        ...batchPopupAction,
        lifecycle_dom_fallback: allowLifecycleDomFallback,
        require_price_dialog_for_dom: requirePriceDialog,
      }
    },
    open_api: {
      status: "not_configured",
      read_only_candidates: [
        "bg.semi.price.review.page.query.order",
        "bg.local.goods.priceorder.query",
        "bg.glo.product.search",
        "temu.local.goods.recommendedprice.query"
      ],
      excluded_write_actions: [
        "bg.local.goods.priceorder.accept",
        "bg.local.goods.priceorder.negotiate",
        "temu.local.goods.priceorder.reject",
        "bg.semi.price.review.confirm.order",
        "bg.semi.price.review.reject.order"
      ]
    },
    safety: {
      read_only: true,
      no_submit_clicks: true,
      no_headers_or_cookies: true
    },
    capturedAt: new Date().toISOString()
  };
}

async function captureCurrentTemuPriceQuotePage(sourceTab) {
  const tab = sourceTab?.id ? sourceTab : await getActiveBusinessTab({ allowAny: true });
  if (!tab?.id || !/temu\.com/i.test(String(tab.url || ""))) {
    return { ok: false, error: "not_temu_quote_page", statusText: "请在 Temu 核价页面采集" };
  }
  const connection = await readConnectionContext();
  if (!connection) {
    return { ok: false, error: "missing_plugin_session", statusText: "插件未连接工作台" };
  }
  const baseUrl = connection.http_base;
  if (!isAllowedWorkbenchUrl(baseUrl)) {
    return { ok: false, error: "unsupported_workbench_url", statusText: "工作台地址不受支持" };
  }

  await waitForTabReady(tab.id, 20000);
  await injectNetworkProbe(tab.id);
  const since = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  const rawRecords = await getProbeCaptures(tab.id, "temu_price_quote_discovery", since, 50);
  const records = rawRecords.filter(isPriceQuoteDiscoveryNetworkRecord).slice(0, 50);
  const dom = await extractPriceQuoteDomSnapshot(tab.id, { requirePriceDialog: false, rowLimit: PRICE_QUOTE_DOM_ROW_LIMIT });
  if (dom.row_truncated) {
    return {
      ok: false,
      error: "price_quote_dom_row_limit_exceeded",
      statusText: dom.truncation_error,
      help: "请缩小当前页展示数量后重新采集，插件不会静默丢弃超限 SKU。",
    };
  }
  const capture = {
    captures: { network: { matched_count: records.length, records } },
    dom,
    page: { url: tab.url || "", title: tab.title || "" },
    safety: { read_only: true, direct_current_page: true, no_submit_clicks: true },
    capturedAt: new Date().toISOString()
  };
  if (!records.length && !dom.rows.length) {
    return { ok: false, error: "quote_page_not_recognized", statusText: "未识别到当前页核价数据，请确认页面已加载完成" };
  }

  const response = await fetch(workbenchHttpUrl(baseUrl, "/plugin/price-verification/capture-batches/current/chunks"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_token: connection.session_token, page_url: tab.url || "", capture })
  });
  if (!response.ok) {
    if ([401, 403].includes(response.status)) await clearConnectionState();
    const errorText = await readWorkbenchError(response);
    return {
      ok: false,
      error: "price_quote_capture_failed",
      statusText: errorText ? `核价入库失败：${errorText.slice(0, 40)}` : "核价入库失败",
      help: errorText
    };
  }
  const payload = await response.json();
  if (payload.tenant_context !== undefined) {
    tenantContext.assertServerTenantContext(connection, payload.tenant_context);
  }
  return {
    ok: true,
    command_type: "temu_price_quote_direct_capture",
    item_count: Number(payload?.chunk?.item_count || 0),
    batch_id: payload?.batch?.batch_id || "",
    statusText: payload?.message || `核价本页已入库：${Number(payload?.chunk?.item_count || 0)} 条`,
    capturedAt: capture.capturedAt
  };
}

async function runSourceBrowserImageSearchCommand(baseUrl, sessionToken, command) {
  const payload = command.payload || {};
  const tasks = Array.isArray(payload.tasks) ? payload.tasks.slice(0, SOURCE_IMAGE_SEARCH_TASK_LIMIT) : [];
  const maxCandidates = Math.max(1, Math.min(Number(payload.max_candidates_per_quote || SOURCE_IMAGE_SEARCH_CANDIDATE_LIMIT), 20));
  const detailCandidateLimit = Math.max(0, Math.min(Number(payload.detail_candidates_per_quote || payload.detailCandidateLimit || TEMAISHUJU_DETAIL_CAPTURE_LIMIT), TEMAISHUJU_DETAIL_CAPTURE_LIMIT));
  const detailSkuValidation = payload.capture_strategy === "source_detail_sku_validation";
  const temaishujuBackground = !detailSkuValidation && (payload.capture_strategy === "temaishuju_background_image_search" || payload.provider === "temaishuju");
  const autoAssistantSidebar = payload.capture_strategy === "auto_1688_assistant_sidebar" || payload.auto_open_sidebar === true;
  const sidebarOnly = !temaishujuBackground && (autoAssistantSidebar || payload.capture_strategy === "active_1688_assistant_sidebar" || payload.single_quote_mode === true);
  const sourceTaskWorkerCount = resolveSourceTaskWorkerCount(payload, tasks.length, temaishujuBackground || detailSkuValidation);
  const startedAt = new Date().toISOString();
  const result = {
    command_type: "source_browser_image_search",
    mode: detailSkuValidation ? "source_detail_sku_validation" : (temaishujuBackground ? "temaishuju_background_image_search" : (sidebarOnly ? (autoAssistantSidebar ? "auto_1688_assistant_sidebar" : "active_1688_assistant_sidebar") : "browser_1688_image_search")),
    statusText: temaishujuBackground ? "准备后台打开特卖数据图搜" : (sidebarOnly ? (autoAssistantSidebar ? "准备自动触发 1688 助手图搜" : "准备读取当前 1688 助手侧栏") : "准备执行 1688 浏览器图搜"),
    items: [],
    counts: sourceBrowserCounts([], tasks.length),
    safety: {
      read_only: true,
      no_product_draft_write: true,
      no_supplier_write_actions: true,
      no_api_token: true
    },
    startedAt,
    capturedAt: startedAt,
    source_task_worker_count: sourceTaskWorkerCount
  };
  if (!tasks.length) {
    return {
      ...result,
      error: "missing_source_search_tasks",
      statusText: "没有可执行的图搜任务：请先完成核价采集并确认有完整主图"
    };
  }

  sourceBrowserActiveResults.set(command.id, result);

  const publishProgress = async (statusText) => {
    if (sourceBrowserCancelledCommands.has(command.id)) return;
    result.statusText = statusText;
    result.counts = sourceBrowserCounts(result.items, tasks.length);
    result.capturedAt = new Date().toISOString();
    try {
      await postResult(baseUrl, sessionToken, command.id, "running", attachSourceContractsToResult(result));
    } catch (error) {
      warnWorkbench("source browser progress post failed; task continues", error);
    }
  };
  await publishProgress(temaishujuBackground ? `已创建特卖数据后台找货源：0/${tasks.length}` : (sidebarOnly ? (autoAssistantSidebar ? `已创建单品自动图搜：0/${tasks.length}` : `已创建单品侧栏采集：0/${tasks.length}`) : `已创建图搜队列：0/${tasks.length}`));

  const runPreparedTemaishujuSourceTask = async (workerTabId, task, index) => {
    let item;
    const partialKey = `${command.id}:${task.quote_key}`;
    try {
      const taskPromise = detailSkuValidation
        ? runSingleSourceDetailSkuValidationTask(workerTabId || null, task, {
          taskIndex: index + 1,
          totalTasks: tasks.length
        })
        : runSingleTemaishujuImageSearchTask(workerTabId || null, task, {
          maxCandidates,
          detailCandidateLimit,
          taskIndex: index + 1,
          totalTasks: tasks.length,
          partialKey
        });
      item = await withTimeout(
        taskPromise,
        SOURCE_TEMAISHUJU_SINGLE_TASK_TIMEOUT_MS,
        "source_single_search_timeout"
      );
    } catch (error) {
      if (isSourceSearchTimeoutError(error)) {
        const partialItem = sourceBrowserPartialItems.get(partialKey);
        item = temaishujuBackground && partialItem
          ? sourceBrowserPartialTimeoutItem(task, partialItem, error)
          : sourceBrowserManualChallengeItem(task, error, "source_single_search_timeout", detailSkuValidation ? "SKU 详情验证超时：请稍后重试这条商品" : "特卖数据后台找货源超时：请稍后重试这条商品");
      } else {
        item = sourceBrowserFailedItem(task, error, detailSkuValidation ? "source_detail_sku_validation_failed" : "temaishuju_search_task_failed");
      }
    }
    sourceBrowserPartialItems.delete(partialKey);
    return item;
  };

  let tab = null;
  let temaishujuWindowId = null;
  try {
    if (temaishujuBackground || detailSkuValidation) {
      const opened = await openTemaishujuBackgroundWindow();
      tab = opened.tab;
      temaishujuWindowId = opened.windowId;
    } else if (!sidebarOnly) {
      tab = await findOrOpenSourceImageSearchTab(SOURCE_IMAGE_SEARCH_PAGE_URL);
    }
    let sourceConcurrentHandled = false;
    if ((temaishujuBackground || detailSkuValidation) && sourceTaskWorkerCount > 1) {
      sourceConcurrentHandled = true;
      const workers = [{ tab, windowId: temaishujuWindowId, workerIndex: 0 }];
      temaishujuWindowId = null;
      let nextTaskIndex = 0;
      let stopWorkers = false;
      try {
        for (let workerIndex = 1; workerIndex < sourceTaskWorkerCount; workerIndex += 1) {
          await delay(SOURCE_TEMAISHUJU_TASK_WORKER_STAGGER_MS);
          const opened = await openTemaishujuBackgroundWindow();
          workers.push({ tab: opened.tab, windowId: opened.windowId, workerIndex });
        }
        await publishProgress(`后台并发 ${sourceTaskWorkerCount} 路匹配货源：0/${tasks.length}`);
        const workerTasks = workers.map(async (worker) => {
          await delay(worker.workerIndex * SOURCE_TEMAISHUJU_TASK_WORKER_STAGGER_MS);
          while (!stopWorkers) {
            if (sourceBrowserCancelledCommands.has(command.id)) {
              stopWorkers = true;
              break;
            }
            const index = nextTaskIndex;
            nextTaskIndex += 1;
            if (index >= tasks.length) break;
            const task = normalizeSourceImageSearchTask(tasks[index], index);
            await publishProgress(`${detailSkuValidation ? "SKU 验证" : "后台找货源"} worker ${worker.workerIndex + 1}/${sourceTaskWorkerCount}：${index + 1}/${tasks.length} ${task.skc_id || task.product_title || task.quote_key}`);
            const item = await runPreparedTemaishujuSourceTask(worker.tab?.id || null, task, index);
            result.items.push(item);
            if (item.status === "manual_challenge" && item.risk_control_state?.blocked) {
              stopWorkers = true;
              result.error = "source_search_manual_challenge";
              result.statusText = detailSkuValidation ? "SKU 详情验证出现访问限制，已暂停队列" : "特卖数据页面出现访问限制，已暂停后台找货源";
              break;
            }
            await publishProgress(`后台并发已处理 ${result.items.length}/${tasks.length}，候选 ${sourceBrowserCounts(result.items, tasks.length).candidate_count}`);
            await delay(900 + Math.floor(Math.random() * 900));
          }
        });
        await Promise.all(workerTasks);
      } finally {
        for (const worker of workers) {
          if (worker.windowId) {
            try {
              await chrome.windows.remove(worker.windowId);
            } catch (_error) {
              // The background window may already be closed by the operator.
            }
          }
        }
      }
    }
    if (!sourceConcurrentHandled) {
    for (let index = 0; index < tasks.length; index += 1) {
      if (sourceBrowserCancelledCommands.has(command.id)) break;
      const task = normalizeSourceImageSearchTask(tasks[index], index);
      await publishProgress(temaishujuBackground
        ? `后台寻找货源 ${index + 1}/${tasks.length}：${task.skc_id || task.product_title || task.quote_key}`
        : (sidebarOnly
          ? (autoAssistantSidebar
            ? `正在自动触发 1688 图搜 ${index + 1}/${tasks.length}：${task.skc_id || task.product_title || task.quote_key}`
            : `正在读取侧栏 ${index + 1}/${tasks.length}：${task.skc_id || task.product_title || task.quote_key}`)
          : `正在图搜 ${index + 1}/${tasks.length}：${task.skc_id || task.product_title || task.quote_key}`));
      if (!sidebarOnly && !temaishujuBackground && !detailSkuValidation) {
        tab = await ensureSourceImageSearchTab(tab, SOURCE_IMAGE_SEARCH_PAGE_URL);
      }
      let item;
      const partialKey = `${command.id}:${task.quote_key}`;
      try {
        const taskPromise = detailSkuValidation
          ? runSingleSourceDetailSkuValidationTask(tab?.id || null, task, {
            taskIndex: index + 1,
            totalTasks: tasks.length
          })
          : (temaishujuBackground
          ? runSingleTemaishujuImageSearchTask(tab?.id || null, task, {
            maxCandidates,
            detailCandidateLimit,
            taskIndex: index + 1,
            totalTasks: tasks.length,
            partialKey
          })
          : runSingleSourceImageSearchTask(tab?.id || null, task, {
            maxCandidates,
            taskIndex: index + 1,
            totalTasks: tasks.length,
            sidebarOnly,
            autoAssistantSidebar,
            preferAssistantSidebar: true
          }));
        item = await withTimeout(
          taskPromise,
          (temaishujuBackground || detailSkuValidation) ? SOURCE_TEMAISHUJU_SINGLE_TASK_TIMEOUT_MS : SOURCE_ASSISTANT_COMMAND_TIMEOUT_MS,
          "source_single_search_timeout"
        );
      } catch (error) {
        if (isSourceSearchTimeoutError(error)) {
          const partialItem = sourceBrowserPartialItems.get(partialKey);
          item = temaishujuBackground && partialItem
            ? sourceBrowserPartialTimeoutItem(task, partialItem, error)
            : sourceBrowserManualChallengeItem(task, error, "source_single_search_timeout", temaishujuBackground ? "特卖数据后台找货源超时：请稍后重试这条商品" : "自动图搜单品超时：请确认 1688 助手侧栏是否已弹出，或稍后重试这条商品");
        } else if (!sidebarOnly && !temaishujuBackground && isMissingChromeTabError(error)) {
          tab = await findOrOpenSourceImageSearchTab(SOURCE_IMAGE_SEARCH_PAGE_URL, { forceNew: true });
          try {
            item = await withTimeout(
              runSingleSourceImageSearchTask(tab.id, task, {
                maxCandidates,
                taskIndex: index + 1,
                totalTasks: tasks.length,
                sidebarOnly: false,
                preferAssistantSidebar: true
              }),
              SOURCE_ASSISTANT_COMMAND_TIMEOUT_MS,
              "source_single_search_timeout"
            );
            item.recovered_from = "source_search_tab_recreated";
          } catch (retryError) {
            item = isSourceSearchTimeoutError(retryError)
              ? sourceBrowserManualChallengeItem(task, retryError, "source_single_search_timeout", "自动图搜重试超时：请确认 1688 图搜侧栏是否能正常打开")
              : sourceBrowserFailedItem(task, retryError, "source_search_tab_recreate_failed");
          }
        } else {
          item = sourceBrowserFailedItem(task, error, temaishujuBackground ? "temaishuju_search_task_failed" : "source_search_task_failed");
        }
      }
      sourceBrowserPartialItems.delete(partialKey);
      result.items.push(item);
      if (item.status === "manual_challenge" && item.risk_control_state?.blocked) {
        result.error = "source_search_manual_challenge";
        result.statusText = temaishujuBackground ? "特卖数据页面出现访问限制，已暂停后台找货源" : "1688 出现访问限制，已暂停图搜队列";
        break;
      }
      await publishProgress(`${temaishujuBackground ? "后台已处理" : "已处理"} ${result.items.length}/${tasks.length}，候选 ${sourceBrowserCounts(result.items, tasks.length).candidate_count}`);
      await delay(900 + Math.floor(Math.random() * 900));
    }
    }
  } finally {
    if (temaishujuWindowId) {
      try {
        await chrome.windows.remove(temaishujuWindowId);
      } catch (_error) {
        // The background window may already be closed by the operator.
      }
    }
    sourceBrowserActiveResults.delete(command.id);
    sourceBrowserCancelledCommands.delete(command.id);
  }

  result.counts = sourceBrowserCounts(result.items, tasks.length);
  result.statusText = result.error
    ? result.statusText
    : temaishujuBackground
      ? `后台找货源完成：处理 ${result.counts.processed_quotes}/${tasks.length}，命中货源 ${result.counts.matched_quotes}，需人工 ${result.counts.manual_challenge_quotes}`
      : sidebarOnly
      ? `${autoAssistantSidebar ? "自动图搜" : "侧栏采集"}完成：处理 ${result.counts.processed_quotes}/${tasks.length}，命中货源 ${result.counts.matched_quotes}，需人工 ${result.counts.manual_challenge_quotes}`
      : `图搜完成：处理 ${result.counts.processed_quotes}/${tasks.length}，命中货源 ${result.counts.matched_quotes}，需人工 ${result.counts.manual_challenge_quotes}`;
  result.capturedAt = new Date().toISOString();
  return attachSourceContractsToResult(result);
}

function isMissingChromeTabError(error) {
  const message = String(error?.message || error || "");
  return /No tab with id|Tabs cannot be edited|Cannot access.*tab|Invalid tab ID|tab was closed/i.test(message);
}

async function ensureSourceImageSearchTab(tab, fallbackUrl) {
  if (tab?.id) {
    try {
      await chrome.tabs.get(tab.id);
      return tab;
    } catch (error) {
      if (!isMissingChromeTabError(error)) throw error;
    }
  }
  return findOrOpenSourceImageSearchTab(fallbackUrl, { forceNew: true });
}

function sourceBrowserFailedItem(task, error, reason) {
  return {
    quote_key: task.quote_key,
    source_match_key: task.source_match_key || "",
    source_quote_keys: Array.isArray(task.source_quote_keys) ? task.source_quote_keys : [],
    source_quote_count: task.source_quote_count || (Array.isArray(task.source_quote_keys) ? task.source_quote_keys.length : 0),
    skc_id: task.skc_id,
    sku_id: task.sku_id,
    spu_or_goods_id: task.spu_or_goods_id,
    site: task.site,
    product_title: task.product_title,
    main_image_url: task.main_image_url,
    status: "failed",
    error: reason || "source_search_task_failed",
    statusText: `1688 image search failed: ${String(error?.message || error || "unknown_error").slice(0, 180)}`,
    manual_search_url: task.manual_search_url || buildSourceKeywordSearchUrl(task.product_title),
    source_page_url: "",
    candidates: [],
    captured_at: new Date().toISOString()
  };
}

function sourceBrowserManualChallengeItem(task, error, reason, statusText) {
  return {
    quote_key: task.quote_key,
    source_match_key: task.source_match_key || "",
    source_quote_keys: Array.isArray(task.source_quote_keys) ? task.source_quote_keys : [],
    source_quote_count: task.source_quote_count || (Array.isArray(task.source_quote_keys) ? task.source_quote_keys.length : 0),
    skc_id: task.skc_id,
    sku_id: task.sku_id,
    spu_or_goods_id: task.spu_or_goods_id,
    site: task.site,
    product_title: task.product_title,
    main_image_url: task.main_image_url,
    status: "manual_challenge",
    error: reason || "source_search_manual_challenge",
    statusText: statusText || `自动图搜需要人工确认：${String(error?.message || error || "unknown_error").slice(0, 160)}`,
    help: "插件只会触发当前商品主图旁的 1688 图搜入口并读取侧栏。若侧栏未弹出、出现验证/访问限制，保留人工接管，不做绕过。",
    manual_search_url: task.manual_search_url || buildSourceKeywordSearchUrl(task.product_title),
    source_page_url: "",
    candidates: [],
    captured_at: new Date().toISOString()
  };
}

function sourceBrowserPartialTimeoutItem(task, partialItem, error) {
  const candidates = Array.isArray(partialItem?.candidates) ? partialItem.candidates : [];
  return {
    ...sourceBrowserManualChallengeItem(
      task,
      error,
      "source_single_search_timeout",
      candidates.length
        ? `已识别 ${candidates.length} 个候选，详情补采超时；先保留候选，稍后可重试补采价格/运费/SKU`
        : "自动图搜单品超时：未能保留候选，请稍后重试这条商品"
    ),
    ...(partialItem || {}),
    status: candidates.length ? "partial_detail_timeout" : "manual_challenge",
    error: candidates.length ? "source_detail_capture_timeout" : "source_single_search_timeout",
    statusText: candidates.length
      ? `已识别 ${candidates.length} 个候选，详情补采超时；先保留候选，稍后可重试补采价格/运费/SKU`
      : "自动图搜单品超时：未能保留候选，请稍后重试这条商品",
    candidates,
    partial_result: candidates.length > 0,
    timeout_error: String(error?.message || error || "source_single_search_timeout").slice(0, 180),
    captured_at: new Date().toISOString()
  };
}

function isSourceSearchTimeoutError(error) {
  return /timeout|timed out|source_single_search_timeout|source_browser_image_search_command_timeout|page_script_timeout|assistant_image_search_trigger_timeout/i.test(String(error?.message || error || ""));
}

function sourceBrowserCommandTimeoutResult(command, error, partialResult = null) {
  const payload = command?.payload || {};
  const rawTasks = Array.isArray(payload.tasks) ? payload.tasks.slice(0, SOURCE_IMAGE_SEARCH_TASK_LIMIT) : [];
  const tasks = rawTasks.map((rawTask, index) => normalizeSourceImageSearchTask(rawTask, index));
  const partialItems = Array.isArray(partialResult?.items) ? partialResult.items.filter((item) => item && typeof item === "object") : [];
  const completedKeys = new Set(partialItems.map((item) => String(item.quote_key || item.skc_id || "")));
  const timeoutItems = tasks.filter((task) => !completedKeys.has(String(task.quote_key || task.skc_id || ""))).map((task) => ({
    ...sourceBrowserManualChallengeItem(
      task,
      error,
      "source_browser_image_search_command_timeout",
      "自动图搜命令超时：已停止等待本次执行，请重试单条商品或确认 1688 助手侧栏是否被验证/页面限制卡住"
    ),
    synthetic_timeout: true
  }));
  const items = [...partialItems, ...timeoutItems];
  const capturedAt = new Date().toISOString();
  return attachSourceContractsToResult({
    command_type: "source_browser_image_search",
    mode: payload.capture_strategy === "temaishuju_background_image_search" || payload.provider === "temaishuju"
      ? "temaishuju_background_image_search"
      : (payload.capture_strategy === "auto_1688_assistant_sidebar" || payload.auto_open_sidebar === true
        ? "auto_1688_assistant_sidebar"
        : "browser_1688_image_search"),
    status: "failed",
    error: "source_browser_image_search_command_timeout",
    statusText: "自动图搜命令超时：插件已回传可见状态，避免页面一直显示运行中",
    items,
    counts: sourceBrowserCounts(items, tasks.length),
    safety: {
      read_only: true,
      no_product_draft_write: true,
      no_supplier_write_actions: true,
      no_api_token: true
    },
    capturedAt
  });
}

function sourceBrowserCommandAbortedResult(command, error, partialResult = null) {
  const payload = command?.payload || {};
  const rawTasks = Array.isArray(payload.tasks) ? payload.tasks.slice(0, SOURCE_IMAGE_SEARCH_TASK_LIMIT) : [];
  const partialItems = Array.isArray(partialResult?.items) ? partialResult.items.filter((item) => item && typeof item === "object") : [];
  const capturedAt = new Date().toISOString();
  return attachSourceContractsToResult({
    ...(partialResult && typeof partialResult === "object" ? partialResult : {}),
    command_type: "source_browser_image_search",
    mode: payload.capture_strategy === "temaishuju_background_image_search" || payload.provider === "temaishuju"
      ? "temaishuju_background_image_search"
      : (payload.capture_strategy === "auto_1688_assistant_sidebar" || payload.auto_open_sidebar === true
        ? "auto_1688_assistant_sidebar"
        : "browser_1688_image_search"),
    status: "failed",
    error: "source_browser_command_aborted",
    statusText: `自动图搜被非超时异常中断，已保留 ${partialItems.length}/${rawTasks.length} 条阶段结果：${String(error?.message || error || "unknown_error").slice(0, 140)}`,
    items: partialItems,
    counts: sourceBrowserCounts(partialItems, rawTasks.length),
    safety: {
      read_only: true,
      no_product_draft_write: true,
      no_supplier_write_actions: true,
      no_api_token: true
    },
    capturedAt
  });
}

function normalizeSourceImageSearchTask(rawTask, index) {
  const text = (value) => String(value ?? "").replace(/\s+/g, " ").trim();
  const imageList = (value) => {
    const rawItems = Array.isArray(value)
      ? value
      : (value && typeof value === "object"
        ? Object.values(value)
        : String(value || "").split(/[\s,;|]+/));
    const output = [];
    const seen = new Set();
    for (const item of rawItems) {
      const url = normalizeHttpUrl(item);
      if (!url || seen.has(url)) continue;
      seen.add(url);
      output.push(url);
      if (output.length >= 8) break;
    }
    return output;
  };
  const rawMainImageUrl = normalizeHttpUrl(rawTask?.main_image_url || rawTask?.image_search_input_url);
  const targetImageUrls = [];
  for (const url of [
    rawMainImageUrl,
    ...imageList(rawTask?.target_image_urls),
    ...imageList(rawTask?.product_image_urls),
    ...imageList(rawTask?.extra_image_urls)
  ]) {
    if (url && !targetImageUrls.includes(url)) targetImageUrls.push(url);
    if (targetImageUrls.length >= 8) break;
  }
  const mainImageUrl = rawMainImageUrl || targetImageUrls[0] || "";
  const quoteKey = text(rawTask?.quote_key) || [rawTask?.skc_id, rawTask?.sku_id, rawTask?.spu_or_goods_id, rawTask?.site]
    .map(text)
    .map((item) => item || "-")
    .join("|");
  return {
    quote_key: quoteKey || `quote-${index + 1}`,
    source_match_key: text(rawTask?.source_match_key),
    source_quote_keys: Array.isArray(rawTask?.source_quote_keys) ? rawTask.source_quote_keys.map(text).filter(Boolean).slice(0, 80) : [],
    source_quote_count: Number(rawTask?.source_quote_count || 0) || 0,
    skc_id: text(rawTask?.skc_id),
    sku_id: text(rawTask?.sku_id),
    spu_or_goods_id: text(rawTask?.spu_or_goods_id),
    site: text(rawTask?.site),
    product_title: text(rawTask?.product_title),
    main_image_url: mainImageUrl,
    image_search_input_url: rawMainImageUrl || mainImageUrl,
    extra_image_urls: targetImageUrls.filter((url) => url !== mainImageUrl).slice(0, 8),
    target_image_urls: targetImageUrls.slice(0, 8),
    original_declared_price_cny: rawTask?.original_declared_price_cny ?? null,
    adjusted_declared_price_cny: rawTask?.adjusted_declared_price_cny ?? null,
    manual_search_url: text(rawTask?.manual_search_url),
    offer_id: text(rawTask?.offer_id),
    source_url: normalizeHttpUrl(rawTask?.source_url || rawTask?.detail_url),
    source_title: text(rawTask?.source_title),
    candidate_main_image_url: normalizeHttpUrl(rawTask?.candidate_main_image_url || rawTask?.source_main_image_url),
    candidate_list_rank: Number(rawTask?.candidate_list_rank || rawTask?.list_rank || 0) || 0,
    selected_variant: rawTask?.selected_variant && typeof rawTask.selected_variant === "object" ? rawTask.selected_variant : {},
    source_variant_records: Array.isArray(rawTask?.source_variant_records) ? rawTask.source_variant_records.slice(0, 20) : [],
    source_product_identity_ai_status: text(rawTask?.source_product_identity_ai_status),
    source_product_identity_ai_confidence: text(rawTask?.source_product_identity_ai_confidence),
    source_product_identity_ai_reason: text(rawTask?.source_product_identity_ai_reason),
    source_product_identity_ai_model: text(rawTask?.source_product_identity_ai_model)
  };
}

function buildTemaishujuImageSearchUrl(imageUrl) {
  const normalized = normalizeHttpUrl(imageUrl);
  const url = new URL(TEMAISHUJU_IMAGE_SEARCH_PAGE_URL);
  if (normalized) url.searchParams.set("url", normalized);
  url.searchParams.set("from", "url");
  return url.href;
}

async function openTemaishujuBackgroundWindow() {
  const created = await chrome.windows.create({
    url: "about:blank",
    focused: false,
    type: "popup",
    width: TEMAISHUJU_BACKGROUND_WINDOW_WIDTH,
    height: TEMAISHUJU_BACKGROUND_WINDOW_HEIGHT
  });
  const tab = (created.tabs || [])[0];
  if (!created.id || !tab?.id) {
    throw new Error("temaishuju_background_window_not_created");
  }
  return { windowId: created.id, tab };
}

function resolveSourceTaskWorkerCount(payload, totalTasks, enabled) {
  if (!enabled || Number(totalTasks || 0) <= 1) return 1;
  const requested = Number(payload?.source_task_worker_count || payload?.task_worker_count || payload?.concurrent_tasks || SOURCE_TEMAISHUJU_TASK_WORKER_DEFAULT);
  const clamped = Math.max(1, Math.min(Number.isFinite(requested) ? Math.floor(requested) : SOURCE_TEMAISHUJU_TASK_WORKER_DEFAULT, SOURCE_TEMAISHUJU_TASK_WORKER_MAX));
  return Math.min(clamped, Number(totalTasks || 1));
}

function temaishujuCaptureStillLoading(capture) {
  if (!capture || (capture.candidates || []).length > 0 || capture.manual_challenge) return false;
  const sample = [
    capture.error,
    capture.statusText,
    capture.header_text_sample,
    capture.page_title
  ].map((value) => String(value || "")).join(" ");
  return /temaishuju_search_loading|\u6b63\u5728\u56fe\u641c\u540c\u6b3e|\u6b63\u5728[^\n]{0,12}\u56fe\u641c|loading/i.test(sample);
}

async function runSingleTemaishujuImageSearchTask(tabId, task, options = {}) {
  const capturedAt = new Date().toISOString();
  const searchUrl = buildTemaishujuImageSearchUrl(task.main_image_url);
  const baseItem = {
    quote_key: task.quote_key,
    skc_id: task.skc_id,
    sku_id: task.sku_id,
    spu_or_goods_id: task.spu_or_goods_id,
    site: task.site,
    product_title: task.product_title,
    main_image_url: task.main_image_url,
    status: "failed",
    statusText: "",
    manual_search_url: searchUrl,
    source_page_url: searchUrl,
    candidates: [],
    captured_at: capturedAt
  };
  if (!tabId) {
    return {
      ...baseItem,
      error: "missing_temaishuju_background_tab",
      statusText: "没有创建特卖数据后台采集标签"
    };
  }
  if (!task.main_image_url) {
    return {
      ...baseItem,
      error: "missing_main_image_url",
      statusText: "缺少主图，不能执行特卖数据图搜"
    };
  }

  await chrome.tabs.update(tabId, { url: searchUrl, active: false });
  await waitForTabReady(tabId, 30000);
  await delay(TEMAISHUJU_IMAGE_SEARCH_WAIT_MS);
  let tab = await chrome.tabs.get(tabId);
  let capture = await captureTemaishujuImageSearchCandidatesFromTab(tab, maxCandidatesFromOptions(options));
  const resultDeadline = Date.now() + TEMAISHUJU_IMAGE_SEARCH_RESULT_WAIT_MS;
  while (temaishujuCaptureStillLoading(capture) && Date.now() < resultDeadline) {
    await delay(3000);
    tab = await chrome.tabs.get(tabId);
    capture = await captureTemaishujuImageSearchCandidatesFromTab(tab, maxCandidatesFromOptions(options));
  }
  if (temaishujuCaptureStillLoading(capture)) {
    return {
      ...baseItem,
      status: "manual_challenge",
      error: "source_single_search_timeout",
      statusText: "特卖数据图搜仍在加载：请稍后重试这条商品",
      help: "页面还停留在“正在图搜同款”，本次不把它记为无货源。",
      source_page_url: capture.source_page_url || tab.url || searchUrl,
      manual_search_url: capture.source_page_url || tab.url || searchUrl,
      temaishuju_state: capture
    };
  }
  if (capture.manual_challenge) {
    return {
      ...baseItem,
      status: "manual_challenge",
      error: capture.error || "temaishuju_manual_challenge",
      statusText: capture.statusText || "特卖数据页面需要人工处理",
      help: capture.help || "请确认现有 Edge 已登录特卖数据，并且页面没有验证码或访问限制。",
      source_page_url: capture.source_page_url || tab.url || searchUrl,
      risk_control_state: capture.risk_control_state || null,
      temaishuju_state: capture
    };
  }
  if ((capture.candidates || []).length && options.partialKey) {
    sourceBrowserPartialItems.set(options.partialKey, {
      ...baseItem,
      status: "partial_detail_pending",
      error: "detail_capture_pending",
      statusText: `已识别 ${(capture.candidates || []).length} 个候选，正在补采详情价格/运费/SKU`,
      source_page_url: capture.source_page_url || tab.url || searchUrl,
      manual_search_url: capture.source_page_url || tab.url || searchUrl,
      candidates: capture.candidates || [],
      temaishuju_state: {
        ...capture,
        detail_capture: { status: "pending", attempted_count: 0, enriched_count: 0 }
      },
      partial_result: true,
      captured_at: new Date().toISOString()
    });
  }
  const detailCapture = await enrichTemaishujuCandidatesWithDetailEvidence(tabId, capture.candidates || [], { ...options, task });
  const candidates = detailCapture.candidates || capture.candidates || [];
  return {
    ...baseItem,
    status: candidates.length ? "succeeded" : "no_results",
    statusText: candidates.length ? `特卖数据识别到 ${candidates.length} 个候选` : "特卖数据图搜没有识别到可用候选",
    source_page_url: capture.source_page_url || tab.url || searchUrl,
    manual_search_url: capture.source_page_url || tab.url || searchUrl,
    candidates,
    temaishuju_state: {
      ...capture,
      detail_capture: detailCapture.state || {}
    },
    captured_at: new Date().toISOString()
  };
}

async function runSingleSourceDetailSkuValidationTask(tabId, task, options = {}) {
  const capturedAt = new Date().toISOString();
  const detailUrl = canonicalProductCaptureUrl(task.source_url || "", task.offer_id || productIdFromCaptureUrl(task.source_url || ""));
  const baseItem = {
    quote_key: task.quote_key,
    source_match_key: task.source_match_key || "",
    source_quote_keys: Array.isArray(task.source_quote_keys) ? task.source_quote_keys : [],
    source_quote_count: task.source_quote_count || (Array.isArray(task.source_quote_keys) ? task.source_quote_keys.length : 0),
    skc_id: task.skc_id,
    sku_id: task.sku_id,
    spu_or_goods_id: task.spu_or_goods_id,
    site: task.site,
    product_title: task.product_title,
    main_image_url: task.main_image_url,
    extra_image_urls: task.extra_image_urls || [],
    target_image_urls: task.target_image_urls || [task.main_image_url].filter(Boolean),
    status: "failed",
    statusText: "",
    manual_search_url: detailUrl,
    source_page_url: detailUrl,
    candidates: [],
    captured_at: capturedAt
  };
  if (!tabId) {
    return { ...baseItem, error: "missing_source_detail_validation_tab", statusText: "Missing background tab for SKU validation" };
  }
  if (!detailUrl) {
    return { ...baseItem, error: "missing_source_detail_url", statusText: "Missing 1688 detail URL for SKU validation" };
  }

  await chrome.tabs.update(tabId, { url: detailUrl, active: false });
  await waitForTabReady(tabId, TEMAISHUJU_DETAIL_READY_TIMEOUT_MS);
  await delay(TEMAISHUJU_DETAIL_LOAD_WAIT_MS);
  const detail = await capture1688OfferDetailEvidenceFromTab(tabId, detailUrl, {
    product_title: task.product_title,
    main_image_url: task.main_image_url,
    candidate_main_image_url: task.candidate_main_image_url,
    candidate_title: task.source_title,
    candidate_list_rank: task.candidate_list_rank,
    selected_variant: task.selected_variant,
    source_variant_records: task.source_variant_records,
    force_selected_variant: true,
    validation_strategy: "source_detail_sku_validation"
  });
  const candidate = mergeTemaishujuDetailEvidence({
    candidate_id: task.offer_id || detailUrl,
    offer_id: task.offer_id || productIdFromCaptureUrl(detailUrl),
    source_url: detailUrl,
    source_title: task.source_title || task.product_title || "",
    title: task.source_title || task.product_title || "",
    main_image_url: task.candidate_main_image_url || "",
    image_url: task.candidate_main_image_url || "",
    list_rank: task.candidate_list_rank || options.taskIndex || 1,
    capture_method: "source_detail_sku_validation",
    source_platform: "1688",
    source_product_identity_ai_status: task.source_product_identity_ai_status || "same_product",
    source_product_identity_ai_variant_match: true,
    source_product_identity_ai_matched_variant_index: task.selected_variant?.matched_variant_index ?? null,
    source_product_identity_ai_matched_variant_text: task.selected_variant?.matched_variant_text || "",
    source_product_identity_ai_matched_variant_image_url: task.selected_variant?.matched_variant_image_url || "",
    source_product_identity_ai_matched_variant_price_text: task.selected_variant?.matched_variant_price_text || "",
    source_product_identity_ai_matched_variant_price_cny: task.selected_variant?.matched_variant_price_cny ?? null,
    source_product_identity_ai_matched_variant_selected: task.selected_variant?.matched_variant_selected ?? null,
    source_product_identity_ai_confidence: task.source_product_identity_ai_confidence || "",
    source_product_identity_ai_reason: task.source_product_identity_ai_reason || "verified by previous SKU-level product identity judge",
    source_product_identity_ai_model: task.source_product_identity_ai_model || "",
    raw_payload: {
      source_detail_sku_validation: true,
      selected_variant: task.selected_variant || {},
      source_variant_records: Array.isArray(task.source_variant_records) ? task.source_variant_records.slice(0, 20) : []
    }
  }, detail);
  return {
    ...baseItem,
    status: detail?.ok ? "succeeded" : "failed",
    error: detail?.ok ? "" : (detail?.error || "source_detail_sku_validation_failed"),
    statusText: detail?.ok ? "SKU validation captured" : "SKU validation failed",
    source_page_url: detailUrl,
    manual_search_url: detailUrl,
    candidates: detail?.ok ? [candidate] : [],
    detail_validation: detail,
    captured_at: new Date().toISOString()
  };
}

async function captureTemaishujuImageSearchCandidatesFromTab(tab, limit) {
  if (!tab?.id) return { ok: false, error: "missing_tab", candidates: [] };
  const candidateLimit = Math.max(1, Math.min(Number(limit || SOURCE_IMAGE_SEARCH_CANDIDATE_LIMIT), 20));
  const captured = await executeMainWorld(tab.id, [candidateLimit], async (candidateLimit) => {
    const now = new Date().toISOString();
    const text = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const absUrl = (url) => {
      url = text(url);
      if (!url || /^data:|^blob:/i.test(url)) return "";
      if (url.startsWith("//")) return location.protocol + url;
      try {
        return new URL(url, location.href).href;
      } catch (_error) {
        return "";
      }
    };
    const visible = (element) => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 4 && rect.height > 4;
    };
    const srcsetLast = (value) => text(value).split(",").map((part) => part.trim().split(/\s+/)[0]).filter(Boolean).pop() || "";
    const imageUrl = (image) => absUrl(image.currentSrc || image.src || image.getAttribute("data-src") || image.getAttribute("src") || srcsetLast(image.getAttribute("srcset")));
    const offerIdFromValue = (value) => {
      const raw = decodeURIComponent(String(value || ""));
      const match = raw.match(/(?:offerId|offer_id|offer\/|\/offer\/|detail\.1688\.com\/offer\/)(\d{6,})/i);
      return match ? match[1] : "";
    };
    const canonical1688Url = (value) => {
      const offerId = offerIdFromValue(value);
      if (offerId) return `https://detail.1688.com/offer/${offerId}.html`;
      try {
        const parsed = new URL(String(value || ""), location.href);
        if (/1688\.com$/i.test(parsed.hostname)) {
          parsed.search = "";
          parsed.hash = "";
          return parsed.href;
        }
      } catch (_error) {
        // Fall through and return a cleaned raw value.
      }
      return text(value).replace(/&amp;/g, "&");
    };
    const sourceUrlFromCard = (card) => {
      const values = [];
      const push = (value) => {
        value = text(value);
        if (value) values.push(value);
      };
      const links = [];
      if (card.closest?.("a[href]")) links.push(card.closest("a[href]"));
      links.push(...Array.from(card.querySelectorAll("a[href]")));
      links.forEach((link) => push(absUrl(link.getAttribute("href"))));
      Array.from(card.querySelectorAll("*")).slice(0, 80).forEach((node) => {
        for (const attr of Array.from(node.attributes || [])) {
          if (/href|url|link|offer|target|data/i.test(attr.name)) push(attr.value);
        }
      });
      for (const value of values) {
        const decoded = decodeURIComponent(value);
        const detailMatch = decoded.match(/https?:\/\/detail\.1688\.com\/offer\/\d+\.html[^"' <)]*/i);
        if (detailMatch) return canonical1688Url(detailMatch[0]);
        const genericMatch = decoded.match(/https?:\/\/[^"' <)]*1688\.com[^"' <)]*offer[^"' <)]*/i);
        if (genericMatch) return canonical1688Url(genericMatch[0]);
      }
      const offerId = values.map(offerIdFromValue).find(Boolean);
      return offerId ? `https://detail.1688.com/offer/${offerId}.html` : "";
    };
    const priceText = (value) => {
      const match = text(value).match(/[¥￥]\s*\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?/);
      return match ? match[0].replace(/\s+/g, "") : "";
    };
    const salesText = (value) => {
      const match = text(value).match(/(?:已售出|销量|成交|售出)\s*[\d,.万wWkK]+\s*(?:件|单|笔)?/);
      return match ? match[0] : "";
    };
    const repurchaseText = (value) => {
      const match = text(value).match(/复购率[:：]?\s*[\d.]+%/);
      return match ? match[0] : "";
    };
    const badgeText = (value) => {
      const match = text(value).match(/精选货源|优选货源|精选|爆款|同款|官方/);
      return match ? match[0] : "";
    };
    const titleFromCard = (cardText, image) => {
      let title = text(image.getAttribute("alt") || image.getAttribute("title"));
      if (!title || /^[\d¥￥.\s-]+$/.test(title)) {
        title = cardText
          .replace(/[¥￥]\s*\d+(?:\.\d+)?(?:\s*[-~至]\s*\d+(?:\.\d+)?)?/g, " ")
          .replace(/(?:已售出|销量|成交|售出)\s*[\d,.万wWkK]+\s*(?:件|单|笔)?/g, " ")
          .replace(/复购率[:：]?\s*[\d.]+%/g, " ")
          .replace(/精选货源|优选货源|精选|1688官网|1688|拼多多批发|拼多多|淘宝|Temu|Shein|Shopee|亚马逊|速卖通|Alibaba国际站/g, " ")
          .replace(/\s+/g, " ")
          .trim();
      }
      return title.slice(0, 160);
    };
    const pageText = text(document.body?.innerText || document.documentElement?.innerText || "");
    if (/登录|验证码|安全验证|访问受限|请先登录|扫码登录/.test(pageText) && !/[¥￥]\s*\d/.test(pageText)) {
      return {
        ok: false,
        manual_challenge: true,
        error: "temaishuju_login_or_challenge",
        statusText: "特卖数据页面需要登录或验证",
        help: "请在现有 Edge 里处理特卖数据登录/验证后重试。",
        source_page_url: location.href,
        page_title: document.title,
        candidates: []
      };
    }

    const headerText = pageText.slice(0, 900);
    const images = Array.from(document.querySelectorAll("img"))
      .filter(visible)
      .filter((image) => {
        const rect = image.getBoundingClientRect();
        return rect.width >= 100 && rect.height >= 100 && imageUrl(image);
      });
    const cards = [];
    const seenCard = new Set();
    for (const image of images) {
      let node = image;
      for (let depth = 0; node && depth < 8; depth += 1, node = node.parentElement) {
        const rect = node.getBoundingClientRect();
        const cardText = text(node.innerText || node.textContent);
        if (
          rect.width >= 160 && rect.width <= 420
          && rect.height >= 210 && rect.height <= 620
          && /[¥￥]\s*\d/.test(cardText)
          && (/1688|已售出|复购率|精选货源|货源/.test(cardText) || sourceUrlFromCard(node))
        ) {
          if (!seenCard.has(node)) {
            seenCard.add(node);
            cards.push({ card: node, image });
          }
          break;
        }
      }
    }

    const candidates = [];
    const excluded = [];
    const seen = new Set();
    for (const { card, image } of cards) {
      const rawText = text(card.innerText || card.textContent);
      const sourceUrl = sourceUrlFromCard(card);
      const offerId = offerIdFromValue(sourceUrl) || offerIdFromValue(rawText);
      const title = titleFromCard(rawText, image);
      const price = priceText(rawText);
      const img = imageUrl(image);
      const key = offerId ? `offer:${offerId}` : (sourceUrl ? `url:${sourceUrl.replace(/[?#].*$/, "")}` : `img:${img}`);
      const reason = !sourceUrl
        ? "missing_source_url"
        : (!title ? "missing_title" : (!price ? "missing_price" : ""));
      if (reason) {
        excluded.push({ reason, title, source_url: sourceUrl, image_url: img });
        continue;
      }
      if (seen.has(key)) {
        excluded.push({ reason: "duplicate_candidate", title, source_url: sourceUrl, image_url: img });
        continue;
      }
      seen.add(key);
      const rect = card.getBoundingClientRect();
      candidates.push({
        candidate_id: offerId || sourceUrl || `${title}-${candidates.length + 1}`,
        source_platform: "1688",
        offer_id: offerId,
        source_url: sourceUrl,
        source_title: title,
        main_image_url: img,
        price_text: price,
        sales_text: salesText(rawText),
        source_badge: badgeText(rawText),
        repurchase_rate_text: repurchaseText(rawText),
        search_page_url: location.href,
        list_rank: candidates.length + 1,
        capture_method: "temaishuju_image_search",
        captured_at: now,
        raw_payload: {
          list_card_text: rawText.slice(0, 900),
          card_rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
          temaishuju_search_url: location.href,
          page_title: document.title
        }
      });
      if (candidates.length >= candidateLimit) break;
    }

    return {
      ok: candidates.length > 0,
      error: candidates.length ? "" : "temaishuju_candidates_not_found",
      statusText: candidates.length ? `已读取 ${candidates.length} 个特卖数据候选` : "特卖数据页面已打开，但没有识别到候选卡片",
      source_page_url: location.href,
      page_url: location.href,
      page_title: document.title,
      header_text_sample: headerText,
      card_count: cards.length,
      excluded_candidate_count: excluded.length,
      excluded_candidates: excluded.slice(0, 20),
      candidates
    };
  }, { attempts: 1, timeoutMs: DEFAULT_MAIN_WORLD_SCRIPT_TIMEOUT_MS });
  return captured || { ok: false, error: "temaishuju_script_no_result", candidates: [] };
}

async function enrichTemaishujuCandidatesWithDetailEvidence(tabId, candidates, options = {}) {
  const input = Array.isArray(candidates) ? candidates : [];
  const output = input.map((candidate) => ({ ...candidate }));
  const maxDetails = Math.max(0, Math.min(
    Number(options?.detail_candidates_per_quote || options?.detailCandidateLimit || TEMAISHUJU_DETAIL_CAPTURE_LIMIT),
    TEMAISHUJU_DETAIL_CAPTURE_LIMIT,
    output.length
  ));
  const detailIndexes = temaishujuDetailCandidateIndexes(input, options, maxDetails);
  if (!tabId || !output.length || maxDetails <= 0 || !detailIndexes.length) {
    return {
      candidates: output,
      state: { status: output.length ? "detail_capture_skipped" : "no_candidates", attempted_count: 0, enriched_count: 0 }
    };
  }

  const attempts = [];
  let blocked = false;
  for (const index of detailIndexes) {
    const candidate = output[index];
    const detailUrl = temaishujuCandidateDetailUrl(candidate);
    if (!detailUrl) {
      output[index] = mergeTemaishujuDetailEvidence(candidate, {
        ok: false,
        error: "missing_1688_detail_url",
        raw_payload: { detail_capture_status: "missing_1688_detail_url" }
      });
      attempts.push({ index, offer_id: candidate.offer_id || "", status: "missing_1688_detail_url" });
      continue;
    }

    try {
      await chrome.tabs.update(tabId, { url: detailUrl, active: false });
      await waitForTabReady(tabId, TEMAISHUJU_DETAIL_READY_TIMEOUT_MS);
      await delay(TEMAISHUJU_DETAIL_LOAD_WAIT_MS);
      const challenge = await detectSourceSearchChallenge(tabId);
      if (challenge.manual_challenge) {
        output[index] = mergeTemaishujuDetailEvidence(candidate, {
          ok: false,
          error: challenge.error || "detail_manual_challenge",
          raw_payload: {
            detail_capture_status: "manual_challenge",
            shipping_detail_url: detailUrl,
            risk_control_state: challenge.risk_control_state || null
          }
        });
        attempts.push({ index, offer_id: candidate.offer_id || "", status: "manual_challenge", error: challenge.error || "" });
        blocked = true;
        break;
      }

      let detail = await capture1688OfferDetailEvidenceFromTab(tabId, detailUrl, {
        product_title: options?.task?.product_title || "",
        skc_id: options?.task?.skc_id || "",
        sku_id: options?.task?.sku_id || "",
        spu_or_goods_id: options?.task?.spu_or_goods_id || "",
        main_image_url: options?.task?.main_image_url || "",
        candidate_title: candidate.source_title || "",
        candidate_offer_id: candidate.offer_id || "",
        candidate_main_image_url: candidate.main_image_url || "",
        candidate_list_rank: candidate.list_rank || index + 1
      });
      if (detailNeedsSkuMatrixPrice(detail)) {
        try {
          const detailTab = await chrome.tabs.get(tabId);
          const productCapture = await captureProductFromTab(detailTab || { id: tabId, url: detailUrl }, { commandType: "source_detail_sku_matrix_probe" });
          detail = mergeSkuMatrixPriceIntoSourceDetail(detail, productCapture);
        } catch (matrixError) {
          detail = markSkuMatrixProbeStatus(detail, {
            status: "failed",
            error: String(matrixError?.message || matrixError || "sku_matrix_probe_failed").slice(0, 180)
          });
        }
      }
      output[index] = mergeTemaishujuDetailEvidence(candidate, detail);
      attempts.push({
        index,
        offer_id: candidate.offer_id || productIdFromCaptureUrl(detailUrl),
        status: detail.ok ? "captured" : (detail.error || "failed"),
        freight_text: detail.freight_text || "",
        weight_text: detail.weight_text || ""
      });
      await delay(450);
    } catch (error) {
      const message = String(error?.message || error || "");
      output[index] = mergeTemaishujuDetailEvidence(candidate, {
        ok: false,
        error: message || "detail_capture_failed",
        raw_payload: {
          detail_capture_status: "failed",
          shipping_detail_url: detailUrl,
          detail_capture_error: message.slice(0, 180)
        }
      });
      attempts.push({ index, offer_id: candidate.offer_id || productIdFromCaptureUrl(detailUrl), status: "failed", error: message.slice(0, 180) });
    }
  }

  return {
    candidates: output,
    state: {
      status: blocked ? "manual_challenge" : "completed",
      attempted_count: attempts.length,
      enriched_count: attempts.filter((attempt) => attempt.status === "captured").length,
      selected_indexes: detailIndexes,
      attempts: attempts.slice(0, 10)
    }
  };
}

function temaishujuDetailCandidateIndexes(candidates, options = {}, maxDetails = TEMAISHUJU_DETAIL_CAPTURE_LIMIT) {
  const input = Array.isArray(candidates) ? candidates : [];
  const limit = Math.max(0, Math.min(Number(maxDetails || 0), input.length));
  if (!limit) return [];

  const targetText = normalizeTemaishujuDetailText([
    options?.task?.product_title,
    options?.task?.skc_id,
    options?.task?.sku_id,
    options?.task?.spu_or_goods_id
  ].filter(Boolean).join(" "));

  const scored = input.map((candidate, index) => {
    const rank = Number(candidate?.list_rank || index + 1);
    const candidateText = normalizeTemaishujuDetailText([
      candidate?.source_title,
      candidate?.price_text,
      candidate?.raw_payload?.list_card_text,
      candidate?.raw_payload?.source_title,
      candidate?.raw_payload?.title
    ].filter(Boolean).join(" "));
    let score = Math.max(0, 120 - Math.min(rank, 30) * 5);
    if (candidate?.source_url || candidate?.offer_id) score += 12;
    score += temaishujuDetailKeywordScore(targetText, candidateText);
    return { index, rank, score };
  });

  const chosen = new Set();
  const guaranteedCount = Math.min(3, limit, input.length);
  for (let index = 0; index < guaranteedCount; index += 1) {
    chosen.add(index);
  }
  scored.sort((left, right) => (right.score - left.score) || (left.rank - right.rank) || (left.index - right.index));
  for (const item of scored) {
    if (chosen.size >= limit) break;
    chosen.add(item.index);
  }
  return Array.from(chosen);
}

function normalizeTemaishujuDetailText(value) {
  return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
}

function temaishujuDetailKeywordScore(targetText, candidateText) {
  if (!targetText || !candidateText) return 0;
  const pairs = [
    { target: /owl|cat\s*head|animal\s*head|猫头|猫头鹰|动物头/, source: /owl|猫头鹰|猫头|猫咪头|动物头|异形/, score: 120 },
    { target: /clipper|scissor|cutter|nail|claw|指甲|剪刀|指甲剪|指甲钳/, source: /clipper|scissor|cutter|nail|claw|指甲|剪刀|指甲剪|指甲钳|甲剪|宠物剪/, score: 100 },
    { target: /trimmer|grinder|磨甲|修剪|修甲/, source: /trimmer|grinder|磨甲|修剪|修甲|打磨/, score: 80 },
    { target: /comb|brush|deshed|毛梳|梳子|排梳/, source: /comb|brush|deshed|毛梳|梳子|排梳|去浮毛|开结/, score: 100 },
    { target: /guard|shield|splash|防飞溅|挡板|保护/, source: /guard|shield|splash|防飞溅|挡板|护板|保护|带罩/, score: 70 },
    { target: /file|kit|set|套装|组合|锉|搓/, source: /file|kit|set|套装|组合|锉|搓|磨甲器/, score: 55 },
    { target: /blue|蓝|黑|black|yellow|黄|pink|粉|green|绿|white|白/, source: /blue|蓝|黑|black|yellow|黄|pink|粉|green|绿|white|白/, score: 45 },
    { target: /large|small|大号|小号|中号/, source: /large|small|大号|小号|中号|规格/, score: 35 },
    { target: /stainless|steel|metal|不锈钢|金属/, source: /stainless|steel|metal|不锈钢|金属|钢/, score: 35 },
    { target: /pet|cat|dog|宠物|猫|狗/, source: /pet|cat|dog|宠物|猫|狗/, score: 30 }
  ];
  return pairs.reduce((score, pair) => {
    return score + (pair.target.test(targetText) && pair.source.test(candidateText) ? pair.score : 0);
  }, 0);
}

function temaishujuCandidateDetailUrl(candidate) {
  const offerId = String(candidate?.offer_id || productIdFromCaptureUrl(candidate?.source_url || candidate?.candidate_id || "") || "").trim();
  const detailUrl = canonicalProductCaptureUrl(candidate?.source_url || "", offerId);
  if (/^https:\/\/detail\.1688\.com\/offer\/\d+\.html$/i.test(detailUrl)) return detailUrl;
  return "";
}

function mergeTemaishujuDetailEvidence(candidate, detail) {
  const merged = { ...candidate };
  const rawPayload = candidate && typeof candidate.raw_payload === "object" && candidate.raw_payload ? { ...candidate.raw_payload } : {};
  const detailPayload = detail && typeof detail.raw_payload === "object" && detail.raw_payload ? detail.raw_payload : {};
  merged.raw_payload = {
    ...rawPayload,
    ...detailPayload,
    detail_capture_ok: Boolean(detail?.ok)
  };
  if (detail?.source_title && !merged.source_title) merged.source_title = detail.source_title;
  if (detail?.freight_text) merged.freight_text = detail.freight_text;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "freight_cny")) merged.freight_cny = detail.freight_cny;
  if (detail?.weight_text) merged.weight_text = detail.weight_text;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "weight_kg")) merged.weight_kg = detail.weight_kg;
  if (detail?.weight_source) merged.weight_source = detail.weight_source;
  if (detail?.min_order_quantity && !merged.min_order_quantity) merged.min_order_quantity = detail.min_order_quantity;
  if (detail?.source_spec_text && !merged.source_spec_text) merged.source_spec_text = detail.source_spec_text;
  if (detail?.source_selected_spec_text) merged.source_selected_spec_text = detail.source_selected_spec_text;
  if (detail?.source_matched_spec_text) merged.source_matched_spec_text = detail.source_matched_spec_text;
  if (detail?.source_sku_match_status) merged.source_sku_match_status = detail.source_sku_match_status;
  if (detail?.source_sku_match_note) merged.source_sku_match_note = detail.source_sku_match_note;
  if (detail?.source_product_visual_match_status) merged.source_product_visual_match_status = detail.source_product_visual_match_status;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "source_product_visual_match_score")) merged.source_product_visual_match_score = detail.source_product_visual_match_score;
  if (Array.isArray(detail?.source_product_visual_match_evidence)) merged.source_product_visual_match_evidence = detail.source_product_visual_match_evidence;
  else if (detail?.source_product_visual_match_evidence) merged.source_product_visual_match_evidence = detail.source_product_visual_match_evidence;
  if (detail?.freight_confidence) merged.freight_confidence = detail.freight_confidence;
  if (detail?.freight_source) merged.freight_source = detail.freight_source;
  if (detail?.sku_price_source) merged.sku_price_source = detail.sku_price_source;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "source_cost_closed")) merged.source_cost_closed = detail.source_cost_closed;
  if (detail?.sku_preview_product_amount_text) merged.sku_preview_product_amount_text = detail.sku_preview_product_amount_text;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "sku_preview_product_amount_cny")) merged.sku_preview_product_amount_cny = detail.sku_preview_product_amount_cny;
  if (detail?.sku_preview_freight_text) merged.sku_preview_freight_text = detail.sku_preview_freight_text;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "sku_preview_freight_cny")) merged.sku_preview_freight_cny = detail.sku_preview_freight_cny;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "source_variant_count")) merged.source_variant_count = detail.source_variant_count;
  if (Array.isArray(detail?.source_variant_preview)) merged.source_variant_preview = detail.source_variant_preview;
  if (Array.isArray(detail?.source_variant_records)) merged.source_variant_records = detail.source_variant_records;
  if (detail?.employee_action_validation && typeof detail.employee_action_validation === "object") merged.employee_action_validation = detail.employee_action_validation;
  if (detail?.employee_action_validation_status) merged.employee_action_validation_status = detail.employee_action_validation_status;
  if (detail?.employee_action_selected_spec_text) merged.employee_action_selected_spec_text = detail.employee_action_selected_spec_text;
  if (detail?.employee_action_selected_variant_image_url) merged.employee_action_selected_variant_image_url = detail.employee_action_selected_variant_image_url;
  if (detail?.employee_action_sku_price_text) merged.employee_action_sku_price_text = detail.employee_action_sku_price_text;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "employee_action_sku_price_cny")) merged.employee_action_sku_price_cny = detail.employee_action_sku_price_cny;
  if (detail?.employee_action_product_amount_text) merged.employee_action_product_amount_text = detail.employee_action_product_amount_text;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "employee_action_product_amount_cny")) merged.employee_action_product_amount_cny = detail.employee_action_product_amount_cny;
  if (detail?.employee_action_freight_text) merged.employee_action_freight_text = detail.employee_action_freight_text;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "employee_action_freight_cny")) merged.employee_action_freight_cny = detail.employee_action_freight_cny;
  if (detail?.employee_action_weight_text) merged.employee_action_weight_text = detail.employee_action_weight_text;
  if (Object.prototype.hasOwnProperty.call(detail || {}, "employee_action_weight_kg")) merged.employee_action_weight_kg = detail.employee_action_weight_kg;
  if (detail?.employee_action_weight_source) merged.employee_action_weight_source = detail.employee_action_weight_source;
  if (detail?.employee_action_min_order_quantity) merged.employee_action_min_order_quantity = detail.employee_action_min_order_quantity;
  if (Array.isArray(detail?.employee_action_trace)) merged.employee_action_trace = detail.employee_action_trace;
  if (detail?.sku_preview_freight_text) {
    merged.freight_text = detail.sku_preview_freight_text;
    if (Object.prototype.hasOwnProperty.call(detail || {}, "sku_preview_freight_cny")) merged.freight_cny = detail.sku_preview_freight_cny;
  }
  const detailSkuPriceSource = normalizeSourceToken(detail?.sku_price_source);
  const detailHasClosedMatchedPriceSource = Boolean(detailSkuPriceSource && !/matrix_price|list_price|selected_default_sku_price|default_sku_confirmed_price/.test(detailSkuPriceSource));
  if (detail?.matched_price_text && ["matched_by_dimension", "default_sku_confirmed", "single_visible_spec"].includes(detail?.source_sku_match_status) && detailHasClosedMatchedPriceSource) {
    merged.price_text = detail.matched_price_text;
    if (Object.prototype.hasOwnProperty.call(detail || {}, "matched_price_cny")) merged.price_cny = detail.matched_price_cny;
  }
  return attachSourceCaptureContract(merged);
}

function sourceDetailNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  const raw = String(value ?? "").replace(/,/g, "").trim();
  if (!raw) return null;
  const match = raw.match(/(\d+(?:\.\d+)?)/);
  if (!match) return null;
  const parsed = Number(match[1]);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeSourceToken(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
}

function detailNeedsSkuMatrixPrice(detail) {
  if (!detail?.ok) return false;
  const status = normalizeSourceToken(detail.source_sku_match_status);
  if (!["default_sku_confirmed", "matched_by_dimension", "single_visible_spec"].includes(status)) return false;
  if (sourceDetailNumber(detail.matched_price_cny) !== null) return false;
  if (sourceDetailNumber(detail.sku_preview_product_amount_cny) !== null) return false;
  return true;
}

function cloneDetailRawPayload(detail) {
  return detail && typeof detail.raw_payload === "object" && detail.raw_payload ? { ...detail.raw_payload } : {};
}

function markSkuMatrixProbeStatus(detail, state) {
  const rawPayload = cloneDetailRawPayload(detail);
  rawPayload.sku_matrix_probe_status = state?.status || "unknown";
  if (state?.error) rawPayload.sku_matrix_probe_error = String(state.error).slice(0, 180);
  return { ...(detail || {}), raw_payload: rawPayload };
}

function normalizeSkuComparableText(value) {
  return String(value || "")
    .replace(/\s+/g, "")
    .replace(/[()[\]{}"'`~!@#$%^&*_+=|\\/<>,.?;:\-，。；：、（）【】《》“”‘’]/g, "")
    .toLowerCase();
}

function cleanSelectedSkuValue(value) {
  let raw = String(value || "").replace(/\s+/g, " ").trim();
  const colonIndex = raw.search(/[:\uFF1A]/);
  if (colonIndex >= 0 && colonIndex < 24) raw = raw.slice(colonIndex + 1);
  raw = raw
    .replace(/(?:CNY|RMB|USD|US\$|\$|¥|￥)\s*\d+(?:\.\d+)?/ig, " ")
    .replace(/\b(?:selected|default|sku|product|visual|confirmed|price|stock)\b/ig, " ")
    .replace(/\s+/g, " ")
    .trim();
  return raw.slice(0, 120);
}

function selectedSkuValuesFromSourceDetail(detail) {
  const rawPayload = cloneDetailRawPayload(detail);
  const values = [];
  const seen = new Set();
  const add = (value, reason = "") => {
    const cleaned = cleanSelectedSkuValue(value);
    const key = normalizeSkuComparableText(cleaned);
    if (!cleaned || key.length < 2 || seen.has(key)) return;
    seen.add(key);
    values.push({ value: cleaned, key, reason });
  };
  const addPreview = (preview, reason) => {
    if (!Array.isArray(preview)) return;
    preview
      .filter((item) => item && typeof item === "object" && item.selected)
      .forEach((item) => add(item.value || item.text || "", reason));
  };
  addPreview(detail?.source_variant_preview, "detail_selected_preview");
  addPreview(rawPayload.source_variant_preview, "raw_selected_preview");
  addPreview(rawPayload.source_variant_options_preview, "raw_selected_options_preview");
  const selectedText = String(detail?.source_selected_spec_text || rawPayload.source_selected_spec_text || detail?.source_matched_spec_text || rawPayload.source_matched_spec_text || "");
  selectedText
    .split(/[|;,\uFF1B\uFF0C]+/g)
    .forEach((part) => add(part, "selected_spec_text"));
  return values.slice(0, 6);
}

function normalizeSourceImageUrl(value) {
  const raw = String(value || "").trim();
  if (!raw || /^data:/i.test(raw)) return "";
  try {
    const parsed = new URL(raw);
    parsed.hash = "";
    parsed.search = "";
    return parsed.href;
  } catch (_error) {
    return raw.replace(/[?#].*$/, "");
  }
}

function sourceImageSignature(value) {
  const normalized = normalizeSourceImageUrl(value).toLowerCase();
  if (!normalized) return "";
  let raw = normalized;
  try {
    raw = decodeURIComponent(new URL(normalized).pathname || normalized);
  } catch (_error) {
    raw = normalized;
  }
  return raw
    .replace(/\.(?:jpg|jpeg|png|webp|gif)(?:_.*)?$/i, "")
    .replace(/[_-](?:sum|\d{2,5}x\d{2,5}|\d{2,5}w|\d{2,5}h|q\d+)$/i, "")
    .replace(/[^a-z0-9]+/gi, "")
    .slice(-80);
}

function sameSourceImageAsset(left, right) {
  const leftUrl = normalizeSourceImageUrl(left);
  const rightUrl = normalizeSourceImageUrl(right);
  if (!leftUrl || !rightUrl) return false;
  if (leftUrl === rightUrl || leftUrl.includes(rightUrl) || rightUrl.includes(leftUrl)) return true;
  const leftSig = sourceImageSignature(leftUrl);
  const rightSig = sourceImageSignature(rightUrl);
  return Boolean(leftSig && rightSig && leftSig.length >= 16 && rightSig.length >= 16 && (leftSig.includes(rightSig) || rightSig.includes(leftSig)));
}

function selectedSkuImagesFromSourceDetail(detail) {
  const rawPayload = cloneDetailRawPayload(detail);
  const urls = [];
  const add = (value) => {
    const url = normalizeSourceImageUrl(value);
    if (url && !urls.includes(url)) urls.push(url);
  };
  const scanPreview = (preview) => {
    if (!Array.isArray(preview)) return;
    preview
      .filter((item) => item && typeof item === "object" && item.selected)
      .forEach((item) => add(item.image_url || item.imageUrl || ""));
  };
  scanPreview(detail?.source_variant_preview);
  scanPreview(rawPayload.source_variant_preview);
  scanPreview(rawPayload.source_variant_options_preview);
  if (Array.isArray(rawPayload.selected_sku_image_urls)) rawPayload.selected_sku_image_urls.forEach(add);
  return urls.slice(0, 8);
}

function sourceSkuMatrixCombosFromProductCapture(productCapture) {
  const product = productCapture && typeof productCapture.product === "object" ? productCapture.product : {};
  const rawCombos = [
    ...(Array.isArray(product.variant_combinations) ? product.variant_combinations : []),
    ...(Array.isArray(product.raw_variant_combinations) ? product.raw_variant_combinations : [])
  ];
  const output = [];
  const seen = new Set();
  for (const combo of rawCombos) {
    if (!combo || typeof combo !== "object") continue;
    const attributes = combo.attributes && typeof combo.attributes === "object" ? combo.attributes : {};
    const comboText = [
      ...Object.entries(attributes).map(([name, value]) => `${name}:${value}`),
      combo.value,
      combo.text,
      combo.sku
    ].filter(Boolean).join(" ");
    const price = sourceDetailNumber(combo.price ?? combo.price_cny ?? combo.priceText ?? combo.salePrice ?? combo.skuPrice);
    const imageUrl = normalizeSourceImageUrl(combo.image_url || combo.imageUrl || combo.imgUrl || combo.skuImageUrl || "");
    const key = JSON.stringify({ attributes, price, imageUrl, sku: combo.sku || "" }).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    output.push({
      attributes,
      text: comboText,
      price,
      raw_price: combo.price ?? combo.price_cny ?? combo.priceText ?? combo.salePrice ?? combo.skuPrice ?? "",
      image_url: imageUrl,
      sku: String(combo.sku || combo.skuId || combo.id || ""),
      stock: combo.stock || combo.stock_text || "",
      selected: Boolean(combo.selected),
      confidence: combo.confidence || "",
      source: combo.source || ""
    });
  }
  return output.slice(0, 120);
}

function scoreSourceSkuMatrixCombo(combo, selectedValues, selectedImages) {
  const comboText = normalizeSkuComparableText(combo.text);
  let valueMatches = 0;
  let score = 0;
  for (const selected of selectedValues) {
    if (!selected.key) continue;
    if (comboText.includes(selected.key) || selected.key.includes(comboText)) {
      valueMatches += 1;
      score += 55;
      continue;
    }
    const tokens = selected.key.match(/[a-z0-9]{3,}|[\u4e00-\u9fff]{2}/g) || [];
    const matchedTokens = tokens.filter((token) => comboText.includes(token));
    if (tokens.length && matchedTokens.length >= Math.min(tokens.length, 2)) {
      valueMatches += 1;
      score += 28;
    }
  }
  const imageMatched = selectedImages.some((imageUrl) => sameSourceImageAsset(imageUrl, combo.image_url));
  if (imageMatched) score += 80;
  if (combo.selected) score += 25;
  if (combo.price !== null && combo.price > 0) score += 5;
  if (selectedValues.length && valueMatches === selectedValues.length) score += 25;
  return { score, valueMatches, imageMatched };
}

function findSelectedSkuMatrixPrice(detail, productCapture) {
  const selectedValues = selectedSkuValuesFromSourceDetail(detail);
  const selectedImages = selectedSkuImagesFromSourceDetail(detail);
  const combos = sourceSkuMatrixCombosFromProductCapture(productCapture)
    .filter((combo) => combo.price !== null && combo.price > 0 && combo.price < 10000);
  let best = null;
  for (const combo of combos) {
    const scored = scoreSourceSkuMatrixCombo(combo, selectedValues, selectedImages);
    const hasStrongSkuEvidence = scored.imageMatched
      || (selectedValues.length > 0 && scored.valueMatches === selectedValues.length)
      || (selectedValues.length === 1 && scored.score >= 60)
      || (!selectedValues.length && combo.selected);
    if (!hasStrongSkuEvidence) continue;
    const candidate = { ...combo, ...scored };
    if (!best || candidate.score > best.score) best = candidate;
  }
  return {
    selected_values: selectedValues.map((item) => item.value),
    selected_images: selectedImages,
    combo_count: combos.length,
    matched: best
  };
}

function mergeSkuMatrixPriceIntoSourceDetail(detail, productCapture) {
  if (!detail?.ok) return detail;
  const matrix = findSelectedSkuMatrixPrice(detail, productCapture);
  const rawPayload = cloneDetailRawPayload(detail);
  rawPayload.sku_matrix_probe_status = matrix.matched ? "matched_selected_sku" : "no_selected_sku_price_match";
  rawPayload.sku_matrix_combo_count = matrix.combo_count;
  rawPayload.sku_matrix_selected_values = matrix.selected_values.slice(0, 6);
  rawPayload.sku_matrix_selected_images = matrix.selected_images.slice(0, 4);
  rawPayload.product_capture_status = productCapture?.error || (productCapture?.supported === false ? "unsupported" : "captured");
  if (!matrix.matched) return { ...detail, raw_payload: rawPayload };

  const price = Number(matrix.matched.price);
  const status = normalizeSourceToken(detail.source_sku_match_status);
  const skuPriceSource = status === "matched_by_dimension"
    ? "matched_sku_matrix_price"
    : (status === "default_sku_confirmed" ? "default_sku_confirmed_matrix_price" : "selected_sku_matrix_price");
  const priceText = `selected SKU matrix price ¥${price}`;
  rawPayload.sku_matrix_price_text = priceText;
  rawPayload.sku_matrix_price_cny = price;
  rawPayload.sku_matrix_price_source = skuPriceSource;
  rawPayload.sku_matrix_match_score = matrix.matched.score;
  rawPayload.sku_matrix_match_value_count = matrix.matched.valueMatches;
  rawPayload.sku_matrix_match_image = matrix.matched.imageMatched;
  rawPayload.sku_matrix_match_attributes = matrix.matched.attributes || {};
  rawPayload.sku_matrix_match_image_url = matrix.matched.image_url || "";
  rawPayload.sku_matrix_match_sku = matrix.matched.sku || "";

  const freightConfidence = detail.freight_confidence || "";
  return {
    ...detail,
    sku_matrix_price_text: detail.sku_matrix_price_text || priceText,
    sku_matrix_price_cny: sourceDetailNumber(detail.sku_matrix_price_cny) ?? price,
    sku_matrix_price_source: detail.sku_matrix_price_source || skuPriceSource,
    source_cost_closed: Boolean(detail.source_cost_closed && freightConfidence !== "missing"),
    raw_payload: rawPayload
  };
}

async function capture1688OfferDetailEvidenceFromTab(tabId, detailUrl, matchContext = {}) {
  const captured = await executeMainWorld(tabId, [detailUrl, matchContext || {}], async (expectedDetailUrl, matchContext) => {
    const now = new Date().toISOString();
    const text = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const bodyText = text(document.body?.innerText || document.documentElement?.innerText || "");
    const rawScriptText = Array.from(document.scripts || [])
      .map((script) => script.textContent || "")
      .join("\n")
      .slice(0, 1800000);
    const scriptText = rawScriptText.slice(0, 220000);
    const combinedText = `${bodyText}\n${scriptText}`;
    const lines = (document.body?.innerText || "")
      .split(/\n+/)
      .map(text)
      .filter(Boolean)
      .slice(0, 600);
    const title = text(
      document.querySelector("h1")?.innerText
      || document.querySelector("[class*='title']")?.innerText
      || document.title
    ).slice(0, 180);
    const firstLine = (pattern) => lines.find((line) => pattern.test(line)) || "";
    const contextOf = (pattern, fallback = "") => {
      const matched = combinedText.match(pattern);
      if (matched?.index !== undefined) {
        const start = Math.max(0, matched.index - 90);
        return text(combinedText.slice(start, matched.index + matched[0].length + 120)).slice(0, 260);
      }
      return text(fallback).slice(0, 260);
    };
    const normalizeWeightUnit = (unit) => {
      const raw = text(unit).toLowerCase();
      if (/kg|公斤|千克/.test(raw)) return "kg";
      if (/g|克/.test(raw)) return "g";
      return "";
    };
    const weightKgFromNumberAndUnit = (value, unit = "") => {
      const number = Number(String(value ?? "").replace(/,/g, "").match(/\d+(?:\.\d+)?/)?.[0] || NaN);
      if (!Number.isFinite(number) || number <= 0 || number > 100000) return null;
      const normalizedUnit = normalizeWeightUnit(unit);
      if (normalizedUnit === "g") return Math.round((number / 1000) * 10000) / 10000;
      if (normalizedUnit === "kg") return Math.round(number * 10000) / 10000;
      return Math.round((number > 20 ? number / 1000 : number) * 10000) / 10000;
    };
    const parseWeightFromText = (value) => {
      const raw = text(value);
      if (!raw) return { text: "", kg: null, source: "" };
      const directPatterns = [
        /(?:包装重量|商品重量|商品件重|计费重量|毛重|净重|重量|weight|grossWeight|packageWeight)[^\d]{0,28}(\d+(?:\.\d+)?)\s*(kg|公斤|千克|g|克)/i,
        /"(?:weight|grossWeight|packageWeight)"\s*:\s*"?(\d+(?:\.\d+)?)"?\s*,?\s*"(?:unit|weightUnit)"\s*:\s*"(kg|公斤|千克|g|克)"/i
      ];
      for (const pattern of directPatterns) {
        const match = raw.match(pattern);
        if (match) {
          const unit = normalizeWeightUnit(match[2]) || match[2];
          return { text: `重量 ${match[1]}${unit}`.slice(0, 80), kg: weightKgFromNumberAndUnit(match[1], unit), source: "direct_weight_text" };
        }
      }
      const labelUnitPatterns = [
        /(?:包装重量|商品重量|商品件重|计费重量|毛重|净重|重量)\s*[（(]\s*(kg|公斤|千克|g|克)\s*[）)]?[^\d]{0,22}(\d+(?:\.\d+)?)/i,
        /(?:包装重量|商品重量|商品件重|计费重量|毛重|净重|重量)\s*(kg|公斤|千克|g|克)[^\d]{0,22}(\d+(?:\.\d+)?)/i
      ];
      for (const pattern of labelUnitPatterns) {
        const match = raw.match(pattern);
        if (match) {
          const unit = normalizeWeightUnit(match[1]) || match[1];
          return { text: `重量 ${match[2]}${unit}`.slice(0, 80), kg: weightKgFromNumberAndUnit(match[2], unit), source: "label_unit_weight_text" };
        }
      }
      const kgField = raw.match(/"(?:weightKg|grossWeightKg|packageWeightKg)"\s*:\s*"?(\d+(?:\.\d+)?)"?/i);
      if (kgField) return { text: `重量 ${kgField[1]}kg`.slice(0, 80), kg: weightKgFromNumberAndUnit(kgField[1], "kg"), source: "structured_weight_kg" };
      return { text: "", kg: null, source: "" };
    };
    const capturePackageInfoText = () => {
      const snippets = [];
      const push = (value) => {
        const snippet = text(value);
        if (!snippet || snippet.length < 4 || snippet.length > 1800) return;
        if (!/(包装信息|商品件重|包装重量|商品重量|计费重量|毛重|净重|重量\s*[（(]?\s*(?:g|kg|克|公斤|千克)?\s*[）)]?)/i.test(snippet)) return;
        if (!snippets.includes(snippet)) snippets.push(snippet);
      };
      Array.from(document.querySelectorAll("tr, table, dl, ul, ol, section, [class*='package'], [class*='param'], [class*='attribute'], [class*='detail']"))
        .slice(0, 900)
        .forEach((element) => {
          const rect = element.getBoundingClientRect?.();
          if (rect && (rect.width <= 2 || rect.height <= 2)) return;
          if (element.tagName === "TR") {
            push(Array.from(element.children || []).map((child) => text(child.innerText || child.textContent)).join(" "));
          } else {
            push(element.innerText || element.textContent || "");
          }
        });
      return snippets.slice(0, 18).join("\n").slice(0, 6000);
    };
    const cleanFreightContext = (value) => text(value)
        .replace(/退货包运费|退换货运费|运费险|官方包退货|包退货/gi, " ")
        .replace(/运费\s*(?:先采后付\s*)?0\s*元下单/gi, " ")
        .replace(/(?:下单返拿样费|拿样费|先采后付\s*0\s*元下单|0\s*元下单|首单[^，。；\n]{0,32}免运费|减\s*\d+(?:\.\d+)?\s*元且?免运费|跨境无忧|支持跨境|跨境铺货)/gi, " ");
    const nonProcurementFreightRe = /(?:跨境|国际|海外|目的地|清关|关税|哥伦比亚|colombia|destination|country|cross[-\s]?border|international|overseas)[^。\n]{0,28}(?:运费|物流|配送|快递|邮费|shipping|freight|postage)|(?:运费|物流|配送|快递|邮费|shipping|freight|postage)[^。\n]{0,28}(?:跨境|国际|海外|目的地|清关|关税|哥伦比亚|colombia|destination|country|cross[-\s]?border|international|overseas)/i;
    const freightSnippetIsProcurementCost = (value) => {
      const snippet = cleanFreightContext(value);
      return Boolean(snippet && !nonProcurementFreightRe.test(snippet));
    };
    const explicitFreightAmountFromText = (value) => {
      const raw = cleanFreightContext(value);
      if (!raw) return { text: "", cny: null };
      const freightPatterns = [
        /(?:另需运费|预估运费|运费\s*[（(]\s*预估\s*[）)]|运费|邮费|物流|快递|配送|shipping|freight|postage)[^\d¥￥]{0,32}[¥￥]?\s*(\d+(?:\.\d+)?)/i,
        /[¥￥]\s*(\d+(?:\.\d+)?)[^\n]{0,18}(?:运费|邮费|物流|快递|配送|shipping|freight|postage)/i,
        /(\d+(?:\.\d+)?)\s*元(?:起)?[^\n]{0,12}(?:运费|邮费|物流|快递|配送)/i
      ];
      for (const pattern of freightPatterns) {
        const globalPattern = new RegExp(pattern.source, pattern.flags.includes("g") ? pattern.flags : `${pattern.flags}g`);
        for (const matched of raw.matchAll(globalPattern)) {
          const cny = Number(matched[1]);
          const snippet = matched[0].replace(/\s+/g, " ");
          if (!Number.isFinite(cny)) continue;
          if (!freightSnippetIsProcurementCost(snippet)) continue;
          if (cny === 0 && /先采后付|0元下单|首单|拿样费|免运费/.test(snippet)) continue;
          return { text: snippet.slice(0, 120), cny };
        }
      }
      return { text: "", cny: null };
    };
    const freeLine = firstLine(/包邮|免运费|免邮|卖家承担运费|free\s*shipping/i);
    const freightLine = firstLine(/包邮|免运费|免邮|运费|邮费|物流|快递|配送|shipping|freight|postage/i);
    let freightText = "";
    let freightCny = null;
    const explicitFreightAmount = explicitFreightAmountFromText(combinedText);
    if (explicitFreightAmount.cny !== null) {
      freightText = explicitFreightAmount.text || `运费 ¥${explicitFreightAmount.cny}`;
      freightCny = explicitFreightAmount.cny;
    } else if (freeLine && !/退货包运费|退换货运费/i.test(freeLine) && freightSnippetIsProcurementCost(freeLine)) {
      freightText = "包邮";
      freightCny = 0;
    } else {
      const freightPatterns = [
        /(?:运费|邮费|物流|快递|配送|shipping|freight|postage)[^\d¥￥]{0,32}[¥￥]?\s*(\d+(?:\.\d+)?)/i,
        /[¥￥]\s*(\d+(?:\.\d+)?)[^\n]{0,18}(?:运费|邮费|物流|快递|配送|shipping|freight|postage)/i,
        /(\d+(?:\.\d+)?)\s*元(?:起)?[^\n]{0,12}(?:运费|邮费|物流|快递|配送)/i
      ];
      const freightMatch = freightPatterns.map((pattern) => combinedText.match(pattern)).find(Boolean);
      if (freightMatch && freightSnippetIsProcurementCost(freightMatch[0])) {
        freightCny = Number(freightMatch[1]);
        freightText = `${freightLine || "运费"} ¥${freightMatch[1]}`.slice(0, 120);
      } else if (freightLine && freightSnippetIsProcurementCost(freightLine)) {
        freightText = freightLine.slice(0, 120);
      }
    }
    const detailFreightText = freightText;
    const detailFreightCny = freightCny;
    const previewFreightAmountShouldOverrideDetail = (amount) => {
      if (!amount || amount.cny === null || amount.cny === undefined) return false;
      if (!freightSnippetIsProcurementCost(amount.text || `运费 ¥${amount.cny}`)) return false;
      if (amount.cny === 0 && detailFreightCny !== null && detailFreightCny !== undefined && detailFreightCny > 0) {
        return false;
      }
      return true;
    };

    let packageInfoText = capturePackageInfoText();
    const initialPackageWeight = parseWeightFromText(packageInfoText);
    const initialCombinedWeight = parseWeightFromText(combinedText);
    let weightText = initialPackageWeight.text || initialCombinedWeight.text || "";
    let weightKg = initialPackageWeight.kg ?? initialCombinedWeight.kg ?? null;
    let weightSource = initialPackageWeight.text ? "package_info" : (initialCombinedWeight.text ? initialCombinedWeight.source : "");
    const refreshWeightEvidence = () => {
      packageInfoText = capturePackageInfoText();
      const packageWeight = parseWeightFromText(packageInfoText);
      const refreshedBody = text(document.body?.innerText || document.documentElement?.innerText || "");
      const refreshedWeight = parseWeightFromText(refreshedBody);
      weightText = packageWeight.text || refreshedWeight.text || weightText;
      weightKg = packageWeight.kg ?? refreshedWeight.kg ?? weightKg;
      weightSource = packageWeight.text ? "package_info" : (refreshedWeight.text ? refreshedWeight.source : weightSource);
    };

    const moqUnitPattern = "(?:件|片|个|套|条|只|支|把|双|组|盒|箱|台|pcs?|pieces?|sets?)?";
    const moqLine = firstLine(new RegExp(`\\d+\\s*${moqUnitPattern}\\s*(?:起批|起订|起订量|起批量)`, "i"));
    const moqMatch = (moqLine || bodyText).match(new RegExp(`(\\d+)\\s*${moqUnitPattern}\\s*(?:起批|起订|起订量|起批量)`, "i"));
    const visible = (element) => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 2 && rect.height > 2;
    };
    const moneyText = (value) => {
      const raw = text(value);
      const patterns = [
        /(?:[¥￥]|RMB|CNY)\s*(\d+(?:\.\d+)?)/i,
        /(\d+(?:\.\d+)?)\s*(?:元)/i,
        /(?:price|价格|价)[^\d]{0,16}(\d+(?:\.\d+)?)/i
      ];
      for (const pattern of patterns) {
        const match = raw.match(pattern);
        if (match) return match[0].replace(/\s+/g, "");
      }
      return "";
    };
    const moneyValue = (value) => {
      const match = moneyText(value).match(/\d+(?:\.\d+)?/);
      return match ? Number(match[0]) : null;
    };
    const parseLabeledMoney = (labels, maxGap = 52, lineSource = lines, textSource = combinedText) => {
      const labelSource = labels.join("|");
      const labelPattern = new RegExp(labelSource, "i");
      const moneyPattern = new RegExp(`(?:${labelSource})[^\\d¥￥]{0,${maxGap}}[¥￥]?\\s*(\\d+(?:\\.\\d+)?)`, "i");
      const sources = [
        ...lineSource.filter((line) => labelPattern.test(line)),
        textSource.slice(0, 180000)
      ];
      for (const sourceText of sources) {
        const matched = text(sourceText).match(moneyPattern);
        if (matched) {
          const value = Number(matched[1]);
          if (Number.isFinite(value)) {
            return { text: matched[0].replace(/\s+/g, " ").slice(0, 120), cny: value };
          }
        }
      }
      return { text: "", cny: null };
    };
    let skuPreviewProductAmount = parseLabeledMoney(["商品金额", "商品合计", "货品金额", "商品总额"]);
    let skuPreviewFreightAmount = parseLabeledMoney(["另需运费", "预估运费", "运费\\(预估\\)", "运费（预估）"]);
    if (skuPreviewFreightAmount.cny !== null && !freightSnippetIsProcurementCost(skuPreviewFreightAmount.text)) {
      skuPreviewFreightAmount = { text: "", cny: null };
    }
    if (previewFreightAmountShouldOverrideDetail(skuPreviewFreightAmount)) {
      freightText = skuPreviewFreightAmount.text || `另需运费 ¥${skuPreviewFreightAmount.cny}`;
      freightCny = skuPreviewFreightAmount.cny;
    }
    const refreshSkuPreviewAmounts = () => {
      const refreshedBodyText = text(document.body?.innerText || document.documentElement?.innerText || "");
      const refreshedLines = (document.body?.innerText || "")
        .split(/\n+/)
        .map(text)
        .filter(Boolean)
        .slice(0, 1000);
      skuPreviewProductAmount = parseLabeledMoney(["商品金额", "商品合计", "货品金额", "商品总额"], 72, refreshedLines, refreshedBodyText);
      skuPreviewFreightAmount = parseLabeledMoney(["另需运费", "预估运费", "运费\\(预估\\)", "运费（预估）"], 72, refreshedLines, refreshedBodyText);
      if (skuPreviewFreightAmount.cny !== null && !freightSnippetIsProcurementCost(skuPreviewFreightAmount.text)) {
        skuPreviewFreightAmount = { text: "", cny: null };
      }
      if (previewFreightAmountShouldOverrideDetail(skuPreviewFreightAmount)) {
        freightText = skuPreviewFreightAmount.text || `另需运费 ¥${skuPreviewFreightAmount.cny}`;
        freightCny = skuPreviewFreightAmount.cny;
      }
    };
    const stockText = (value) => {
      const match = text(value).match(/(?:库存|现货|可售|余量)\s*[\d,.万wW]+\s*(?:件|片|个|套|条|只)?/);
      return match ? match[0] : "";
    };
    const dimensionKey = (value) => {
      const match = text(value).toLowerCase().match(/(\d{2,4}(?:\.\d+)?)\s*(?:cm|厘米)?\s*(?:x|\*|×|乘)\s*(\d{2,4}(?:\.\d+)?)\s*(?:cm|厘米)?/i);
      if (!match) return "";
      const nums = [Number(match[1]), Number(match[2])].filter((num) => Number.isFinite(num) && num > 0).sort((a, b) => a - b);
      if (nums.length !== 2) return "";
      return nums.map((num) => (Number.isInteger(num) ? String(num) : String(num).replace(/\.0+$/, ""))).join("x");
    };
    const quoteDimensionSignals = Array.from(new Set(
      text(matchContext?.product_title || "")
        .match(/\d{2,4}(?:\.\d+)?\s*(?:cm|厘米)?\s*(?:x|\*|×|乘)\s*\d{2,4}(?:\.\d+)?\s*(?:cm|厘米)?/ig) || []
    )).map((value) => ({ text: text(value), key: dimensionKey(value) })).filter((item) => item.key);
    const optionValueText = (value) => text(value)
      .replace(/(?:[¥￥]|RMB|CNY)\s*\d+(?:\.\d+)?/ig, " ")
      .replace(/\d+(?:\.\d+)?\s*元/g, " ")
      .replace(/(?:price|价格|价)[^\d]{0,16}\d+(?:\.\d+)?/ig, " ")
      .replace(/(?:库存|现货|可售|余量)\s*[\d,.万wW]+\s*(?:件|片|个|套|条|只)?/g, " ")
      .replace(/\b(?:已选|请选择|颜色|尺寸|规格|尺码|型号|款式|花色)\b[:：]?/g, " ")
      .replace(/滚动查看更多规格|立即下单|加入购物车|跨境铺货|代发下单|收藏|客服/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 80);
    const inferGroupName = (value, contextText = "") => {
      const raw = `${value} ${contextText}`;
      if (dimensionKey(value) || /尺寸|尺码|规格大小|长宽|cm|厘米/i.test(raw)) return "尺寸";
      if (/颜色|花色|色系|款式|图案|\[[A-Za-z0-9_-]{2,}\]/.test(raw)) return "颜色/款式";
      if (/规格|型号|属性/.test(raw)) return "规格";
      return "规格";
    };
    const selectedClassRe = /selected|active|current|checked|choosed|chosen|focus|sku-selected|is-select|is_selected/i;
    const disabledClassRe = /disabled|disable|soldout|sold-out|unavailable/i;
    const elementState = (element) => {
      let selected = false;
      let disabled = false;
      let cursor = element;
      for (let depth = 0; cursor && depth < 3; depth += 1, cursor = cursor.parentElement) {
        const cls = String(cursor.className || "");
        const aria = `${cursor.getAttribute?.("aria-selected") || ""} ${cursor.getAttribute?.("aria-checked") || ""}`;
        selected = selected || selectedClassRe.test(cls) || /\btrue\b/i.test(aria);
        disabled = disabled || disabledClassRe.test(cls) || cursor.getAttribute?.("disabled") !== null;
      }
      return { selected, disabled };
    };
    const normalizeImageUrl = (value) => {
      const raw = String(value || "").trim();
      if (!raw || /^data:/i.test(raw)) return "";
      try {
        const parsed = new URL(raw, location.href);
        parsed.hash = "";
        parsed.search = "";
        return parsed.href;
      } catch (_error) {
        return raw.replace(/[?#].*$/, "");
      }
    };
    const imageFromElement = (element) => {
      if (!element) return "";
      const direct = element.matches?.("img") ? element : null;
      const image = direct || element.querySelector?.("img");
      const candidates = [
        image?.currentSrc,
        image?.src,
        image?.getAttribute?.("data-src"),
        image?.getAttribute?.("data-lazy-src"),
        element.getAttribute?.("data-img"),
        element.getAttribute?.("data-image"),
        element.getAttribute?.("data-img-url"),
        element.getAttribute?.("style")
      ].filter(Boolean);
      for (const candidate of candidates) {
        const styleMatch = String(candidate).match(/url\(["']?([^"')]+)["']?\)/i);
        const normalized = normalizeImageUrl(styleMatch ? styleMatch[1] : candidate);
        if (normalized) return normalized;
      }
      return "";
    };
    const imageSignature = (value) => {
      const normalized = normalizeImageUrl(value).toLowerCase();
      if (!normalized) return "";
      let raw = "";
      try {
        const parsed = new URL(normalized);
        raw = decodeURIComponent(parsed.pathname || normalized);
      } catch (_error) {
        raw = normalized;
      }
      raw = raw
        .replace(/\.(?:jpg|jpeg|png|webp|gif)(?:_.*)?$/i, "")
        .replace(/[_-](?:\d{2,5}x\d{2,5}|\d{2,5}w|\d{2,5}h)$/i, "")
        .replace(/[^a-z0-9]+/gi, "");
      return raw.slice(-80);
    };
    const sameImageAsset = (left, right) => {
      const leftUrl = normalizeImageUrl(left);
      const rightUrl = normalizeImageUrl(right);
      if (!leftUrl || !rightUrl) return false;
      if (leftUrl === rightUrl || leftUrl.includes(rightUrl) || rightUrl.includes(leftUrl)) return true;
      const leftSig = imageSignature(leftUrl);
      const rightSig = imageSignature(rightUrl);
      return Boolean(leftSig && rightSig && leftSig.length >= 16 && rightSig.length >= 16 && (leftSig.includes(rightSig) || rightSig.includes(leftSig)));
    };
    const pageProductImages = Array.from(document.images || [])
      .filter(visible)
      .map((image) => {
        const rect = image.getBoundingClientRect();
        const url = imageFromElement(image);
        const contextText = text(`${image.alt || ""} ${image.title || ""} ${image.className || ""} ${image.parentElement?.className || ""}`);
        let score = Math.min(rect.width * rect.height / 1000, 80);
        if (/main|gallery|preview|sku|thumb|offer|product|image/i.test(contextText)) score += 25;
        if (rect.width >= 180 && rect.height >= 180) score += 20;
        if (rect.top >= -80 && rect.top <= window.innerHeight * 1.4) score += 10;
        return { url, score, width: Math.round(rect.width), height: Math.round(rect.height), context: contextText.slice(0, 80) };
      })
      .filter((item) => item.url && item.width >= 48 && item.height >= 48)
      .sort((left, right) => right.score - left.score)
      .slice(0, 12);
    const specOptions = [];
    const seenSpecOption = new Set();
    const clickableSpecOptions = [];
    const clickSkuElementForPreview = async (element) => {
      if (!element || !visible(element)) return false;
      try {
        element.scrollIntoView({ block: "center", inline: "nearest" });
        element.click();
        await new Promise((resolve) => setTimeout(resolve, 650));
        return true;
      } catch (_error) {
        return false;
      }
    };
    const ensureSelectedSkuQuantityPreview = async (selectedValue = "", selectedElement = null) => {
      const selectedText = text(selectedValue);
      const unsafeActionRe = /加入购物车|加购物车|立即下单|马上订购|代发下单|跨境铺货|询盘|联系|客服|收藏|发布|上架/i;
      const plusTextRe = /^(?:\+|＋|加|增加)$/;
      const plusClassRe = /plus|increase|increment|add|next-number-picker-handler-up|quantity-add|amount-add/i;
      const numericInput = (scope) => {
        if (!scope?.querySelectorAll) return null;
        return Array.from(scope.querySelectorAll("input"))
          .filter(visible)
          .find((input) => /^\d*$/.test(String(input.value || "")));
      };
      const currentQuantity = (scope) => {
        const input = numericInput(scope);
        if (input) {
          const value = Number(input.value || 0);
          return Number.isFinite(value) ? value : 0;
        }
        const matched = text(scope?.innerText || "").match(/(?:已选|数量|采购量|订购量)[^\d]{0,12}(\d+)/);
        return matched ? Number(matched[1]) : null;
      };
      const rowScopes = [];
      if (selectedElement) {
        let cursor = selectedElement;
        for (let depth = 0; cursor && depth < 7; depth += 1, cursor = cursor.parentElement) {
          rowScopes.push(cursor);
        }
      }
      const selectors = [
        "tr",
        "[class*='sku']",
        "[class*='spec']",
        "[class*='offer']",
        "[class*='table']",
        "[class*='row']",
        "[class*='quantity']",
        "div"
      ].join(",");
      rowScopes.push(...Array.from(document.querySelectorAll(selectors))
        .filter(visible)
        .filter((element) => {
          const raw = text(element.innerText || element.textContent || "");
          if (!raw || raw.length > 800) return false;
          return selectedText ? raw.includes(selectedText) : /库存|现货|可售|采购量|订购量|数量/.test(raw);
        })
        .sort((left, right) => text(left.innerText).length - text(right.innerText).length)
        .slice(0, 12));
      const uniqueScopes = [];
      const seenScopes = new Set();
      for (const scope of rowScopes) {
        if (!scope || seenScopes.has(scope)) continue;
        seenScopes.add(scope);
        uniqueScopes.push(scope);
      }
      for (const scope of uniqueScopes) {
        const qty = currentQuantity(scope);
        if (qty !== null && qty >= 1) return true;
        const controls = Array.from(scope.querySelectorAll?.("button,[role='button'],a,span,div") || [])
          .filter(visible)
          .filter((control) => {
            const raw = text(control.innerText || control.textContent || control.getAttribute?.("aria-label") || control.getAttribute?.("title") || "");
            const cls = String(control.className || "");
            if (unsafeActionRe.test(raw) || unsafeActionRe.test(cls)) return false;
            if (plusTextRe.test(raw)) return true;
            return plusClassRe.test(cls) || plusClassRe.test(raw);
          });
        const plus = controls[0];
        if (!plus) continue;
        try {
          plus.scrollIntoView({ block: "center", inline: "nearest" });
          plus.click();
          await new Promise((resolve) => setTimeout(resolve, 900));
          return true;
        } catch (_error) {
          // Try the next scoped row.
        }
      }
      return false;
    };
    const selectedSkuRowPriceFromText = (value) => {
      const raw = text(value);
      if (!raw || raw.length > 460) return { text: "", cny: null };
      if (!/(?:规格|默认|库存|现货|可售|[-−]\s*\d+\s*\+)/.test(raw)) return { text: "", cny: null };
      if (/(?:运费|物流|邮费|首件预估|到手价|起批|已售|销量|优惠|满\d|券|返拿样费)/.test(raw) && !/(?:库存|现货|可售)/.test(raw)) {
        return { text: "", cny: null };
      }
      const patterns = [
        /(?:规格|型号|款式|颜色|默认|包装)[^¥￥]{0,120}[¥￥]\s*(\d+(?:\.\d+)?)[^¥￥]{0,90}(?:库存|现货|可售|[-−]\s*\d+\s*\+)/,
        /[¥￥]\s*(\d+(?:\.\d+)?)[^¥￥]{0,90}(?:库存|现货|可售|[-−]\s*\d+\s*\+)/,
        /(?:库存|现货|可售)[^¥￥]{0,90}[¥￥]\s*(\d+(?:\.\d+)?)/
      ];
      for (const pattern of patterns) {
        const match = raw.match(pattern);
        if (!match) continue;
        const value = Number(match[1]);
        if (Number.isFinite(value) && value > 0 && value < 10000) {
          return { text: match[0].replace(/\s+/g, " ").slice(0, 140), cny: Math.round(value * 100) / 100 };
        }
      }
      return { text: "", cny: null };
    };
    const selectedSkuRowPriceNearHints = (value, selectedValueHints = []) => {
      const raw = text(value);
      if (!raw) return { text: "", cny: null, matched_hint: "" };
      const hints = uniqueTexts(selectedValueHints.map(optionValueText).filter((item) => item.length >= 2)).slice(0, 6);
      for (const hint of hints) {
        const index = raw.indexOf(hint);
        if (index < 0) continue;
        const scoped = raw.slice(index, Math.min(raw.length, index + 260));
        const parsed = selectedSkuRowPriceFromText(scoped);
        if (parsed.cny !== null) {
          return {
            ...parsed,
            text: `${hint} ${parsed.text}`.replace(/\s+/g, " ").slice(0, 160),
            matched_hint: hint
          };
        }
      }
      return { text: "", cny: null, matched_hint: "" };
    };
    const findSelectedSkuRowPrice = (selectedValueHints = []) => {
      const candidates = [];
      const addCandidate = (raw, source, element = null) => {
        const hintParsed = selectedSkuRowPriceNearHints(raw, selectedValueHints);
        const parsed = hintParsed.cny !== null ? hintParsed : selectedSkuRowPriceFromText(raw);
        if (parsed.cny === null) return;
        const rowText = text(raw);
        const hasQuantityControl = Boolean(
          /[-−]\s*\d+\s*\+/.test(rowText)
          || element?.querySelector?.("input")
          || Array.from(element?.querySelectorAll?.("button,[role='button'],span,div") || [])
            .some((control) => /^(?:[+＋]|[-−])$/.test(text(control.innerText || control.textContent || control.getAttribute?.("aria-label") || "")))
        );
        let score = 0;
        if (/规格/.test(rowText)) score += 30;
        if (/默认/.test(rowText)) score += 12;
        if (/(?:库存|现货|可售)/.test(rowText)) score += 35;
        if (hasQuantityControl) score += 25;
        if (source === "dom_row") score += 12;
        if (parsed.matched_hint) score += 45;
        if (rowText.length <= 160) score += 12;
        if (/(?:首件预估|到手价|运费|起批|已售|销量|优惠|券)/.test(rowText)) score -= 35;
        candidates.push({
          text: parsed.text || `selected SKU row price ¥${parsed.cny}`,
          cny: parsed.cny,
          source,
          score,
          matched_hint: parsed.matched_hint || "",
          context: rowText.slice(0, 360)
        });
      };
      for (let index = 0; index < lines.length; index += 1) {
        addCandidate(lines[index], "line");
        if (index + 1 < lines.length) addCandidate(`${lines[index]} ${lines[index + 1]}`, "line_pair");
      }
      const rowSelector = [
        "tr",
        "[class*='sku']",
        "[class*='spec']",
        "[class*='table']",
        "[class*='row']",
        "[class*='quantity']",
        "[class*='amount']",
        "[class*='order']",
        "[class*='purchase']"
      ].join(",");
      Array.from(document.querySelectorAll(rowSelector))
        .filter(visible)
        .forEach((element) => {
          const raw = text(element.innerText || element.textContent || "");
          if (!raw || raw.length > 520) return;
          addCandidate(raw, "dom_row", element);
        });
      candidates.sort((left, right) => right.score - left.score || left.context.length - right.context.length);
      return candidates[0] || { text: "", cny: null, source: "", score: 0, context: "" };
    };
    const pushSpecOption = (entry) => {
      const value = optionValueText(entry.value || entry.text || "");
      if (!value || value.length < 2 || /^(颜色|尺寸|规格|尺码|款式|型号|花色)$/.test(value)) return;
      if (/首页|我的阿里|下载插件|采购车|消息|官方服务|找货寻源|运费|退货|跨境无忧|支持跨境/.test(value)) return;
      const groupName = entry.group_name || inferGroupName(value, entry.context_text || "");
      const key = `${groupName}|${value}`.toLowerCase();
      const dimKey = dimensionKey(value);
      const existingIndex = seenSpecOption.has(key) ? specOptions.findIndex((item) => `${item.group_name}|${item.value}`.toLowerCase() === key) : -1;
      const normalized = {
        group_name: groupName,
        value,
        text: text(entry.text || value).slice(0, 120),
        price_text: entry.price_text || "",
        price_cny: typeof entry.price_cny === "number" ? entry.price_cny : moneyValue(entry.price_text || entry.text || ""),
        stock_text: entry.stock_text || "",
        image_url: entry.image_url || "",
        selected: Boolean(entry.selected),
        disabled: Boolean(entry.disabled),
        dimension_key: dimKey
      };
      if (existingIndex >= 0) {
        const existing = specOptions[existingIndex];
        specOptions[existingIndex] = {
          ...existing,
          price_text: existing.price_text || normalized.price_text,
          price_cny: existing.price_cny ?? normalized.price_cny,
          stock_text: existing.stock_text || normalized.stock_text,
          image_url: existing.image_url || normalized.image_url,
          selected: Boolean(existing.selected || normalized.selected),
          disabled: Boolean(existing.disabled || normalized.disabled),
          dimension_key: existing.dimension_key || normalized.dimension_key
        };
        return;
      }
      seenSpecOption.add(key);
      specOptions.push(normalized);
    };

    const optionSelector = [
      "[class*='sku'] li",
      "[class*='sku'] button",
      "[class*='sku'] [role='button']",
      "[class*='sku'] [class*='item']",
      "[class*='sku'] [class*='value']",
      "[class*='prop'] li",
      "[class*='prop'] button",
      "[class*='prop'] [role='button']",
      "[class*='prop'] [class*='item']",
      "[class*='spec'] li",
      "[class*='spec'] button",
      "[class*='spec'] [role='button']",
      "[class*='spec'] [class*='item']",
      "[class*='sale'] li",
      "[class*='sale'] [class*='item']"
    ].join(",");
    Array.from(document.querySelectorAll(optionSelector)).forEach((element) => {
      if (!visible(element)) return;
      const raw = text(element.innerText || element.textContent);
      if (!raw || raw.length > 160) return;
      const value = optionValueText(raw);
      if (!value || (!dimensionKey(value) && !/\[[A-Za-z0-9_-]{2,}\]|[\u4e00-\u9fff]{2,}/.test(value))) return;
      const parentText = text(element.parentElement?.innerText || "").slice(0, 240);
      const state = elementState(element);
      const optionImageUrl = imageFromElement(element) || imageFromElement(element.parentElement);
      const groupName = inferGroupName(value, parentText);
      clickableSpecOptions.push({
        element,
        group_name: groupName,
        value,
        dimension_key: dimensionKey(value),
        image_url: optionImageUrl,
        selected: state.selected,
        disabled: state.disabled
      });
      pushSpecOption({
        group_name: groupName,
        value,
        text: raw,
        price_text: moneyText(raw),
        stock_text: stockText(raw),
        image_url: optionImageUrl,
        selected: state.selected,
        disabled: state.disabled,
        context_text: parentText
      });
    });

    const optionGroupLabelRe = /^(颜色|尺寸|规格|尺码|型号|款式|花色)$/;
    let currentGroup = "";
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      if (optionGroupLabelRe.test(line)) {
        currentGroup = line;
        continue;
      }
      if (/立即下单|加入购物车|跨境铺货|代发下单|商品金额|收藏|客服/.test(line)) {
        if (!/库存|[¥￥]\s*\d/.test(line)) currentGroup = "";
      }
      const rowText = text([line, lines[index + 1] || "", lines[index + 2] || ""].join(" "));
      const value = optionValueText(line);
      if (!value || value.length > 80) continue;
      if (!currentGroup && !dimensionKey(value) && !/\[[A-Za-z0-9_-]{2,}\]/.test(value)) continue;
      pushSpecOption({
        group_name: currentGroup || inferGroupName(value, rowText),
        value,
        text: rowText,
        price_text: moneyText(rowText),
        stock_text: stockText(rowText),
        selected: false,
        disabled: false,
        context_text: rowText
      });
    }

    const uniqueTexts = (items) => {
      const output = [];
      const seen = new Set();
      for (const item of items || []) {
        const value = text(item);
        const key = value.toLowerCase();
        if (!value || seen.has(key)) continue;
        seen.add(key);
        output.push(value);
      }
      return output;
    };
    const targetMainImageUrls = [];
    const addTargetImageUrl = (value) => {
      const url = normalizeImageUrl(value);
      if (url && !targetMainImageUrls.includes(url)) targetMainImageUrls.push(url);
    };
    addTargetImageUrl(matchContext?.main_image_url || "");
    if (Array.isArray(matchContext?.target_image_urls)) matchContext.target_image_urls.forEach(addTargetImageUrl);
    if (Array.isArray(matchContext?.extra_image_urls)) matchContext.extra_image_urls.forEach(addTargetImageUrl);
    const targetMainImageUrl = targetMainImageUrls[0] || "";
    const candidateMainImageUrl = normalizeImageUrl(matchContext?.candidate_main_image_url || "");
    const candidateListRank = Number(matchContext?.candidate_list_rank || 0);
    const markSkuOptionSelected = (option) => {
      if (!option) return;
      for (const item of clickableSpecOptions) {
        if (item.group_name && option.group_name && item.group_name === option.group_name) item.selected = false;
        if (item.value === option.value && (!option.group_name || !item.group_name || item.group_name === option.group_name)) item.selected = true;
      }
      for (const item of specOptions) {
        if (item.group_name && option.group_name && item.group_name === option.group_name) item.selected = false;
        if (item.value === option.value && (!option.group_name || !item.group_name || item.group_name === option.group_name)) item.selected = true;
      }
    };
    const selectedVariantTarget = matchContext?.selected_variant && typeof matchContext.selected_variant === "object" ? matchContext.selected_variant : {};
    const forcedVariantRawText = text([
      selectedVariantTarget.matched_variant_text || "",
      selectedVariantTarget.spec_text || "",
      selectedVariantTarget.value || "",
      selectedVariantTarget.name || ""
    ].join(" "));
    const forcedVariantText = optionValueText(forcedVariantRawText);
    const forcedVariantImageUrl = normalizeImageUrl(selectedVariantTarget.matched_variant_image_url || selectedVariantTarget.image_url || "");
    const forcedTokens = new Set(
      forcedVariantText
        .toLowerCase()
        .split(/[^a-z0-9\u4e00-\u9fff]+/i)
        .map((item) => item.trim())
        .filter((item) => item.length >= 2)
    );
    let forcedSkuSelectionApplied = false;
    let imageMatchedSkuOption = null;
    if (matchContext?.force_selected_variant && (forcedVariantText || forcedVariantImageUrl)) {
      const forcedOptions = clickableSpecOptions
        .filter((item) => !item.disabled)
        .map((item) => {
          const valueText = optionValueText(`${item.group_name || ""}:${item.value || ""}`);
          const valueTokens = valueText
            .toLowerCase()
            .split(/[^a-z0-9\u4e00-\u9fff]+/i)
            .map((part) => part.trim())
            .filter((part) => part.length >= 2);
          let score = 0;
          if (forcedVariantImageUrl && item.image_url && sameImageAsset(item.image_url, forcedVariantImageUrl)) score += 120;
          if (forcedVariantText && (forcedVariantText.includes(item.value) || item.value.includes(forcedVariantText) || forcedVariantText.includes(valueText))) score += 95;
          const overlap = valueTokens.filter((token) => forcedTokens.has(token)).length;
          score += overlap * 24;
          if (item.selected) score += 6;
          return { ...item, score };
        })
        .filter((item) => item.score >= 70)
        .sort((left, right) => right.score - left.score || Number(right.selected) - Number(left.selected));
      const clickedGroups = new Set();
      for (const option of forcedOptions) {
        const groupKey = option.group_name || option.dimension_key || "single";
        if (clickedGroups.has(groupKey)) continue;
        if (option.element && !option.selected) {
          const clicked = await clickSkuElementForPreview(option.element);
          if (!clicked) continue;
        }
        await ensureSelectedSkuQuantityPreview(option.value, option.element);
        markSkuOptionSelected(option);
        clickedGroups.add(groupKey);
        forcedSkuSelectionApplied = true;
        imageMatchedSkuOption = imageMatchedSkuOption || option;
      }
      if (forcedSkuSelectionApplied) {
        refreshSkuPreviewAmounts();
      }
    }
    const imageMatchedSkuOptions = imageMatchedSkuOption ? [] : clickableSpecOptions
      .filter((item) => !item.disabled && normalizeImageUrl(item.image_url))
      .map((item) => {
        const optionImageUrl = normalizeImageUrl(item.image_url);
        let score = 0;
        if (candidateMainImageUrl && sameImageAsset(optionImageUrl, candidateMainImageUrl)) score += 90;
        if (targetMainImageUrls.some((url) => sameImageAsset(optionImageUrl, url))) score += 70;
        if (item.selected) score += 8;
        return { ...item, score };
      })
      .filter((item) => item.score >= 70)
      .sort((left, right) => right.score - left.score || Number(right.selected) - Number(left.selected));
    if (imageMatchedSkuOptions.length) {
      imageMatchedSkuOption = imageMatchedSkuOptions[0];
      if (!imageMatchedSkuOption.selected && imageMatchedSkuOption.element) {
        const clicked = await clickSkuElementForPreview(imageMatchedSkuOption.element);
        if (clicked) {
          await ensureSelectedSkuQuantityPreview(imageMatchedSkuOption.value, imageMatchedSkuOption.element);
          refreshSkuPreviewAmounts();
        }
      } else {
        await ensureSelectedSkuQuantityPreview(imageMatchedSkuOption.value, imageMatchedSkuOption.element);
        refreshSkuPreviewAmounts();
      }
      markSkuOptionSelected(imageMatchedSkuOption);
    }

    const selectedOptions = specOptions.filter((item) => item.selected && !item.disabled);
    const selectedSpecText = selectedOptions.length
      ? selectedOptions.map((item) => `${item.group_name}:${item.value}${item.price_text ? ` ${item.price_text}` : ""}`).join("；").slice(0, 220)
      : lines
        .filter((line) => /已选/.test(line))
        .slice(0, 4)
        .join(" | ")
        .slice(0, 220);
    const variantPreview = specOptions
      .filter((item) => !item.disabled)
      .slice(0, 30)
      .map((item) => ({
        group_name: item.group_name,
        value: item.value,
        price_text: item.price_text,
        price_cny: item.price_cny,
        stock_text: item.stock_text,
        image_url: item.image_url,
        selected: item.selected,
        dimension_key: item.dimension_key
      }));
    const selectedSkuImageUrls = uniqueTexts(selectedOptions.map((item) => normalizeImageUrl(item.image_url)));
    const parseScriptPriceValue = (key, rawValue) => {
      const value = Number(String(rawValue || "").replace(/,/g, ""));
      if (!Number.isFinite(value) || value <= 0) return null;
      const keyText = String(key || "");
      const normalized = /cent|fen|分/i.test(keyText) ? value / 100 : value;
      if (!Number.isFinite(normalized) || normalized <= 0 || normalized > 10000) return null;
      return Math.round(normalized * 100) / 100;
    };
    const priceCandidatesFromScriptContext = (context) => {
      const output = [];
      const priceFieldRe = /["']?([A-Za-z0-9_]*(?:price|Price|amount|Amount|offerPrice|skuPrice|salePrice|discountPrice|promotionPrice|priceCent|priceInCent|priceInFen)[A-Za-z0-9_]*)["']?\s*[:=]\s*["']?(\d+(?:\.\d+)?)["']?/g;
      const acceptedPriceKeyRe = /^(?:price|pricevalue|pricecent|priceincent|priceinfen|saleprice|skuprice|skupricecent|discountprice|offerprice|promotionprice|unitprice|amount|amountcent)$/i;
      const configPriceKeyRe = /(?:replace|power|switch|config|setting|standard|feature|flag|enable|disable|show|hide|display|template|render)/i;
      let match = null;
      while ((match = priceFieldRe.exec(context)) && output.length < 24) {
        const key = match[1] || "";
        if (!acceptedPriceKeyRe.test(key) || configPriceKeyRe.test(key)) continue;
        if (/range|origin|market|retail|suggest|activity|coupon|freight|ship|post|logistic|deposit|tax/i.test(key)) continue;
        const cny = parseScriptPriceValue(key, match[2]);
        if (cny !== null) output.push({ key, cny });
      }
      return output;
    };
    const indexMatches = (haystack, needle, limit = 10) => {
      const indexes = [];
      const rawNeedle = String(needle || "");
      if (!haystack || rawNeedle.length < 8) return indexes;
      let cursor = 0;
      while (indexes.length < limit) {
        const index = haystack.indexOf(rawNeedle, cursor);
        if (index < 0) break;
        indexes.push(index);
        cursor = index + rawNeedle.length;
      }
      return indexes;
    };
    const findSelectedSkuScriptPrice = () => {
      const selectedValues = uniqueTexts([
        ...selectedOptions.map((item) => item.value),
        ...selectedOptions.map((item) => item.text),
        selectedSpecText
      ])
        .map((item) => optionValueText(item))
        .filter((item) => item.length >= 3)
        .slice(0, 8);
      const selectedImageSignatures = selectedSkuImageUrls
        .map((url) => imageSignature(url))
        .filter((item) => item.length >= 16)
        .slice(0, 6);
      const probes = [
        ...selectedValues.map((value) => ({ type: "selected_value", needle: value, score: 65 })),
        ...selectedImageSignatures.map((value) => ({ type: "selected_image", needle: value, score: 75 }))
      ];
      let best = null;
      for (const probe of probes) {
        for (const index of indexMatches(rawScriptText, probe.needle, 12)) {
          const context = rawScriptText.slice(Math.max(0, index - 2600), Math.min(rawScriptText.length, index + probe.needle.length + 2600));
          const prices = priceCandidatesFromScriptContext(context);
          if (!prices.length) continue;
          const contextText = text(context).slice(0, 520);
          const valueHits = selectedValues.filter((value) => context.includes(value)).length;
          const imageHits = selectedImageSignatures.filter((value) => context.includes(value)).length;
          for (const price of prices) {
            const score = probe.score + valueHits * 18 + imageHits * 22 + (/sku|spec|saleprop|offer|inventory|stock/i.test(context) ? 12 : 0);
            const candidate = {
              text: `selected SKU matrix price ¥${price.cny}`,
              cny: price.cny,
              source: "selected_sku_script_context",
              price_key: price.key,
              score,
              probe_type: probe.type,
              value_hits: valueHits,
              image_hits: imageHits,
              context_sample: contextText
            };
            if (!best || candidate.score > best.score) best = candidate;
          }
        }
      }
      if (!best || best.score < 80) {
        return {
          text: "",
          cny: null,
          source: "",
          score: best?.score || 0,
          selected_values: selectedValues.slice(0, 6),
          selected_image_signatures: selectedImageSignatures.slice(0, 4)
        };
      }
      return {
        ...best,
        selected_values: selectedValues.slice(0, 6),
        selected_image_signatures: selectedImageSignatures.slice(0, 4)
      };
    };
    const selectedSkuScriptPrice = findSelectedSkuScriptPrice();
    const objectValueByKeys = (object, keys) => {
      if (!object || typeof object !== "object") return undefined;
      for (const key of keys) {
        if (Object.prototype.hasOwnProperty.call(object, key) && object[key] !== undefined && object[key] !== null && object[key] !== "") {
          return object[key];
        }
      }
      return undefined;
    };
    const structuredMoneyValue = (value, keyHint = "") => {
      if (value && typeof value === "object") {
        value = objectValueByKeys(value, ["value", "amount", "price", "cent", "fen", "text"]);
      }
      const raw = String(value ?? "").replace(/,/g, "").trim();
      if (!raw) return null;
      const match = raw.match(/\d+(?:\.\d+)?/);
      if (!match) return null;
      let number = Number(match[0]);
      if (!Number.isFinite(number) || number <= 0) return null;
      if (/cent|fen|pricecent|priceincent|priceinfen/i.test(String(keyHint || ""))) number /= 100;
      if (!Number.isFinite(number) || number <= 0 || number > 10000) return null;
      return Math.round(number * 100) / 100;
    };
    const structuredPriceDetailFromObject = (object) => {
      if (!object || typeof object !== "object") return null;
      const keys = [
        "discountPrice",
        "promotionPrice",
        "activityPrice",
        "salePrice",
        "skuPrice",
        "finalPrice",
        "consignPrice",
        "offerPrice",
        "price",
        "priceCent",
        "priceInCent",
        "priceInFen",
        "amount",
        "amountCent"
      ];
      for (const key of keys) {
        if (!Object.prototype.hasOwnProperty.call(object, key)) continue;
        const price = structuredMoneyValue(object[key], key);
        if (price !== null) return { cny: price, key };
      }
      return null;
    };
    const structuredPriceFromObject = (object) => {
      const detail = structuredPriceDetailFromObject(object);
      return detail ? detail.cny : null;
    };
    const structuredSkuIdFromObject = (object, fallback = "") => text(objectValueByKeys(object, ["skuId", "skuID", "id", "sku", "specId", "specItemId"]) || fallback);
    const structuredStockTextFromObject = (object) => {
      if (!object || typeof object !== "object") return "";
      const value = objectValueByKeys(object, ["inventoryCount", "stock", "stockNum", "stockCount", "quantity", "amountOnSale", "canBookCount", "availableQuantity"]);
      if (value === undefined) return "";
      const raw = text(value);
      return raw ? `stock ${raw}`.slice(0, 80) : "";
    };
    const structuredWeightDetailFromObject = (object) => {
      if (!object || typeof object !== "object") return { text: "", kg: null, source: "" };
      const keys = [
        "weight",
        "weightKg",
        "skuWeight",
        "unitWeight",
        "grossWeight",
        "packageWeight",
        "itemWeight",
        "netWeight"
      ];
      const unit = objectValueByKeys(object, ["weightUnit", "unit", "weight_unit", "grossWeightUnit", "packageWeightUnit"]);
      for (const key of keys) {
        if (!Object.prototype.hasOwnProperty.call(object, key)) continue;
        let value = object[key];
        let valueUnit = unit || "";
        if (value && typeof value === "object") {
          valueUnit = objectValueByKeys(value, ["unit", "weightUnit", "uom"]) || valueUnit;
          value = objectValueByKeys(value, ["value", "amount", "weight", "text"]);
        }
        const inferredUnit = valueUnit || (/kg/i.test(key) ? "kg" : "");
        const parsed = parseWeightFromText(`${key} ${value}${inferredUnit || ""}`);
        if (parsed.text) return { ...parsed, source: `structured_sku_${key}` };
        const numeric = weightKgFromNumberAndUnit(value, inferredUnit);
        if (numeric !== null) {
          const displayUnit = normalizeWeightUnit(inferredUnit) || (Number(value) > 20 ? "g" : "kg");
          return {
            text: `重量 ${String(value).match(/\d+(?:\.\d+)?/)?.[0] || numeric}${displayUnit}`.slice(0, 80),
            kg: numeric,
            source: `structured_sku_${key}`
          };
        }
      }
      return { text: "", kg: null, source: "" };
    };
    const walkStructuredObjects = (root, callback, limit = 5000) => {
      const seen = new WeakSet();
      const stack = [root];
      let visited = 0;
      while (stack.length && visited < limit) {
        const value = stack.pop();
        if (!value || typeof value !== "object") continue;
        if (value === window || value === document || value.nodeType) continue;
        if (seen.has(value)) continue;
        seen.add(value);
        visited += 1;
        callback(value);
        const children = Array.isArray(value) ? value : Object.values(value);
        for (let index = 0; index < children.length && stack.length < limit; index += 1) {
          const child = children[index];
          if (child && typeof child === "object") stack.push(child);
        }
      }
      return visited;
    };
    const structuredSourcesFromWindow = () => {
      const output = [];
      [
        "detailData",
        "context",
        "globalData",
        "offerDetailData",
        "productDetailData",
        "offerData",
        "pageData",
        "__INIT_DATA__",
        "__PAGE_DATA__",
        "__PAGE_DATA",
        "GLOBAL_DATA",
        "FE_GLOBALS"
      ].forEach((key) => {
        try {
          const value = window[key];
          if (value && typeof value === "object") output.push({ source: `window:${key}`, value });
        } catch (_error) {}
      });
      return output;
    };
    const structuredPropValueMap = (skuProps) => {
      const map = {};
      if (!Array.isArray(skuProps)) return map;
      for (const prop of skuProps) {
        if (!prop || typeof prop !== "object") continue;
        const groupName = text(objectValueByKeys(prop, ["name", "title", "label", "propName", "propertyName", "attributeName", "specName", "optionName", "salePropName"]) || "");
        const propId = text(objectValueByKeys(prop, ["id", "propId", "pid", "propertyId", "fid"]) || "");
        const values = objectValueByKeys(prop, ["values", "value", "children", "items", "props", "propertyValues", "propValues", "valueList", "specValueList", "optionList", "specValues", "valueItems", "saleValueList"]);
        if (!Array.isArray(values)) continue;
        for (const item of values) {
          if (!item || typeof item !== "object") continue;
          const value = optionValueText(objectValueByKeys(item, ["value", "text", "label", "name", "valueName", "propertyValueName", "specValueName", "optionValue", "title"]) || "");
          if (!value) continue;
          const entry = {
            group_name: groupName || inferGroupName(value),
            value,
            image_url: imageFromElement(null) || normalizeImageUrl(objectValueByKeys(item, ["imageUrl", "image", "imgUrl", "picUrl", "skuImageUrl", "originImage", "url"]) || ""),
            stock_text: structuredStockTextFromObject(item)
          };
          [
            objectValueByKeys(item, ["id", "valueId", "vid", "fid", "propertyValueId", "specId"]),
            propId && objectValueByKeys(item, ["id", "valueId", "vid", "fid", "propertyValueId", "specId"]) ? `${propId}:${objectValueByKeys(item, ["id", "valueId", "vid", "fid", "propertyValueId", "specId"])}` : "",
            propId && objectValueByKeys(item, ["id", "valueId", "vid", "fid", "propertyValueId", "specId"]) ? `${propId}_${objectValueByKeys(item, ["id", "valueId", "vid", "fid", "propertyValueId", "specId"])}` : "",
            value
          ].filter(Boolean).forEach((key) => { map[String(key)] = entry; });
        }
      }
      return map;
    };
    const structuredAttrsFromObject = (object, propMap = {}) => {
      const attrs = {};
      const attrSource = objectValueByKeys(object, ["specAttributes", "skuAttributes", "attributes", "saleProps", "props", "properties", "specs", "skuProps", "saleAttrs", "saleSpecs", "specList"]);
      const putAttr = (name, value) => {
        const cleanValue = optionValueText(value);
        if (!cleanValue) return;
        attrs[text(name || inferGroupName(cleanValue)) || "spec"] = cleanValue;
      };
      if (Array.isArray(attrSource)) {
        for (const attr of attrSource) {
          if (!attr || typeof attr !== "object") continue;
          putAttr(
            objectValueByKeys(attr, ["name", "title", "label", "propName", "propertyName", "attributeName"]),
            objectValueByKeys(attr, ["value", "text", "label", "valueName", "propertyValueName", "name"])
          );
        }
      } else if (attrSource && typeof attrSource === "object") {
        Object.entries(attrSource).forEach(([key, value]) => {
          if (value && typeof value === "object") {
            putAttr(
              objectValueByKeys(value, ["name", "title", "label", "propName", "propertyName"]) || key,
              objectValueByKeys(value, ["value", "text", "label", "valueName", "name"])
            );
          } else {
            putAttr(key, value);
          }
        });
      }
      return attrs;
    };
    const cleanSourceAttributeName = (value) => {
      const cleaned = text(value)
        .replace(/^[\s:：-]+|[\s:：-]+$/g, "")
        .replace(/\s+/g, " ");
      if (!cleaned || cleaned.length > 80) return "";
      if (/^(?:key|value|name|label|text|id|sku|skuid|sku id)$/i.test(cleaned)) return "";
      if (/price|stock|inventory|freight|shipping|logistics|coupon|discount|saleprice|sku/i.test(cleaned)) return "";
      if (/价格|库存|现货|可售|起批|已售|销量|运费|物流|快递|优惠|折扣/.test(cleaned)) return "";
      return cleaned;
    };
    const sourceAttributeUnitFromName = (name) => {
      const raw = text(name).toLowerCase();
      if (!raw) return "";
      if (/(容量|容积|capacity|volume)/i.test(raw)) {
        if (/毫升|(?:^|[^a-z])ml(?:$|[^a-z])/i.test(raw)) return "ml";
        if (/升|(?:^|[^a-z])l(?:$|[^a-z])/i.test(raw)) return "L";
        if (/fl\s*oz|盎司/i.test(raw)) return "fl oz";
        if (/oz/i.test(raw)) return "oz";
      }
      if (/(重量|克重|weight)/i.test(raw)) {
        if (/千克|公斤|(?:^|[^a-z])kg(?:$|[^a-z])/i.test(raw)) return "kg";
        if (/克|(?:^|[^a-z])g(?:$|[^a-z])/i.test(raw)) return "g";
      }
      if (/(袖长|长度|宽度|高度|尺寸|length|width|height|sleeve)/i.test(raw)) {
        if (/毫米|(?:^|[^a-z])mm(?:$|[^a-z])/i.test(raw)) return "mm";
        if (/厘米|(?:^|[^a-z])cm(?:$|[^a-z])/i.test(raw)) return "cm";
        if (/英寸|inch|inches|(?:^|[^a-z])in(?:$|[^a-z])/i.test(raw)) return "in";
      }
      if (/(包装数量|件数|数量|package\s*quantity|pack\s*quantity|quantity|count)/i.test(raw)) {
        if (/件|个|只|片|pcs?|pieces?|pack|set/i.test(raw)) return "pcs";
      }
      return "";
    };
    const cleanSourceAttributeValue = (value, name = "") => {
      const cleaned = optionValueText(value)
        .replace(/^[\s:：-]+|[\s:：-]+$/g, "")
        .replace(/\s+/g, " ");
      if (!cleaned || cleaned.length > 140) return "";
      if (/^(?:全部参数|商品参数|产品参数|规格参数|详细参数|基本参数)$/i.test(cleaned)) return "";
      if (/^(?:¥|￥|\$)?\d+(?:\.\d+)?$/.test(cleaned)) {
        const unit = sourceAttributeUnitFromName(name);
        return unit && !/^(?:¥|￥|\$)/.test(cleaned) ? `${cleaned} ${unit}` : "";
      }
      if (/(?:库存|现货|可售|价格|起批|已售|销量|¥|￥|\$)\s*\d|\d+\s*(?:库存|现货|可售|起批|已售|销量)/i.test(cleaned)) return "";
      return cleaned;
    };
    const extractSourceAttributeData = () => {
      const attributes = {};
      const pairs = [];
      const table = [];
      const seen = new Set();
      const addPair = (name, value, source) => {
        const cleanName = cleanSourceAttributeName(name);
        const cleanValue = cleanSourceAttributeValue(value);
        if (!cleanName || !cleanValue || cleanName === cleanValue) return;
        const key = cleanName.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);
        attributes[cleanName] = cleanValue;
        pairs.push({ name: cleanName, value: cleanValue, source });
        table.push({ key: cleanName, value: cleanValue, source });
      };
      const childTexts = (element) => Array.from(element?.children || [])
        .map((child) => text(child.innerText || child.textContent))
        .filter(Boolean);
      const addRowFromCells = (element, source) => {
        const cells = childTexts(element);
        if (cells.length >= 2) addPair(cells[0], cells.slice(1).join(" "), source);
      };
      const parameterSignalRe = /parameter|attribute|property|specification|product\s*detail|产品参数|商品参数|规格参数|详细参数|基本参数|产品属性|商品属性/i;
      const skuContainerRe = /sku|sale[-_]?prop|variation|variant/i;
      const containers = Array.from(document.querySelectorAll("table, tr, dl, ul, ol, [class*='param'], [class*='attribute'], [class*='property'], [class*='specification'], [data-testid*='attribute'], [data-spm*='attribute']"))
        .filter(visible)
        .slice(0, 120);
      for (const container of containers) {
        const tagName = String(container.tagName || "").toUpperCase();
        const classText = text(`${container.className || ""} ${container.getAttribute?.("class") || ""} ${container.getAttribute?.("data-testid") || ""} ${container.getAttribute?.("data-spm") || ""}`);
        const fullText = text(container.innerText || container.textContent);
        const parameterLike = ["TABLE", "TR", "DL"].includes(tagName) || parameterSignalRe.test(`${classText} ${fullText.slice(0, 160)}`);
        if (!parameterLike || skuContainerRe.test(classText) && !parameterSignalRe.test(`${classText} ${fullText.slice(0, 160)}`)) continue;
        if (tagName === "TR") addRowFromCells(container, "dom-parameter-table");
        Array.from(container.querySelectorAll?.("tr") || []).slice(0, 80).forEach((row) => addRowFromCells(row, "dom-parameter-table"));
        const children = Array.from(container.children || []);
        for (let index = 0; index < children.length; index += 1) {
          const child = children[index];
          addRowFromCells(child, "dom-parameter-list");
          const childTag = String(child.tagName || "").toUpperCase();
          if (childTag === "DT" && children[index + 1] && String(children[index + 1].tagName || "").toUpperCase() === "DD") {
            addPair(child.innerText || child.textContent, children[index + 1].innerText || children[index + 1].textContent, "dom-parameter-dl");
          }
        }
        fullText.split(/\n+/).map(text).filter(Boolean).slice(0, 80).forEach((line) => {
          const matched = line.match(/^([^:：]{2,40})[:：]\s*(.{1,140})$/);
          if (matched) addPair(matched[1], matched[2], "dom-parameter-text");
        });
      }
      const attrContainerKeyRe = /attributes?|properties|parameters?|specs?|productProps?|productProperties|productFeatures|featureList|detailList|attrList|propList/i;
      const skuOrCommerceKeyRe = /sku|skuProps?|skuInfo|saleProps?|inventory|stock|price|freight|shipping|logistics|coupon|promotion/i;
      const nameKeys = ["name", "title", "label", "key", "attrName", "attributeName", "propertyName", "propName", "specName"];
      const valueKeys = ["value", "text", "content", "attrValue", "attributeValue", "propertyValue", "propValue", "valueName", "propertyValueName", "specValue"];
      const addFromContainer = (value, source) => {
        if (Array.isArray(value)) {
          value.slice(0, 200).forEach((item) => {
            if (!item || typeof item !== "object") return;
            const name = objectValueByKeys(item, nameKeys);
            const attrValue = objectValueByKeys(item, valueKeys);
            if (name && attrValue !== undefined) addPair(name, attrValue, source);
          });
        } else if (value && typeof value === "object") {
          Object.entries(value).slice(0, 200).forEach(([key, item]) => {
            if (item && typeof item === "object") {
              const name = objectValueByKeys(item, nameKeys) || key;
              const attrValue = objectValueByKeys(item, valueKeys);
              if (name && attrValue !== undefined) addPair(name, attrValue, source);
            } else {
              addPair(key, item, source);
            }
          });
        }
      };
      for (const source of structuredSourcesFromWindow()) {
        walkStructuredObjects(source.value, (object) => {
          if (!object || typeof object !== "object" || Array.isArray(object)) return;
          Object.entries(object).forEach(([key, value]) => {
            if (!attrContainerKeyRe.test(key) || skuOrCommerceKeyRe.test(key)) return;
            addFromContainer(value, `${source.source}:${key}`);
          });
        }, 5000);
      }
      return { attributes, pairs: pairs.slice(0, 80), table: table.slice(0, 80) };
    };
    const structuredAttrsFromSkuKey = (key, item, propMap = {}) => {
      const attrs = structuredAttrsFromObject(item, propMap);
      const addMapped = (mapped) => {
        if (!mapped?.value) return;
        attrs[mapped.group_name || inferGroupName(mapped.value)] = mapped.value;
      };
      String(key || "")
        .split(/[;,\s]+/)
        .filter(Boolean)
        .forEach((part) => {
          addMapped(propMap[part]);
          part.split(/[:_]/).filter(Boolean).forEach((piece) => addMapped(propMap[piece]));
        });
      return attrs;
    };
    const collectStructuredStockBySkuId = (root) => {
      const output = new Map();
      walkStructuredObjects(root, (object) => {
        const mapSource = object.skuMap || object.inventorySkuMap || object.repoSkuMap || object.skuInventoryMap;
        if (!mapSource || typeof mapSource !== "object") return;
        const entries = Array.isArray(mapSource) ? mapSource.map((item, index) => [String(index), item]) : Object.entries(mapSource);
        for (const [key, item] of entries) {
          if (!item || typeof item !== "object") continue;
          const skuId = structuredSkuIdFromObject(item, key);
          const stock = structuredStockTextFromObject(item);
          if (skuId && stock && !output.has(skuId)) output.set(skuId, stock);
        }
      }, 2500);
      return output;
    };
    const structuredSkuCombosFromWindow = () => {
      const combos = [];
      const seenCombo = new Set();
      for (const source of structuredSourcesFromWindow()) {
        const stockBySkuId = collectStructuredStockBySkuId(source.value);
        walkStructuredObjects(source.value, (object) => {
          const skuProps = object.skuProps || object.sku_props || object.saleProps || object.salePropList || object.props || object.propertyList || object.skuAttrs || object.skuAttrList;
          const mapSource = object.skuInfoMap || object.skuMap || object.skuInfo || object.skuInfos || object.skuItems || object.skus || object.skuList;
          if (!mapSource || typeof mapSource !== "object") return;
          const propMap = structuredPropValueMap(skuProps);
          const entries = Array.isArray(mapSource) ? mapSource.map((item, index) => [String(index), item]) : Object.entries(mapSource);
          for (const [key, item] of entries) {
            if (!item || typeof item !== "object") continue;
            const priceDetail = structuredPriceDetailFromObject(item);
            if (!priceDetail) continue;
            const price = priceDetail.cny;
            const attrs = structuredAttrsFromSkuKey(key, item, propMap);
            const attrValues = Object.values(attrs).filter(Boolean);
            const imageUrl = normalizeImageUrl(objectValueByKeys(item, ["imageUrl", "image", "imgUrl", "picUrl", "skuImageUrl"]) || attrValues.map((value) => propMap[value]?.image_url).find(Boolean) || "");
            const skuId = structuredSkuIdFromObject(item, key);
            const skuPathFields = skuPathFieldsFromObject(item, key);
            const stockTextValue = structuredStockTextFromObject(item) || (skuId ? stockBySkuId.get(skuId) : "") || "";
            const structuredWeight = structuredWeightDetailFromObject(item);
            const comboKey = `${source.source}|${key}|${skuId}|${price}`;
            if (seenCombo.has(comboKey)) continue;
            seenCombo.add(comboKey);
            combos.push({
              source: source.source,
              key: String(key),
              sku_id: skuId,
              price,
              price_key: priceDetail.key,
              text: `detailData skuInfoMap price CNY ${price}`,
              stock_text: stockTextValue,
              weight_text: structuredWeight.text || "",
              weight_kg: structuredWeight.kg,
              weight_source: structuredWeight.source || "",
              image_url: imageUrl,
              attributes: attrs,
              attribute_values: attrValues,
              ...skuPathFields
            });
          }
        }, 5000);
      }
      return combos.slice(0, 500);
    };
    const structuredSkuCombosForDetail = structuredSkuCombosFromWindow();
    const sourceAttributeData = extractSourceAttributeData();
    const findStructuredSkuPrice = (combos = structuredSkuCombosForDetail) => {
      const selectedValueHints = uniqueTexts([
        ...selectedOptions.map((item) => item.value),
        ...selectedOptions.map((item) => item.text),
        selectedSpecText,
        imageMatchedSkuOption?.value || "",
        matchedByDimension?.value || "",
        matchedSpecText || ""
      ])
        .map((item) => optionValueText(item))
        .filter((item) => item.length >= 2)
        .slice(0, 10);
      const selectedImageUrlsForStructured = uniqueTexts([
        ...selectedSkuImageUrls,
        imageMatchedSkuOption?.image_url || "",
        matchedByDimension?.image_url || ""
      ]).filter(Boolean);
      const selectedImageSignatures = selectedImageUrlsForStructured
        .map((url) => imageSignature(url))
        .filter((item) => item.length >= 16)
        .slice(0, 6);
      let best = null;
      for (const combo of combos) {
        const attrText = optionValueText(combo.attribute_values.join(" "));
        const comboImageSignature = imageSignature(combo.image_url);
        let score = 0;
        let matchedHint = "";
        for (const hint of selectedValueHints) {
          if (!hint) continue;
          if (attrText && attrText === hint) {
            score += 120;
            matchedHint = matchedHint || hint;
          } else if (attrText && (attrText.includes(hint) || (attrText.length >= 3 && hint.includes(attrText)))) {
            score += 80;
            matchedHint = matchedHint || hint;
          }
        }
        if (comboImageSignature && selectedImageSignatures.some((sig) => sig === comboImageSignature || sig.includes(comboImageSignature) || comboImageSignature.includes(sig))) {
          score += 90;
          matchedHint = matchedHint || "sku_image";
        }
        if (combo.stock_text) score += 8;
        if (combo.sku_id) score += 8;
        if (combos.length === 1 && !selectedValueHints.length) score += 70;
        if (!best || score > best.score) {
          best = { ...combo, score, matched_hint: matchedHint, candidates_count: combos.length };
        }
      }
      if (!best || best.score < 70) {
        return {
          text: "",
          cny: null,
          source: "",
          sku_id: "",
          stock_text: "",
          weight_text: "",
          weight_kg: null,
          weight_source: "",
          score: best?.score || 0,
          matched_hint: "",
          attributes: {},
          candidates_count: combos.length
        };
      }
      return {
        text: best.text,
        cny: best.price,
        source: best.source,
        sku_id: best.sku_id || "",
        stock_text: best.stock_text || "",
        weight_text: best.weight_text || "",
        weight_kg: best.weight_kg ?? null,
        weight_source: best.weight_source || "",
        score: best.score,
        matched_hint: best.matched_hint || "",
        attributes: best.attributes || {},
        key: best.key || "",
        price_key: best.price_key || "",
        candidates_count: best.candidates_count || combos.length
      };
    };
    const currentLargeProductImageUrls = pageProductImages
      .filter((item) => item.width >= 160 && item.height >= 160 && !/sku|thumb|value|option/i.test(item.context || ""))
      .slice(0, 4)
      .map((item) => item.url);
    const defaultProductImageUrls = uniqueTexts([
      ...selectedSkuImageUrls,
      ...currentLargeProductImageUrls
    ]);
    const productVisualEvidence = [];
    let productVisualScore = 0;
    let hasDefaultVisualBasis = false;
    const addProductVisualEvidence = (label, points, defaultBasis = false) => {
      productVisualEvidence.push(label);
      productVisualScore += points;
      hasDefaultVisualBasis = Boolean(hasDefaultVisualBasis || defaultBasis);
    };
    const defaultMatchesCandidateImage = Boolean(candidateMainImageUrl && defaultProductImageUrls.some((url) => sameImageAsset(url, candidateMainImageUrl)));
    const selectedMatchesCandidateImage = Boolean(candidateMainImageUrl && selectedSkuImageUrls.some((url) => sameImageAsset(url, candidateMainImageUrl)));
    const defaultMatchesTargetImage = Boolean(targetMainImageUrls.length && defaultProductImageUrls.some((url) => targetMainImageUrls.some((targetUrl) => sameImageAsset(url, targetUrl))));
    if (forcedSkuSelectionApplied && imageMatchedSkuOption) addProductVisualEvidence(`employee_action_selected_ai_target_sku:${imageMatchedSkuOption.value}`, 70, true);
    if (imageMatchedSkuOption) addProductVisualEvidence(`auto_selected_sku_by_candidate_or_target_image:${imageMatchedSkuOption.value}`, 28, true);
    if (selectedMatchesCandidateImage) addProductVisualEvidence("selected_sku_product_visual_matches_candidate_product", 62, true);
    else if (defaultMatchesCandidateImage) addProductVisualEvidence("default_or_current_product_visual_matches_candidate_product", 55, true);
    if (defaultMatchesTargetImage) addProductVisualEvidence("default_or_current_product_visual_matches_temu_product", 55, true);
    if (targetMainImageUrls.length && candidateMainImageUrl && targetMainImageUrls.some((url) => sameImageAsset(url, candidateMainImageUrl))) {
      addProductVisualEvidence("temu_product_visual_matches_candidate_product", 45, Boolean(defaultProductImageUrls.length));
    }
    if (candidateListRank > 0 && candidateMainImageUrl && defaultProductImageUrls.length) {
      if (candidateListRank === 1) addProductVisualEvidence("temaishuju_visual_search_rank_1_with_current_detail_image", 30, false);
      else if (candidateListRank <= 3) addProductVisualEvidence("temaishuju_visual_search_top_3_with_current_detail_image", 20, false);
      else if (candidateListRank <= 5) addProductVisualEvidence("temaishuju_visual_search_top_5_with_default_detail_image", 18, false);
    }
    if (candidateMainImageUrl && defaultProductImageUrls.length) addProductVisualEvidence("candidate_and_default_product_images_available", 15, false);
    if (selectedSkuImageUrls.length) addProductVisualEvidence("selected_sku_thumbnail_visible", 12, false);
    else if (defaultProductImageUrls.length) addProductVisualEvidence("current_detail_product_image_visible", 10, false);
    const meaningfulTokens = (value) => text(value).toLowerCase()
      .split(/[^a-z0-9\u4e00-\u9fff]+/i)
      .filter((item) => item.length >= 4 && !/^(with|from|this|that|for|and|the|shop|sale|wholesale|factory|supplier)$/.test(item))
      .slice(0, 30);
    const targetAuxTokens = new Set(meaningfulTokens(matchContext?.product_title || ""));
    const sourceAuxTokens = meaningfulTokens(`${title} ${selectedSpecText} ${matchContext?.candidate_title || ""}`);
    const auxiliaryOverlap = sourceAuxTokens.filter((item) => targetAuxTokens.has(item));
    if (auxiliaryOverlap.length >= 2) addProductVisualEvidence(`aux_text_overlap:${auxiliaryOverlap.slice(0, 4).join(",")}`, 8, false);
    const quantitySignal = (value) => {
      const raw = text(value).toLowerCase();
      if (!raw) return null;
      const patterns = [
        /(\d+(?:\.\d+)?)\s*[- ]?\s*(?:pack|packs|pcs|pc|pieces|piece|set|sets|kit|kits)\b/i,
        /(\d+(?:\.\d+)?)\s*(?:件套|件装|只装|支装|个装|入装|套装|片装|双装|组装)/
      ];
      for (const pattern of patterns) {
        const match = raw.match(pattern);
        if (match) return Number(match[1]);
      }
      if (/\b(single|one)\s*(?:piece|pc|pack|set)\b|单个|单件|1个装/.test(raw)) return 1;
      return null;
    };
    const targetQuantitySignal = quantitySignal(matchContext?.product_title || "");
    const sourceQuantitySignal = quantitySignal(`${title} ${selectedSpecText} ${matchContext?.candidate_title || ""}`);
    const quantityOrPackConflict = targetQuantitySignal !== null
      && sourceQuantitySignal !== null
      && Math.abs(Number(targetQuantitySignal) - Number(sourceQuantitySignal)) > 0.01;
    if (quantityOrPackConflict) {
      productVisualEvidence.push(`quantity_or_pack_conflict:target_${targetQuantitySignal}_source_${sourceQuantitySignal}`);
    }
    const sourceProductVisualMatchScore = Math.max(0, Math.min(100, Math.round(productVisualScore)));
    const sourceProductVisualMatchStatus = quantityOrPackConflict
      ? "quantity_or_pack_conflict"
      : (hasDefaultVisualBasis && sourceProductVisualMatchScore >= 65
        ? "confirmed"
        : (hasDefaultVisualBasis && sourceProductVisualMatchScore >= 45
          ? "likely_same_product"
          : "insufficient_product_visual_evidence"));
    const defaultSkuProductVisualConfirmed = sourceProductVisualMatchStatus === "confirmed";
    const quoteDimensionKeys = new Set(quoteDimensionSignals.map((item) => item.key));
    const matchedByDimension = quoteDimensionKeys.size
      ? specOptions.find((item) => item.dimension_key && quoteDimensionKeys.has(item.dimension_key) && !item.disabled) || null
      : null;
    const matchedSpecText = matchedByDimension
      ? `${matchedByDimension.group_name}:${matchedByDimension.value}${matchedByDimension.price_text ? ` ${matchedByDimension.price_text}` : ""}${matchedByDimension.stock_text ? ` ${matchedByDimension.stock_text}` : ""}`.slice(0, 220)
      : "";
    let matchedSkuPreviewSelected = Boolean(
      matchedByDimension
      && (matchedByDimension.selected || (selectedSpecText && selectedSpecText.includes(matchedByDimension.value)))
    );
    if (!defaultSkuProductVisualConfirmed && matchedByDimension && !matchedSkuPreviewSelected) {
      const matchedClickable = clickableSpecOptions.find((item) => (
        !item.disabled
        && item.dimension_key
        && item.dimension_key === matchedByDimension.dimension_key
        && (item.value === matchedByDimension.value || item.value.includes(matchedByDimension.value) || matchedByDimension.value.includes(item.value))
      ));
      if (matchedClickable?.element) {
        try {
          await clickSkuElementForPreview(matchedClickable.element);
          await ensureSelectedSkuQuantityPreview(matchedClickable.value, matchedClickable.element);
          refreshSkuPreviewAmounts();
          refreshWeightEvidence();
          matchedSkuPreviewSelected = true;
        } catch (error) {
          matchedSkuPreviewSelected = false;
        }
      }
    }
    if (defaultSkuProductVisualConfirmed && skuPreviewProductAmount.cny === null) {
      const selectedClickable = clickableSpecOptions.find((item) => item.selected && !item.disabled)
        || clickableSpecOptions.find((item) => !item.disabled && selectedSpecText && selectedSpecText.includes(item.value));
      if (selectedClickable?.element) {
        await clickSkuElementForPreview(selectedClickable.element);
        await ensureSelectedSkuQuantityPreview(selectedClickable.value, selectedClickable.element);
      } else {
        await ensureSelectedSkuQuantityPreview(selectedOptions[0]?.value || selectedSpecText || "", null);
      }
      refreshSkuPreviewAmounts();
      refreshWeightEvidence();
    }
    const selectedSkuRowPrice = findSelectedSkuRowPrice(selectedOptions.map((item) => item.value));
    const selectedDefaultOption = selectedOptions.find((item) => item.price_cny !== null || item.price_text) || selectedOptions[0] || null;
    let skuMatchStatus = "matrix_not_found";
    if (defaultSkuProductVisualConfirmed) {
      skuMatchStatus = "default_sku_confirmed";
    } else if (matchedByDimension && matchedSkuPreviewSelected) {
      skuMatchStatus = "matched_by_dimension";
    } else if (matchedByDimension) {
      skuMatchStatus = selectedSpecText ? "default_selected_needs_review" : "matrix_no_quote_spec";
    } else if (quoteDimensionKeys.size && specOptions.some((item) => item.dimension_key)) {
      skuMatchStatus = "quote_dimension_not_found";
    } else if (specOptions.length > 1) {
      skuMatchStatus = selectedSpecText ? "default_selected_needs_review" : "matrix_no_quote_spec";
    } else if (specOptions.length === 1) {
      skuMatchStatus = "single_visible_spec";
    }
    const effectiveMatchedSpecText = skuMatchStatus === "default_sku_confirmed" ? selectedSpecText : matchedSpecText;
    const sourceSpecText = skuMatchStatus === "default_sku_confirmed"
      ? `default SKU product visual confirmed: ${selectedSpecText || title}`.slice(0, 260)
      : (matchedSpecText
        ? `匹配规格：${matchedSpecText}`
      : (selectedSpecText
        ? `页面默认/已选：${selectedSpecText}；可选规格 ${specOptions.length || 0} 个，需复核`
        : (specOptions.length ? `可选规格 ${specOptions.length} 个，平台标题未识别到明确规格，需复核` : "")));
    const skuMatchNote = skuMatchStatus === "default_sku_confirmed"
      ? "default SKU confirmed by product-visual evidence; title/SKU text used only as auxiliary conflict check"
      : (skuMatchStatus === "matched_by_dimension"
        ? "已按平台标题尺寸匹配对应货源规格"
        : (skuMatchStatus === "quote_dimension_not_found"
        ? "平台标题包含尺寸，但货源可见规格未找到同尺寸"
        : (sourceProductVisualMatchStatus === "quantity_or_pack_conflict"
          ? "default SKU visual evidence blocked by quantity/pack conflict"
          : (specOptions.length > 1 ? "default SKU not confirmed by product-visual evidence; keep review" : ""))));
    const selectedRowPriceAppliesToCurrentSku = selectedSkuRowPrice.cny !== null && (
      skuMatchStatus === "default_sku_confirmed"
      || skuMatchStatus === "single_visible_spec"
      || (matchedByDimension && matchedSkuPreviewSelected)
    );
    const structuredSkuPrice = findStructuredSkuPrice();
    const structuredPriceAppliesToCurrentSku = structuredSkuPrice.cny !== null && (
      skuMatchStatus === "default_sku_confirmed"
      || skuMatchStatus === "single_visible_spec"
      || (matchedByDimension && matchedSkuPreviewSelected)
    );
    const selectedOptionPriceAllowed = specOptions.length <= 1 || skuMatchStatus === "single_visible_spec";
    const defaultConfirmedPriceText = skuPreviewProductAmount.text
      || (structuredPriceAppliesToCurrentSku ? structuredSkuPrice.text : "")
      || (selectedRowPriceAppliesToCurrentSku ? selectedSkuRowPrice.text : "")
      || (selectedOptionPriceAllowed ? selectedDefaultOption?.price_text : "")
      || "";
    const defaultConfirmedPriceCny = skuPreviewProductAmount.cny !== null
      ? skuPreviewProductAmount.cny
      : (structuredPriceAppliesToCurrentSku
        ? structuredSkuPrice.cny
        : (selectedRowPriceAppliesToCurrentSku
          ? selectedSkuRowPrice.cny
          : (selectedOptionPriceAllowed ? (selectedDefaultOption?.price_cny ?? null) : null)));
    const effectiveMatchedPriceText = skuMatchStatus === "default_sku_confirmed" && defaultConfirmedPriceText
      ? defaultConfirmedPriceText
      : (matchedByDimension && matchedSkuPreviewSelected && skuPreviewProductAmount.text
      ? skuPreviewProductAmount.text
      : (structuredPriceAppliesToCurrentSku && structuredSkuPrice.text
        ? structuredSkuPrice.text
        : (selectedRowPriceAppliesToCurrentSku && selectedSkuRowPrice.text
          ? selectedSkuRowPrice.text
          : (selectedOptionPriceAllowed ? (matchedByDimension?.price_text || selectedDefaultOption?.price_text || "") : ""))));
    const effectiveMatchedPriceCny = skuMatchStatus === "default_sku_confirmed" && defaultConfirmedPriceCny !== null
      ? defaultConfirmedPriceCny
      : (matchedByDimension && matchedSkuPreviewSelected && skuPreviewProductAmount.cny !== null
      ? skuPreviewProductAmount.cny
      : (structuredPriceAppliesToCurrentSku && structuredSkuPrice.cny !== null
        ? structuredSkuPrice.cny
        : (selectedRowPriceAppliesToCurrentSku && selectedSkuRowPrice.cny !== null
          ? selectedSkuRowPrice.cny
          : (selectedOptionPriceAllowed ? (matchedByDimension?.price_cny ?? selectedDefaultOption?.price_cny ?? null) : null))));
    const previewFreightAppliesToMatchedSku = previewFreightAmountShouldOverrideDetail(skuPreviewFreightAmount) && (skuMatchStatus === "default_sku_confirmed" || !matchedByDimension || matchedSkuPreviewSelected);
    if (!previewFreightAppliesToMatchedSku) {
      freightText = detailFreightText;
      freightCny = detailFreightCny;
    }
    const freightConfidence = previewFreightAppliesToMatchedSku
      ? "sku_preview"
      : (detailFreightCny === 0
        ? "explicit_free_shipping"
        : (typeof detailFreightCny === "number" && Number.isFinite(detailFreightCny) ? "explicit_detail" : "missing"));
    const freightSource = previewFreightAppliesToMatchedSku
      ? "sku_preview_total"
      : (detailFreightCny === 0 ? "detail_free_shipping" : (freightConfidence === "explicit_detail" ? "detail_freight_line" : ""));
    const skuPriceSource = skuMatchStatus === "default_sku_confirmed"
      ? (skuPreviewProductAmount.cny !== null
        ? "default_sku_confirmed_preview_amount"
        : (structuredPriceAppliesToCurrentSku ? "default_sku_confirmed_structured_price" : (selectedRowPriceAppliesToCurrentSku ? "default_sku_confirmed_row_price" : (selectedSkuScriptPrice.cny !== null ? "default_sku_confirmed_matrix_price" : "default_sku_confirmed_price"))))
      : (matchedByDimension && matchedSkuPreviewSelected
      ? (matchedSkuPreviewSelected && skuPreviewProductAmount.cny !== null ? "matched_sku_preview_amount" : (structuredPriceAppliesToCurrentSku ? "matched_sku_structured_price" : (selectedRowPriceAppliesToCurrentSku ? "matched_sku_row_price" : "matched_sku_price")))
      : (skuMatchStatus === "single_visible_spec"
        ? (skuPreviewProductAmount.cny !== null ? "sku_preview_product_amount" : (structuredPriceAppliesToCurrentSku ? "selected_sku_structured_price" : (selectedRowPriceAppliesToCurrentSku ? "selected_sku_row_price" : "single_visible_spec")))
        : (selectedSpecText ? "selected_default_sku_price" : "list_price")));
    const matrixSkuPriceSource = selectedSkuScriptPrice.cny !== null
      ? (skuMatchStatus === "matched_by_dimension"
        ? "matched_sku_matrix_price"
        : (skuMatchStatus === "default_sku_confirmed" ? "default_sku_confirmed_matrix_price" : "selected_sku_matrix_price"))
      : "";
    const closedSkuPriceSource = [
      "default_sku_confirmed_preview_amount",
      "default_sku_confirmed_structured_price",
      "default_sku_confirmed_row_price",
      "matched_sku_preview_amount",
      "matched_sku_structured_price",
      "matched_sku_row_price",
      "matched_sku_price",
      "sku_preview_product_amount",
      "selected_sku_structured_price",
      "selected_sku_row_price",
      "single_visible_spec"
    ].includes(skuPriceSource);
    const sourceCostClosed = Boolean(
      (skuMatchStatus === "default_sku_confirmed" || skuMatchStatus === "matched_by_dimension" || skuMatchStatus === "single_visible_spec")
      && freightConfidence !== "missing"
      && effectiveMatchedPriceCny !== null
      && closedSkuPriceSource
    );
    const variantRecordKey = (record) => [
      record.spec_text || "",
      record.name || "",
      record.image_url || "",
      record.price_text || "",
      record.stock_text || "",
      record.weight_text || "",
      record.source || ""
    ].join("|").toLowerCase();
    const selectedComparableValues = uniqueTexts([
      ...selectedOptions.map((item) => item.value),
      ...selectedOptions.map((item) => item.text),
      selectedSpecText,
      matchedSpecText,
      imageMatchedSkuOption?.value || "",
      matchedByDimension?.value || ""
    ])
      .map((item) => optionValueText(item))
      .filter((item) => item.length >= 2)
      .slice(0, 12);
    const variantRecordSelected = (specText, imageUrl = "") => {
      const comparable = optionValueText(specText);
      if (comparable && selectedComparableValues.some((value) => comparable.includes(value) || value.includes(comparable))) return true;
      const signature = imageSignature(imageUrl);
      return Boolean(signature && selectedSkuImageUrls.some((url) => {
        const selectedSignature = imageSignature(url);
        return selectedSignature && (signature === selectedSignature || signature.includes(selectedSignature) || selectedSignature.includes(signature));
      }));
    };
    const variantRecords = [];
    const pushVariantRecord = (record) => {
      const name = text(record.name || record.group_name || "SKU").slice(0, 80);
      const specText = text(record.spec_text || record.value || record.text || "").slice(0, 180);
      if (!specText) return;
      const imageUrl = normalizeImageUrl(record.image_url || "");
      const priceCny = typeof record.price_cny === "number" && Number.isFinite(record.price_cny)
        ? Math.round(record.price_cny * 100) / 100
        : null;
      const priceText = text(record.price_text || (priceCny !== null ? `CNY ${priceCny}` : "")).slice(0, 120);
      const parsedWeight = parseWeightFromText(record.weight_text || record.weight_kg || "");
      const recordWeightKg = typeof record.weight_kg === "number" && Number.isFinite(record.weight_kg)
        ? Math.round(record.weight_kg * 10000) / 10000
        : parsedWeight.kg;
      const normalized = {
        spec_text: specText,
        name,
        image_url: imageUrl,
        selected: Boolean(record.selected || variantRecordSelected(specText, imageUrl)),
        price_text: priceText,
        price_cny: priceCny,
        weight_text: text(parsedWeight.text || record.weight_text || (recordWeightKg !== null && recordWeightKg !== undefined ? `重量 ${recordWeightKg}kg` : "")).slice(0, 80),
        weight_kg: recordWeightKg,
        weight_source: text(record.weight_source || parsedWeight.source || "").slice(0, 80),
        stock_text: text(record.stock_text || "").slice(0, 120),
        evidence: Array.isArray(record.evidence) ? record.evidence.slice(0, 6) : [text(record.evidence || record.source || "detail_page")].filter(Boolean),
        source: text(record.source || "detail_page").slice(0, 120)
      };
      const key = variantRecordKey(normalized);
      if (!key || variantRecords.some((item) => variantRecordKey(item) === key)) return;
      variantRecords.push(normalized);
    };
    variantPreview.forEach((item) => {
      pushVariantRecord({
        spec_text: `${item.group_name || "SKU"}:${item.value || ""}`,
        name: item.group_name || "SKU",
        image_url: item.image_url || "",
        selected: item.selected,
        price_text: item.price_text || (item.price_cny !== null && item.price_cny !== undefined ? `CNY ${item.price_cny}` : ""),
        price_cny: typeof item.price_cny === "number" ? item.price_cny : null,
        weight_text: item.weight_text || "",
        weight_kg: typeof item.weight_kg === "number" ? item.weight_kg : null,
        weight_source: item.weight_source || "",
        stock_text: item.stock_text || "",
        evidence: ["visible_sku_option", item.dimension_key ? `dimension:${item.dimension_key}` : ""].filter(Boolean),
        source: "visible_sku_option"
      });
    });
    structuredSkuCombosForDetail
      .filter((combo) => combo && typeof combo === "object")
      .slice(0, 80)
      .forEach((combo) => {
        const attributes = combo.attributes && typeof combo.attributes === "object" ? combo.attributes : {};
        const specText = Object.entries(attributes)
          .map(([name, value]) => `${name}:${value}`)
          .join("; ") || text(combo.attribute_values?.join(" ") || combo.key || "");
        if (!combo.image_url && !Object.keys(attributes).length && /^\d+$/.test(specText)) return;
        pushVariantRecord({
          spec_text: specText,
          name: Object.keys(attributes).join("/") || "SKU",
          image_url: combo.image_url || "",
          selected: variantRecordSelected(specText, combo.image_url),
          price_text: combo.price != null ? `CNY ${combo.price}` : "",
          price_cny: typeof combo.price === "number" ? combo.price : null,
          weight_text: combo.weight_text || "",
          weight_kg: typeof combo.weight_kg === "number" ? combo.weight_kg : null,
          weight_source: combo.weight_source || "",
          stock_text: combo.stock_text || "",
          evidence: [combo.source || "structured_sku_data", combo.price_key ? `price_field:${combo.price_key}` : "", combo.sku_id ? `sku_id:${combo.sku_id}` : "", combo.weight_source ? `weight:${combo.weight_source}` : ""].filter(Boolean),
          source: combo.source || "structured_sku_data"
        });
      });
    const sourceVariantRecords = variantRecords.slice(0, 20);
    const selectedVariantWeightRecord = sourceVariantRecords.find((record) => record.selected && (record.weight_text || record.weight_kg !== null && record.weight_kg !== undefined))
      || sourceVariantRecords.find((record) => record.weight_text || record.weight_kg !== null && record.weight_kg !== undefined)
      || null;
    const employeeActionWeightText = selectedVariantWeightRecord?.weight_text || weightText || "";
    const employeeActionWeightKg = selectedVariantWeightRecord?.weight_kg ?? weightKg ?? null;
    const employeeActionWeightSource = selectedVariantWeightRecord?.weight_source || weightSource || "";
    const employeeActionMinOrderQuantity = moqMatch ? (moqMatch[0] || moqMatch[1] || "") : "";
    const employeeActionSkuPriceText = effectiveMatchedPriceText
      || (skuPreviewProductAmount.cny !== null ? skuPreviewProductAmount.text : "")
      || (structuredSkuPrice.cny !== null ? structuredSkuPrice.text : "")
      || (selectedSkuRowPrice.cny !== null ? selectedSkuRowPrice.text : "");
    const employeeActionSkuPriceCny = effectiveMatchedPriceCny !== null && effectiveMatchedPriceCny !== undefined
      ? effectiveMatchedPriceCny
      : (skuPreviewProductAmount.cny !== null
        ? skuPreviewProductAmount.cny
        : (structuredSkuPrice.cny !== null
          ? structuredSkuPrice.cny
          : selectedSkuRowPrice.cny));
    const employeeActionFreightText = previewFreightAppliesToMatchedSku && skuPreviewFreightAmount.text ? skuPreviewFreightAmount.text : freightText || "";
    const employeeActionFreightCny = previewFreightAppliesToMatchedSku && skuPreviewFreightAmount.cny !== null && skuPreviewFreightAmount.cny !== undefined
      ? skuPreviewFreightAmount.cny
      : freightCny;
    const employeeActionStatus = sourceCostClosed
      ? "passed"
      : ((employeeActionSkuPriceCny !== null && employeeActionSkuPriceCny !== undefined) || (employeeActionFreightCny !== null && employeeActionFreightCny !== undefined)
        ? "captured"
        : "incomplete");
    const employeeActionValidation = {
      status: employeeActionStatus,
      detail_page_url: location.href || expectedDetailUrl,
      selected_spec_text: effectiveMatchedSpecText || selectedSpecText || "",
      selected_sku_text: effectiveMatchedSpecText || selectedSpecText || "",
      selected_variant_image_url: selectedSkuImageUrls[0] || imageMatchedSkuOption?.image_url || "",
      sku_price_text: employeeActionSkuPriceText || "",
      sku_price_cny: employeeActionSkuPriceCny,
      product_amount_text: skuPreviewProductAmount.text || "",
      product_amount_cny: skuPreviewProductAmount.cny,
      freight_text: employeeActionFreightText || "",
      freight_cny: employeeActionFreightCny,
      weight_text: employeeActionWeightText || "",
      weight_kg: employeeActionWeightKg,
      weight_source: employeeActionWeightSource || "",
      min_order_quantity: employeeActionMinOrderQuantity || "",
      source_attributes: sourceAttributeData.attributes,
      source_attribute_pairs: sourceAttributeData.pairs,
      source_attribute_table: sourceAttributeData.table,
      price_source: skuPriceSource,
      freight_source: freightSource,
      sku_match_status: skuMatchStatus,
      action_trace: [
        "open_1688_detail",
        specOptions.length ? `capture_sku_options:${specOptions.length}` : "capture_sku_options:none",
        forcedSkuSelectionApplied ? "select_ai_target_sku" : "use_current_or_auto_selected_sku",
        selectedSpecText ? "read_selected_sku" : "read_default_detail",
        skuPreviewProductAmount.cny !== null ? "read_product_amount_preview" : "product_amount_preview_missing",
        employeeActionFreightCny !== null && employeeActionFreightCny !== undefined ? "read_freight" : "freight_missing",
        employeeActionWeightKg !== null && employeeActionWeightKg !== undefined ? "read_weight" : "weight_missing",
        employeeActionMinOrderQuantity ? "read_min_order_quantity" : "min_order_quantity_missing"
      ],
      captured_at: now
    };

    return {
      ok: true,
      captured_at: now,
      detail_page_url: location.href || expectedDetailUrl,
      source_title: title,
      freight_text: freightText,
      freight_cny: freightCny,
      weight_text: weightText,
      weight_kg: weightKg,
      weight_source: weightSource,
      min_order_quantity: employeeActionMinOrderQuantity || (moqMatch ? moqMatch[1] : ""),
      source_spec_text: sourceSpecText,
      source_selected_spec_text: selectedSpecText,
      source_matched_spec_text: effectiveMatchedSpecText,
      source_sku_match_status: skuMatchStatus,
      source_sku_match_note: skuMatchNote,
      source_product_visual_match_status: sourceProductVisualMatchStatus,
      source_product_visual_match_score: sourceProductVisualMatchScore,
      source_product_visual_match_evidence: productVisualEvidence.slice(0, 12),
      freight_confidence: freightConfidence,
      freight_source: freightSource,
      source_cost_closed: sourceCostClosed,
      sku_price_source: skuPriceSource,
      matched_sku_preview_selected: skuMatchStatus === "default_sku_confirmed" ? true : matchedSkuPreviewSelected,
      sku_preview_product_amount_text: skuPreviewProductAmount.text,
      sku_preview_product_amount_cny: skuPreviewProductAmount.cny,
      sku_preview_freight_text: skuPreviewFreightAmount.text,
      sku_preview_freight_cny: skuPreviewFreightAmount.cny,
        structured_sku_price_text: structuredSkuPrice.cny !== null ? structuredSkuPrice.text : "",
        structured_sku_price_cny: structuredSkuPrice.cny,
        structured_sku_price_source: structuredSkuPrice.source || "",
        structured_sku_price_field: structuredSkuPrice.price_key || "",
        structured_sku_id: structuredSkuPrice.sku_id || "",
        structured_sku_stock_text: structuredSkuPrice.stock_text || "",
      structured_sku_match_score: structuredSkuPrice.score || 0,
      structured_sku_match_hint: structuredSkuPrice.matched_hint || "",
      selected_sku_row_price_text: selectedSkuRowPrice.cny !== null ? selectedSkuRowPrice.text : "",
      selected_sku_row_price_cny: selectedSkuRowPrice.cny,
      selected_sku_row_price_source: selectedSkuRowPrice.source || "",
      sku_matrix_price_text: selectedSkuScriptPrice.cny !== null ? selectedSkuScriptPrice.text : "",
      sku_matrix_price_cny: selectedSkuScriptPrice.cny,
      sku_matrix_price_source: matrixSkuPriceSource,
      source_variant_count: specOptions.length,
      source_variant_preview: variantPreview.slice(0, 12),
      source_variant_records: sourceVariantRecords,
      source_attributes: sourceAttributeData.attributes,
      source_attribute_pairs: sourceAttributeData.pairs,
      source_attribute_table: sourceAttributeData.table,
      matched_price_text: effectiveMatchedPriceText,
      matched_price_cny: effectiveMatchedPriceCny,
      employee_action_validation: employeeActionValidation,
      employee_action_validation_status: employeeActionStatus,
      employee_action_selected_spec_text: employeeActionValidation.selected_spec_text,
      employee_action_selected_variant_image_url: employeeActionValidation.selected_variant_image_url,
      employee_action_sku_price_text: employeeActionValidation.sku_price_text,
      employee_action_sku_price_cny: employeeActionValidation.sku_price_cny,
      employee_action_product_amount_text: employeeActionValidation.product_amount_text,
      employee_action_product_amount_cny: employeeActionValidation.product_amount_cny,
      employee_action_freight_text: employeeActionValidation.freight_text,
      employee_action_freight_cny: employeeActionValidation.freight_cny,
      employee_action_weight_text: employeeActionValidation.weight_text,
      employee_action_weight_kg: employeeActionValidation.weight_kg,
      employee_action_weight_source: employeeActionValidation.weight_source,
      employee_action_min_order_quantity: employeeActionValidation.min_order_quantity,
      employee_action_trace: employeeActionValidation.action_trace,
      raw_payload: {
        detail_capture_status: "captured",
        shipping_detail_url: location.href || expectedDetailUrl,
        shipping_text_sample: contextOf(/包邮|免运费|免邮|运费|邮费|物流|快递|配送|shipping|freight|postage/i, freightLine),
        weight_text_sample: contextOf(/包装重量|商品重量|商品件重|计费重量|毛重|净重|重量|weight|grossWeight|packageWeight/i, weightText || packageInfoText),
        package_info_text_sample: packageInfoText.slice(0, 1200),
        weight_kg: weightKg,
        weight_source: weightSource,
        detail_title: title,
        source_selected_spec_text: selectedSpecText,
        source_matched_spec_text: effectiveMatchedSpecText,
        source_sku_match_status: skuMatchStatus,
        source_product_visual_match_status: sourceProductVisualMatchStatus,
        source_product_visual_match_score: sourceProductVisualMatchScore,
        source_product_visual_match_evidence: productVisualEvidence.slice(0, 12),
        freight_confidence: freightConfidence,
        freight_source: freightSource,
        source_cost_closed: sourceCostClosed,
        sku_price_source: skuPriceSource,
        matched_sku_preview_selected: skuMatchStatus === "default_sku_confirmed" ? true : matchedSkuPreviewSelected,
        sku_preview_product_amount_text: skuPreviewProductAmount.text,
        sku_preview_product_amount_cny: skuPreviewProductAmount.cny,
        sku_preview_freight_text: skuPreviewFreightAmount.text,
        sku_preview_freight_cny: skuPreviewFreightAmount.cny,
        structured_sku_price_text: structuredSkuPrice.cny !== null ? structuredSkuPrice.text : "",
        structured_sku_price_cny: structuredSkuPrice.cny,
        structured_sku_price_source: structuredSkuPrice.source || "",
        structured_sku_price_key: structuredSkuPrice.key || "",
        structured_sku_price_field: structuredSkuPrice.price_key || "",
        structured_sku_id: structuredSkuPrice.sku_id || "",
        structured_sku_stock_text: structuredSkuPrice.stock_text || "",
        structured_sku_match_score: structuredSkuPrice.score || 0,
        structured_sku_match_hint: structuredSkuPrice.matched_hint || "",
        structured_sku_attributes: structuredSkuPrice.attributes || {},
        structured_sku_candidates_count: structuredSkuPrice.candidates_count || 0,
        structured_sku_price_applies_to_current_sku: structuredPriceAppliesToCurrentSku,
        selected_sku_row_price_text: selectedSkuRowPrice.cny !== null ? selectedSkuRowPrice.text : "",
        selected_sku_row_price_cny: selectedSkuRowPrice.cny,
        selected_sku_row_price_source: selectedSkuRowPrice.source || "",
        selected_sku_row_price_context: selectedSkuRowPrice.context || "",
        selected_sku_row_price_score: selectedSkuRowPrice.score || 0,
        selected_sku_row_price_matched_hint: selectedSkuRowPrice.matched_hint || "",
        sku_matrix_price_text: selectedSkuScriptPrice.cny !== null ? selectedSkuScriptPrice.text : "",
        sku_matrix_price_cny: selectedSkuScriptPrice.cny,
        sku_matrix_price_source: matrixSkuPriceSource,
        sku_matrix_probe_status: selectedSkuScriptPrice.cny !== null ? "matched_selected_sku_script_context" : "no_selected_sku_script_price",
        sku_matrix_match_score: selectedSkuScriptPrice.score || 0,
        sku_matrix_price_key: selectedSkuScriptPrice.price_key || "",
        sku_matrix_probe_type: selectedSkuScriptPrice.probe_type || "",
        sku_matrix_selected_values: selectedSkuScriptPrice.selected_values || [],
        sku_matrix_selected_image_signatures: selectedSkuScriptPrice.selected_image_signatures || [],
        sku_matrix_context_sample: selectedSkuScriptPrice.context_sample || "",
        source_variant_count: specOptions.length,
        source_variant_preview: variantPreview.slice(0, 12),
        source_variant_options_preview: variantPreview.slice(0, 30),
        source_variant_records: sourceVariantRecords,
        source_attributes: sourceAttributeData.attributes,
        source_attribute_pairs: sourceAttributeData.pairs,
        source_attribute_table: sourceAttributeData.table,
        source_sku_match_note: skuMatchNote,
        employee_action_validation: employeeActionValidation,
        employee_action_validation_status: employeeActionStatus,
        employee_action_selected_spec_text: employeeActionValidation.selected_spec_text,
        employee_action_selected_variant_image_url: employeeActionValidation.selected_variant_image_url,
        employee_action_sku_price_text: employeeActionValidation.sku_price_text,
        employee_action_sku_price_cny: employeeActionValidation.sku_price_cny,
        employee_action_product_amount_text: employeeActionValidation.product_amount_text,
        employee_action_product_amount_cny: employeeActionValidation.product_amount_cny,
        employee_action_freight_text: employeeActionValidation.freight_text,
        employee_action_freight_cny: employeeActionValidation.freight_cny,
        employee_action_weight_text: employeeActionValidation.weight_text,
        employee_action_weight_kg: employeeActionValidation.weight_kg,
        employee_action_weight_source: employeeActionValidation.weight_source,
        employee_action_min_order_quantity: employeeActionValidation.min_order_quantity,
        employee_action_trace: employeeActionValidation.action_trace,
        image_matched_sku_option: imageMatchedSkuOption ? {
          group_name: imageMatchedSkuOption.group_name || "",
          value: imageMatchedSkuOption.value || "",
          image_url: imageMatchedSkuOption.image_url || "",
          score: imageMatchedSkuOption.score || 0
        } : null,
        default_sku_product_image_urls: defaultProductImageUrls.slice(0, 5),
        selected_sku_image_urls: selectedSkuImageUrls.slice(0, 5),
        candidate_main_image_url: candidateMainImageUrl,
        target_main_image_url: targetMainImageUrl,
        target_image_urls: targetMainImageUrls.slice(0, 8),
        quantity_or_pack_conflict: quantityOrPackConflict,
        quote_dimension_signals: quoteDimensionSignals.slice(0, 6),
        matched_price_text: effectiveMatchedPriceText,
        matched_price_cny: effectiveMatchedPriceCny,
        detail_captured_at: now
      }
    };
  }, { attempts: 1, timeoutMs: DEFAULT_MAIN_WORLD_SCRIPT_TIMEOUT_MS });
  return captured || { ok: false, error: "detail_script_no_result", raw_payload: { detail_capture_status: "script_no_result", shipping_detail_url: detailUrl } };
}

async function runSingleSourceImageSearchTask(tabId, task, options = {}) {
  const capturedAt = new Date().toISOString();
  const baseItem = {
    quote_key: task.quote_key,
    source_match_key: task.source_match_key || "",
    source_quote_keys: Array.isArray(task.source_quote_keys) ? task.source_quote_keys : [],
    source_quote_count: task.source_quote_count || (Array.isArray(task.source_quote_keys) ? task.source_quote_keys.length : 0),
    skc_id: task.skc_id,
    sku_id: task.sku_id,
    spu_or_goods_id: task.spu_or_goods_id,
    site: task.site,
    product_title: task.product_title,
    main_image_url: task.main_image_url,
    extra_image_urls: task.extra_image_urls || [],
    target_image_urls: task.target_image_urls || [task.main_image_url].filter(Boolean),
    status: "failed",
    statusText: "",
    manual_search_url: task.manual_search_url || buildSourceKeywordSearchUrl(task.product_title),
    source_page_url: "",
    candidates: [],
    captured_at: capturedAt
  };
  if (!task.main_image_url) {
    return {
      ...baseItem,
      status: "failed",
      error: "missing_main_image_url",
      statusText: "缺少主图，不能执行图搜"
    };
  }

  if (options.preferAssistantSidebar !== false) {
    const sidebarCapture = await withTimeout(
      captureCurrent1688AssistantSidebarCandidates(maxCandidatesFromOptions(options)),
      SOURCE_ASSISTANT_TRIGGER_TIMEOUT_MS,
      "assistant_sidebar_capture_timeout"
    );
    if (sidebarCapture.candidates?.length) {
      return {
        ...baseItem,
        status: "succeeded",
        statusText: `已从当前 1688 助手侧栏读取 ${sidebarCapture.candidates.length} 个候选`,
        source_page_url: sidebarCapture.source_page_url || sidebarCapture.page_url || "",
        candidates: sidebarCapture.candidates,
        assistant_sidebar_state: sidebarCapture,
        captured_at: new Date().toISOString()
      };
    }
    if (options.autoAssistantSidebar) {
      const trigger = await withTimeout(
        trigger1688AssistantImageSearchFromWorkbench(task),
        SOURCE_ASSISTANT_TRIGGER_TIMEOUT_MS,
        "assistant_image_search_trigger_timeout"
      );
      if (trigger.ok) {
        const waitedSidebar = await waitFor1688AssistantSidebarCandidates(maxCandidatesFromOptions(options), SOURCE_ASSISTANT_SIDEBAR_WAIT_MS);
        if (waitedSidebar.candidates?.length) {
          return {
            ...baseItem,
            status: "succeeded",
            statusText: `已自动触发 1688 图搜并读取 ${waitedSidebar.candidates.length} 个候选`,
            source_page_url: waitedSidebar.source_page_url || waitedSidebar.page_url || trigger.page_url || "",
            candidates: waitedSidebar.candidates,
            assistant_sidebar_state: waitedSidebar,
            assistant_trigger_state: trigger,
            captured_at: new Date().toISOString()
          };
        }
        return {
          ...baseItem,
          status: "manual_challenge",
          error: waitedSidebar.error || "assistant_sidebar_wait_timeout",
          statusText: waitedSidebar.statusText || "已点击 1688 图搜入口，但等待侧栏候选超时",
          help: "如果 1688 助手侧栏已打开但页面没有回传，请先确认插件版本已重新加载；若侧栏出现验证码或访问受限，保留人工处理，不做绕过。",
          source_page_url: waitedSidebar.source_page_url || waitedSidebar.page_url || trigger.page_url || "",
          assistant_trigger_state: trigger,
          assistant_sidebar_state: waitedSidebar
        };
      }
      if (options.sidebarOnly) {
        return {
          ...baseItem,
          status: "manual_challenge",
          error: trigger.error || sidebarCapture.error || "assistant_image_search_trigger_not_found",
          statusText: trigger.statusText || "没有在目标商品主图附近找到 1688 图搜同款入口",
          help: "请确认 1688 采购助手插件已启用，工作台商品主图左上角能在鼠标悬停后出现“图搜同款”。插件只会点击目标主图附近的图搜入口。",
          source_page_url: trigger.page_url || sidebarCapture.source_page_url || sidebarCapture.page_url || "",
          assistant_trigger_state: trigger,
          assistant_sidebar_state: sidebarCapture
        };
      }
    }
    if (options.sidebarOnly) {
      return {
        ...baseItem,
        status: "manual_challenge",
        error: sidebarCapture.error || "assistant_sidebar_not_found",
        statusText: sidebarCapture.statusText || "当前页面没有可读取的 1688 助手侧栏结果",
        help: "把鼠标放到核价商品主图，点击 1688 助手的图搜同款，等页面内侧栏出现结果后，再重试这个单品。",
        source_page_url: sidebarCapture.source_page_url || sidebarCapture.page_url || ""
      };
    }
  }

  const directSearchUrl = buildSourceImageSearchUrl(task.main_image_url);
  await chrome.tabs.update(tabId, { active: true, url: directSearchUrl });
  await waitForTabReady(tabId, 25000);
  await delay(SOURCE_IMAGE_SEARCH_WAIT_MS);
  let currentTab = await chrome.tabs.get(tabId);
  let challenge = await detectSourceSearchChallenge(tabId);
  if (challenge.manual_challenge) {
    return {
      ...baseItem,
      status: "manual_challenge",
      error: challenge.error || "source_search_manual_challenge",
      statusText: challenge.statusText || "1688 图搜页需要人工处理",
      help: challenge.help || "请在浏览器里处理验证或访问限制后重试；插件不会绕过验证。",
      source_page_url: currentTab.url || directSearchUrl,
      risk_control_state: challenge.risk_control_state || null
    };
  }

  let candidates = await captureSourceSearchCandidatesFromTab(currentTab, maxCandidatesFromOptions(options));
  if (!candidates.length) {
    const upload = await tryUploadSourceImageForSearch(tabId, task.main_image_url);
    if (upload.ok) {
      await delay(SOURCE_IMAGE_SEARCH_UPLOAD_WAIT_MS);
      currentTab = await chrome.tabs.get(tabId);
      challenge = await detectSourceSearchChallenge(tabId);
      if (challenge.manual_challenge) {
        return {
          ...baseItem,
          status: "manual_challenge",
          error: challenge.error || "source_search_manual_challenge",
          statusText: challenge.statusText || "1688 图搜页需要人工处理",
          help: challenge.help || "请在浏览器里处理验证或访问限制后重试；插件不会绕过验证。",
          source_page_url: currentTab.url || directSearchUrl,
          risk_control_state: challenge.risk_control_state || null
        };
      }
      candidates = await captureSourceSearchCandidatesFromTab(currentTab, maxCandidatesFromOptions(options));
    } else if (upload.manual_challenge) {
      return {
        ...baseItem,
        status: "manual_challenge",
        error: upload.error || "upload_blocked",
        statusText: upload.statusText || "1688 图搜上传入口需要人工接管",
        help: upload.help || "可点击打开 1688 搜索页，手动上传主图后再重试采集。",
        source_page_url: currentTab.url || directSearchUrl
      };
    }
  }

  currentTab = await chrome.tabs.get(tabId);
  return {
    ...baseItem,
    status: candidates.length ? "succeeded" : "no_results",
    statusText: candidates.length ? `识别到 ${candidates.length} 个 1688 候选` : "1688 图搜没有识别到候选",
    source_page_url: currentTab.url || directSearchUrl,
    candidates,
    captured_at: new Date().toISOString()
  };
}

function maxCandidatesFromOptions(options) {
  return Math.max(1, Math.min(Number(options?.maxCandidates || SOURCE_IMAGE_SEARCH_CANDIDATE_LIMIT), 20));
}

async function waitFor1688AssistantSidebarCandidates(limit, timeoutMs) {
  const deadline = Date.now() + Math.max(3000, Number(timeoutMs || SOURCE_ASSISTANT_SIDEBAR_WAIT_MS));
  let lastResult = null;
  while (Date.now() < deadline) {
    const result = await withTimeout(
      captureCurrent1688AssistantSidebarCandidates(limit),
      SOURCE_ASSISTANT_TRIGGER_TIMEOUT_MS,
      "assistant_sidebar_capture_timeout"
    );
    if (result?.candidates?.length) return result;
    lastResult = result;
    await delay(SOURCE_ASSISTANT_SIDEBAR_POLL_MS);
  }
  return {
    ...(lastResult || {}),
    ok: false,
    error: lastResult?.error || "assistant_sidebar_wait_timeout",
    statusText: lastResult?.statusText || "等待 1688 助手侧栏候选超时",
    candidates: []
  };
}

function isLikelyWorkbenchTab(tab) {
  try {
    const parsed = new URL(String(tab?.url || ""));
    const host = parsed.hostname.toLowerCase();
    return parsed.protocol === "http:"
      && (host === "127.0.0.1" || host === "localhost" || /^10\.|^192\.168\.|^172\.(1[6-9]|2\d|3[01])\./.test(host))
      && (parsed.port === "8010" || parsed.pathname === "/" || /workbench|local|price|source/i.test(parsed.href));
  } catch (_error) {
    return false;
  }
}

function isLikelyAssistantHostTab(tab) {
  const value = `${tab?.url || ""} ${tab?.title || ""}`;
  return isLikelyWorkbenchTab(tab) || /1688|图搜|货源|sourcing|source|price/i.test(value);
}

async function trigger1688AssistantImageSearchFromWorkbench(task) {
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const currentWindowTabs = await chrome.tabs.query({ currentWindow: true });
  const orderedTabs = [];
  const pushTab = (tab) => {
    if (tab?.id && !orderedTabs.some((item) => item.id === tab.id)) orderedTabs.push(tab);
  };
  currentWindowTabs.filter(isLikelyWorkbenchTab).forEach(pushTab);
  if (!orderedTabs.length && isLikelyWorkbenchTab(activeTab)) pushTab(activeTab);
  if (!orderedTabs.length) pushTab(activeTab);

  let lastResult = null;
  for (const tab of orderedTabs.slice(0, 3)) {
    try {
      const result = await trigger1688AssistantImageSearchFromTab(tab, task);
      if (result?.ok) return result;
      if (result?.found_target_image || result?.found_trigger || result?.page_has_quote_signal) lastResult = result;
    } catch (error) {
      lastResult = { ok: false, error: String(error?.message || error), page_url: tab.url || "" };
    }
  }
  return lastResult || {
    ok: false,
    error: "workbench_quote_image_not_found",
    statusText: "没有找到可触发 1688 图搜的工作台商品主图",
    candidates: []
  };
}

async function trigger1688AssistantImageSearchFromTab(tab, task) {
  if (!tab?.id) return { ok: false, error: "missing_tab" };
  const captured = await executeMainWorld(tab.id, [task], async (rawTask) => {
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const text = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const visible = (element) => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0 && rect.width > 5 && rect.height > 5;
    };
    const normalizeUrl = (value) => {
      try {
        if (!value) return "";
        const parsed = new URL(value, location.href);
        parsed.hash = "";
        return parsed.href;
      } catch (_error) {
        return "";
      }
    };
    const stripUrl = (value) => normalizeUrl(value).replace(/[?#].*$/, "");
    const task = rawTask && typeof rawTask === "object" ? rawTask : {};
    const targetImageUrls = [];
    const addTargetImageUrl = (value) => {
      const url = stripUrl(value);
      if (url && !targetImageUrls.includes(url)) targetImageUrls.push(url);
    };
    addTargetImageUrl(task.main_image_url);
    if (Array.isArray(task.target_image_urls)) task.target_image_urls.forEach(addTargetImageUrl);
    if (Array.isArray(task.extra_image_urls)) task.extra_image_urls.forEach(addTargetImageUrl);
    const targetImageUrl = targetImageUrls[0] || "";
    const ids = [task.skc_id, task.sku_id, task.spu_or_goods_id].map(text).filter(Boolean);
    const quoteKey = text(task.quote_key);
    const titleTokens = text(task.product_title)
      .toLowerCase()
      .split(/[^a-z0-9\u4e00-\u9fa5]+/i)
      .filter((item) => item.length >= 4)
      .slice(0, 8);
    const sameUrl = (left, right) => {
      if (!left || !right) return false;
      return left === right || left.includes(right) || right.includes(left);
    };
    const nodeTextCache = new WeakMap();
    const getNodeText = (node, maxLength = 2500) => {
      if (!node) return "";
      if (nodeTextCache.has(node)) return nodeTextCache.get(node);
      const value = text(String(node.innerText || node.textContent || "").slice(0, maxLength));
      nodeTextCache.set(node, value);
      return value;
    };
    const targetMatchesCard = (card) => {
      if (!card) return false;
      const attrQuoteKey = text(card.getAttribute?.("data-source-quote-key"));
      const attrIds = [
        card.getAttribute?.("data-skc-id"),
        card.getAttribute?.("data-sku-id"),
        card.getAttribute?.("data-spu-id")
      ].map(text).filter(Boolean);
      if (quoteKey && attrQuoteKey === quoteKey) return true;
      if (ids.length && attrIds.some((id) => ids.includes(id))) return true;
      const value = getNodeText(card);
      return ids.some((id) => value.includes(id));
    };
    const findDataTaggedTarget = () => {
      const cards = Array.from(document.querySelectorAll("[data-source-quote-card='true']"))
        .filter(visible)
        .filter(targetMatchesCard);
      for (const card of cards) {
        const image = Array.from(card.querySelectorAll("img[data-source-main-image='true'], img"))
          .filter(visible)
          .find((candidate) => {
            const src = stripUrl(candidate.currentSrc || candidate.src || candidate.getAttribute("data-src") || candidate.getAttribute("src"));
            return !targetImageUrls.length || targetImageUrls.some((url) => sameUrl(src, url));
          }) || Array.from(card.querySelectorAll("img")).filter(visible)[0];
        if (image) return { image, card, score: 500, match_source: "data_source_quote_card" };
      }
      return null;
    };
    const nearestCard = (image) => {
      let best = null;
      let node = image;
      for (let depth = 0; node && depth < 10; depth += 1, node = node.parentElement) {
        if (!visible(node)) continue;
        const rect = node.getBoundingClientRect();
        if (rect.width > Math.max(960, window.innerWidth * 0.92) || rect.height > Math.max(1200, window.innerHeight * 1.8)) continue;
        const value = getNodeText(node);
        if (rect.width >= 160 && rect.height >= 120 && (ids.some((id) => value.includes(id)) || /SKC|SKU|SPU|原申报|平台核价|新申报/i.test(value))) {
          best = node;
        }
      }
      return best || image.parentElement;
    };
    const scoreImage = (image) => {
      if (!visible(image)) return 0;
      const src = stripUrl(image.currentSrc || image.src || image.getAttribute("data-src") || image.getAttribute("src"));
      const imageText = text(`${image.getAttribute("alt") || ""} ${image.getAttribute("title") || ""}`);
      const card = nearestCard(image);
      const cardText = getNodeText(card);
      let score = 0;
      if (targetImageUrls.length && src && targetImageUrls.some((url) => sameUrl(src, url))) score += 120;
      for (const id of ids) {
        if (cardText.includes(id)) score += 35;
      }
      const lower = `${cardText} ${imageText}`.toLowerCase();
      for (const token of titleTokens) {
        if (lower.includes(token)) score += 5;
      }
      const rect = image.getBoundingClientRect();
      if (rect.width >= 70 && rect.height >= 70) score += 6;
      if (/图搜|同款|1688/.test(cardText)) score += 3;
      return score;
    };
    const images = Array.from(document.querySelectorAll("img")).filter(visible);
    const directImages = targetImageUrls.length
      ? images.filter((image) => targetImageUrls.some((url) => sameUrl(stripUrl(image.currentSrc || image.src || image.getAttribute("data-src") || image.getAttribute("src")), url)))
      : [];
    const imagesToScore = directImages.length ? directImages : images.slice(0, 120);
    const scored = imagesToScore
      .map((image) => ({ image, score: scoreImage(image), card: nearestCard(image) }))
      .filter((item) => item.score >= 35)
      .sort((a, b) => b.score - a.score);
    const target = findDataTaggedTarget() || scored[0];
    if (!target) {
      return {
        ok: false,
        error: "target_quote_image_not_found",
        statusText: "工作台当前页没有定位到这条核价商品的主图",
        page_url: location.href,
        page_title: document.title,
        page_has_quote_signal: /核价及货源|核价预览|SKC|SKU|平台核价/.test(text(document.body?.innerText || document.body?.textContent || "")),
        found_target_image: false
      };
    }
    target.image.scrollIntoView({ block: "center", inline: "center" });
    await sleep(180);
    const dispatchHover = (element) => {
      if (!element) return;
      const rect = element.getBoundingClientRect();
      const eventInit = { bubbles: true, cancelable: true, view: window, clientX: rect.left + Math.min(24, rect.width / 2), clientY: rect.top + Math.min(24, rect.height / 2) };
      for (const type of ["pointerover", "pointerenter", "mouseover", "mouseenter", "mousemove"]) {
        element.dispatchEvent(type.startsWith("pointer") ? new PointerEvent(type, eventInit) : new MouseEvent(type, eventInit));
      }
    };
    dispatchHover(target.card);
    dispatchHover(target.image);
    await sleep(180);
    const imageRect = target.image.getBoundingClientRect();
    const cardRect = target.card?.getBoundingClientRect?.() || imageRect;
    const rectInfo = (rect) => ({
      x: Math.round(rect.x),
      y: Math.round(rect.y),
      w: Math.round(rect.width),
      h: Math.round(rect.height)
    });
    const elementLabel = (element) => text(`${element?.innerText || element?.textContent || ""} ${element?.getAttribute?.("aria-label") || ""} ${element?.getAttribute?.("title") || ""} ${element?.className || ""}`);
    const assistantButtonSelector = ".find-in-1688-btn";
    const getAssistantButton = (element) => {
      if (!element || element === document.body || element === document.documentElement) return null;
      try {
        if (element.matches?.(assistantButtonSelector)) return element;
        const closest = element.closest?.(assistantButtonSelector);
        return closest && target.card?.contains?.(closest) ? closest : null;
      } catch (_error) {
        return null;
      }
    };
    const isTopLeftOverlayRect = (rect) => {
      if (!rect || rect.width <= 0 || rect.height <= 0) return false;
      const maxW = Math.max(96, Math.min(180, imageRect.width + 48));
      const maxH = Math.max(34, Math.min(86, imageRect.height * 0.6));
      return rect.width <= maxW
        && rect.height <= maxH
        && rect.left >= imageRect.left - 18
        && rect.top >= imageRect.top - 18
        && rect.left <= imageRect.left + Math.min(imageRect.width, 112)
        && rect.top <= imageRect.top + Math.min(imageRect.height, 76);
    };
    const topLeftPoints = () => [
      [imageRect.left + 10, imageRect.top + 10],
      [imageRect.left + 20, imageRect.top + 16],
      [imageRect.left + 36, imageRect.top + 18],
      [imageRect.left + 56, imageRect.top + 18],
      [imageRect.left + 76, imageRect.top + 22]
    ].map(([x, y]) => [
      Math.max(0, Math.min(window.innerWidth - 1, x)),
      Math.max(0, Math.min(window.innerHeight - 1, y))
    ]);
    const targetImageSource = normalizeUrl(target.image.currentSrc || target.image.src || target.image.getAttribute("data-src") || target.image.getAttribute("src") || task.main_image_url);
    const forceShow1688HoverButton = () => {
      try {
        const extensionGlobal = window.__1688_EXTENSION;
        if (!extensionGlobal?.events?.emit) return "";
        extensionGlobal.events.emit("set-hover-image-btn-state", {
          imageSource: targetImageSource || task.main_image_url || "",
          btnTop: Math.max(0, imageRect.top + window.scrollY),
          btnLeft: Math.max(0, imageRect.left + window.scrollX),
          btnBottom: Math.max(0, imageRect.bottom + window.scrollY),
          btnRight: Math.max(0, imageRect.right + window.scrollX),
          showBtn: true,
          expand: true,
          forceBtnEnabled: true,
          forceShowDrawerFindGoods: true,
          forceAiFeaturePermitted: true,
          disableHoverDetection: true
        });
        return text(extensionGlobal.hoverSearchBtnId || "");
      } catch (_error) {
        return "";
      }
    };
    const getDynamicHoverButton = () => {
      const hoverButtonId = text(window.__1688_EXTENSION?.hoverSearchBtnId || "");
      if (!hoverButtonId) return null;
      const button = document.getElementById(hoverButtonId);
      if (!button) return null;
      const rect = button.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return null;
      return {
        element: button,
        reason: "1688_extension_hover_button_id",
        hover_button_id: hoverButtonId
      };
    };
    const triggerText = /图搜同款|搜同款|找同款|找货源|1688.*图搜|图搜/i;
    const blockedText = /加选品池|批量询盘|导出|去1688查看|清空|默认设置|历史记录|淘宝|亚马逊|速卖通|OZON/i;
    const findInjectedAssistantButton = () => {
      const dynamicHoverButton = getDynamicHoverButton();
      if (dynamicHoverButton?.element) return dynamicHoverButton;
      const cardButtons = Array.from(target.card?.querySelectorAll?.(assistantButtonSelector) || []);
      if (cardButtons.length) {
        const visibleButton = cardButtons.find((button) => {
          const rect = button.getBoundingClientRect();
          return isTopLeftOverlayRect(rect) || visible(button);
        });
        return {
          element: visibleButton || cardButtons[0],
          reason: visibleButton ? "target_card_injected_button" : "target_card_hidden_injected_button"
        };
      }

      for (const [x, y] of topLeftPoints()) {
        const stack = typeof document.elementsFromPoint === "function"
          ? document.elementsFromPoint(x, y)
          : [document.elementFromPoint(x, y)].filter(Boolean);
        for (const hit of stack) {
          const button = getAssistantButton(hit);
          if (button) return { element: button, reason: "top_left_assistant_button_hit", point: { x: Math.round(x), y: Math.round(y) } };
        }
      }

      const nearbyButtons = Array.from(document.querySelectorAll(assistantButtonSelector))
        .filter((button) => {
          const rect = button.getBoundingClientRect();
          return target.card?.contains?.(button) || isTopLeftOverlayRect(rect);
        })
        .sort((a, b) => {
          const ar = a.getBoundingClientRect();
          const br = b.getBoundingClientRect();
          return Math.hypot(ar.left - imageRect.left, ar.top - imageRect.top) - Math.hypot(br.left - imageRect.left, br.top - imageRect.top);
        });
      if (nearbyButtons.length) return { element: nearbyButtons[0], reason: "nearby_injected_button" };

      return null;
    };
    const waitForInjectedAssistantButton = async () => {
      const deadline = Date.now() + 2500;
      let found = null;
      forceShow1688HoverButton();
      while (Date.now() < deadline) {
        found = findInjectedAssistantButton();
        if (found?.element) return found;
        dispatchHover(target.card);
        dispatchHover(target.image);
        forceShow1688HoverButton();
        await sleep(120);
      }
      return findInjectedAssistantButton();
    };
    const nearTarget = (element) => {
      if (!visible(element)) return false;
      const rect = element.getBoundingClientRect();
      const overlapsImage = rect.left <= imageRect.right + 36 && rect.right >= imageRect.left - 36 && rect.top <= imageRect.bottom + 36 && rect.bottom >= imageRect.top - 36;
      const insideCard = rect.left >= cardRect.left - 12 && rect.right <= cardRect.right + 12 && rect.top >= cardRect.top - 12 && rect.bottom <= cardRect.bottom + 12;
      return overlapsImage || insideCard;
    };
    const compactTriggerCandidate = (node) => {
      if (!nearTarget(node)) return false;
      const rect = node.getBoundingClientRect();
      const value = elementLabel(node);
      if (!triggerText.test(value) || blockedText.test(value)) return false;
      if (value.length > 120) return false;
      return isTopLeftOverlayRect(rect);
    };
    const injected = await waitForInjectedAssistantButton();
    const trigger = injected?.element || Array.from(document.querySelectorAll("button,a,div,span"))
      .filter(visible)
      .find(compactTriggerCandidate);
    let clickable = trigger || null;
    let fallbackClickReason = injected?.reason || "";
    if (!clickable) {
      for (const [x, y] of topLeftPoints()) {
        const hit = document.elementFromPoint(x, y);
        let node = hit;
        for (let depth = 0; node && depth < 4; depth += 1, node = node.parentElement) {
          if (compactTriggerCandidate(node)) {
            clickable = node;
            fallbackClickReason = "top_left_text_trigger_hit";
            break;
          }
        }
        if (clickable) break;
        const hitValue = elementLabel(hit);
        const hitRect = hit?.getBoundingClientRect?.();
        if (hit && hit !== target.image && hit !== document.body && hit !== document.documentElement && !blockedText.test(hitValue) && hitValue.length <= 120 && isTopLeftOverlayRect(hitRect)) {
          clickable = hit;
          fallbackClickReason = "top_left_overlay_hit";
          break;
        }
      }
    }
    if (!clickable) {
      return {
        ok: false,
        error: "same_item_search_trigger_not_found",
        statusText: "已定位商品主图，但没有找到可点击的 1688 图搜同款浮层",
        page_url: location.href,
        page_title: document.title,
        found_target_image: true,
        found_trigger: false,
        target_score: target.score,
        target_image_rect: rectInfo(imageRect),
        target_card_rect: rectInfo(cardRect),
        assistant_button_count_in_card: target.card?.querySelectorAll?.(assistantButtonSelector)?.length || 0,
        top_left_probe_points: topLeftPoints().map(([x, y]) => ({ x: Math.round(x), y: Math.round(y) }))
      };
    }
    const clickRect = clickable.getBoundingClientRect();
    if (clickRect.width > 0 && clickRect.height > 0) {
      const clickInit = {
        bubbles: true,
        cancelable: true,
        view: window,
        clientX: clickRect.left + Math.max(2, Math.min(clickRect.width - 2, clickRect.width / 2)),
        clientY: clickRect.top + Math.max(2, Math.min(clickRect.height - 2, clickRect.height / 2))
      };
      clickable.dispatchEvent(new MouseEvent("mousedown", clickInit));
      clickable.dispatchEvent(new MouseEvent("mouseup", clickInit));
    }
    clickable.click();
    return {
      ok: true,
      statusText: "已点击目标主图附近的 1688 图搜同款入口",
      page_url: location.href,
      page_title: document.title,
      found_target_image: true,
      found_trigger: true,
      fallback_click_reason: fallbackClickReason,
      trigger_text: elementLabel(clickable).slice(0, 120),
      trigger_selector: clickable.matches?.(assistantButtonSelector) ? assistantButtonSelector : (injected?.hover_button_id ? `#${injected.hover_button_id}` : ""),
      trigger_rect: rectInfo(clickRect),
      target_image_rect: rectInfo(imageRect),
      target_card_rect: rectInfo(cardRect),
      assistant_button_count_in_card: target.card?.querySelectorAll?.(assistantButtonSelector)?.length || 0,
      target_score: target.score,
      target_match_source: target.match_source || "",
      extension_hover_button_id: text(window.__1688_EXTENSION?.hoverSearchBtnId || ""),
      target_image_src: normalizeUrl(target.image.currentSrc || target.image.src || "")
    };
  }, { attempts: 1, timeoutMs: SOURCE_ASSISTANT_TAB_SCRIPT_TIMEOUT_MS });
  return captured || { ok: false, error: "assistant_trigger_script_no_result", page_url: tab.url || "" };
}

async function captureCurrent1688AssistantSidebarCandidates(limit) {
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabs = [];
  const pushTab = (tab) => {
    if (tab?.id && !tabs.some((item) => item.id === tab.id)) tabs.push(tab);
  };
  if (isLikelyAssistantHostTab(activeTab)) pushTab(activeTab);
  const currentWindowTabs = await chrome.tabs.query({ currentWindow: true });
  for (const tab of currentWindowTabs) {
    if (isLikelyAssistantHostTab(tab)) pushTab(tab);
  }
  if (!tabs.length) pushTab(activeTab);

  let lastResult = null;
  for (const tab of tabs.slice(0, 4)) {
    try {
      const result = await capture1688AssistantSidebarCandidatesFromTab(tab, limit);
      if (result?.candidates?.length) return result;
      if (result?.has_sidebar_host || result?.has_shadow_root) lastResult = result;
    } catch (error) {
      lastResult = { ok: false, error: String(error?.message || error), page_url: tab.url || "" };
    }
  }
  return lastResult || {
    ok: false,
    error: "assistant_sidebar_not_found",
      statusText: "当前浏览器窗口没有找到 1688 助手侧栏"
  };
}

async function capture1688AssistantSidebarCandidatesFromTab(tab, limit) {
  if (!tab?.id) return { ok: false, error: "missing_tab" };
  const captured = await executeMainWorld(tab.id, [Math.max(1, Math.min(Number(limit || 5), 10))], async (candidateLimit) => {
    const now = new Date().toISOString();
    const text = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const lines = (value) => String(value || "").split(/\n+/).map(text).filter(Boolean);
    const absUrl = (url) => {
      try {
        if (!url) return "";
        return new URL(url, location.href).href;
      } catch (_error) {
        return "";
      }
    };
    const visible = (element) => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) !== 0 && rect.width > 4 && rect.height > 4;
    };
    const firstMatch = (value, pattern) => {
      const matched = String(value || "").match(pattern);
      return matched ? text(matched[0]) : "";
    };
    const priceText = (value) => firstMatch(value, /[¥￥]\s*\d+(?:\.\d+)?(?:\s*[-~至]\s*[¥￥]?\s*\d+(?:\.\d+)?)?/);
    const freightText = (value) => {
      if (/包邮|免运费|免邮/i.test(value)) return "包邮";
      return firstMatch(value, /(?:运费|邮费|物流|快递)[^\d¥￥]{0,12}[¥￥]?\s*\d+(?:\.\d+)?\s*(?:元起|元)?/);
    };
    const moqText = (value) => firstMatch(value, /\d+(?:\.\d+)?\s*(?:个|件|台|套|只|盒|箱|支|把|双|组|pcs?|pieces?|sets?)\s*(?:起批|起订)/i);
    const salesText = (value) => firstMatch(value, /(?:销量|月销|年销量|成交|已售)[^\s]{0,14}/);
    const offerIdFromUrl = (value) => {
      const matched = String(value || "").match(/\/offer\/(\d+)\.html|[?&](?:offerId|offer_id|productId|product_id)=(\d+)/i);
      return matched ? (matched[1] || matched[2] || "") : "";
    };
    const titleFromCard = (cardText, imageAlt) => {
      const blocked = /^(?:¥|￥|\d+$|起批|运费|销量|月销|年销量|48h|7天|包邮|清空|导出|默认排序|更多筛选|历史记录|加选品池|批量询盘|1688|淘宝|亚马逊|速卖通|OZON)/i;
      const found = lines(cardText).find((line) => line.length >= 5 && !blocked.test(line) && !/[¥￥]\s*\d/.test(line));
      return text(found || imageAlt || "").slice(0, 180);
    };
    const shopFromCard = (cardText) => {
      const found = lines(cardText).reverse().find((line) => /(公司|工厂|厂|商行|店|官方供应链|供应链)/.test(line) && line.length <= 40);
      return text(found || "");
    };
    const locationFromCard = (cardText) => {
      const found = firstMatch(cardText, /(广东|浙江|江苏|山东|福建|河北|河南|湖北|湖南|安徽|江西|四川|重庆|上海|北京|天津|广西|云南|陕西|辽宁|吉林|黑龙江|内蒙古|新疆|宁波|义乌|深圳|广州|东莞|温州|金华|台州|泉州|汕头|佛山|中山|宁波|苏州|杭州)[^\s]{0,8}/);
      return found;
    };
    const bestCardForImage = (image) => {
      let best = null;
      let node = image;
      for (let depth = 0; node && depth < 9; depth += 1, node = node.parentElement) {
        if (!visible(node)) continue;
        const rect = node.getBoundingClientRect();
        const value = text(node.innerText || node.textContent);
        if (rect.width > 90 && rect.width < 460 && rect.height > 110 && rect.height < 620 && /[¥￥]\s*\d/.test(value)) {
          best = node;
        }
      }
      return best;
    };
    const findVisibleSidebarRoot = () => {
      const signal = /1688\s*图搜|图搜找货|去1688查看|加选品池|批量询盘|默认排序|更多筛选/;
      const candidates = Array.from(document.querySelectorAll("aside, section, div"))
        .filter(visible)
        .filter((element) => {
          const rect = element.getBoundingClientRect();
          const value = text(element.innerText || element.textContent);
          if (!signal.test(value) || !/[¥￥]\s*\d/.test(value)) return false;
          return rect.width >= 280 && rect.height >= 260 && rect.right >= window.innerWidth * 0.45;
        })
        .sort((a, b) => {
          const ar = a.getBoundingClientRect();
          const br = b.getBoundingClientRect();
          return (br.width * br.height) - (ar.width * ar.height);
        });
      return candidates[0] || null;
    };
    const sidebarSignal = /1688\s*图搜|图搜找货|去1688查看|加选品池|批量询盘|默认排序|更多筛选/;
    const host = document.querySelector("#market-mate-for-1688, [id*='market-mate-for-1688']");
    const shadowRoot = host?.shadowRoot || null;
    const lightDomRoot = host && sidebarSignal.test(text(host.innerText || host.textContent)) ? host : null;
    const root = shadowRoot || lightDomRoot || findVisibleSidebarRoot();
    if (!root) {
      return {
        ok: false,
        error: host ? "assistant_shadow_root_not_open" : "assistant_sidebar_not_found",
        has_sidebar_host: Boolean(host),
        has_shadow_root: Boolean(shadowRoot),
        page_url: location.href,
        page_title: document.title,
        candidates: []
      };
    }
    const rootTextSample = text(root.innerText || root.textContent || "").slice(0, 700);
    const rootImages = Array.from(root.querySelectorAll("img")).filter(visible);
    const candidateImages = rootImages
      .filter(visible)
      .filter((image) => {
        const rect = image.getBoundingClientRect();
        const src = absUrl(image.currentSrc || image.src || image.getAttribute("data-src") || image.getAttribute("src"));
        if (!src || /^data:/i.test(src)) return false;
        if (rect.width < 56 || rect.height < 56) return false;
        const parentText = text(bestCardForImage(image)?.innerText || "");
        return /[¥￥]\s*\d/.test(parentText);
      });
    const seenCards = new Set();
    const candidates = [];
    for (const image of candidateImages) {
      const card = bestCardForImage(image);
      if (!card || seenCards.has(card)) continue;
      seenCards.add(card);
      const rawText = text(card.innerText || card.textContent);
      const rect = card.getBoundingClientRect();
      const imageUrl = absUrl(image.currentSrc || image.src || image.getAttribute("data-src") || image.getAttribute("src"));
      const link = card.closest("a[href]") || card.querySelector("a[href]");
      const sourceUrl = absUrl(link?.getAttribute("href") || "");
      const offerId = offerIdFromUrl(sourceUrl);
      const title = titleFromCard(rawText, image.getAttribute("alt") || image.getAttribute("title") || "");
      if (!title && !priceText(rawText)) continue;
      candidates.push({
        candidate_id: offerId || sourceUrl || `${title}-${candidates.length + 1}`,
        source_platform: "1688",
        offer_id: offerId,
        source_url: sourceUrl,
        source_title: title,
        main_image_url: imageUrl,
        price_text: priceText(rawText),
        freight_text: freightText(rawText),
        min_order_quantity: moqText(rawText),
        source_spec_text: title,
        source_quantity_text: moqText(rawText),
        shop_name: shopFromCard(rawText),
        location: locationFromCard(rawText),
        sales_text: salesText(rawText),
        list_rank: candidates.length + 1,
        capture_method: "1688_procurement_assistant_sidebar",
        captured_at: now,
        raw_payload: {
          list_card_text: rawText.slice(0, 900),
          card_rect: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
          page_url: location.href
        }
      });
      if (candidates.length >= candidateLimit) break;
    }
    return {
      ok: candidates.length > 0,
      error: candidates.length > 0 ? "" : "assistant_sidebar_candidates_not_found",
      statusText: candidates.length > 0 ? `已读取 ${candidates.length} 个助手侧栏候选` : "助手侧栏已打开，但没有识别到候选商品卡片",
      has_sidebar_host: Boolean(host),
      has_shadow_root: Boolean(shadowRoot),
      sidebar_root_text_sample: rootTextSample,
      sidebar_image_count: rootImages.length,
      sidebar_candidate_image_count: candidateImages.length,
      source_page_url: location.href,
      page_url: location.href,
      page_title: document.title,
      candidates
    };
  }, { attempts: 1, timeoutMs: SOURCE_ASSISTANT_TAB_SCRIPT_TIMEOUT_MS });
  return captured || { ok: false, error: "assistant_sidebar_script_no_result", page_url: tab.url || "" };
}

async function captureSourceSearchCandidatesFromTab(tab, limit) {
  const list = await captureProductListFromTab(tab);
  if (list.error && list.error !== "unsupported_product_list_page") return [];
  const products = Array.isArray(list.products) ? list.products : [];
  return dedupeSourceSearchCandidates(
    products.map((product, index) => normalizeSourceSearchCandidateFromListProduct(product, index + 1)),
    limit
  );
}

function firstNonEmptySourceText(...values) {
  for (const value of values) {
    const text = String(value == null ? "" : value).replace(/\s+/g, " ").trim();
    if (text) return text;
  }
  return "";
}

function sourceCardTextFromListProduct(raw, rawPayload) {
  return firstNonEmptySourceText(
    raw.source_card_text,
    raw.list_card_text,
    raw.card_text,
    raw.raw_text,
    rawPayload.source_card_text,
    rawPayload.list_card_text,
    rawPayload.card_text,
    rawPayload.raw_text
  );
}

function freightTextFromListProduct(raw, rawPayload) {
  const direct = firstNonEmptySourceText(
    raw.freight_text,
    raw.freightText,
    raw.shipping_text,
    raw.shippingText,
    raw.postage_text,
    raw.postageText,
    raw.freight,
    raw.shipping,
    raw.postage,
    rawPayload.freight_text,
    rawPayload.freightText,
    rawPayload.shipping_text,
    rawPayload.shippingText,
    rawPayload.postage_text,
    rawPayload.postageText,
    rawPayload.freight,
    rawPayload.shipping,
    rawPayload.postage
  );
  if (direct) return direct;
  const cardText = sourceCardTextFromListProduct(raw, rawPayload);
  if (/包邮|免运费|免邮|free\s*shipping|shipping\s*free/i.test(cardText)) return "free_shipping";
  const matched = cardText.match(/(?:运费|邮费|物流|快递|freight|shipping|postage)[^\d¥￥]{0,10}[¥￥]?\s*\d+(?:\.\d+)?/i);
  return matched ? matched[0] : "";
}

function weightTextFromListProduct(raw, rawPayload) {
  const direct = firstNonEmptySourceText(
    raw.weight_text,
    raw.weightText,
    raw.weight_kg,
    raw.weightKg,
    raw.weight,
    raw.gross_weight,
    raw.grossWeight,
    raw.package_weight,
    raw.packageWeight,
    rawPayload.weight_text,
    rawPayload.weightText,
    rawPayload.weight_kg,
    rawPayload.weightKg,
    rawPayload.weight,
    rawPayload.gross_weight,
    rawPayload.grossWeight,
    rawPayload.package_weight,
    rawPayload.packageWeight
  );
  if (direct) return direct;
  const cardText = sourceCardTextFromListProduct(raw, rawPayload);
  const matched = cardText.match(/(?:重量|毛重|净重|weight)[^\d]{0,10}\d+(?:\.\d+)?\s*(?:kg|公斤|千克|g|克)/i);
  return matched ? matched[0] : "";
}

function normalizeSourceSearchCandidateFromListProduct(product, rank) {
  const raw = product && typeof product === "object" ? product : {};
  const productId = String(raw.product_id || productIdFromCaptureUrl(raw.product_link || raw.link || raw.source_ref) || "").trim();
  const sourceUrl = canonicalProductCaptureUrl(raw.product_link || raw.link || raw.url || raw.source_ref || "", productId);
  const title = cleanCapturedProductTitleForDraft(raw.title || raw.product_name || raw.subject || "");
  const imageUrl = normalizeHttpUrl(raw.main_image_url || raw.image_url || raw.imageUrl || raw.imgUrl || "");
  const priceText = String(raw.price_text || raw.priceText || raw.price || "").trim();
  const rawPayload = raw.raw_payload && typeof raw.raw_payload === "object" ? raw.raw_payload : {};
  const cardText = sourceCardTextFromListProduct(raw, rawPayload);
  return {
    candidate_id: productId || sourceUrl || title || `candidate-${rank}`,
    source_platform: raw.platform || "1688",
    offer_id: productId,
    source_url: sourceUrl,
    source_title: title,
    main_image_url: imageUrl,
    price_text: priceText,
    freight_text: freightTextFromListProduct(raw, rawPayload),
    weight_text: weightTextFromListProduct(raw, rawPayload),
    shop_name: String(raw.shop_name || raw.seller_name || raw.sellerName || rawPayload.shop_name || rawPayload.companyName || "").trim(),
    location: String(raw.location || raw.area || raw.province || rawPayload.location || rawPayload.area || "").trim(),
    sales_text: String(raw.sales_text || raw.sales || raw.sold || raw.orders || "").trim(),
    list_rank: rank,
    capture_method: "1688_browser_image_search",
    captured_at: new Date().toISOString(),
    raw_payload: {
      list_card_text: cardText.slice(0, 600),
      list_page_url: String(rawPayload.list_page_url || raw.list_page_url || "").slice(0, 500)
    }
  };
}

function dedupeSourceSearchCandidates(candidates, limit) {
  const output = [];
  const seen = new Set();
  for (const candidate of candidates || []) {
    const key = candidate.offer_id
      ? `offer:${candidate.offer_id}`
      : (candidate.source_url ? `url:${candidate.source_url.replace(/[?#].*$/, "")}` : `title:${candidate.source_title}`);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    output.push(candidate);
    if (output.length >= limit) break;
  }
  return output;
}

async function tryUploadSourceImageForSearch(tabId, imageUrl) {
  const data = await fetchSourceImageAsDataUrl(imageUrl);
  if (!data.ok) {
    return {
      ok: false,
      manual_challenge: true,
      error: data.error || "image_fetch_failed",
      statusText: "主图无法转成临时上传文件",
      help: "可打开 1688 图搜页手动上传主图；插件不会保存图片或平台凭证。"
    };
  }
  const upload = await executeMainWorld(tabId, [data.dataUrl, data.fileName, data.mimeType], async (imageDataUrl, uploadFileName, uploadMimeType) => {
    const text = (value) => (value == null ? "" : String(value)).replace(/\s+/g, " ").trim();
    const visible = (element) => {
      if (!element) return false;
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && rect.width > 2 && rect.height > 2;
    };
    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const findInput = () => {
      const inputs = Array.from(document.querySelectorAll("input[type='file']"));
      return inputs.find(visible) || inputs[0] || null;
    };
    let input = findInput();
    if (!input) {
      const trigger = Array.from(document.querySelectorAll("button,a,div,span,label"))
        .find((node) => /图搜|搜图|以图|图片|上传|camera|image|pic/i.test(`${text(node.innerText || node.textContent)} ${text(node.getAttribute?.("aria-label"))} ${text(node.getAttribute?.("title"))} ${text(node.className)}`));
      if (trigger) {
        trigger.click();
        await sleep(900);
        input = findInput();
      }
    }
    if (!input) {
      return { ok: false, manual_challenge: true, error: "upload_input_not_found" };
    }
    const response = await fetch(imageDataUrl);
    const blob = await response.blob();
    const file = new File([blob], uploadFileName || "source-image.jpg", { type: uploadMimeType || blob.type || "image/jpeg" });
    const dataTransfer = new DataTransfer();
    dataTransfer.items.add(file);
    input.files = dataTransfer.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return { ok: true, file_name: file.name, file_size: file.size };
  }, { attempts: 1 });
  return upload || { ok: false, manual_challenge: true, error: "upload_script_no_result" };
}

async function fetchSourceImageAsDataUrl(imageUrl) {
  try {
    const response = await fetch(imageUrl, { credentials: "omit" });
    if (!response.ok) return { ok: false, error: `image_http_${response.status}` };
    const blob = await response.blob();
    if (!blob || blob.size <= 0) return { ok: false, error: "empty_image" };
    if (blob.size > SOURCE_IMAGE_SEARCH_IMAGE_MAX_BYTES) return { ok: false, error: "image_too_large" };
    const mimeType = blob.type || "image/jpeg";
    const buffer = await blob.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const chunkSize = 0x8000;
    for (let index = 0; index < bytes.length; index += chunkSize) {
      binary += String.fromCharCode(...bytes.slice(index, index + chunkSize));
    }
    const ext = mimeType.includes("png") ? "png" : (mimeType.includes("webp") ? "webp" : "jpg");
    return {
      ok: true,
      dataUrl: `data:${mimeType};base64,${btoa(binary)}`,
      fileName: `source-search.${ext}`,
      mimeType
    };
  } catch (error) {
    return { ok: false, error: String(error?.message || error) };
  }
}

async function detectSourceSearchChallenge(tabId) {
  const risk = await detectProductCaptureRiskControl(tabId);
  if (risk.blocked || risk.risk_reason === "auxiliary_only") {
    return {
      manual_challenge: true,
      error: risk.blocked ? "risk_control_blocked" : "manual_security_challenge",
      statusText: risk.blocked ? "1688 访问受限，已暂停图搜" : "1688 页面出现验证提示，需要人工处理",
      help: "请在浏览器里处理验证/访问限制后重试；插件不会自动绕过。",
      risk_control_state: risk
    };
  }
  return { manual_challenge: false, risk_control_state: risk };
}

async function findOrOpenSourceImageSearchTab(url, options = {}) {
  const target = new URL(url);
  const tabs = await chrome.tabs.query({ currentWindow: true });
  const existing = options.forceNew ? null : tabs.find((tab) => {
    try {
      const parsed = new URL(tab.url || "");
      return /(^|\.)1688\.com$/i.test(parsed.hostname) && parsed.pathname === target.pathname;
    } catch (_error) {
      return false;
    }
  });
  if (existing?.id) {
    await chrome.tabs.update(existing.id, { active: true, url });
    return { id: existing.id };
  }
  const created = await chrome.tabs.create({ url, active: true });
  return { id: created.id };
}

function buildSourceImageSearchUrl(imageUrl) {
  const url = new URL(SOURCE_IMAGE_SEARCH_PAGE_URL);
  url.searchParams.set("tab", "imageSearch");
  url.searchParams.set("imageAddress", imageUrl);
  return url.href;
}

function buildSourceKeywordSearchUrl(title) {
  const keywords = String(title || "").replace(/\s+/g, " ").trim().slice(0, 120);
  const url = new URL("https://s.1688.com/selloffer/offer_search.htm");
  if (keywords) url.searchParams.set("keywords", keywords);
  return url.href;
}

function normalizeHttpUrl(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if (text.startsWith("//")) return `https:${text}`;
  if (/^https?:\/\//i.test(text)) return text;
  return "";
}

function sourceContractHasValue(value) {
  if (value === null || value === undefined) return false;
  if (typeof value === "boolean") return true;
  if (typeof value === "number") return Number.isFinite(value) && value !== 0;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return true;
}

function sourceContractPathValue(source, path) {
  let current = source || {};
  for (const part of String(path || "").split(".")) {
    if (!current || typeof current !== "object" || !(part in current)) return undefined;
    current = current[part];
  }
  return current;
}

function sourceContractFirst(source, paths) {
  for (const path of paths) {
    const value = sourceContractPathValue(source, path);
    if (sourceContractHasValue(value)) return { value, path };
  }
  return { value: undefined, path: paths[0] || "" };
}

function sourceContractEvidenceSource(path) {
  if (String(path || "").startsWith("raw_payload.")) return "raw_payload";
  if (String(path || "").startsWith("employee_action_validation.")) return "employee_action_validation";
  return "candidate";
}

function sourceContractBuild(candidate) {
  const source = candidate && typeof candidate === "object" ? candidate : {};
  const fields = [
    ["offer_id", ["offer_id", "offerId", "product_id", "productId"], "identity", true],
    ["source_url", ["source_url", "product_link", "link", "url"], "identity", true],
    ["source_title", ["source_title", "title", "product_name", "raw_payload.detail_title"], "identity", true],
    ["main_image_url", ["main_image_url", "image_url", "imageUrl", "imgUrl"], "identity", true],
    ["capture_method", ["capture_method"], "identity", true],
    ["captured_at", ["captured_at", "capturedAt", "raw_payload.detail_captured_at"], "identity", true],
    ["employee_action_sku_price_cny", ["employee_action_sku_price_cny", "employee_action_validation.sku_price_cny"], "cost", false],
    ["employee_action_freight_cny", ["employee_action_freight_cny", "employee_action_validation.freight_cny"], "cost", false],
    ["sku_price_source", ["sku_price_source", "employee_action_validation.price_source"], "cost", false],
    ["freight_confidence", ["freight_confidence"], "cost", false],
    ["freight_source", ["freight_source", "employee_action_validation.freight_source"], "cost", false],
    ["source_cost_closed", ["source_cost_closed"], "cost", false],
    ["source_sku_match_status", ["source_sku_match_status", "employee_action_validation.sku_match_status"], "sku", false],
    ["source_selected_spec_text", ["source_selected_spec_text", "employee_action_validation.selected_spec_text"], "sku", false],
    ["source_matched_spec_text", ["source_matched_spec_text"], "sku", false],
    ["source_variant_count", ["source_variant_count", "raw_payload.source_variant_count"], "sku", false],
    ["source_variant_records", ["source_variant_records", "raw_payload.source_variant_records"], "sku", false],
    ["employee_action_weight_kg", ["employee_action_weight_kg", "employee_action_validation.weight_kg"], "weight", false],
    ["employee_action_weight_text", ["employee_action_weight_text", "employee_action_validation.weight_text"], "weight", false],
    ["source_attribute_table", ["source_attribute_table", "raw_payload.source_attribute_table"], "weight", false],
    ["package_info_text_sample", ["package_info_text_sample", "raw_payload.package_info_text_sample"], "weight", false],
    ["selected_sku_image_urls", ["selected_sku_image_urls", "raw_payload.selected_sku_image_urls"], "image", false],
    ["source_product_visual_match_status", ["source_product_visual_match_status"], "image", false],
    ["source_product_visual_match_score", ["source_product_visual_match_score"], "image", false],
    ["source_product_visual_match_evidence", ["source_product_visual_match_evidence"], "image", false],
    ["detail_capture_status", ["raw_payload.detail_capture_status"], "detail", false]
  ];
  const fieldConfidence = {};
  const missingReasons = {};
  const evidenceSources = {};
  const groupSummary = {};
  for (const [name, paths, group, required] of fields) {
    const found = sourceContractFirst(source, paths);
    const present = sourceContractHasValue(found.value);
    fieldConfidence[name] = present ? "present" : "missing";
    if (!groupSummary[group]) groupSummary[group] = { present: 0, total: 0, required_missing: 0 };
    groupSummary[group].total += 1;
    if (present) {
      groupSummary[group].present += 1;
      evidenceSources[name] = sourceContractEvidenceSource(found.path);
    } else {
      missingReasons[name] = required ? "missing_required_field" : "not_captured";
      if (required) groupSummary[group].required_missing += 1;
    }
  }
  for (const group of Object.keys(groupSummary)) {
    const bucket = groupSummary[group];
    bucket.coverage = Number((bucket.present / Math.max(1, bucket.total)).toFixed(4));
  }
  const missingFields = Object.keys(fieldConfidence).filter((field) => fieldConfidence[field] !== "present");
  const presentCount = Object.keys(fieldConfidence).length - missingFields.length;
  const records = Array.isArray(source.source_variant_records) ? source.source_variant_records : (Array.isArray(source.raw_payload?.source_variant_records) ? source.raw_payload.source_variant_records : []);
  const skuImages = records.filter((record) => record && typeof record === "object" && sourceContractHasValue(record.image_url || record.img_url)).length;
  return {
    schema_version: SOURCE_CAPTURE_CONTRACT_VERSION,
    capture_completeness: Number((presentCount / Math.max(1, Object.keys(fieldConfidence).length)).toFixed(4)),
    field_confidence: fieldConfidence,
    missing_reasons: missingReasons,
    missing_capture_fields: missingFields,
    evidence_sources: evidenceSources,
    group_summary: groupSummary,
    sku_matrix_summary: {
      option_group_count: new Set(records.map((record) => String(record?.group_name || record?.name || "")).filter(Boolean)).size,
      combination_count: records.length,
      missing_price_count: records.filter((record) => !sourceContractHasValue(record?.price_cny || record?.price_text)).length,
      missing_image_count: records.length - skuImages,
      missing_stock_count: records.filter((record) => !sourceContractHasValue(record?.stock_text || record?.stock)).length,
      matrix_complete: records.length > 0 && records.every((record) => sourceContractHasValue(record?.price_cny || record?.price_text))
    },
    image_role_summary: {
      main_count: sourceContractHasValue(source.main_image_url || source.image_url) ? 1 : 0,
      detail_count: Array.isArray(source.raw_payload?.default_sku_product_image_urls) ? source.raw_payload.default_sku_product_image_urls.length : 0,
      sku_count: skuImages,
      video_cover_count: 0,
      promo_or_service_count: 0,
      sku_image_coverage: records.length ? Number((skuImages / records.length).toFixed(4)) : 0
    },
    shipping_summary: {
      first_unit_freight_cny: source.employee_action_freight_cny ?? source.freight_cny ?? source.employee_action_validation?.freight_cny ?? null,
      freight_confidence: source.freight_confidence || "",
      freight_source: source.freight_source || source.employee_action_validation?.freight_source || "",
      quantity_basis: source.employee_action_min_order_quantity || source.min_order_quantity || source.employee_action_validation?.min_order_quantity || "",
      procurement_cost_context: source.source_cost_closed === true ? "closed_loop" : "needs_review"
    },
    weight_summary: {
      weight_kg: source.employee_action_weight_kg ?? source.weight_kg ?? source.employee_action_validation?.weight_kg ?? null,
      weight_text: source.employee_action_weight_text || source.weight_text || source.employee_action_validation?.weight_text || "",
      weight_source: source.employee_action_weight_source || source.weight_source || source.employee_action_validation?.weight_source || "",
      variant_weight_coverage: sourceContractHasValue(source.employee_action_weight_kg ?? source.weight_kg ?? source.employee_action_validation?.weight_kg) ? 1 : 0
    },
    detail_enrichment_summary: {
      detail_attempted: sourceContractHasValue(source.raw_payload?.detail_capture_status || source.raw_payload?.detail_capture_ok),
      detail_enriched: source.raw_payload?.detail_capture_status === "captured" || source.raw_payload?.detail_capture_ok === true,
      detail_skipped_reason: sourceContractHasValue(source.raw_payload?.detail_capture_status) ? "" : "not_reported",
      manual_challenge_count: source.raw_payload?.detail_capture_status === "manual_challenge" ? 1 : 0,
      timeout_count: source.raw_payload?.detail_capture_status === "timeout" ? 1 : 0
    }
  };
}

function attachSourceCaptureContract(candidate) {
  if (!candidate || typeof candidate !== "object") return candidate;
  const capturedFields = candidate.captured_fields && typeof candidate.captured_fields === "object" ? { ...candidate.captured_fields } : {};
  const contract = capturedFields.preflight_contract && typeof capturedFields.preflight_contract === "object"
    ? capturedFields.preflight_contract
    : sourceContractBuild(candidate);
  return {
    ...candidate,
    source_capture_contract_version: contract.schema_version || SOURCE_CAPTURE_CONTRACT_VERSION,
    capture_completeness: contract.capture_completeness,
    missing_capture_fields: Array.isArray(contract.missing_capture_fields) ? contract.missing_capture_fields : [],
    evidence_sources: contract.evidence_sources || {},
    captured_fields: {
      ...capturedFields,
      preflight_contract: contract
    }
  };
}

function attachSourceContractsToResult(result) {
  const output = { ...(result || {}) };
  output.items = Array.isArray(output.items)
    ? output.items.map((item) => ({
      ...item,
      candidates: Array.isArray(item.candidates) ? item.candidates.map(attachSourceCaptureContract) : []
    }))
    : [];
  return output;
}

function sourceBrowserCounts(items, totalTasks) {
  const list = Array.isArray(items) ? items : [];
  const candidateCount = list.reduce((sum, item) => sum + (Array.isArray(item.candidates) ? item.candidates.length : 0), 0);
  return {
    total_tasks: Number(totalTasks || list.length || 0),
    processed_quotes: list.length,
    succeeded_quotes: list.filter((item) => item.status === "succeeded").length,
    matched_quotes: list.filter((item) => Array.isArray(item.candidates) && item.candidates.length > 0).length,
    candidate_count: candidateCount,
    no_result_quotes: list.filter((item) => item.status === "no_results").length,
    partial_detail_timeout_quotes: list.filter((item) => item.status === "partial_detail_timeout").length,
    manual_challenge_quotes: list.filter((item) => item.status === "manual_challenge").length,
    failed_quotes: list.filter((item) => item.status === "failed").length
  };
}

async function extractPriceQuoteDomSnapshot(tabId, options = {}) {
  return executeMainWorld(tabId, [options], async (opts = {}) => {
    const now = new Date().toISOString();
    const requirePriceDialog = Boolean(opts?.requirePriceDialog);
    const rowLimit = Math.max(1, Math.min(Number(opts?.rowLimit || 500), 1000));
    const dialogSelector = "[role='dialog'], .ant-modal, .ant-drawer, .semi-modal, .modal, .drawer, [class*='modal'], [class*='dialog'], [class*='drawer']";
    const priceDialogTextRe = /批量查看并确认申报价格|查看并确认申报价格|调整后申报价格|新申报价格|原申报价格|申请调整更新申报价格/;
    const visible = (element) => {
      if (!element) return false;
      const style = getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 1 && rect.height > 1;
    };
    const text = (element) => String(element?.innerText || element?.textContent || "").replace(/\s+/g, " ").trim();
    const absUrl = (url) => {
      try {
        if (!url) return "";
        return new URL(url, location.href).href;
      } catch (_error) {
        return "";
      }
    };
    const srcsetLast = (value) => String(value || "").split(",").map((part) => part.trim().split(/\s+/)[0]).filter(Boolean).pop() || "";
    const bgUrl = (element) => {
      try {
        const match = String(getComputedStyle(element).backgroundImage || "").match(/url\((['"]?)(.*?)\1\)/i);
        return match ? match[2] : "";
      } catch (_error) {
        return "";
      }
    };
    const imageUrl = (element) => absUrl(
      element?.currentSrc
      || element?.src
      || element?.getAttribute?.("data-src")
      || element?.getAttribute?.("data-lazy-src")
      || element?.getAttribute?.("data-original")
      || element?.getAttribute?.("data-url")
      || element?.getAttribute?.("src")
      || srcsetLast(element?.getAttribute?.("srcset"))
      || bgUrl(element)
    );
    const imageNodes = (element) => {
      if (!element?.querySelectorAll) return [];
      return [
        element,
        ...Array.from(element.querySelectorAll("img, source, [style*='background-image'], [data-src], [data-lazy-src], [data-original], [data-url]"))
      ];
    };
    const cellImages = (element) => {
      const seen = new Set();
      const output = [];
      for (const node of imageNodes(element)) {
        const url = imageUrl(node);
        if (!url || seen.has(url)) continue;
        seen.add(url);
        output.push(url);
        if (output.length >= 5) break;
      }
      return output;
    };
    const cellLink = (element) => {
      // 商品标题/图片在核价弹窗里通常是 <a>，href 携带商品详情页的 goods_id/productId，
      // 采集下来用于为每条报价补齐官方链接。
      for (const node of [element, ...Array.from(element?.querySelectorAll?.("a[href]") || [])]) {
        if (!node?.getAttribute) continue;
        const href = absUrl(node.getAttribute("href") || "");
        if (!href) continue;
        if (/#$/.test(href) || /javascript:/i.test(href)) continue;
        return href;
      }
      return "";
    };
    const rowImages = (element, scope) => {
      const direct = cellImages(element);
      if (direct.length) return direct.slice(0, 8);
      if (element?.closest?.(dialogSelector)) return [];
      const rect = element?.getBoundingClientRect?.();
      if (!rect || rect.height < 16) return [];
      const output = [];
      const seen = new Set();
      const top = rect.top - 10;
      const bottom = rect.bottom + 10;
      for (const image of Array.from((scope || document).querySelectorAll("img, [style*='background-image'], [data-src], [data-lazy-src], [data-original], [data-url]"))) {
        if (!visible(image)) continue;
        const imageRect = image.getBoundingClientRect();
        const centerY = imageRect.top + imageRect.height / 2;
        if (centerY < top || centerY > bottom) continue;
        if (imageRect.width < 20 || imageRect.height < 20) continue;
        const url = imageUrl(image);
        if (!url || seen.has(url)) continue;
        if (/avatar|user|logo|icon/i.test(url) && !/product|goods|sku|img\.kwcdn|alicdn|cbu01/i.test(url)) continue;
        seen.add(url);
        output.push(url);
        if (output.length >= 8) break;
      }
      return output;
    };
    const isQuoteLikeText = (value) => /申报价格|核价|价格申报|待卖家确认|待供应商确认|skc|sku|货号|spu/i.test(String(value || ""));
    const rowKey = (value) => String(value || "").replace(/\s+/g, " ").trim().slice(0, 500);
    const seenRows = new Set();
    const rows = [];
    const dialogScopes = Array.from(document.querySelectorAll(dialogSelector))
      .filter(visible)
      .filter((element) => priceDialogTextRe.test(text(element)));
    const bodyFallbackUsed = dialogScopes.length === 0 && !requirePriceDialog;
    const scopes = (dialogScopes.length ? dialogScopes : (bodyFallbackUsed ? [document.body] : []))
      .filter(visible)
      .slice(0, 8);
    const scrollTargets = Array.from(new Set(scopes.flatMap((scope) => [
      scope,
      ...Array.from(scope.querySelectorAll("*"))
    ]))).filter((element) => {
      const style = getComputedStyle(element);
      return /(auto|scroll)/.test(style.overflowY)
        && element.scrollHeight > element.clientHeight + 80
        && element.clientHeight > 80;
    }).sort((left, right) => (right.scrollHeight - right.clientHeight) - (left.scrollHeight - left.clientHeight)).slice(0, 2);
    const originalScrollTops = scrollTargets.map((element) => ({ element, top: element.scrollTop }));
    const maxScrollableHeight = Math.max(0, ...scrollTargets.map((element) => element.scrollHeight - element.clientHeight));
    const scrollStep = Math.max(240, ...scrollTargets.map((element) => Math.floor(element.clientHeight * 0.75)));
    const scrollOffsets = scrollTargets.length
      ? Array.from({ length: Math.min(20, Math.ceil(maxScrollableHeight / scrollStep) + 1) }, (_value, index) => Math.min(maxScrollableHeight, index * scrollStep))
      : [0];
    const nextPaint = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    let tableCount = 0;
    for (const scrollOffset of scrollOffsets) {
      for (const target of scrollTargets) target.scrollTop = scrollOffset;
      if (scrollTargets.length) await nextPaint();
      for (const scope of scopes) {
      for (const table of Array.from(scope.querySelectorAll("table")).filter(visible)) {
        tableCount += 1;
        const headers = Array.from(table.querySelectorAll("thead th")).map(text);
        for (const tr of Array.from(table.querySelectorAll("tbody tr")).filter(visible)) {
          const cells = Array.from(tr.querySelectorAll("td, th")).map((cell, index) => ({
            header: headers[index] || "",
            text: text(cell),
            images: cellImages(cell),
            url: cellLink(cell)
          }));
          if (cells.some((cell) => cell.text || cell.images.length)) {
            const key = rowKey(cells.map((cell) => cell.text).filter(Boolean).join(" | "));
            if (seenRows.has(key)) continue;
            seenRows.add(key);
            rows.push({
              source: "dom_table",
              cells,
              images: rowImages(tr, scope),
              text: cells.map((cell) => cell.text).filter(Boolean).join(" | "),
              link: cells.map((cell) => cell.url).find(Boolean) || "",
              capturedAt: now
            });
          }
        }
      }
      const gridRows = Array.from(scope.querySelectorAll("[role='row'], .ant-table-row, .semi-table-row, .table-row"))
        .filter(visible);
      for (const row of gridRows) {
        if (row.closest("table")) continue;
        const cells = Array.from(row.querySelectorAll("[role='cell'], .ant-table-cell, .semi-table-cell, td, th"))
          .filter(visible)
          .map((cell) => ({ header: "", text: text(cell), images: cellImages(cell), url: cellLink(cell) }));
        if (cells.length && cells.some((cell) => /skc|sku|申报|价格|核价|¥|￥|\d{5,}/i.test(cell.text) || cell.images.length)) {
          const key = rowKey(cells.map((cell) => cell.text).filter(Boolean).join(" | "));
          if (seenRows.has(key)) continue;
          seenRows.add(key);
          rows.push({
            source: "dom_grid",
            cells,
            images: rowImages(row, scope),
            text: cells.map((cell) => cell.text).filter(Boolean).join(" | "),
            link: cells.map((cell) => cell.url).find(Boolean) || "",
            capturedAt: now
          });
        }
      }
      const genericRows = Array.from(scope.querySelectorAll("div, li"))
        .filter(visible)
        .filter((element) => {
          if (element.closest("table")) return false;
          const rect = element.getBoundingClientRect();
          if (rect.width < 360 || rect.height < 48 || rect.height > Math.min(720, window.innerHeight * 0.85)) return false;
          const value = text(element);
          if (value.length < 20 || value.length > 2200) return false;
          if (!isQuoteLikeText(value)) return false;
          if (!/(申报价格|价格申报|待卖家确认|待供应商确认)/.test(value)) return false;
          if (!/(skc|sku|货号|spu|\d{8,})/i.test(value)) return false;
          return Boolean(element.querySelector("img")) || /¥|￥|元|\d+(?:\.\d+)?/.test(value);
        });
      for (const row of genericRows) {
        const directChildren = Array.from(row.children).filter(visible);
        const cellNodes = directChildren.length >= 2 ? directChildren : [row];
        const cells = cellNodes.map((cell) => ({
          header: "",
          text: text(cell),
          images: cellImages(cell),
          url: cellLink(cell)
        })).filter((cell) => cell.text || cell.images.length);
        if (!cells.length) continue;
        const rowText = cells.map((cell) => cell.text).filter(Boolean).join(" | ");
        const key = rowKey(rowText);
        if (seenRows.has(key)) continue;
        seenRows.add(key);
        rows.push({
          source: "dom_lifecycle_list",
          cells,
          images: rowImages(row, scope),
          text: rowText,
          link: cells.map((cell) => cell.url).find(Boolean) || "",
          capturedAt: now
        });
      }
      }
    }
    for (const { element, top } of originalScrollTops) element.scrollTop = top;
    const imageCount = new Set(rows.flatMap((row) => row.images || [])).size;
    const rowTruncated = rows.length > rowLimit;
    return {
      rows: rows.slice(0, rowLimit),
      total_row_count: rows.length,
      row_limit: rowLimit,
      row_truncated: rowTruncated,
      truncation_error: rowTruncated
        ? `当前页识别到 ${rows.length} 行核价 SKU，超过安全上限 ${rowLimit} 行；本次未入库，请缩小当前页数量后重试。`
        : "",
      table_count: tableCount,
      image_count: imageCount,
      dialog_present: dialogScopes.length > 0,
      dialog_count: dialogScopes.length,
      body_fallback_used: bodyFallbackUsed,
      require_price_dialog: requirePriceDialog,
      page_text_sample: text(document.body).slice(0, 1200),
      capturedAt: now
    };
  });
}

async function openPriceQuoteBatchDialog(tabId, options = {}) {
  return executeMainWorld(tabId, [options], async (opts = {}) => {
    const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
    const targetPageSize = Math.max(0, Math.min(Number(opts?.targetPageSize || 0), 100));
    const visible = (element) => {
      if (!element) return false;
      const style = getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 1 && rect.height > 1;
    };
    const text = (element) => String(element?.innerText || element?.textContent || "").replace(/\s+/g, " ").trim();
    const disabled = (element) => Boolean(
      element?.disabled
      || element?.getAttribute?.("aria-disabled") === "true"
      || /\bdisabled\b|is-disabled/.test(String(element?.className || ""))
    );
    const dialogSelector = "[role='dialog'], .ant-modal, .ant-drawer, .semi-modal, .modal, .drawer, [class*='modal'], [class*='dialog'], [class*='drawer']";
    const priceDialogTextRe = /批量查看并确认申报价格|查看并确认申报价格|调整后申报价格|新申报价格|原申报价格|申请调整更新申报价格/;
    const hasPriceDialog = () => {
      const dialogs = Array.from(document.querySelectorAll(dialogSelector))
        .filter(visible)
        .some((element) => priceDialogTextRe.test(text(element)));
      if (dialogs) return true;
      const bodyText = text(document.body);
      return /调整后申报价格|新申报价格|原申报价格/.test(bodyText) && /全部提交\(\d+项\)|批量查看并确认申报价格/.test(bodyText);
    };
    const findButton = () => Array.from(document.querySelectorAll("button, [role='button']"))
      .filter(visible)
      .find((element) => /批量查看并确认申报价格|批量查看/.test(text(element)));
    const clickElement = (element) => {
      element.scrollIntoView?.({ block: "center", inline: "center" });
      element.click();
    };
    const findPageSizeControl = (targetSize) => {
      const targetText = String(targetSize || 100);
      const pageLabels = Array.from(document.querySelectorAll("span, div, label"))
        .filter((element) => visible(element) && text(element).includes("每页"));
      const numeric = Array.from(document.querySelectorAll("button, span, div, a, input"))
        .filter((element) => visible(element))
        .map((element) => {
          const value = element.tagName === "INPUT" ? String(element.value || "") : text(element);
          return { element, text: value, rect: element.getBoundingClientRect() };
        })
        .filter((item) => /^(10|20|30|40|50|80|100|200)$/.test(item.text));
      if (pageLabels.length > 0) {
        const labelRect = pageLabels[pageLabels.length - 1].getBoundingClientRect();
        const nearby = numeric
          .filter((item) => Math.abs((item.rect.top + item.rect.height / 2) - (labelRect.top + labelRect.height / 2)) < 90)
          .sort((a, b) => {
            if (a.text === targetText && b.text !== targetText) return -1;
            if (b.text === targetText && a.text !== targetText) return 1;
            return Math.abs(a.rect.left - labelRect.right) - Math.abs(b.rect.left - labelRect.right);
          })[0];
        if (nearby) return nearby;
      }
      return numeric
        .filter((item) => item.rect.top > window.innerHeight * 0.42 && item.rect.left > window.innerWidth * 0.45)
        .sort((a, b) => {
          if (a.text === targetText && b.text !== targetText) return -1;
          if (b.text === targetText && a.text !== targetText) return 1;
          return b.rect.top - a.rect.top || b.rect.left - a.rect.left;
        })[0] || null;
    };
    const selectTargetPageSize = async (targetSize) => {
      if (!targetSize) return { ok: true, skipped: true };
      const targetText = String(targetSize);
      const originalX = window.scrollX;
      const originalY = window.scrollY;
      const restoreScroll = async () => {
        window.scrollTo({ left: originalX, top: originalY, behavior: "auto" });
        await delay(250);
      };
      const selectNative = async (scrolled) => {
        const nativeSelect = Array.from(document.querySelectorAll("select"))
          .filter(visible)
          .find((select) => Array.from(select.options || []).some((option) => option.textContent?.trim() === targetText || option.value === targetText));
        if (!nativeSelect) return null;
        const option = Array.from(nativeSelect.options || []).find((item) => item.textContent?.trim() === targetText || item.value === targetText);
        if (!option) return null;
        if (nativeSelect.value !== option.value) {
          nativeSelect.value = option.value;
          nativeSelect.dispatchEvent(new Event("input", { bubbles: true }));
          nativeSelect.dispatchEvent(new Event("change", { bubbles: true }));
          await delay(800);
        }
        return { ok: true, method: "select", selectedText: targetText, scrolled };
      };
      const selectCustom = async (scrolled) => {
        const control = findPageSizeControl(targetSize);
        if (!control) return null;
        if (control.text === targetText) {
          return { ok: true, method: "custom", selectedText: targetText, already: true, scrolled };
        }
        clickElement(control.element);
        await delay(450);
        const option = Array.from(document.querySelectorAll("button, span, div, a, li"))
          .filter((element) => visible(element) && text(element) === targetText)
          .map((element) => ({ element, rect: element.getBoundingClientRect() }))
          .sort((a, b) => b.rect.top - a.rect.top || b.rect.left - a.rect.left)[0];
        if (!option) {
          return { ok: false, error: `已打开每页条数控件，但没有找到 ${targetText} 选项`, before: control.text, scrolled };
        }
        clickElement(option.element);
        await delay(900);
        return { ok: true, method: "custom", before: control.text, selectedText: targetText, scrolled };
      };
      let result = await selectNative(false) || await selectCustom(false);
      if (!result || result.ok === false) {
        const scrollTarget = Math.max(document.documentElement?.scrollHeight || 0, document.body?.scrollHeight || 0);
        window.scrollTo({ left: originalX, top: scrollTarget, behavior: "auto" });
        await delay(700);
        result = await selectNative(true) || await selectCustom(true);
      }
      await restoreScroll();
      return result || {
        ok: false,
        error: `没有找到每页条数控件；可能只能读取当前页，建议把每页切到 ${targetText} 后重采`,
        scrolled: true
      };
    };
    const checkboxClickTarget = () => {
      const selectors = [
        "thead input[type='checkbox']",
        ".ant-table-header input[type='checkbox']",
        ".semi-table-header input[type='checkbox']",
        ".ant-table-selection-column input[type='checkbox']",
        "input[type='checkbox']",
        "[role='checkbox']",
        ".ant-checkbox-wrapper",
        ".semi-checkbox",
        ".arco-checkbox",
      ];
      const seen = new Set();
      for (const selector of selectors) {
        for (const element of Array.from(document.querySelectorAll(selector))) {
          const target = element.closest?.("label, .ant-checkbox-wrapper, .semi-checkbox, .arco-checkbox") || element;
          if (!target || seen.has(target)) continue;
          seen.add(target);
          if (!visible(target) && !visible(element)) continue;
          const targetText = text(target);
          if (/全部提交|提交|议价|驳回|发布|删除/.test(targetText)) continue;
          return target;
        }
      }
      return null;
    };

    if (hasPriceDialog()) {
      return {
        ok: true,
        already_open: true,
        pageSizeSelection: targetPageSize ? { ok: false, skipped: true, error: "批量核价弹窗已打开，无法再确认列表每页条数；如只返回50项，请关闭弹窗并重新采集" } : { ok: true, skipped: true },
        statusText: "批量核价弹窗已打开"
      };
    }

    const pageSizeSelection = await selectTargetPageSize(targetPageSize);

    let button = findButton();
    if (!button) {
      return {
        ok: false,
        error: "batch_price_button_not_found",
        statusText: "没有找到“批量查看并确认申报价格”按钮",
        pageSizeSelection,
        pageTextSample: text(document.body).slice(0, 500)
      };
    }

    let clickedSelectAll = false;
    if (disabled(button)) {
      const checkbox = checkboxClickTarget();
      if (!checkbox) {
        return {
          ok: false,
          error: "select_all_checkbox_not_found",
          statusText: "批量查看按钮不可用，且没有找到列表全选框",
          pageSizeSelection
        };
      }
      clickElement(checkbox);
      clickedSelectAll = true;
      await delay(900);
      button = findButton();
    }

    if (!button || disabled(button)) {
      return {
        ok: false,
        error: "batch_price_button_disabled",
        statusText: "已尝试全选当前页，但批量查看按钮仍不可用",
        clickedSelectAll,
        pageSizeSelection
      };
    }

    clickElement(button);
    for (let attempt = 0; attempt < 16; attempt += 1) {
      await delay(500);
      if (hasPriceDialog()) {
        return {
          ok: true,
          clickedSelectAll,
          clickedBatchButton: true,
          pageSizeSelection,
          statusText: "已打开批量查看并确认申报价格弹窗"
        };
      }
    }

    return {
      ok: false,
      error: "batch_price_dialog_not_opened",
      clickedSelectAll,
      clickedBatchButton: true,
      pageSizeSelection,
      statusText: "已点击批量查看按钮，但未等到核价弹窗"
    };
  }, { attempts: 1 });
}

async function readActivePageContext() {
  const tab = await getActiveBusinessTab({ allowAny: true });
  const response = await chrome.tabs.sendMessage(tab.id, { type: "READ_PAGE_CONTEXT" });
  return response;
}

async function captureCurrentProductPage() {
  const tab = await getActiveBusinessTab({ allowAny: true });
  return captureProductFromTab(tab, { commandType: "product_capture_current_page" });
}

async function readWorkbenchError(response) {
  const text = await response.text();
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed.detail === "string" && parsed.detail.trim()) {
      return parsed.detail.trim();
    }
  } catch (_error) {
    // Non-JSON errors are returned by some local proxy/server failures.
  }
  return text || `HTTP ${response.status}`;
}

function captureFailureStatusText(errorText, status = 0) {
  const message = String(errorText || "").replace(/\s+/g, " ").trim();
  if (/唯一身份和完整销售属性|规格加载完成/.test(message)) return "规格未加载完整，请刷新后单采";
  if (/商品 ID 与目标商品不一致/.test(message)) return "商品页面未加载正确，请刷新后单采";
  if (/缺少商品标题|标题质量不足|标题疑似/.test(message)) return "商品标题未加载完整，请稍后单采";
  if (/缺少可靠主图|主图/.test(message)) return "商品主图未加载完整，请稍后单采";
  if (message) return `入池失败：${message.slice(0, 42)}`;
  return `工作台入池失败：${status || "未知错误"}`;
}

async function captureProductToWorkbench(sourceTab) {
  const tab = sourceTab?.id ? sourceTab : await getActiveBusinessTab({ allowAny: true });
  const captured = await captureProductFromTab(tab, { commandType: "product_capture_to_workbench" });
  if (captured.error) {
    const blockedFailure = await blockedByOtherExtensionFailure(tab, "采集失败：其他插件拦截了商品数据");
    if (blockedFailure) return blockedFailure;
    return { ok: false, ...captured };
  }
  const connection = await readConnectionContext();
  if (!connection) {
    return {
      ok: false,
      error: "missing_plugin_session",
      statusText: "插件未连接工作台",
      help: "请先打开扩展弹窗连接本地工作台。"
    };
  }
  const baseUrl = connection.http_base;
  if (!isAllowedWorkbenchUrl(baseUrl)) {
    return {
      ok: false,
      error: "unsupported_workbench_url",
      statusText: "工作台地址只支持本机或局域网内网 HTTP 地址",
      help: "请在扩展弹窗里填写 127.0.0.1、localhost、10.x、172.16-31.x 或 192.168.x 地址。"
    };
  }
  const response = await fetch(workbenchHttpUrl(baseUrl, "/plugin/product-capture/draft"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ session_token: connection.session_token, product: captured.product })
  });
  if (!response.ok) {
    if ([401, 403, 404].includes(response.status)) await clearConnectionState();
    const errorText = await readWorkbenchError(response);
    return {
      ok: false,
      error: "workbench_capture_failed",
      statusText: captureFailureStatusText(errorText, response.status),
      help: errorText,
      url: captured.url,
      capturedAt: new Date().toISOString()
    };
  }
  const payload = await response.json();
  if (payload.tenant_context !== undefined) {
    tenantContext.assertServerTenantContext(connection, payload.tenant_context);
  }
  if (payload.skipped) {
    return {
      ok: true,
      skipped: true,
      command_type: "product_capture_to_workbench",
      statusText: payload.message || "已跳过重复入池",
      product: captured.product,
      draft: null,
      url: captured.url,
      capturedAt: new Date().toISOString()
    };
  }
  return {
    ok: true,
    command_type: "product_capture_to_workbench",
    statusText: payload.message || "已采集并加入待处理池",
    product: captured.product,
    draft: payload.draft,
    url: captured.url,
    capturedAt: new Date().toISOString()
  };
}

function productBatchCaptureTabCandidate(tab) {
  const rawUrl = String(tab?.url || "");
  let parsed = null;
  try {
    parsed = new URL(rawUrl);
  } catch (_error) {
    return null;
  }
  const hostname = parsed.hostname || "";
  if (!/(^|\.)((temu)|(1688)|(alibaba))\.com$/i.test(hostname)) return null;
  if (isExcludedProductCaptureUrl(rawUrl)) return null;
  const text = `${rawUrl} ${tab?.title || ""}`.toLowerCase();
  let score = 10;
  if (tab?.active) score += 18;
  if (/search|offer_search|result|list|market|huo|page|p4p|supplier|factory|wholesale|找货源|批发|采购|同款|相似/.test(text)) score += 20;
  if (/\/offer\/\d+\.html|-g-\d+|goods\.html|product-detail/.test(text)) score += 8;
  if (/1688\.com|alibaba\.com|temu\.com/.test(text)) score += 4;
  return {
    tab,
    score,
    lastAccessed: Number(tab?.lastAccessed || 0)
  };
}

async function findRecentProductBatchCaptureTab(payload = {}) {
  const targetUrl = normalizeHttpUrl(payload?.target_url || payload?.url || "");
  const tabs = await chrome.tabs.query({});
  const candidates = tabs
    .map(productBatchCaptureTabCandidate)
    .filter(Boolean)
    .sort((left, right) => {
      if (right.score !== left.score) return right.score - left.score;
      if (right.lastAccessed !== left.lastAccessed) return right.lastAccessed - left.lastAccessed;
      return Number(right.tab?.id || 0) - Number(left.tab?.id || 0);
    });
  if (targetUrl) {
    const target = candidates.find((candidate) => normalizeHttpUrl(candidate.tab?.url || "") === targetUrl);
    if (target) return { ok: true, tab: target.tab, selected_reason: "target_url" };
    return {
      ok: false,
      error: "product_batch_target_tab_not_found",
      statusText: "没有找到要后台批量采集的目标页面",
      help: "请确认目标 TEMU/1688/Alibaba 页面仍然打开，或回到目标页面使用网页左下角的批量采集按钮。"
    };
  }
  if (!candidates.length) {
    return {
      ok: false,
      error: "no_product_batch_capture_tab",
      statusText: "没有找到可后台批量采集的平台列表页",
      help: "请先打开 TEMU/1688/Alibaba 商品列表或相似商品页，再回到工作台点击后台批量采集。"
    };
  }
  const selected = candidates[0];
  const second = candidates[1];
  if (
    second
    && second.score >= selected.score - 5
    && selected.lastAccessed > 0
    && second.lastAccessed > 0
    && Math.abs(selected.lastAccessed - second.lastAccessed) < 800
  ) {
    return {
      ok: false,
      error: "ambiguous_product_batch_capture_tab",
      statusText: "同时找到多个最近使用的平台页，未自动选择",
      help: "请先切到要采集的商品列表页，再回到工作台点击后台批量采集；或直接使用页面左下角“批量采集本页”。",
      candidates: candidates.slice(0, 5).map((candidate) => ({
        title: candidate.tab?.title || "",
        url: candidate.tab?.url || "",
        score: candidate.score,
        lastAccessed: candidate.lastAccessed || null
      }))
    };
  }
  return { ok: true, tab: selected.tab, selected_reason: "recent_product_tab" };
}

async function runProductBatchCaptureCommand(baseUrl, sessionToken, command) {
  const payload = command?.payload || {};
  const startedAt = new Date().toISOString();
  const publishProgress = async (statusText, extra = {}) => {
    await postResult(baseUrl, sessionToken, command.id, "running", {
      command_type: "product_batch_capture_current_page",
      statusText,
      queued_from_workbench: true,
      safety: {
        read_only: true,
        no_platform_write: true,
        no_submit_clicks: true,
        no_product_publish: true
      },
      startedAt,
      capturedAt: new Date().toISOString(),
      ...extra
    });
  };
  await publishProgress("插件已领取后台批量采集任务，正在定位最近的平台列表页");
  const selected = await findRecentProductBatchCaptureTab(payload);
  if (!selected.ok) {
    return {
      command_type: "product_batch_capture_current_page",
      queued_from_workbench: true,
      ok: false,
      error: selected.error || "product_batch_capture_tab_not_found",
      statusText: selected.statusText || "没有找到可后台批量采集的平台页",
      help: selected.help || "",
      candidates: selected.candidates || [],
      safety: {
        read_only: true,
        no_platform_write: true,
        no_submit_clicks: true,
        no_product_publish: true
      },
      startedAt,
      capturedAt: new Date().toISOString()
    };
  }
  await publishProgress(`已选择后台批量采集页：${(selected.tab?.title || selected.tab?.url || "").slice(0, 80)}`, {
    selected_reason: selected.selected_reason || "",
    source_tab: {
      id: selected.tab?.id || null,
      title: selected.tab?.title || "",
      url: selected.tab?.url || ""
    }
  });
  const result = await captureVisibleProductsToWorkbench(selected.tab);
  return {
    ...result,
    command_type: "product_batch_capture_current_page",
    queued_from_workbench: true,
    selected_reason: selected.selected_reason || "",
    source_tab: {
      id: selected.tab?.id || null,
      title: selected.tab?.title || "",
      url: selected.tab?.url || ""
    },
    safety: {
      ...(result?.safety && typeof result.safety === "object" ? result.safety : {}),
      read_only: true,
      no_platform_write: true,
      no_submit_clicks: true,
      no_product_publish: true
    },
    startedAt,
    capturedAt: new Date().toISOString()
  };
}

async function captureVisibleProductsToWorkbench(sourceTab) {
  const tab = sourceTab?.id ? sourceTab : await getActiveBusinessTab({ allowAny: true });
  const job = createProductBatchCaptureJob(tab);
  if (!job) {
    return {
      ok: false,
      error: "batch_capture_limit_reached",
      statusText: `批量采集已达上限：最多同时运行 ${PRODUCT_BATCH_ACTIVE_JOB_LIMIT} 个，请等待当前采集完成或先中断。`,
      active_batch_capture_limit: PRODUCT_BATCH_ACTIVE_JOB_LIMIT,
      active_batch_capture_count: activeProductBatchCaptureJobCount(),
      capturedAt: new Date().toISOString()
    };
  }
  try {
    return await captureVisibleProductsToWorkbenchJob(tab, job);
  } finally {
    finishProductBatchCaptureJob(job);
  }
}

async function captureVisibleProductsToWorkbenchJob(tab, job) {
  const captured = await captureProductListFromTab(tab);
  if (captured.error) return { ok: false, ...captured };
  if (isProductBatchCaptureCancelled(job)) {
    return {
      ok: true,
      cancelled: true,
      command_type: "product_batch_capture_to_workbench",
      statusText: "批量采集已中断，未继续打开详情页",
      captured_count: 0,
      skipped_count: 0,
      products: [],
      drafts: [],
      url: captured.url || tab.url || "",
      capturedAt: new Date().toISOString()
    };
  }
  const products = Array.isArray(captured.products) ? captured.products : [];
  if (!products.length) {
    const blockedFailure = await blockedByOtherExtensionFailure(tab, "批量采集失败：其他插件拦截了商品数据");
    if (blockedFailure) return blockedFailure;
    const skippedItems = Array.isArray(captured.skipped) ? captured.skipped : [];
    const reasonCounts = {};
    for (const item of skippedItems) {
      const reason = String(item?.reason || item?.error || "unknown").trim() || "unknown";
      reasonCounts[reason] = (reasonCounts[reason] || 0) + 1;
    }
    const reasonText = Object.entries(reasonCounts)
      .sort((left, right) => right[1] - left[1])
      .slice(0, 3)
      .map(([reason, count]) => `${reason}×${count}`)
      .join("，");
    const candidateCount = Number(captured.total_candidates || 0);
    return {
      ok: false,
      error: "no_list_products",
      statusText: candidateCount
        ? `识别到 ${candidateCount} 个候选但都被过滤：${reasonText || "原因未知"}`
        : "当前页没有识别到可批量采集的商品",
      help: "批量采集只需要先识别商品详情链接，标题、主图、规格和 SKU 会在详情页复用单品采集获取。请确认页面是 TEMU/1688/Alibaba 搜索或列表页，并已加载商品卡片。",
      skipped_count: captured.skipped_count || 0,
      skipped: skippedItems,
      url: captured.url || tab.url || "",
      capturedAt: new Date().toISOString()
    };
  }
  const connection = await readConnectionContext();
  const settings = await chrome.storage.local.get(["productBatchDetailWorkerCount"]);
  if (!connection) {
    return {
      ok: false,
      error: "missing_plugin_session",
      statusText: "插件未连接工作台",
      help: "请先打开扩展弹窗连接本地工作台。"
    };
  }
  const baseUrl = connection.http_base;
  if (!isAllowedWorkbenchUrl(baseUrl)) {
    return {
      ok: false,
      error: "unsupported_workbench_url",
      statusText: "工作台地址只支持本机或局域网 HTTP 地址",
      help: "请在扩展弹窗里填写 127.0.0.1、localhost、10.x、172.16-31.x 或 192.168.x 地址。"
    };
  }

  const normalizedProducts = products.map(normalizeBatchListProduct);
  if (isProductBatchCaptureCancelled(job)) {
    return {
      ok: true,
      cancelled: true,
      command_type: "product_batch_capture_to_workbench",
      statusText: "批量采集已中断，已停止详情补采",
      captured_count: 0,
      skipped_count: 0,
      products: [],
      drafts: [],
      url: captured.url || tab.url || "",
      capturedAt: new Date().toISOString()
    };
  }
  const existingDraftsBySource = await queryExistingCapturedProductDrafts(baseUrl, connection.session_token, normalizedProducts);
  const skippedExistingDrafts = [];
  const productsNeedingDetail = [];
  for (const product of normalizedProducts) {
    const sourceRef = productCaptureSourceRef(product);
    const existingDraft = sourceRef ? existingDraftsBySource.get(sourceRef) : null;
    if (existingDraft?.detail_capture_complete) {
      skippedExistingDrafts.push({
        title: product.title || existingDraft.title || "",
        link: product.product_link || product.link || sourceRef,
        error: "existing_complete_draft",
        statusText: "已完整入池，跳过重复详情补采",
        draft_id: existingDraft.draft_id || existingDraft.id || null
      });
      continue;
    }
    productsNeedingDetail.push(product);
  }

  const draftProducts = [];
  const drafts = [];
  const detailErrors = [];
  const errors = [];
  let deepCapturedCount = 0;
  const detailCandidates = productsNeedingDetail.slice(0, PRODUCT_BATCH_DETAIL_CAPTURE_LIMIT);
  const deferredDetailProducts = productsNeedingDetail.slice(PRODUCT_BATCH_DETAIL_CAPTURE_LIMIT);
  const deferredDetails = deferredDetailProducts.map((product) => ({
    title: product.title || "",
    link: product.product_link || product.link || "",
    error: "detail_capture_deferred",
    statusText: `已识别为新商品，本次达到 ${PRODUCT_BATCH_DETAIL_CAPTURE_LIMIT} 个详情采集上限；再次点击批量采集会继续处理后续商品`
  }));
  const detailWorkerCount = resolveProductBatchDetailWorkerCount(settings.productBatchDetailWorkerCount, detailCandidates.length);
  let batchCaptureWindow = null;
  const workerTabs = [];
  const riskControlState = { blocked: false, detail: null };
  try {
    const captureWorkspace = await createProductBatchCaptureWindow(detailWorkerCount);
    batchCaptureWindow = captureWorkspace.window;
    workerTabs.push(...captureWorkspace.tabs);

    const workerResults = await Promise.all(workerTabs.map(async (workerTab, workerIndex) => {
      const workerProducts = detailCandidates.filter((_product, productIndex) => productIndex % detailWorkerCount === workerIndex);
      const result = { draftProducts: [], detailErrors: [], deepCapturedCount: 0 };
      if (workerIndex > 0) {
        await delay(workerIndex * PRODUCT_BATCH_DETAIL_WORKER_STAGGER_MS);
      }

      for (let productIndex = 0; productIndex < workerProducts.length; productIndex += 1) {
        if (riskControlState.blocked || isProductBatchCaptureCancelled(job)) break;
        const product = workerProducts[productIndex];
        if (productIndex > 0 || workerIndex > 0) {
          await safeProductBatchPacingDelay(workerIndex);
        }
        if (riskControlState.blocked || isProductBatchCaptureCancelled(job)) break;
        const detail = await captureBatchProductDetail(product, {
          workerTabId: workerTab?.id,
          workerIndex,
          workerCount: detailWorkerCount,
          activate: workerIndex === 0,
          dedicatedWindow: true,
          cancelJob: job
        });
        if (detail.cancelled || isProductBatchCaptureCancelled(job)) {
          result.detailErrors.push({
            title: product.title || "",
            link: product.product_link || product.link || "",
            error: detail.error || "batch_capture_cancelled",
            statusText: detail.statusText || "员工已中断批量采集",
            worker_index: detail.worker_index ?? workerIndex,
            worker_count: detail.worker_count ?? detailWorkerCount
          });
          break;
        }
        if (detail.risk_control) {
          riskControlState.blocked = true;
          riskControlState.detail = detail;
          result.detailErrors.push({
            title: product.title || "",
            link: product.product_link || product.link || "",
            error: detail.error || "risk_control_blocked",
            statusText: detail.statusText || "1688 访问被拒绝，已暂停批量采集",
            help: detail.help || "",
            worker_index: detail.worker_index ?? workerIndex,
            worker_count: detail.worker_count ?? detailWorkerCount
          });
          break;
        }
        if (detail.ok && detail.product) {
          const mergedProduct = mergeListAndDetailProduct(product, detail.product, detail);
          result.deepCapturedCount += 1;
          if (isProductBatchCaptureCancelled(job)) break;
          const saved = await postBatchCapturedProductDraft(baseUrl, connection.session_token, mergedProduct);
          if (saved.ok && saved.skipped) {
            skippedExistingDrafts.push({
              title: mergedProduct.title || "",
              link: mergedProduct.product_link || mergedProduct.link || "",
              error: saved.reason || "capture_skipped",
              statusText: saved.message || "已跳过重复入池",
              help: saved.help || ""
            });
          } else if (saved.ok) {
            result.draftProducts.push(mergedProduct);
            drafts.push(saved.draft || {});
          } else {
            errors.push({
              title: mergedProduct.title || "",
              link: mergedProduct.product_link || mergedProduct.link || "",
              status: saved.status || 0,
              error: saved.error || "workbench_capture_failed",
              statusText: saved.statusText || saved.error || "工作台入池失败",
              help: saved.help || ""
            });
          }
          continue;
        }
        result.detailErrors.push({
          title: product.title || "",
          link: product.product_link || product.link || "",
          error: detail.error || "detail_capture_failed",
          statusText: detail.statusText || "详情页单品采集失败",
          help: detail.help || "",
          worker_index: detail.worker_index ?? workerIndex,
          worker_count: detail.worker_count ?? detailWorkerCount
        });
      }
      return result;
    }));

    for (const result of workerResults) {
      draftProducts.push(...result.draftProducts);
      detailErrors.push(...result.detailErrors);
      deepCapturedCount += result.deepCapturedCount;
    }
  } finally {
    if (riskControlState.blocked && riskControlState.detail?.tab_id) {
      await focusProductBatchCaptureTab(riskControlState.detail.tab_id);
    } else if (batchCaptureWindow?.id != null) {
      try {
        await chrome.windows.remove(batchCaptureWindow.id);
      } catch (_error) {
        // The dedicated capture window may already be closed by the user.
      }
    }
  }

  if (isProductBatchCaptureCancelled(job)) {
    const skippedCount = Number(captured.skipped_count || 0) + skippedExistingDrafts.length + detailErrors.length + errors.length;
    return {
      ok: true,
      cancelled: true,
      command_type: "product_batch_capture_to_workbench",
      statusText: drafts.length > 0
        ? `已中断批量采集，已入池 ${drafts.length} 个，未继续打开后续详情页`
        : "已中断批量采集，未继续打开后续详情页",
      captured_count: drafts.length,
      deep_captured_count: deepCapturedCount,
      existing_complete_count: skippedExistingDrafts.length,
      new_detail_candidate_count: productsNeedingDetail.length,
      detail_attempted_count: detailCandidates.length,
      deferred_detail_count: deferredDetails.length,
      detail_worker_count: detailWorkerCount,
      batch_capture_window: true,
      active_batch_capture_limit: PRODUCT_BATCH_ACTIVE_JOB_LIMIT,
      risk_control_blocked: false,
      source_candidates_count: products.length,
      list_scan_limit: PRODUCT_BATCH_LIST_SCAN_LIMIT,
      skipped_count: skippedCount,
      products: draftProducts,
      drafts,
      skipped: [...(captured.skipped || []), ...skippedExistingDrafts, ...detailErrors, ...errors].slice(0, 80),
      deferred: deferredDetails.slice(0, 80),
      base_url: baseUrl,
      url: captured.url || tab.url || "",
      capturedAt: new Date().toISOString()
    };
  }

  if (!draftProducts.length && skippedExistingDrafts.length && !detailErrors.length && !errors.length) {
    return {
      ok: true,
      command_type: "product_batch_capture_to_workbench",
      statusText: `本页 ${skippedExistingDrafts.length} 个商品无需重复入池，已跳过详情补采`,
      captured_count: 0,
      deep_captured_count: 0,
      list_fallback_count: 0,
      existing_complete_count: skippedExistingDrafts.length,
      new_detail_candidate_count: productsNeedingDetail.length,
      detail_attempted_count: 0,
      deferred_detail_count: deferredDetails.length,
      detail_worker_count: detailWorkerCount,
      batch_capture_window: true,
      risk_control_blocked: false,
      risk_control_detail: null,
      source_candidates_count: products.length,
      list_scan_limit: PRODUCT_BATCH_LIST_SCAN_LIMIT,
      skipped_count: Number(captured.skipped_count || 0) + skippedExistingDrafts.length,
      products: [],
      drafts: [],
      skipped: [...(captured.skipped || []), ...skippedExistingDrafts].slice(0, 80),
      deferred: deferredDetails.slice(0, 80),
      base_url: baseUrl,
      url: captured.url || tab.url || "",
      capturedAt: new Date().toISOString()
    };
  }

  if (!draftProducts.length) {
    const blockedFailure = await blockedByOtherExtensionFailure(tab, "批量采集失败：其他插件拦截了商品数据");
    if (blockedFailure) return blockedFailure;
    return {
      ok: false,
      error: "no_deep_captured_products",
      statusText: "批量采集失败：详情页单采没有成功商品",
      help: "插件已识别到商品详情链接，但逐个打开详情页复用单品采集时没有拿到可入池的标题、主图和链接。请确认 TEMU/1688/Alibaba 详情页可正常打开、没有登录/验证拦截，再重试。",
      captured_count: 0,
      existing_complete_count: skippedExistingDrafts.length,
      new_detail_candidate_count: productsNeedingDetail.length,
      detail_attempted_count: detailCandidates.length,
      deferred_detail_count: deferredDetails.length,
      detail_worker_count: detailWorkerCount,
      batch_capture_window: true,
      skipped_count: Number(captured.skipped_count || 0) + skippedExistingDrafts.length + detailErrors.length,
      skipped: [...(captured.skipped || []), ...skippedExistingDrafts, ...detailErrors].slice(0, 80),
      deferred: deferredDetails.slice(0, 80),
      url: captured.url || tab.url || "",
      capturedAt: new Date().toISOString()
    };
  }

  const skippedCount = Number(captured.skipped_count || 0) + skippedExistingDrafts.length + detailErrors.length + errors.length;
  return {
    ok: drafts.length > 0,
    command_type: "product_batch_capture_to_workbench",
    statusText: riskControlState.blocked
      ? `已入池 ${drafts.length} 个，检测到 1688 访问被拒绝，已暂停批量采集；请处理页面提示或稍后继续`
      : drafts.length > 0
      ? `已入池 ${drafts.length} 个（详情单采 ${deepCapturedCount}，并发 ${detailWorkerCount}），详情失败 ${detailErrors.length} 个，跳过 ${skippedCount} 个${deferredDetails.length ? `，待下批 ${deferredDetails.length} 个` : ""}`
      : "批量采集失败：没有商品成功入池",
    captured_count: drafts.length,
    deep_captured_count: deepCapturedCount,
    list_fallback_count: 0,
    existing_complete_count: skippedExistingDrafts.length,
    new_detail_candidate_count: productsNeedingDetail.length,
    detail_attempted_count: detailCandidates.length,
    deferred_detail_count: deferredDetails.length,
    detail_worker_count: detailWorkerCount,
    batch_capture_window: true,
    risk_control_blocked: riskControlState.blocked,
    risk_control_detail: riskControlState.detail || null,
    source_candidates_count: products.length,
    list_scan_limit: PRODUCT_BATCH_LIST_SCAN_LIMIT,
    skipped_count: skippedCount,
    products: draftProducts,
    drafts,
    skipped: [...(captured.skipped || []), ...skippedExistingDrafts, ...detailErrors, ...errors].slice(0, 80),
    deferred: deferredDetails.slice(0, 80),
    base_url: baseUrl,
    url: captured.url || tab.url || "",
    capturedAt: new Date().toISOString()
  };
}

async function postBatchCapturedProductDraft(baseUrl, sessionToken, product) {
  try {
    const response = await fetch(workbenchHttpUrl(baseUrl, "/plugin/product-capture/draft"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ session_token: sessionToken, product })
    });
    if (!response.ok) {
      const errorText = await readWorkbenchError(response);
      return {
        ok: false,
        status: response.status,
        error: errorText,
        statusText: captureFailureStatusText(errorText, response.status),
        help: errorText
      };
    }
    const payload = await response.json();
    if (payload.tenant_context !== undefined) {
      tenantContext.assertServerTenantContext(trustedConnectionForBase(baseUrl), payload.tenant_context);
    }
    if (payload.skipped) {
      return {
        ok: true,
        skipped: true,
        reason: payload.reason || "skipped",
        message: payload.message || "已跳过重复入池",
        draft_id: payload.draft_id || null
      };
    }
    return {
      ok: true,
      skipped: false,
      draft: payload.draft || {},
      message: payload.message || ""
    };
  } catch (error) {
    if (error instanceof tenantContext.TenantContextError) {
      await clearConnectionState();
    }
    return {
      ok: false,
      status: 0,
      error: String(error?.message || error)
    };
  }
}

async function queryExistingCapturedProductDrafts(baseUrl, sessionToken, products) {
  const sourceRefs = [];
  const seen = new Set();
  for (const product of products || []) {
    const sourceRef = productCaptureSourceRef(product);
    if (!sourceRef || seen.has(sourceRef)) continue;
    seen.add(sourceRef);
    sourceRefs.push(sourceRef);
  }
  if (!sourceRefs.length) return new Map();
  try {
    const response = await fetch(workbenchHttpUrl(baseUrl, "/plugin/product-capture/drafts/status"), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ session_token: sessionToken, source_refs: sourceRefs })
    });
    if (!response.ok) return new Map();
    const payload = await response.json();
    if (payload.tenant_context !== undefined) {
      tenantContext.assertServerTenantContext(trustedConnectionForBase(baseUrl), payload.tenant_context);
    }
    const drafts = Array.isArray(payload.drafts) ? payload.drafts : [];
    const output = new Map();
    for (const draft of drafts) {
      const sourceRef = String(draft?.source_ref || "").trim();
      if (sourceRef) output.set(sourceRef, draft);
    }
    return output;
  } catch (error) {
    if (error instanceof tenantContext.TenantContextError) {
      await clearConnectionState();
    }
    return new Map();
  }
}

async function safeProductBatchPacingDelay(workerIndex = 0) {
  const baseDelay = PRODUCT_BATCH_DETAIL_SAFE_PACE_MIN_MS + Math.floor(Math.random() * PRODUCT_BATCH_DETAIL_SAFE_PACE_JITTER_MS);
  const workerOffset = Math.max(0, Number(workerIndex || 0)) * 450;
  await delay(baseDelay + workerOffset);
}

async function detectProductCaptureRiskControl(tabId) {
  try {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: (riskPatternSource, auxiliaryPatternSource) => {
        const riskRe = new RegExp(riskPatternSource, "i");
        const auxiliaryRe = new RegExp(auxiliaryPatternSource, "i");
        const text = (value) => (value == null ? "" : String(value)).replace(/\s+/g, " ").trim();
        const bodyText = text(document.body?.innerText || "");
        const title = text(document.title || "");
        const modalText = Array.from(document.querySelectorAll("[role='dialog'], .modal, .next-dialog, .rax-dialog, [class*='dialog'], [class*='modal']"))
          .map((node) => text(node.innerText || node.textContent || ""))
          .filter(Boolean)
          .join(" ");
        const combined = `${title} ${modalText} ${bodyText.slice(0, 2500)}`;
        const coreMatched = riskRe.test(combined);
        const auxiliaryMatched = auxiliaryRe.test(combined);
        const blocked = coreMatched;
        return {
          blocked,
          risk_reason: coreMatched ? "core_risk_signal" : (auxiliaryMatched ? "auxiliary_only" : ""),
          title: title.slice(0, 120),
          text_sample: combined.slice(0, 240),
          url: location.href
        };
      },
      args: [PRODUCT_BATCH_RISK_CONTROL_RE.source, PRODUCT_BATCH_RISK_AUXILIARY_RE.source]
    });
    return result?.result || { blocked: false };
  } catch (error) {
    return { blocked: false, error: String(error?.message || error) };
  }
}

async function captureBatchProductDetail(listProduct, options = {}) {
  const detailUrl = String(listProduct?.product_link || listProduct?.link || "").trim();
  if (!detailUrl) {
    return { ok: false, error: "missing_detail_url", statusText: "列表卡片缺少详情链接" };
  }
  const expectedProductId = String(listProduct?.product_id || productIdFromCaptureUrl(detailUrl) || "").trim();
  const workerIndex = options.workerIndex == null ? null : Number(options.workerIndex);
  const requestedWorkerCount = Number(options.workerCount || 1);
  const workerCount = Number.isFinite(requestedWorkerCount) && requestedWorkerCount > 0 ? requestedWorkerCount : 1;
  const activateWorkerTab = options.activate !== false;
  const shouldCancel = () => isProductBatchCaptureCancelled(options.cancelJob);
  let detailTab = null;
  let createdTab = false;
  let lastCaptured = null;
  let readyState = null;
  try {
    if (shouldCancel()) {
      return {
        ok: false,
        cancelled: true,
        error: "batch_capture_cancelled",
        statusText: "员工已中断批量采集",
        url: detailUrl,
        worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
        worker_count: workerCount
      };
    }
    if (options.workerTabId) {
      detailTab = await chrome.tabs.update(options.workerTabId, { url: detailUrl, active: activateWorkerTab });
    } else {
      detailTab = await chrome.tabs.create({ url: detailUrl, active: true });
      createdTab = true;
    }
    await waitForTabReady(detailTab.id, 30000);
    if (shouldCancel()) {
      return {
        ok: false,
        cancelled: true,
        error: "batch_capture_cancelled",
        statusText: "员工已中断批量采集",
        url: detailTab.url || detailUrl,
        worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
        worker_count: workerCount
      };
    }
    await delay(PRODUCT_BATCH_DETAIL_LOAD_DELAY_MS);
    detailTab = await chrome.tabs.get(detailTab.id);
    const earlyRisk = await detectProductCaptureRiskControl(detailTab.id);
    if (earlyRisk.blocked) {
      return {
        ok: false,
        error: "risk_control_blocked",
        statusText: "1688 访问被拒绝，已暂停批量采集",
        help: "请先处理当前页面提示，或等待一段时间后再继续；插件不会自动绕过验证。",
        url: detailTab.url || detailUrl,
        tab_id: detailTab.id,
        risk_control: true,
        risk_control_state: earlyRisk,
        worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
        worker_count: workerCount
      };
    }
    const manualChallengeDetected = earlyRisk.risk_reason === "auxiliary_only";
    if (manualChallengeDetected) {
      await focusProductBatchCaptureTab(detailTab.id);
    }
    readyState = await waitForProductDetailEvidence(
      detailTab.id,
      manualChallengeDetected ? PRODUCT_BATCH_MANUAL_CHALLENGE_TIMEOUT_MS : PRODUCT_BATCH_DETAIL_READY_TIMEOUT_MS,
      shouldCancel,
      expectedProductId
    );
    if (shouldCancel()) {
      return {
        ok: false,
        cancelled: true,
        error: "batch_capture_cancelled",
        statusText: "员工已中断批量采集",
        url: detailTab.url || detailUrl,
        worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
        worker_count: workerCount,
        detail_ready: readyState || {}
      };
    }
    const readyRisk = await detectProductCaptureRiskControl(detailTab.id);
    if (readyRisk.blocked) {
      return {
        ok: false,
        error: "risk_control_blocked",
        statusText: "1688 访问被拒绝，已暂停批量采集",
        help: "请先处理当前页面提示，或等待一段时间后再继续；插件不会自动绕过验证。",
        url: detailTab.url || detailUrl,
        tab_id: detailTab.id,
        risk_control: true,
        risk_control_state: readyRisk,
        worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
        worker_count: workerCount
      };
    }
    if (!readyState?.ok && readyRisk.risk_reason === "auxiliary_only") {
      await focusProductBatchCaptureTab(detailTab.id);
      readyState = await waitForProductDetailEvidence(detailTab.id, PRODUCT_BATCH_MANUAL_CHALLENGE_TIMEOUT_MS, shouldCancel, expectedProductId);
      if (shouldCancel()) {
        return {
          ok: false,
          cancelled: true,
          error: "batch_capture_cancelled",
          statusText: "员工已中断批量采集",
          url: detailTab.url || detailUrl,
          worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
          worker_count: workerCount,
          detail_ready: readyState || {}
        };
      }
      const postManualRisk = await detectProductCaptureRiskControl(detailTab.id);
      if (postManualRisk.blocked) {
        return {
          ok: false,
          error: "risk_control_blocked",
          statusText: "1688 访问被拒绝，已暂停批量采集",
          help: "请先处理当前页面提示，或等待一段时间后再继续；插件不会自动绕过验证。",
          url: detailTab.url || detailUrl,
          tab_id: detailTab.id,
          risk_control: true,
          risk_control_state: postManualRisk,
          worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
          worker_count: workerCount
        };
      }
    }
    for (let attempt = 1; attempt <= PRODUCT_BATCH_DETAIL_RETRY_COUNT; attempt += 1) {
      if (shouldCancel()) {
        return {
          ok: false,
          cancelled: true,
          error: "batch_capture_cancelled",
          statusText: "员工已中断批量采集",
          url: detailTab?.url || detailUrl,
          worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
          worker_count: workerCount,
          detail_ready: readyState || {}
        };
      }
      await prepareProductDetailTabForCapture(detailTab.id);
      detailTab = await chrome.tabs.get(detailTab.id);
      const captured = await captureProductFromTab(detailTab, { commandType: "product_batch_detail_capture", expectedProductId });
      lastCaptured = captured;
      if (captured?.risk_control) {
        return {
          ok: false,
          error: "risk_control_blocked",
          statusText: captured.statusText || "1688 访问被拒绝，已暂停批量采集",
          help: captured.help || "请先处理当前页面提示，或等待一段时间后再继续；插件不会自动绕过验证。",
          url: captured.url || detailTab.url || detailUrl,
          tab_id: detailTab.id,
          risk_control: true,
          risk_control_state: captured.risk_control_state || {},
          worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
          worker_count: workerCount
        };
      }
      if (!captured?.error && captured?.product) {
        const capturedProductId = String(
          captured.product.product_id ||
          productIdFromCaptureUrl(captured.product.product_link || captured.product.link || captured.url || detailTab.url || "")
        ).trim();
        if (!productCaptureIdsMatch(expectedProductId, capturedProductId)) {
          return {
            ok: false,
            error: "detail_product_id_mismatch",
            statusText: "详情页商品 ID 与列表商品不一致，已阻止错商品入池",
            help: "请刷新列表页后重新批量采集；如果页面跳转到其它商品，插件不会合并该详情数据。",
            url: captured.url || detailTab.url || detailUrl,
            expected_product_id: expectedProductId,
            captured_product_id: capturedProductId,
            worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
            worker_count: workerCount,
            detail_ready: readyState || {}
          };
        }
        return {
          ok: true,
          product: captured.product,
          url: captured.url || detailTab.url || detailUrl,
          attempts: attempt,
          worker_tab_mode: options.workerTabId
            ? (options.dedicatedWindow
              ? (activateWorkerTab ? "dedicated_window_primary_tab" : "dedicated_window_background_tab")
              : (activateWorkerTab ? "pooled_primary_active_tab" : "pooled_background_tab"))
            : "created_active_tab",
          worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
          worker_count: workerCount,
          detail_ready: readyState || {}
        };
      }
      if (attempt < PRODUCT_BATCH_DETAIL_RETRY_COUNT) {
        await delay(PRODUCT_BATCH_DETAIL_RETRY_DELAY_MS);
      }
    }
    return {
      ok: false,
      error: lastCaptured?.error || "detail_capture_empty",
      statusText: lastCaptured?.statusText || "详情页未返回商品信息",
      help: lastCaptured?.help || "",
      url: lastCaptured?.url || detailTab?.url || detailUrl,
      worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
      worker_count: workerCount,
      detail_ready: readyState || {}
    };
  } catch (error) {
    return {
      ok: false,
      error: String(error?.message || error),
      statusText: "详情页深采集异常",
      url: detailUrl,
      worker_index: Number.isFinite(workerIndex) ? workerIndex : null,
      worker_count: workerCount
    };
  } finally {
    if (createdTab && detailTab?.id) {
      try {
        await chrome.tabs.remove(detailTab.id);
      } catch (_error) {
        // The tab may already be gone if the browser discarded it.
      }
    }
  }
}

async function waitForProductDetailEvidence(tabId, timeoutMs, shouldCancel = () => false, expectedProductId = "") {
  const deadline = Date.now() + timeoutMs;
  let lastState = null;
  while (Date.now() < deadline) {
    if (shouldCancel()) return { ok: false, cancelled: true, error: "batch_capture_cancelled" };
    try {
      await prepareProductDetailTabForCapture(tabId);
      const [result] = await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: (expectedId) => {
          const text = (value) => (value == null ? "" : String(value)).replace(/\s+/g, " ").trim();
          const productIdFromHref = (value) => {
            const raw = String(value || "");
            const patterns = [
              /\/offer\/(\d+)\.html/i,
              /-g-(\d+)(?:\.html|[/?#]|$)/i,
              /[?&](?:offerId|offerid|offer_id|productId|productid|product_id|goods_id|goodsId|item_id|itemId|spu_id|spuId)=(\d+)/i,
              /\/(\d{8,})(?:\.html|[/?#]|$)/i
            ];
            for (const pattern of patterns) {
              const match = raw.match(pattern);
              if (match) return match[1];
            }
            return "";
          };
          const visible = (element) => {
            if (!element) return false;
            const rect = element.getBoundingClientRect();
            const style = getComputedStyle(element);
            return rect.width > 10 && rect.height > 10 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0.05;
          };
          const looksLikeVideoMedia = (element) => {
            let node = element;
            for (let depth = 0; node && depth < 4; depth += 1) {
              const snippet = text([
                node.className || "",
                node.id || "",
                node.getAttribute?.("aria-label") || "",
                node.getAttribute?.("title") || "",
                node.innerText || node.textContent || ""
              ].join(" "));
              if (/video|play|player|poster|movie|media[-_]?video|瑙嗛|鎾斁|涓诲浘瑙嗛|瑙嗛灏侀潰/i.test(snippet)) return true;
              try {
                if (node.querySelector?.("video,[class*='video'],[class*='play'],[aria-label*='播放'],[title*='播放']")) return true;
              } catch (_error) {}
              node = node.parentElement;
            }
            return false;
          };
          const visibleTitleBlockText = () => {
            const stopRe = /商品复购率|已售|热销|新人价|首单|起批|库存|颜色|规格|款式|型号|立即下单|加入采购车|跨境铺货|收藏|客服|店铺|评价|保障|运费|发货|物流/i;
            const badRe = /搜索|找本店|首页|购物车|我的订单|客服|官方服务|下载插件|采购车|消息|有限公司|有限责任公司|经营部|商行|旺铺|诚信通|供应商/i;
            const selectors = "h1,h2,[role='heading'],[data-title],[title],div,span,p,a";
            const elements = Array.from(document.querySelectorAll(selectors)).slice(0, 1200);
            for (const element of elements) {
              if (!visible(element)) continue;
              const rect = element.getBoundingClientRect();
              if (rect.top < -40 || rect.top > Math.max(window.innerHeight || 800, 800)) continue;
              if (rect.width < 120 || rect.height < 12 || rect.height > 260) continue;
              const raw = String(element.innerText || element.textContent || element.getAttribute?.("title") || element.getAttribute?.("aria-label") || "").replace(/\r/g, "\n");
              const lines = raw.split(/\n+/).map(text).filter(Boolean);
              if (lines.length > 8) continue;
              for (const line of lines) {
                const candidate = text(line.split(stopRe)[0] || line);
                if (candidate.length < 8 || candidate.length > 160) continue;
                if (badRe.test(candidate) && candidate.length <= 90) continue;
                if (/^[\u4e00-\u9fa5]{2,8}$/.test(candidate)) continue;
                return candidate;
              }
            }
            return "";
          };
          const title = text(
            document.querySelector("h1")?.innerText ||
            document.querySelector("[class*='title']")?.innerText ||
            visibleTitleBlockText() ||
            document.querySelector("meta[property='og:title']")?.getAttribute("content") ||
            document.title
          );
          const mainImages = Array.from(document.images || []).filter((img) => {
            const src = text(img.currentSrc || img.src || img.getAttribute("data-src") || img.getAttribute("data-lazy-src"));
            if (!src || /logo|avatar|icon|sprite|blank|placeholder/i.test(src)) return false;
            const rect = img.getBoundingClientRect();
            return visible(img) && rect.width >= 120 && rect.height >= 120 && !looksLikeVideoMedia(img);
          });
          const bodyText = text(document.body?.innerText || "");
          const pageProductId = productIdFromHref(location.href);
          const expectedProductId = text(expectedId);
          const productIdMatch = !expectedProductId || !pageProductId || pageProductId === expectedProductId;
          return {
            ok: Boolean(title && mainImages.length && productIdMatch),
            title: title.slice(0, 120),
            image_count: mainImages.length,
            has_price_signal: /[¥￥]\s*\d|起批|拿样价|批发价|新人价/.test(bodyText),
            has_sku_signal: /颜色|规格|尺寸|尺码|sku|SKU|款式|型号/.test(bodyText),
            ready_state: document.readyState,
            url: location.href,
            expected_product_id: expectedProductId,
            product_id: pageProductId,
            product_id_match: productIdMatch
          };
        },
        args: [expectedProductId]
      });
      lastState = result?.result || null;
      if (lastState?.ok) return lastState;
    } catch (error) {
      lastState = { ok: false, error: String(error?.message || error) };
    }
    if (shouldCancel()) return { ok: false, cancelled: true, error: "batch_capture_cancelled" };
    await delay(900);
  }
  return lastState || { ok: false, error: "detail_ready_timeout" };
}

async function prepareProductDetailTabForCapture(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: () => {
        const text = (value) => (value == null ? "" : String(value)).trim();
        const firstMainImage = Array.from(document.images || []).find((img) => {
          const rect = img.getBoundingClientRect();
          const src = text(img.currentSrc || img.src || img.getAttribute("data-src") || img.getAttribute("data-lazy-src"));
          return src && rect.width >= 120 && rect.height >= 120;
        });
        if (firstMainImage) firstMainImage.scrollIntoView({ block: "center", inline: "center" });
        window.dispatchEvent(new Event("scroll"));
        document.dispatchEvent(new Event("scroll"));
        window.setTimeout(() => window.scrollTo({ top: 0, left: 0, behavior: "auto" }), 120);
      }
    });
  } catch (_error) {
    // Some merchant pages block injected scrolling; the normal single-page extractor can still run.
  }
}

function mergeListAndDetailProduct(listProduct, detailProduct, detailMeta = {}) {
  const pickFirst = (...values) => values.find((value) => String(value || "").trim()) || "";
  const pickArray = (preferred, fallback) => (
    Array.isArray(preferred) && preferred.length ? preferred : (Array.isArray(fallback) ? fallback : [])
  );
  const merged = { ...listProduct, ...detailProduct };
  const detailFields = detailProduct.captured_fields || {};
  const listFields = listProduct.captured_fields || {};
  const detailRaw = detailProduct.raw_payload || {};
  const listRaw = listProduct.raw_payload || {};
  const detailQuality = detailProduct.quality || {};
  const listQuality = listProduct.quality || {};
  const mergedProductId = pickFirst(detailProduct.product_id, listProduct.product_id, productIdFromCaptureUrl(detailProduct.product_link || detailProduct.link), productIdFromCaptureUrl(listProduct.product_link || listProduct.link));
  const canonicalLink = canonicalProductCaptureUrl(
    pickFirst(detailProduct.product_link, detailProduct.link, listProduct.product_link, listProduct.link, detailMeta.url),
    mergedProductId
  );
  const detailWorkerIndex = detailMeta.worker_index == null ? null : Number(detailMeta.worker_index);
  const detailWorkerCount = Number(detailMeta.worker_count || 1);

  const mergedTitle = cleanCapturedProductTitleForDraft(pickFirst(detailProduct.title, detailProduct.product_name, listProduct.title, listProduct.product_name));
  merged.title = mergedTitle;
  merged.product_name = mergedTitle;
  merged.image_url = pickFirst(detailProduct.image_url, detailProduct.main_image_url, listProduct.image_url, listProduct.main_image_url);
  merged.imageUrl = pickFirst(detailProduct.imageUrl, detailProduct.image_url, listProduct.imageUrl, listProduct.image_url);
  merged.main_image_url = pickFirst(detailProduct.main_image_url, detailProduct.image_url, listProduct.main_image_url, listProduct.image_url);
  merged.price = pickFirst(detailProduct.price, listProduct.price);
  merged.currency = pickFirst(detailProduct.currency, listProduct.currency, "CNY");
  merged.product_link = canonicalLink || pickFirst(detailProduct.product_link, detailProduct.link, listProduct.product_link, listProduct.link, detailMeta.url);
  merged.link = merged.product_link;
  merged.source_ref = merged.product_link || pickFirst(detailProduct.source_ref, listProduct.source_ref, mergedProductId);
  merged.product_id = mergedProductId;
  merged.sku = pickFirst(detailProduct.sku, listProduct.sku, mergedProductId);
  merged.category = pickFirst(detailProduct.category, listProduct.category, "其他");
  merged.platform = pickFirst(detailProduct.platform, listProduct.platform, "1688");
  merged.source = pickFirst(detailProduct.source, listProduct.source, "browser_extension");
  merged.variant_groups = pickArray(detailProduct.variant_groups, listProduct.variant_groups);
  merged.variant_combinations = pickArray(detailProduct.variant_combinations, listProduct.variant_combinations);
  merged.raw_variant_groups = pickArray(detailProduct.raw_variant_groups, listProduct.raw_variant_groups);
  merged.raw_variant_combinations = pickArray(detailProduct.raw_variant_combinations, listProduct.raw_variant_combinations);
  merged.captured_fields = {
    ...listFields,
    ...detailFields,
    capture_method: "list_page_detail_single_batch",
    list_capture_method: listFields.capture_method || "list_page_detail_link_batch",
    detail_capture_status: "succeeded",
    detail_capture_url: detailMeta.url || detailProduct.product_link || detailProduct.link || "",
    detail_capture_attempts: detailMeta.attempts || 1,
    detail_worker_tab_mode: detailMeta.worker_tab_mode || "",
    detail_worker_index: Number.isFinite(detailWorkerIndex) ? detailWorkerIndex : null,
    detail_worker_count: Number.isFinite(detailWorkerCount) && detailWorkerCount > 0 ? detailWorkerCount : 1,
    list_product_link: listProduct.product_link || listProduct.link || "",
    list_card_price: listProduct.price || "",
    list_card_image_url: listProduct.image_url || listProduct.main_image_url || ""
  };
  merged.raw_payload = {
    ...listRaw,
    ...detailRaw,
    list_page_url: listRaw.list_page_url || "",
    list_card_text: listRaw.list_card_text || "",
    list_card_rect: listRaw.list_card_rect || null,
    detail_page_url: detailMeta.url || detailProduct.product_link || detailProduct.link || "",
    detail_worker_tab_mode: detailMeta.worker_tab_mode || "",
    detail_worker_index: Number.isFinite(detailWorkerIndex) ? detailWorkerIndex : null,
    detail_worker_count: Number.isFinite(detailWorkerCount) && detailWorkerCount > 0 ? detailWorkerCount : 1
  };
  merged.quality = {
    ...listQuality,
    ...detailQuality,
    detail_capture_complete: true,
    detail_worker_count: Number.isFinite(detailWorkerCount) && detailWorkerCount > 0 ? detailWorkerCount : 1,
    batch_capture_complete: true
  };
  return merged;
}

async function captureProductListFromTab(tab) {
  const rawUrl = tab.url || "";
  let hostname = "";
  try {
    hostname = new URL(rawUrl).hostname;
  } catch (_error) {
    hostname = "";
  }
  if (!hostname || !/(^|\.)((temu)|(1688)|(alibaba))\.com$/i.test(hostname)) {
    return {
      supported: false,
      error: "unsupported_product_list_page",
      statusText: "当前页不是支持批量采集的 TEMU/1688/Alibaba 商品列表页",
      url: rawUrl,
      capturedAt: new Date().toISOString()
    };
  }
  if (isExcludedProductCaptureUrl(rawUrl)) {
    return {
      supported: false,
      error: "excluded_customer_service_page",
      statusText: "当前页是 1688 客服/旺旺对话页，不执行商品批量采集",
      help: "请回到 1688 搜索结果页或商品详情页再采集；客服聊天页只保留连接角标，不显示商品采集按钮。",
      url: rawUrl,
      capturedAt: new Date().toISOString()
    };
  }
  const [result] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: extractProductListFromCurrentPage,
    args: [{
      limit: PRODUCT_BATCH_LIST_SCAN_LIMIT,
      maxScrollPasses: PRODUCT_BATCH_LIST_SCROLL_MAX_PASSES,
      scrollWaitMs: PRODUCT_BATCH_LIST_SCROLL_WAIT_MS
    }]
  });
  const payload = result?.result || {};
  if (payload.error) {
    return {
      supported: true,
      ...payload,
      url: rawUrl,
      title: tab.title || "",
      capturedAt: new Date().toISOString()
    };
  }
  return {
    supported: true,
    command_type: "product_batch_capture_to_workbench",
    products: Array.isArray(payload.products) ? payload.products : [],
    skipped: Array.isArray(payload.skipped) ? payload.skipped : [],
    skipped_count: Number(payload.skipped_count || 0),
    total_candidates: Number(payload.total_candidates || 0),
    scan_limit: Number(payload.scan_limit || 0),
    scroll_scan_passes: Number(payload.scroll_scan_passes || 0),
    url: rawUrl,
    title: tab.title || "",
    capturedAt: new Date().toISOString()
  };
}

// 自包含的「页面级 SKU 选项提取」：会通过 chrome.scripting.executeScript 注入到
// 任意 frame（含跨域 sku-panel iframe）执行，因此不能引用 background 作用域变量，
// 所有辅助函数必须内联。输出可序列化的规格组结构（与 pageWideSkuGroups 一致）。
function extractPageSkuGroupsInFrame() {
  const text = (value) => (value == null ? "" : String(value)).replace(/\s+/g, " ").trim();
  const visible = (element) => {
    try {
      if (!element) return false;
      const style = window.getComputedStyle(element);
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0) return false;
      const rect = element.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    } catch (_error) {
      return false;
    }
  };
  const cleanSpecValue = (raw) => {
    let value = text(raw)
      .replace(/^(?:颜色|规格|款式|型号|容量|尺寸|尺码|数量|包装|套装|Color|Size|Style|Capacity|Pack)[:：\s]*/i, "")
      .replace(/\s*(?:已选|请选择|选择|库存|起批|¥|￥|\$).*/i, "")
      .replace(/[\uE000-\uF8FF]+/g, "");
    if (!value || value.length > 40) return "";
    return value;
  };
  const badSpecValueRe = /官方|退货|客服|收藏|好评|店铺|评价|保障|包邮|发货|物流|起批|库存|已售|加购|选择|请选择|加入|立即|下单|采购车|跨境铺货|分销代发|全部参数|商品参数|产品参数|规格参数|详细参数|基本参数/i;
  const optionSelector = [
    "button[class*='sku']",
    "li[class*='sku']",
    "[class*='sku-filter']",
    "[class*='sku-select']",
    "[class*='sku-option']",
    "[class*='sku-item']",
    "[class*='sale-prop']",
    "[class*='feature-item'] button",
    "[class*='feature-item'] [class*='item']",
    "[class*='property-item']",
    "[class*='attribute-item']",
    "[class*='spec-item']",
    "[role='button'][class*='sku']",
    "[role='option']",
    "[role='radio']"
  ].join(",");
  const isLeaf = (element) => {
    try {
      const nested = Array.from(element.querySelectorAll("button, li, [role='button'], [role='option'], [role='radio'], [class*='option'], [class*='item']"))
        .filter((child) => child !== element && visible(child));
      return nested.length === 0;
    } catch (_error) {
      return true;
    }
  };
  const imageUrl = (element) => {
    try {
      const img = element.querySelector("img");
      if (img) return img.currentSrc || img.src || "";
      const style = window.getComputedStyle(element);
      const match = style.backgroundImage && style.backgroundImage.match(/url\(["']?(.*?)["']?\)/);
      return match ? match[1] : "";
    } catch (_error) {
      return "";
    }
  };
  const normalizeGroupName = (name, valueText) => {
    const sample = `${name} ${valueText || ""}`;
    if (/\u989c\u8272/.test(sample)) return "Color";
    if (/\u5c3a\u5bf8|\u5c3a\u7801|\u5927\u5c0f|\u5305\u88c5\u5c3a\u5bf8/.test(sample)) return "Size";
    if (/\u5bb9\u91cf|\u5347|ml\b|\bl\b/.test(sample)) return "Capacity";
    if (/\u89c4\u683c|\u6b3e\u5f0f|\u578b\u53f7|\u6570\u91cf|\u5305\u88c5|\u5957\u88c5/.test(sample)) return "Pack";
    if (/color|colour/.test(sample)) return "Color";
    if (/size/.test(sample)) return "Size";
    if (/style|model/.test(sample)) return "Style";
    return "Style";
  };
  const items = [];
  const seenValues = new Set();
  for (const element of Array.from(document.querySelectorAll(optionSelector))) {
    if (!visible(element) || !isLeaf(element)) continue;
    const value = cleanSpecValue(element.innerText || element.textContent || element.getAttribute("title") || element.getAttribute("aria-label"));
    if (!value || value.length < 1 || value.length > 40 || badSpecValueRe.test(value)) continue;
    if (seenValues.has(value)) continue;
    seenValues.add(value);
    const tagName = String(element.tagName || "").toUpperCase();
    const classText = `${element.className || ""} ${element.getAttribute("aria-selected") || ""} ${element.getAttribute("aria-checked") || ""}`;
    const selected = /selected|active|current|checked|true/i.test(classText);
    const price = element.getAttribute("data-price") || element.getAttribute("data-sale-price") || element.getAttribute("data-sku-price") || "";
    const stock = element.getAttribute("data-stock") || element.getAttribute("data-inventory") || "";
    const sku = element.getAttribute("data-sku-id") || element.getAttribute("data-skuid") || element.getAttribute("data-sku") || "";
    let label = "";
    let strongSkuHint = false;
    let ancestor = element.parentElement;
    for (let depth = 0; ancestor && depth < 5; depth += 1, ancestor = ancestor.parentElement) {
      const ancestorClass = `${ancestor.className || ""}`;
      strongSkuHint = strongSkuHint || /sku-filter|sku-select|sale-prop|sku-option|selector|variant|feature-item|prop|spec|attribute/i.test(ancestorClass);
      const ancestorText = text(ancestor.innerText || ancestor.textContent || "").slice(0, 120);
      const labelMatch = ancestorText.match(/^(颜色|规格|款式|型号|容量|尺寸|尺码|数量|包装|套装|Color|Size|Style|Capacity|Pack)\s*[:：]?\s*(.*)$/i);
      if (labelMatch) {
        label = labelMatch[1];
        break;
      }
      const directChildLabel = Array.from(ancestor.children)
        .map((child) => text(child.innerText || child.textContent || ""))
        .find((childText) => /^(颜色|规格|款式|型号|容量|尺寸|尺码|数量|包装|套装|Color|Size|Style|Capacity|Pack)[:：]?$/i.test(childText));
      if (directChildLabel) {
        label = directChildLabel.replace(/[:：]/g, "");
        break;
      }
    }
    // 只接受“明确是 SKU 选项”的元素：按钮标签、或能找到规格标签、或祖先带
    // 强 SKU 痕迹类名；避免把推荐位/商品卡片等类名带 sku 的元素误当规格值。
    if (!label && !strongSkuHint && tagName !== "BUTTON" && tagName !== "LI") continue;
    items.push({
      source_name: label,
      value,
      image_url: imageUrl(element),
      price,
      stock,
      sku,
      source_sku_id: sku,
      selected,
      selectable: true
    });
  }
  const grouped = new Map();
  for (const item of items) {
    const name = normalizeGroupName(item.source_name || "规格", item.value);
    const list = grouped.get(name) || [];
    list.push(item);
    grouped.set(name, list);
  }
  const output = [];
  for (const [name, values] of grouped) {
    if (values.length < 2) continue;
    output.push({ name, source_name: name, values: values.slice(0, 50) });
  }
  return output;
}

// 在所有 frame（含跨域 sku-panel iframe）执行 extractPageSkuGroupsInFrame 并合并去重。
async function extractPageSkuGroupsFromAllFrames(tabId) {
  let frameResults = [];
  try {
    frameResults = await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      world: "MAIN",
      func: extractPageSkuGroupsInFrame
    });
  } catch (_error) {
    try {
      frameResults = await chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: extractPageSkuGroupsInFrame
      });
    } catch (_error2) {
      return null;
    }
  }
  const merged = [];
  const seenGroupKeys = new Set();
  for (const frame of frameResults || []) {
    const groups = Array.isArray(frame?.result) ? frame.result : [];
    for (const group of groups) {
      if (!group || typeof group !== "object" || !Array.isArray(group.values) || !group.values.length) continue;
      const key = `${group.name || ""}|${group.values.map((v) => String(v.value || "")).join(",")}`;
      if (seenGroupKeys.has(key)) continue;
      seenGroupKeys.add(key);
      merged.push(group);
    }
  }
  return merged;
}

// 由多轴规格组生成组合（笛卡尔积），供跨 frame 兜底结果使用。
function buildCombosFromGroupList(groups) {
  const axes = (groups || [])
    .filter((g) => g && typeof g === "object" && Array.isArray(g.values) && g.values.length > 0)
    .map((g) => ({ name: g.name || g.source_name || "规格", source_name: g.source_name || g.name || "规格", values: g.values }));
  if (!axes.length) return [];
  const combos = [];
  const cartesian = (axisIndex, attrs, evidence) => {
    if (axisIndex >= axes.length) {
      combos.push({
        attributes: { ...attrs },
        source: "frame-dom",
        confidence: "medium",
        image_url: evidence.image_url || "",
        price: evidence.price || "",
        stock: evidence.stock || "",
        sku: evidence.sku || "",
        source_sku_id: evidence.sku || "",
        selectable: true,
        selected: false
      });
      return;
    }
    const axis = axes[axisIndex];
    for (const value of axis.values) {
      cartesian(axisIndex + 1, { ...attrs, [axis.name]: value.value }, {
        image_url: value.image_url || evidence.image_url || "",
        price: value.price || evidence.price || "",
        stock: value.stock || evidence.stock || "",
        sku: value.sku || evidence.sku || ""
      });
    }
  };
  cartesian(0, {}, {});
  return combos;
}

async function captureProductFromTab(tab, { commandType, expectedProductId = "" } = {}) {
  const rawUrl = tab.url || "";
  let hostname = "";
  try {
    hostname = new URL(rawUrl).hostname;
  } catch (_error) {
    hostname = "";
  }
  if (!hostname || !PRODUCT_CAPTURE_HOST_RE.test(hostname)) {
    return {
      command_type: commandType,
      supported: false,
      error: "unsupported_product_page",
      statusText: "当前页不是支持的商品采集页",
      help: "请打开 1688、拼多多、TEMU 的商品详情页后再采集。",
      url: rawUrl,
      capturedAt: new Date().toISOString()
    };
  }
  if (isExcludedProductCaptureUrl(rawUrl)) {
    return {
      command_type: commandType,
      supported: false,
      error: "excluded_customer_service_page",
      statusText: "当前页是客服/旺旺对话页，不执行商品采集",
      help: "请打开真实商品详情页后再点击采集。客服聊天页可能带 offerId 参数，但不是商品详情页。",
      url: rawUrl,
      capturedAt: new Date().toISOString()
    };
  }

  try {
    await injectNetworkProbe(tab.id);
  } catch (_error) {
    // Product DOM extraction can still work if the page blocks probe reinjection.
  }

  if (/temu\.com/.test(hostname)) {
    // Temu 商品数据在 DOMContentLoaded 之后异步渲染；给足渲染时间，
    // 避免规格选项尚未出现时只采到默认单规格。
    await delay(2000);
    // Temu 的规格/颜色选择区同样懒渲染（视口外不创建 DOM），先滚到 SKU 区
    // 或页面中部触发渲染，等它稳定后再提取，避免规格区未渲染导致单规格。
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: () => {
          try {
            const skuArea = Array.from(document.querySelectorAll(
              "[class*='sku'], [data-testid*='sku'], [class*='spec'], [class*='option'], [class*='prop'], [class*='attribute']"
            )).filter((el) => {
              const rect = el.getBoundingClientRect();
              return rect.width > 20 && rect.height > 20;
            }).find((el) => /(颜色|规格|尺寸|尺码|型号|已选|Color|Size|Style|Spec)/i.test(String(el.innerText || el.textContent || "")));
            if (skuArea) {
              skuArea.scrollIntoView({ block: "center" });
            } else {
              window.scrollTo(0, Math.round(document.body.scrollHeight * 0.35));
            }
          } catch (_error) {}
        }
      });
    } catch (_error) {}
    await delay(1500);
  }

  if (/1688|alibaba/i.test(hostname)) {
    // 1688 新版详情页（od-* SPA）的 SKU 区（module-od-sku-selection）按需渲染/懒加载，
    // 且页面顶部常有类名带 sku 的其它元素（面包屑/描述表等）。旧逻辑滚动到“第一个
    // 有文字的 sku 元素”常命中错误目标，导致 SKU 区始终未渲染、规格为空。
    // 这里优先定向滚动到 SKU 区自身（即使还是空占位），两轮滚动 + 等待。
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: () => {
          try {
            const pick = (selector, requireText) => {
              const nodes = Array.from(document.querySelectorAll(selector));
              return (requireText
                ? nodes.find((el) => String(el.innerText || el.textContent || "").trim().length > 0)
                : nodes[0]) || null;
            };
            const target = pick(
              "[class*='sku-selection'], [class*='sku-select'], [class*='module-od-sku'], .feature-item, [class*='feature-item']",
              true
            ) || pick(
              "[class*='sku-selection'], [class*='sku-select'], [class*='module-od-sku'], .feature-item, [class*='feature-item']",
              false
            ) || pick(
              "[class*='sku'], [class*='offer-main'], [class*='price'], [class*='trade'], [class*='buy']",
              true
            );
            if (target) {
              target.scrollIntoView({ block: "center" });
              const offset = target.getBoundingClientRect();
              window.scrollBy(0, Math.max(0, offset.top - window.innerHeight / 2));
            } else {
              window.scrollTo(0, Math.round(document.body.scrollHeight * 0.5));
            }
          } catch (_error) {}
        }
      });
    } catch (_error) {}
    await delay(1600);
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: () => {
          try {
            // SKU 区渲染后再把已出现的规格选项滚到视口中部，保证规格区稳定渲染。
            const nodes = Array.from(document.querySelectorAll(
              ".sku-filter-button, [class*='sku-filter-button'], .feature-item, [class*='feature-item'], [class*='sku-selection'] [class*='option'], [class*='sku-select'] [class*='option']"
            ));
            const target = nodes.filter((el) => {
              const r = el.getBoundingClientRect();
              return r.width > 10 && r.height > 10;
            }).pop();
            if (target) {
              target.scrollIntoView({ block: "center" });
              window.scrollBy(0, -window.innerHeight * 0.15);
            }
          } catch (_error) {}
        }
      });
    } catch (_error) {}
    await delay(1800);
  }

  const riskState = await detectProductCaptureRiskControl(tab.id);
  if (riskState.blocked) {
    return {
      command_type: commandType,
      supported: true,
      error: "risk_control_blocked",
      statusText: "1688 访问被拒绝，已暂停采集",
      help: "请先处理当前页面提示，或等待一段时间后再继续；插件不会自动绕过验证。",
      risk_control: true,
      risk_control_state: riskState,
      url: rawUrl,
      title: tab.title || "",
      capturedAt: new Date().toISOString()
    };
  }

  const [firstResult] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: "MAIN",
    func: extractProductFromCurrentPage,
    args: [expectedProductId]
  });
  let result = firstResult;
  // 1688 新 SPA / Temu 的 SKU 区懒加载偶发未触发（模块加载失败/滚动目标不对），
  // 首次提取无规格时再激进滚动重试一次，避免漏采。
  if (/1688|alibaba|temu/i.test(hostname)) {
    const productHasVariants = (p) => {
      const f = p?.captured_fields;
      return Boolean(
        (f && typeof f === "object" && (Number(f.variant_combinations_count) > 0 || Number(f.variant_groups_count) > 0))
        || (Array.isArray(p?.variant_combinations) && p.variant_combinations.length > 0)
        || (Array.isArray(p?.variant_groups) && p.variant_groups.length > 0)
      );
    };
    if (!productHasVariants(result?.result || {})) {
      try {
        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          world: "MAIN",
          func: () => {
            try {
              // 整页分段滚动，把 SKU 区所在区域拉进视口触发渲染。
              const height = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
              window.scrollTo(0, Math.round(height * 0.35));
            } catch (_error) {}
          }
        });
      } catch (_error) {}
      await delay(2000);
      try {
        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          world: "MAIN",
          func: () => {
            try {
              const skuArea = document.querySelector("[class*='sku-selection'], [class*='module-od-sku'], [class*='sku'], [class*='spec'], [data-testid*='sku'], [class*='price'], [class*='buy'], [class*='option']");
              if (skuArea) skuArea.scrollIntoView({ block: "center" });
            } catch (_error) {}
          }
        });
      } catch (_error) {}
      await delay(1800);
      try {
        const [retryResult] = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          world: "MAIN",
          func: extractProductFromCurrentPage,
          args: [expectedProductId]
        });
        if (productHasVariants(retryResult?.result || {})) {
          result = retryResult;
        }
      } catch (_error) {}
      // 第三路兜底：从所有 frame（含跨域 sku-panel iframe）的探针捕获中捞含
      // SKU 模型的 JSON 响应，注入主页面 __workbenchSkuJsonCache（jsonSourcesFromPage
      // 会把它作为 cached-sku 源交给 walkObjects 识别 skuProps/skuInfoMap 等），
      // 再重跑提取——绕过 1688 SPA 的懒加载渲染，SKU 数据在网络层始终可得。
      // 注意：1688 SKU 接口 URL 不一定带 sku/sale/spec 字样，因此不按 URL 过滤，
      // 而是先收集全部 JSON 响应，再用 SKU 模型关键词 + 结构信号打分挑选候选。
      if (!productHasVariants(result?.result || {})) {
        let frameTexts = [];
        try {
          const frameResults = await chrome.scripting.executeScript({
            target: { tabId: tab.id, allFrames: true },
            world: "MAIN",
            func: () => {
              try {
                const probe = window.__temuWorkbenchNetworkProbe;
                if (!probe) return [];
                return (probe.captures || [])
                  .filter((c) => c && !c.error && c.responseText)
                  .map((c) => String(c.responseText || ""))
                  .filter((t) => t.length > 200 && /^\s*[\[{]/.test(t));
              } catch (_error) { return []; }
            }
          });
          for (const frame of frameResults || []) {
            if (Array.isArray(frame?.result)) frameTexts.push(...frame.result);
          }
        } catch (_error) {}
        const skuModelRe = /skuProps|sku_props|saleProps|salePropList|skuInfoMap|skuMap|skuInfos|skuList|skuItems|goodsSkus|skuInfoList|goodsSkuList|skuAttrs|skuAttrList|skuRecords|saleSkuList|saleProp|sale_prop|saleSpecs|saleSpec|specs|propKey|specKey|skuPrice|sku_price/;
        const skuSignalScore = (t) => {
          let score = 0;
          const signalKeys = ["skuProps", "sku_props", "saleProps", "salePropList", "skuInfoMap", "skuMap", "skuInfos", "skuList", "skuItems", "goodsSkus", "skuInfoList", "goodsSkuList", "skuAttrs", "skuAttrList", "skuRecords", "saleSkuList", "saleProp", "sale_prop", "saleSpecs", "saleSpec", "propKey", "specKey", "skuId", "sku_id", "skuPrice", "sku_price"];
          for (const key of signalKeys) {
            if (t.includes(key)) score += 1;
          }
          return score;
        };
        const candidates = frameTexts
          .filter((t) => skuModelRe.test(t))
          .sort((a, b) => (skuSignalScore(b) - skuSignalScore(a)) || (b.length - a.length));
        for (const candidate of candidates.slice(0, 5)) {
          try {
            JSON.parse(candidate); // 校验 JSON 合法性，避免写入脏数据
            await chrome.scripting.executeScript({
              target: { tabId: tab.id },
              world: "MAIN",
              func: (json) => {
                try { window.__workbenchSkuJsonCache = json; } catch (_error) {}
              },
              args: [candidate]
            });
            const [netResult] = await chrome.scripting.executeScript({
              target: { tabId: tab.id },
              world: "MAIN",
              func: extractProductFromCurrentPage,
              args: [expectedProductId]
            });
            if (productHasVariants(netResult?.result || {})) {
              result = netResult;
              break;
            }
          } catch (_error) {}
        }
      }
      // 第四路兜底：跨 frame DOM 提取。1688 新版 SPA 的 SKU 面板可能渲染在
      // 跨域 iframe（od-panel/sku-panel.html）里，主 frame 的 DOM 提取取不到。
      // 这里在全部 frame 执行自包含的页面级提取并合并进最终结果。
      if (!productHasVariants(result?.result || {})) {
        const frameGroups = await extractPageSkuGroupsFromAllFrames(tab.id);
        if (frameGroups && frameGroups.length) {
          if (!result || !result.result || typeof result.result !== "object") result = { result: {} };
          const product = result.result;
          const combos = buildCombosFromGroupList(frameGroups);
          product.variant_groups = frameGroups;
          product.raw_variant_groups = frameGroups;
          product.variant_combinations = combos;
          product.raw_variant_combinations = combos;
          if (product.captured_fields && typeof product.captured_fields === "object") {
            product.captured_fields.variant_groups = frameGroups;
            product.captured_fields.variant_combinations_count = combos.length;
            product.captured_fields.raw_variant_groups_count = frameGroups.length;
            product.captured_fields.raw_variant_combinations_count = combos.length;
          }
        }
      }
    }
  }
  const product = result?.result || {};
  const capturedProductId = String(product.product_id || productIdFromCaptureUrl(product.product_link || product.link || rawUrl) || "").trim();
  const expectedId = String(expectedProductId || "").trim();
  const productIdMatch = productCaptureIdsMatch(expectedId, capturedProductId);
  if (product.captured_fields && typeof product.captured_fields === "object") {
    product.captured_fields.expected_product_id = expectedId;
    product.captured_fields.capture_product_id = capturedProductId;
    product.captured_fields.capture_product_id_match = productIdMatch;
  }
  product.expected_product_id = expectedId;
  product.capture_product_id = capturedProductId;
  product.capture_product_id_match = productIdMatch;
  if (!productIdMatch) {
    return {
      command_type: commandType,
      supported: true,
      error: "detail_product_id_mismatch",
      statusText: "当前详情页商品 ID 与目标商品不一致",
      help: "请刷新商品详情页或重新批量采集；插件不会把其它商品的标题、图片、SKU 合并入池。",
      product,
      expected_product_id: expectedId,
      captured_product_id: capturedProductId,
      url: rawUrl,
      title: tab.title || "",
      capturedAt: new Date().toISOString()
    };
  }
  const cleanedTitle = cleanCapturedProductTitleForDraft(product.title || product.product_name || "");
  if (cleanedTitle) {
    product.title = cleanedTitle;
    product.product_name = cleanedTitle;
  }
  if (!String(product.title || "").trim()) {
    const capturedFields = product.captured_fields && typeof product.captured_fields === "object" ? product.captured_fields : {};
    product.captured_fields = capturedFields;
    const fallbackTitle = cleanCapturedPageTitleFallback(
      capturedFields.document_title || capturedFields.page_title || tab.title || ""
    );
    if (capturedProductTitleIsUsable(fallbackTitle)) {
      product.title = fallbackTitle;
      product.product_name = fallbackTitle;
      const titleCandidates = Array.isArray(capturedFields.title_candidates) ? capturedFields.title_candidates : [];
      capturedFields.title_candidates = [
        {
          value: fallbackTitle.slice(0, 180),
          score: 70,
          source: "tab.title.fallback",
          url: rawUrl,
          product_id: capturedProductId,
          expected_product_id: expectedId,
          product_id_match: productIdMatch
        },
        ...titleCandidates
      ].slice(0, 6);
      capturedFields.title_fallback_source = "tab.title";
      product.quality = product.quality && typeof product.quality === "object" ? product.quality : {};
      product.quality.title_ok = true;
      product.quality.title_source = "tab.title.fallback";
    }
  }
  const title = String(product.title || "").trim();
  const imageUrl = String(product.image_url || product.imageUrl || "").trim();
  if (!title) {
    return {
      command_type: commandType,
      supported: true,
      error: "missing_product_title",
      statusText: "未识别到可靠商品标题",
      help: "请确认当前页是商品详情页，页面已加载完成后再点击采集。",
      product,
      url: rawUrl,
      title: tab.title || "",
      capturedAt: new Date().toISOString()
    };
  }
  if (!imageUrl) {
    return {
      command_type: commandType,
      supported: true,
      error: "missing_product_image",
      statusText: "未识别到可靠商品主图",
      help: "请确认商品主图已加载，或换到有清晰主图的商品详情页后再采集。",
      product,
      url: rawUrl,
      title: tab.title || "",
      capturedAt: new Date().toISOString()
    };
  }
  const imageScore = Number(product.quality?.image_score || 0);
  const imageQualityFlags = Array.isArray(product.quality?.image_quality_flags) ? product.quality.image_quality_flags : [];
  if (imageQualityFlags.includes("packaging_only_not_product") || imageQualityFlags.includes("page_service_or_icon_image")) {
    return {
      command_type: commandType,
      supported: true,
      error: "official_image_quality_risk",
      statusText: "主图疑似不是清晰商品本体图",
      help: "请确认当前页展示的是完整商品大图，不是包装袋、客服/服务图标或页面噪声图，再重新采集。",
      product,
      url: rawUrl,
      title: tab.title || "",
      capturedAt: new Date().toISOString()
    };
  }
  if (Number.isFinite(imageScore) && imageScore < 12) {
    return {
      command_type: commandType,
      supported: true,
      error: "low_confidence_product_image",
      statusText: "未识别到可靠商品主图",
      help: "当前页识别到的图片不像商品主展示图，请确认商品大图已加载后再采集。",
      product,
      url: rawUrl,
      title: tab.title || "",
      capturedAt: new Date().toISOString()
    };
  }
  return {
    command_type: commandType,
    supported: true,
    statusText: title ? `已采集：${title.slice(0, 40)}` : "已执行采集，但未识别到商品标题",
    product,
    url: rawUrl,
    title: tab.title || "",
    capturedAt: new Date().toISOString()
  };
}

async function extractProductListFromCurrentPage(options = {}) {
  const scanLimit = Math.max(1, Math.min(Number(options?.limit || 40), 80));
  const maxScrollPasses = Math.max(0, Math.min(Number(options?.maxScrollPasses || 0), 20));
  const scrollWaitMs = Math.max(100, Math.min(Number(options?.scrollWaitMs || 650), 2000));
  const host = location.hostname.toLowerCase();
  const isTemuHost = /temu\.com/.test(host);
  const text = (value) => (value == null ? "" : String(value)).replace(/\s+/g, " ").trim();
  const absUrl = (url) => {
    url = text(url);
    if (!url || url.startsWith("data:") || url.startsWith("blob:")) return "";
    if (url.startsWith("//")) return location.protocol + url;
    try {
      return new URL(url, location.href).href;
    } catch (_error) {
      return "";
    }
  };
  const normalizeImageUrl = (url) => {
    url = absUrl(url);
    if (!url) return "";
    url = url.replace(/\.((?:jpg|jpeg|png|webp))_(?:\d+x\d+|sum|q\d+)\.(?:jpg|jpeg|png|webp)$/i, ".$1");
    url = url.replace(/\.((?:jpg|jpeg|png))_\.webp(?:\?.*)?$/i, ".$1");
    url = url.replace(/_(?:\d+x\d+|q\d+)\.(?:jpg|jpeg|png|webp)(\?.*)?$/i, "$1");
    return url;
  };
  const srcsetLast = (value) => text(value).split(",").map((part) => part.trim().split(/\s+/)[0]).filter(Boolean).pop() || "";
  const bgUrl = (element) => {
    if (!element) return "";
    const match = String(getComputedStyle(element).backgroundImage || "").match(/url\((['"]?)(.*?)\1\)/i);
    return match ? match[2] : "";
  };
  const inlineStyleImageUrl = (element) => {
    if (!element) return "";
    const match = String(element.getAttribute?.("style") || "").match(/url\((['"]?)(.*?)\1\)/i);
    return match ? match[2] : "";
  };
  const visible = (element) => {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 12 && rect.height > 12 && style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || 1) > 0.05;
  };
  const offerIdFromUrl = (url) => {
    const value = String(url || "");
    const patterns = [
      /\/offer\/(\d+)\.html/i,
      /-g-(\d+)(?:\.html|[/?#]|$)/i,
      /[?&](?:offerId|offerid|offer_id|productId|productid|product_id|goods_id|goodsId|item_id|itemId|spu_id|spuId)=(\d+)/i,
      /\/(\d{8,})(?:\.html|[/?#]|$)/i
    ];
    for (const pattern of patterns) {
      const match = value.match(pattern);
      if (match) return match[1];
    }
    return "";
  };
  const offerSelector = [
    'a[href*="/offer/"]',
    'a[href*="detail.1688.com"]',
    'a[href*="-g-"]',
    'a[href*="/goods.html"]',
    'a[href*="offerId="]',
    'a[href*="offerid="]',
    'a[href*="offer_id="]',
    'a[href*="productId="]',
    'a[href*="productid="]',
    'a[href*="product_id="]',
    'a[href*="goods_id="]',
    'a[href*="goodsId="]',
    "[data-offer-id]",
    "[data-offerid]",
    "[offer-id]",
    "[offerid]",
    "[data-goods-id]",
    "[data-goodsid]",
    "[data-item-id]",
    "[data-product-id]"
  ].join(",");
  const attrFirst = (element, names) => {
    for (const name of names) {
      const value = text(element?.getAttribute?.(name) || "");
      if (value) return value;
    }
    return "";
  };
  const offerHrefFromElement = (element) => {
    const raw = [
      element?.href,
      element?.getAttribute?.("href"),
      attrFirst(element, ["data-href", "data-url", "data-link", "data-target-url", "data-logurl", "data-spm-anchor-id"])
    ].map(text).find(Boolean) || "";
    let href = absUrl(raw);
    const offerId = offerIdFromUrl(href || raw) || attrFirst(element, ["data-offer-id", "data-offerid", "offer-id", "offerid", "data-goods-id", "data-goodsid", "data-item-id", "data-product-id"]);
    if (!href && offerId) href = /temu\.com/.test(host)
      ? `${location.origin}/goods.html?goods_id=${offerId}`
      : `https://detail.1688.com/offer/${offerId}.html`;
    return href;
  };
  const uniqueOfferLinkCount = (element) => {
    const nodes = Array.from(element?.querySelectorAll?.(offerSelector) || []);
    if (element?.matches?.(offerSelector)) nodes.unshift(element);
    const ids = new Set(
      nodes
        .map((node) => offerIdFromUrl(offerHrefFromElement(node)) || attrFirst(node, ["data-offer-id", "data-offerid", "offer-id", "offerid", "data-goods-id", "data-goodsid", "data-item-id", "data-product-id"]))
        .filter(Boolean)
    );
    return ids.size;
  };
  const addTemuEmbeddedCandidatesFromText = (map, sourceText = "", source = "embedded_payload") => {
    if (!isTemuHost || !sourceText) return;
    const urlPattern = /(?:https?:\/\/[^"'<> ]+)?\/[^"'<> ]*?goods\.html\?[^"'<> ]*?(?:goods_id|goodsId|product_id|productId|item_id|itemId|spu_id|spuId|offerId|offer_id)=(\d{6,})(?:[^"'<> ]*)/gi;
    const idPattern = /\b(?:goods_id|goodsId|product_id|productId|item_id|itemId|spu_id|spuId|offerId|offer_id)\b\s*[:=]\s*["']?(\d{6,})/gi;
    const addToMap = (offerId, candidateSource) => {
      if (!offerId || map.has(offerId)) return;
      map.set(offerId, {
        anchor: document.body,
        href: canonicalOfferHref(`${location.origin}/goods.html?goods_id=${offerId}`, offerId),
        offerId,
        source: candidateSource
      });
    };
    for (const match of sourceText.matchAll(urlPattern)) {
      addToMap(match[1], `${source}:url`);
    }
    for (const match of sourceText.matchAll(idPattern)) {
      addToMap(match[1], `${source}:id`);
    }
  };
  const cleanTitle = (value) => {
    value = text(value)
      .replace(/[【】\[\]{}]/g, " ")
      .replace(/\{[^{}]*region[^{}]*\}/gi, " ")
      .replace(/\bhome\s+kitchen\s*[-–—]\s*(?:Canada|United States|United Kingdom|Australia|Germany|France|Spain|Italy|Japan|Korea|Mexico)\b/gi, " ")
      .replace(/\s*[-–—]\s*(?:Canada|United States|United Kingdom|Australia|Germany|France|Spain|Italy|Japan|Korea|Mexico)\s*$/gi, " ")
      .replace(/(?:品牌|Brand)\s*[:：]\s*[A-Za-z0-9 _.-]{2,48}/gi, " ")
      .replace(/(新人价|热销|严选|包邮|退货包运费|一件代发|支持抖音面单|官方物流|下单返|极速退款|现货|起批|已售|Sold|收藏|回头率|5星好评|粉销商品|畅销商品|本地仓库|最快)\s*/gi, " ")
      .replace(/(?:CA\$|US\$|\$|￥|¥)\s*\d+(?:\.\d+)?/gi, " ")
      .replace(/\b\d+(?:\.\d+)?\s*(?:人付款|人已购|人收藏|件起批|件|个)\b/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (value.length > 180) value = value.slice(0, 180).trim();
    return value;
  };
  const isNoisyTitle = (value) => {
    value = text(value);
    if (value.length < 4) return true;
    if (/^(首页|订单|商品|供应商|下载插件|我的阿里|采购车|消息|官方服务|找工厂|找货源|搜本店|综合|销量|价格|起订量|筛选|更多|客服|购物车|消息|反馈|顶部|查看详情|图搜同款)$/.test(value)) return true;
    if (/^(?:CA\$|US\$|\$|￥|¥)?\s*\d+(?:\.\d+)?(?:\s+已售|\s+Sold)?$/.test(value)) return true;
    if (/^(?:品牌|Brand)\s*[:：]/i.test(value)) return true;
    if (/有限公司|商行|旗舰店|专营店|在线询价|源头厂货|跨境定制|超级工厂|实力工厂/.test(value) && value.length < 28) return true;
    return false;
  };
  const priceFromText = (value) => {
    const matches = Array.from(String(value || "").matchAll(/(?:CA\$|US\$|\$|[¥￥])\s*(\d+(?:\.\d+)?)/g))
      .map((match) => Number(match[1]))
      .filter((price) => Number.isFinite(price) && price > 0 && price < 100000);
    if (!matches.length) return { price: "", candidates: [] };
    const price = Math.max(...matches);
    return { price: String(price), candidates: matches.slice(0, 8) };
  };
  const cardText = (element) => text(element?.innerText || element?.textContent || "");
  const classChain = (element) => {
    const parts = [];
    let node = element;
    for (let depth = 0; node && depth < 5; depth += 1, node = node.parentElement) {
      parts.push(`${node.id || ""} ${node.className || ""}`);
    }
    return parts.join(" ").toLowerCase();
  };
  const adReasonForCard = (card) => {
    const rect = card.getBoundingClientRect();
    const classText = classChain(card);
    const value = cardText(card);
    const productLinks = uniqueOfferLinkCount(card);
    const multiProductLimit = isTemuHost ? 12 : 4;
    if ((rect.width > 560 || rect.height > 680) && productLinks !== 1) return "banner_or_large_promo_block";
    if (/(^|[\s_-])(ad|ads|banner|promotion|sponsor|sponsored|sem)([\s_-]|$)/i.test(classText) && productLinks !== 1) return "ad_class_marker";
    if (/广告|推广|赞助|为你推荐|店铺推荐/.test(value) && productLinks !== 1) return "ad_text_marker";
    if (/源头厂货|超级工厂|实力工厂|跨境定制|免费贴标|在线询价|现货速发|支持ODM|支持OEM/.test(value) && value.length > 220 && productLinks !== 1) {
      return "factory_or_shop_promo_block";
    }
    if (productLinks > multiProductLimit) return "multi_product_promo_block";
    return "";
  };
  const findProductCard = (anchor) => {
    let best = null;
    let node = anchor;
    for (let depth = 0; node && depth < 9; depth += 1, node = node.parentElement) {
      const rect = node.getBoundingClientRect();
      if (rect.width < 130 || rect.height < 100) continue;
      if (rect.width > 900 || rect.height > 720) break;
      const value = cardText(node);
      const hasPrice = /[¥￥]\s*\d/.test(value);
      const hasImage = Array.from(node.querySelectorAll("img")).some((img) => visible(img) && img.getBoundingClientRect().width >= 55 && img.getBoundingClientRect().height >= 55);
      const offerLinks = uniqueOfferLinkCount(node);
      if (hasImage && offerLinks <= 4) {
        best = node;
        if (hasPrice && rect.width <= 460 && rect.height <= 560) return node;
      }
    }
    return best;
  };
  const bestImage = (card, anchor) => {
    const imageElements = Array.from(new Set([
      ...Array.from(anchor.querySelectorAll("img")),
      ...Array.from(card.querySelectorAll("img"))
    ]));
    const candidates = [];
    for (const img of imageElements) {
      if (!visible(img)) continue;
      const rect = img.getBoundingClientRect();
      if (rect.width < 55 || rect.height < 55 || rect.width > 460 || rect.height > 460) continue;
      const raw = img.currentSrc || img.src || img.getAttribute("data-src") || img.getAttribute("data-lazy-src") || srcsetLast(img.getAttribute("srcset")) || bgUrl(img);
      const url = normalizeImageUrl(raw);
      if (!url || /logo|avatar|icon|sprite|loading|blank|placeholder/i.test(url)) continue;
      const score = (rect.width * rect.height) + (anchor.contains(img) ? 2000 : 0);
      candidates.push({ url, score, width: Math.round(rect.width), height: Math.round(rect.height), alt: text(img.alt || img.title || "") });
    }
    candidates.sort((left, right) => right.score - left.score);
    return candidates[0] || null;
  };
  const bestTitle = (card, anchor, image) => {
    const lines = cardText(card)
      .split(/[\n\r]+| {2,}/)
      .map(cleanTitle)
      .filter(Boolean);
    const candidates = [
      anchor.getAttribute("title"),
      anchor.getAttribute("aria-label"),
      anchor.innerText,
      image?.alt || "",
      ...lines
    ]
      .map(cleanTitle)
      .filter((item) => item && !isNoisyTitle(item) && !/^[¥￥]?\d+(?:\.\d+)?$/.test(item));
    candidates.sort((left, right) => {
      const leftScore = Math.min(left.length, 90) + (/[\u4e00-\u9fffA-Za-z]/.test(left) ? 20 : 0);
      const rightScore = Math.min(right.length, 90) + (/[\u4e00-\u9fffA-Za-z]/.test(right) ? 20 : 0);
      return rightScore - leftScore;
    });
    return candidates[0] || "";
  };

  if (!/1688\.com|alibaba\.com|temu\.com/.test(host)) {
    return { error: "unsupported_host", products: [], skipped: [], skipped_count: 0, total_candidates: 0 };
  }

  const canonicalOfferHref = (href, offerId) => {
    href = absUrl(href);
    offerId = text(offerId || offerIdFromUrl(href));
    if (/1688\.com/.test(host) && offerId) return `https://detail.1688.com/offer/${offerId}.html`;
    if (/temu\.com/.test(host)) {
      try {
        const parsed = new URL(href, location.href);
        parsed.hash = "";
        const keepKeys = new Set([
          "goods_id",
          "goodsId",
          "product_id",
          "productId",
          "item_id",
          "itemId",
          "spu_id",
          "spuId",
          "offerId",
          "offer_id"
        ]);
        for (const key of Array.from(parsed.searchParams.keys())) {
          if (!keepKeys.has(key)) parsed.searchParams.delete(key);
        }
        return parsed.href;
      } catch (_error) {
        return href;
      }
    }
    return href;
  };
  const collectLinkItems = () => {
    const nodes = new Set([
      ...Array.from(document.querySelectorAll(offerSelector)),
      ...Array.from(document.querySelectorAll("a"))
        .filter((anchor) => offerIdFromUrl(anchor.href || anchor.getAttribute("href") || ""))
    ]);
    const embeddedMap = new Map();
    const items = [];
    for (const node of nodes) {
      const rawHref = offerHrefFromElement(node);
      const offerId = offerIdFromUrl(rawHref) || attrFirst(node, ["data-offer-id", "data-offerid", "offer-id", "offerid", "data-goods-id", "data-goodsid", "data-item-id", "data-product-id"]);
      const href = canonicalOfferHref(rawHref, offerId);
      if (!href || !offerId) continue;
      items.push({ anchor: node, href, offerId, source: node.tagName === "A" ? "anchor" : "data_offer_id" });
    }
    const html = String(document.body?.innerHTML || "");
    const idPattern = /(?:offerId|offerid|offer_id|goods_id|goodsId|product_id|productId|item_id|itemId|spu_id|spuId|data-offer-id|data-offerid|data-goods-id|-g-|offer\/)(?:["'\s:=/-]+)(\d{6,})/gi;
    for (const match of html.matchAll(idPattern)) {
      const offerId = match[1];
      if (!offerId) continue;
      if (!embeddedMap.has(offerId)) {
        embeddedMap.set(offerId, {
          anchor: document.body,
          href: canonicalOfferHref(/temu\.com/.test(host) ? `${location.origin}/goods.html?goods_id=${offerId}` : `https://detail.1688.com/offer/${offerId}.html`, offerId),
          offerId,
          source: "embedded_offer_id"
        });
      }
    }
    if (isTemuHost) {
      addTemuEmbeddedCandidatesFromText(embeddedMap, html, "page_html");
    }
    const scripts = Array.from(document.querySelectorAll("script:not([src])"));
    for (const script of scripts) {
      if (isTemuHost) {
        addTemuEmbeddedCandidatesFromText(embeddedMap, String(script.textContent || ""), "script_json");
      }
    }
    for (const item of embeddedMap.values()) {
      items.push(item);
    }
    return items;
  };
  const rememberLinkItems = (map) => {
    for (const item of collectLinkItems()) {
      const offerId = item.offerId || offerIdFromUrl(item.href);
      if (!offerId || map.has(offerId)) continue;
      map.set(offerId, item);
    }
  };
  const scannedLinks = new Map();
  const scanLinksAcrossScroll = async () => {
    const originalX = window.scrollX || 0;
    const originalY = window.scrollY || 0;
    let unchangedPasses = 0;
    let lastCount = 0;
    try {
      if (maxScrollPasses > 0) {
        const startY = Math.max(0, originalY - Math.floor(window.innerHeight * 1.2));
        window.scrollTo({ top: startY, left: originalX, behavior: "auto" });
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(scrollWaitMs, 350)));
      }
      rememberLinkItems(scannedLinks);
      for (let pass = 0; pass < maxScrollPasses && scannedLinks.size < scanLimit; pass += 1) {
        const beforeY = window.scrollY || 0;
        const maxY = Math.max(
          document.body?.scrollHeight || 0,
          document.documentElement?.scrollHeight || 0
        ) - window.innerHeight;
        if (beforeY >= maxY - 8 && pass > 0) break;
        window.scrollTo({
          top: Math.min(maxY, beforeY + Math.max(320, Math.floor(window.innerHeight * 0.85))),
          left: originalX,
          behavior: "auto"
        });
        window.dispatchEvent(new Event("scroll"));
        document.dispatchEvent(new Event("scroll"));
        await new Promise((resolve) => window.setTimeout(resolve, scrollWaitMs));
        rememberLinkItems(scannedLinks);
        if (scannedLinks.size === lastCount) {
          unchangedPasses += 1;
        } else {
          unchangedPasses = 0;
          lastCount = scannedLinks.size;
        }
        if (unchangedPasses >= 3) break;
      }
    } finally {
      if (maxScrollPasses > 0) {
        window.scrollTo({ top: originalY, left: originalX, behavior: "auto" });
      }
    }
  };
  await scanLinksAcrossScroll();
  const links = Array.from(scannedLinks.values());
  const currentOfferId = /temu\.com/.test(host) ? offerIdFromUrl(location.href) : "";
  const products = [];
  const skipped = [];
  const seen = new Set();

  for (const item of links) {
    const offerId = item.offerId || offerIdFromUrl(item.href);
    if (!offerId || seen.has(offerId)) continue;
    if (/temu\.com/.test(host) && currentOfferId && offerId === currentOfferId && links.length > 1) {
      skipped.push({ link: item.href, reason: "current_temu_detail_product" });
      continue;
    }
    seen.add(offerId);
    const anchorInDocument = item.anchor && document.documentElement.contains(item.anchor);
    const card = item.source === "embedded_offer_id" || !anchorInDocument
      ? null
      : (findProductCard(item.anchor) || item.anchor.closest?.("article,li,section,div") || item.anchor);
    const adReason = card ? adReasonForCard(card) : "";
    if (adReason) {
      skipped.push({ link: item.href, reason: adReason });
      continue;
    }
    const image = card ? bestImage(card, item.anchor) : null;
    const title = card ? bestTitle(card, item.anchor, image) : "";
    const price = card ? priceFromText(cardText(card)) : { price: "", candidates: [] };
    const rect = card?.getBoundingClientRect?.() || { x: 0, y: 0, width: 0, height: 0 };
    products.push({
      title,
      category: "其他",
      image_url: image?.url || "",
      imageUrl: image?.url || "",
      price: price.price,
      currency: "CNY",
      product_link: item.href,
      link: item.href,
      product_id: offerId,
      sku: offerId,
      variant_groups: [],
      variant_combinations: [],
      raw_variant_groups: [],
      raw_variant_combinations: [],
      source: host,
      platform: /1688\.com/.test(host) ? "1688" : (/temu\.com/.test(host) ? "temu" : "alibaba"),
      captured_fields: {
        capture_method: "list_page_detail_link_batch",
        link_capture_only: true,
        link_source: item.source || "",
        price_source: price.price ? "list_card_text" : "",
        price_candidates: price.candidates,
        image_source: image?.url ? "list_card_optional" : "",
        title_source: title ? "list_card_optional" : "",
        card_rect: {
          x: Math.round(rect.x),
          y: Math.round(rect.y),
          width: Math.round(rect.width),
          height: Math.round(rect.height)
        }
      },
      raw_payload: {
        list_page_url: location.href,
        list_card_text: card ? cardText(card).slice(0, 600) : "",
        list_detail_link: item.href
      },
      quality: {
        link_ok: true,
        title_ok: Boolean(title),
        image_ok: Boolean(image?.url),
        title_source: title ? "list_card_optional" : "",
        image_source: image?.url ? "list_card_optional" : "",
        image_score: image?.score ? Math.round(Math.min(100, image.score / 1000)) : 0
      }
    });
    if (products.length >= scanLimit) break;
  }

  return {
    products,
    skipped: skipped.slice(0, 120),
    skipped_count: skipped.length,
    total_candidates: links.length,
    scroll_scan_passes: maxScrollPasses,
    scan_limit: scanLimit,
    url: location.href,
    title: document.title || "",
    capturedAt: new Date().toISOString()
  };
}

function extractProductFromCurrentPage(expectedProductId = "") {
  const host = location.hostname.toLowerCase();
  const text = (value) => (value == null ? "" : String(value)).replace(/\s+/g, " ").trim();
  const attr = (selector, name) => document.querySelector(selector)?.getAttribute(name) || "";
  const pick = (...values) => values.map(text).find(Boolean) || "";
  const is1688 = /1688\.com|alibaba\.com/.test(host);
  const absUrl = (url) => {
    url = text(url);
    if (!url || url.startsWith("data:") || url.startsWith("blob:")) return "";
    if (url.startsWith("//")) return location.protocol + url;
    try {
      return new URL(url, location.href).href;
    } catch (_error) {
      return "";
    }
  };
  const normalizeImageUrl = (url) => {
    url = absUrl(url);
    if (!url) return "";
    url = url.replace(/\.((?:jpg|jpeg|png|webp))_(?:\d+x\d+|sum|q\d+)\.(?:jpg|jpeg|png|webp)$/i, ".$1");
    url = url.replace(/\.((?:jpg|jpeg|png))_\.webp(?:\?.*)?$/i, ".$1");
    url = url.replace(/_(?:\d+x\d+|q\d+)\.(?:jpg|jpeg|png|webp)(\?.*)?$/i, "$1");
    return url;
  };
  const srcsetLast = (value) => text(value).split(",").map((part) => part.trim().split(/\s+/)[0]).filter(Boolean).pop() || "";
  const bgUrl = (element) => {
    if (!element) return "";
    const match = String(getComputedStyle(element).backgroundImage || "").match(/url\((['"]?)(.*?)\1\)/i);
    return match ? match[2] : "";
  };
  const inlineStyleImageUrl = (element) => {
    if (!element) return "";
    const match = String(element.getAttribute?.("style") || "").match(/url\((['"]?)(.*?)\1\)/i);
    return match ? match[2] : "";
  };
  const visible = (element) => {
    if (!element) return false;
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 10 && rect.height > 10 && style.display !== "none" && style.visibility !== "hidden";
  };
  const productIdFromUrl = () => {
    const href = location.href;
    const patterns = [
      /\/(?:dp|gp\/product|product)\/([A-Z0-9]{10})(?:[/?#]|$)/i,
      /[?&](?:asin|ASIN)=([A-Z0-9]{10})(?:&|$)/,
      /\/offer\/(\d+)\.html/i,
      /-g-(\d+)(?:\.html|[/?#]|$)/i,
      /[?&]offerId=(\d+)/i,
      /[?&]offerid=(\d+)/i,
      /[?&]offer_id=(\d+)/i,
      /[?&]goods_id=(\d+)/i,
      /[?&]goodsId=(\d+)/i,
      /[?&]goodsid=(\d+)/i,
      /[?&]product_id=(\d+)/i,
      /[?&]productId=(\d+)/i,
      /[?&]item_id=(\d+)/i,
      /[?&]itemId=(\d+)/i,
      /[?&]spu_id=(\d+)/i,
      /[?&]spuId=(\d+)/i,
      /\/(\d{8,})(?:\.html|[/?#]|$)/i,
    ];
    for (const pattern of patterns) {
      const match = href.match(pattern);
      if (match) return match[1];
    }
    return "";
  };
  const canonicalProductLink = (value, productId = "") => {
    const raw = text(value);
    productId = text(productId || productIdFromUrl());
    if (!raw && productId && /^\d{8,}$/.test(productId)) return `https://detail.1688.com/offer/${productId}.html`;
    if (!raw) return "";
    try {
      const parsed = new URL(raw);
      const parsedHost = parsed.hostname.toLowerCase();
      if (/amazon\.com$/.test(parsedHost) && /^[A-Z0-9]{10}$/i.test(productId)) return `https://www.amazon.com/dp/${productId.toUpperCase()}`;
      if (/1688\.com$/.test(parsedHost) && productId) return `https://detail.1688.com/offer/${productId}.html`;
      if (/alibaba\.com$/.test(parsedHost)) {
        parsed.search = "";
        parsed.hash = "";
        return parsed.href;
      }
    } catch (_error) {
      return raw;
    }
    return raw;
  };
  const pageProductId = productIdFromUrl();
  const expectedId = text(expectedProductId);
  const pageProductIdMatch = !expectedId
    || !pageProductId
    || pageProductId === expectedId
    || (/^[A-Z0-9]{10}$/i.test(pageProductId) && pageProductId.toUpperCase() === expectedId.toUpperCase());
  const isAmazonHost = /(^|\.)amazon\.com$/i.test(host);
  const extractAmazonProductFromCurrentPage = () => {
    const pageAsin = text(pageProductId).toUpperCase();
    const cleanAmazonTitle = (value) => text(value)
      .replace(/\s*[:|-]\s*Amazon(?:\.com)?\s*$/i, "")
      .replace(/\s*-\s*Amazon(?:\.com)?\s*$/i, "")
      .trim();
    const elementText = (selector) => text(document.querySelector(selector)?.innerText || document.querySelector(selector)?.textContent || "");
    const amazonTitleCandidates = [];
    const addAmazonTitle = (value, score, source) => {
      const cleaned = cleanAmazonTitle(value);
      if (!cleaned || cleaned.length < 4) return;
      amazonTitleCandidates.push({
        value: cleaned.slice(0, 220),
        score,
        source,
        product_id: pageAsin,
        expected_product_id: expectedId,
        product_id_match: pageProductIdMatch
      });
    };
    addAmazonTitle(elementText("#productTitle"), 60, "#productTitle");
    addAmazonTitle(attr("meta[property='og:title']", "content"), 42, "og:title");
    addAmazonTitle(document.title, 22, "document.title");
    amazonTitleCandidates.sort((a, b) => b.score - a.score);

    const normalizeAmazonImageUrl = (url) => {
      url = normalizeImageUrl(url);
      if (!url) return "";
      return url.replace(/\._[^.]+_(?=\.(?:jpg|jpeg|png|webp)(?:[?#]|$))/i, "");
    };
    const amazonImageLooksUsable = (url) => /^https?:\/\//i.test(String(url || ""))
      && /\.(?:jpg|jpeg|png|webp)(?:[?#]|$)/i.test(String(url || ""))
      && !/(?:sprite|logo|transparent-pixel|grey-pixel|loading|placeholder|play-button|video)/i.test(String(url || ""));
    const amazonImageCandidates = [];
    const addAmazonImage = (url, score, source, element = null) => {
      const normalized = normalizeAmazonImageUrl(url);
      if (!normalized || !amazonImageLooksUsable(normalized)) return;
      let adjusted = Number(score || 0);
      if (/m\.media-amazon\.com|ssl-images-amazon\.com/i.test(normalized)) adjusted += 5;
      if (element) {
        const rect = element.getBoundingClientRect?.() || { width: 0, height: 0, top: 9999 };
        const area = Math.max(rect.width || element.naturalWidth || 0, 0) * Math.max(rect.height || element.naturalHeight || 0, 0);
        if (rect.width >= 260 && rect.height >= 260) adjusted += 22;
        if (area >= 60000) adjusted += 8;
        if (rect.width < 80 || rect.height < 80) adjusted -= 14;
        if (rect.top >= -20 && rect.top < Math.max(window.innerHeight || 800, 600)) adjusted += 4;
      }
      amazonImageCandidates.push({ url: normalized, score: adjusted, source });
    };
    const addAmazonImageElement = (element, score, source) => {
      if (!element) return;
      addAmazonImage(element.getAttribute?.("data-old-hires"), score + 24, `${source}:data-old-hires`, element);
      addAmazonImage(element.currentSrc || element.src, score + 16, `${source}:src`, element);
      addAmazonImage(element.getAttribute?.("data-a-hires"), score + 18, `${source}:data-a-hires`, element);
      addAmazonImage(element.getAttribute?.("data-src"), score + 8, `${source}:data-src`, element);
      addAmazonImage(srcsetLast(element.getAttribute?.("srcset")), score + 8, `${source}:srcset`, element);
      const dynamic = element.getAttribute?.("data-a-dynamic-image");
      if (dynamic) {
        try {
          Object.keys(JSON.parse(dynamic)).slice(0, 10).forEach((url) => addAmazonImage(url, score + 28, `${source}:data-a-dynamic-image`, element));
        } catch (_error) {}
      }
      addAmazonImage(bgUrl(element), score, `${source}:background`, element);
      addAmazonImage(inlineStyleImageUrl(element), score, `${source}:inline-style`, element);
    };
    addAmazonImage(attr("meta[property='og:image']", "content"), 34, "og:image");
    addAmazonImage(attr("meta[property='og:image:secure_url']", "content"), 34, "og:image:secure_url");
    [
      "#landingImage",
      "#imgTagWrapperId img",
      "#main-image-container img",
      "#altImages img",
      "#imageBlock img",
      "#imageBlock_feature_div img",
      "#variation_color_name img"
    ].forEach((selector) => {
      document.querySelectorAll(selector).forEach((img) => addAmazonImageElement(img, selector === "#landingImage" ? 60 : 34, selector));
    });
    const seenAmazonImages = new Set();
    for (let index = amazonImageCandidates.length - 1; index >= 0; index -= 1) {
      const key = amazonImageCandidates[index].url.replace(/[?#].*$/, "");
      if (seenAmazonImages.has(key)) {
        amazonImageCandidates.splice(index, 1);
        continue;
      }
      seenAmazonImages.add(key);
    }
    amazonImageCandidates.sort((a, b) => b.score - a.score);
    const amazonProductImageUrls = amazonImageCandidates
      .filter((item) => item.score >= 20)
      .map((item) => item.url)
      .filter((url, index, list) => list.indexOf(url) === index)
      .slice(0, 8);

    const priceText = pick(
      elementText("#corePriceDisplay_desktop_feature_div .a-price .a-offscreen"),
      elementText("#corePrice_feature_div .a-price .a-offscreen"),
      elementText("#priceblock_ourprice"),
      elementText("#priceblock_dealprice"),
      elementText("#apex_desktop .a-price .a-offscreen"),
      attr("meta[property='product:price:amount']", "content")
    );
    const currencyText = pick(attr("meta[property='product:price:currency']", "content"), /\$/.test(priceText) ? "USD" : "");
    const normalizedAmazonPrice = (() => {
      const match = String(priceText || "").replace(/,/g, "").match(/(?:US\$|\$)?\s*([0-9]+(?:\.[0-9]{1,2})?)/i);
      if (!match) return "";
      const prefix = currencyText === "USD" || /\$/.test(priceText) ? "USD " : "";
      return `${prefix}${match[1]}`.trim();
    })();

    const breadcrumbParts = Array.from(document.querySelectorAll("#wayfinding-breadcrumbs_feature_div a, #wayfinding-breadcrumbs_container a"))
      .map((node) => text(node.innerText || node.textContent))
      .filter(Boolean)
      .slice(0, 8);
    const amazonCategory = breadcrumbParts.join(" > ") || "其他";

    const sourceAttributes = {};
    const sourceAttributePairs = [];
    const sourceAttributeTable = [];
    const addAmazonAttribute = (name, value, source) => {
      const key = text(name).replace(/^[\s:：-]+|[\s:：-]+$/g, "");
      const val = text(value).replace(/^[\s:：-]+|[\s:：-]+$/g, "");
      if (!key || !val || key.length > 80 || val.length > 200) return;
      if (/^(?:price|delivery|shipping|returns?|payment|quantity|cart|buy now)$/i.test(key)) return;
      if (!sourceAttributes[key]) sourceAttributes[key] = val;
      sourceAttributePairs.push({ name: key, value: val, source });
      sourceAttributeTable.push({ key, value: val, source });
    };
    document.querySelectorAll("#productOverview_feature_div tr, #productDetails_detailBullets_sections1 tr, #productDetails_techSpec_section_1 tr").forEach((row) => {
      const cells = Array.from(row.querySelectorAll?.("th, td") || []).map((cell) => text(cell.innerText || cell.textContent)).filter(Boolean);
      if (cells.length >= 2) addAmazonAttribute(cells[0], cells.slice(1).join(" "), "amazon_detail_table");
    });
    document.querySelectorAll("#detailBullets_feature_div li, #detailBulletsWrapper_feature_div li").forEach((item) => {
      const raw = text(item.innerText || item.textContent);
      const parts = raw.split(/\s*[:：]\s*/);
      if (parts.length >= 2) addAmazonAttribute(parts[0], parts.slice(1).join(": "), "amazon_detail_bullet");
    });
    addAmazonAttribute("availability", elementText("#availability"), "amazon_buybox");
    addAmazonAttribute("brand_or_store", elementText("#bylineInfo"), "amazon_byline");
    const boughtText = text((document.body?.innerText || "").match(/\b[\d,.]+[Kk+]?\+?\s+bought in past month\b/i)?.[0] || "");
    if (boughtText) addAmazonAttribute("recent_sales_signal", boughtText, "amazon_body_text");

    const amazonGroupName = (raw, fallback = "") => {
      const sample = `${raw || ""} ${fallback || ""}`;
      if (/color|colour|颜色/i.test(sample)) return "Color";
      if (/size|尺码|尺寸/i.test(sample)) return "Size";
      if (/style|fit|pattern|款式|图案/i.test(sample)) return "Style";
      return text(raw || fallback || "Style") || "Style";
    };
    const selectedFromContainerLabel = (container) => {
      const label = text(container.querySelector?.(".a-form-label, label")?.innerText || container.querySelector?.(".a-form-label, label")?.textContent || "");
      const parts = label.split(/\s*[:：]\s*/);
      return parts.length >= 2 ? text(parts.slice(1).join(": ")) : "";
    };
    const valueFromAmazonOption = (option) => {
      const img = option.querySelector?.("img");
      const buttonText = option.querySelector?.(".a-button-text, .twisterTextDiv, .dropdownAvailable, .swatch-title-text");
      return text(
        img?.getAttribute?.("alt")
        || option.getAttribute?.("title")
        || option.getAttribute?.("aria-label")
        || buttonText?.innerText
        || buttonText?.textContent
        || option.innerText
        || option.textContent
        || option.getAttribute?.("value")
      )
        .replace(/\$\s*\d+(?:\.\d{1,2})?.*$/i, "")
        .replace(/\s*\([^)]*unavailable[^)]*\)\s*/i, "")
        .trim();
    };
    const asinFromAmazonOption = (option) => {
      const direct = pick(
        option.getAttribute?.("data-asin"),
        option.getAttribute?.("data-defaultasin"),
        option.getAttribute?.("data-csa-c-item-id"),
        option.getAttribute?.("value")
      );
      const match = String(direct || option.getAttribute?.("data-dp-url") || option.getAttribute?.("href") || "").match(/[A-Z0-9]{10}/i);
      return match ? match[0].toUpperCase() : "";
    };
    const amazonOptionImageUrl = (option) => {
      const nodes = [option, ...Array.from(option.querySelectorAll?.("img, [style*='background'], [data-src], [data-old-hires], [data-a-hires]") || [])].slice(0, 16);
      for (const node of nodes) {
        const isImageNode = String(node?.tagName || "").toLowerCase() === "img";
        const candidates = [
          isImageNode ? node.getAttribute?.("data-old-hires") : "",
          isImageNode ? node.getAttribute?.("data-a-hires") : "",
          isImageNode ? node.currentSrc : "",
          isImageNode ? node.src : "",
          isImageNode ? srcsetLast(node.getAttribute?.("srcset")) : "",
          node.getAttribute?.("data-src"),
          node.getAttribute?.("data-old-hires"),
          node.getAttribute?.("data-a-hires"),
          node.getAttribute?.("data-image"),
          bgUrl(node),
          inlineStyleImageUrl(node)
        ].filter(Boolean);
        for (const candidate of candidates) {
          const normalized = normalizeAmazonImageUrl(candidate);
          if (amazonImageLooksUsable(normalized)) return normalized;
        }
      }
      return "";
    };
    const amazonVariantGroups = [];
    const seenAmazonGroups = new Set();
    document.querySelectorAll("#twister [id^='variation_'], [id^='variation_']").forEach((container) => {
      const id = String(container.id || container.getAttribute?.("id") || "");
      if (!/^variation_/i.test(id)) return;
      const rawLabel = text(container.querySelector?.(".a-form-label, label")?.innerText || container.querySelector?.(".a-form-label, label")?.textContent || "");
      const rawName = text((rawLabel.split(/\s*[:：]\s*/)[0] || "") || id.replace(/^variation_/i, "").replace(/_/g, " "));
      const name = amazonGroupName(rawName, id);
      if (seenAmazonGroups.has(name)) return;
      const options = [];
      const selectedLabel = selectedFromContainerLabel(container);
      const optionNodes = Array.from(container.querySelectorAll?.("option, li, [role='button'], .a-button") || []);
      for (const option of optionNodes.slice(0, 80)) {
        const value = valueFromAmazonOption(option);
        if (!value || value.length > 80 || /select|choose|see all buying options|add to/i.test(value)) continue;
        const optionAsin = asinFromAmazonOption(option);
        const imageUrl = amazonOptionImageUrl(option);
        const classText = text(`${option.className || ""} ${option.getAttribute?.("class") || ""} ${option.getAttribute?.("aria-checked") || ""} ${option.getAttribute?.("aria-selected") || ""}`);
        const selected = /swatchselect|selected|a-button-selected|true/i.test(classText)
          || (selectedLabel && value.toLowerCase() === selectedLabel.toLowerCase())
          || Boolean(option.selected);
        const optionPrice = text(option.querySelector?.(".a-price .a-offscreen")?.innerText || option.querySelector?.(".a-price .a-offscreen")?.textContent || "").replace(/\s+/g, " ");
        const key = JSON.stringify({ value: value.toLowerCase(), imageUrl, optionAsin });
        if (options.some((item) => JSON.stringify({ value: item.value.toLowerCase(), imageUrl: item.image_url || "", optionAsin: item.source_sku_id || "" }) === key)) continue;
        options.push({
          source_name: rawName || name,
          value,
          image_url: imageUrl,
          price: optionPrice,
          stock: "",
          sku: optionAsin,
          source_sku_id: optionAsin,
          selected,
          selectable: true
        });
      }
      if (!options.length && selectedLabel) {
        options.push({ source_name: rawName || name, value: selectedLabel, image_url: "", price: "", stock: "", sku: "", source_sku_id: "", selected: true, selectable: true });
      }
      if (!options.length) return;
      seenAmazonGroups.add(name);
      amazonVariantGroups.push({ name, source_name: rawName || name, values: options.slice(0, 40) });
    });
    const amazonSelectedAttributes = {};
    const amazonVariantCombinations = [];
    amazonVariantGroups.forEach((group) => {
      (group.values || []).forEach((item) => {
        if (item.selected) amazonSelectedAttributes[group.source_name || group.name] = item.value;
        if (item.source_sku_id || item.image_url || item.price || item.selected) {
          amazonVariantCombinations.push({
            attributes: { [group.source_name || group.name]: item.value },
            price: item.price || "",
            stock: "",
            sku: item.source_sku_id || "",
            source_sku_id: item.source_sku_id || "",
            image_url: item.image_url || "",
            selected: Boolean(item.selected),
            selectable: true,
            source: "amazon-variation-dom",
            confidence: item.source_sku_id || item.image_url ? "medium" : "low"
          });
        }
      });
    });

    const title = amazonTitleCandidates[0]?.value || "";
    const image = amazonImageCandidates[0] || {};
    const canonicalLink = canonicalProductLink(location.href, pageAsin);
    return {
      title,
      category: amazonCategory,
      image_url: image.url || "",
      imageUrl: image.url || "",
      image_urls: amazonProductImageUrls,
      product_image_urls: amazonProductImageUrls,
      price: normalizedAmazonPrice || priceText,
      currency: currencyText || (/\$/.test(priceText) ? "USD" : ""),
      product_link: canonicalLink,
      link: canonicalLink,
      product_id: pageAsin,
      source_product_id: pageAsin,
      sku: pageAsin,
      expected_product_id: expectedId,
      capture_product_id: pageAsin,
      capture_product_id_match: pageProductIdMatch,
      variant_groups: amazonVariantGroups,
      variant_combinations: amazonVariantCombinations.slice(0, 60),
      raw_variant_groups: amazonVariantGroups,
      raw_variant_combinations: amazonVariantCombinations.slice(0, 60),
      selected_attributes: amazonSelectedAttributes,
      source_attributes: sourceAttributes,
      source_attribute_pairs: sourceAttributePairs.slice(0, 80),
      source_attribute_table: sourceAttributeTable.slice(0, 80),
      source: host,
      platform: "amazon",
      captured_fields: {
        specs: Object.entries(sourceAttributes).slice(0, 20).map(([key, value]) => `${key}: ${value}`),
        source_attributes: sourceAttributes,
        source_attribute_pairs: sourceAttributePairs.slice(0, 80),
        source_attribute_table: sourceAttributeTable.slice(0, 80),
        variant_groups: amazonVariantGroups,
        variant_combinations_count: amazonVariantCombinations.length,
        raw_variant_groups_count: amazonVariantGroups.length,
        raw_variant_combinations_count: amazonVariantCombinations.length,
        price_source: normalizedAmazonPrice ? "amazon_dom" : "",
        price_currency: currencyText || (/\$/.test(priceText) ? "USD" : ""),
        price_confidence: normalizedAmazonPrice ? "high" : "",
        price_candidates: normalizedAmazonPrice ? [{ amount: Number(normalizedAmazonPrice.replace(/[^0-9.]/g, "")), currency: currencyText || "USD", value: priceText || normalizedAmazonPrice, source: "amazon_dom", confidence: "high" }] : [],
        document_title: document.title || "",
        page_title: document.title || "",
        title_candidates: amazonTitleCandidates.slice(0, 4),
        image_candidates: amazonImageCandidates.slice(0, 4).map((item) => ({ url: item.url, score: Math.round(item.score), source: item.source, media_type: "image" })),
        product_image_urls: amazonProductImageUrls,
        image_quality_flags: [],
        image_score: Math.round(Number(image.score || 0)),
        capture_url: location.href,
        expected_product_id: expectedId,
        capture_product_id: pageAsin,
        source_product_id: pageAsin,
        capture_product_id_match: pageProductIdMatch,
        category_path: breadcrumbParts,
        platform: "amazon"
      },
      quality: {
        title_ok: Boolean(title),
        image_ok: Boolean(image.url),
        title_source: amazonTitleCandidates[0]?.source || "",
        image_source: image.source || "",
        image_score: Math.round(Number(image.score || 0)),
        image_quality_flags: []
      }
    };
  };
  if (isAmazonHost) return extractAmazonProductFromCurrentPage();
  const serviceTextRe = /官方包退货|官方仓退货|官方退货|包退货|退货包运费|运费险|7天无理由|极速退款|先采后付|跨境铺货|分销代发|已售|已加购|人已加购|起批|库存|客服|收藏|好评|店铺|评价|保障|包邮|发货|物流/i;
  const cleanProductTitle = (value) => text(value)
    .replace(/跨境\s*(?:亚马逊|amazon|temu|tiktok|tik\s*tok|shein|ebay|eBay|速卖通|aliexpress|wish)?/gi, "")
    .replace(/\b(?:Amazon|TEMU|TikTok|Tik\s*Tok|SHEIN|eBay|AliExpress|Wish)\b/gi, "")
    .replace(/(?:亚马逊|特姆|拼多多|抖音|速卖通|希音|虾皮|Shopee|Lazada)/gi, "")
    .replace(/(?:厂家直销|工厂直销|源头厂家|源头工厂|源头直供|源头供应|厂家批发|工厂批发|厂家供应|实力厂家|实力工厂|超级工厂|一件代发|支持代发|跨境专供|外贸专供|外贸爆款|电商爆款)/gi, "")
    .replace(/(?:现货批发|批发定制|来图定制|支持定制|支持ODM|支持OEM|免费贴标|免费拿样|一件起批|混批)/gi, "")
    .replace(/[【】\[\]（）()]/g, " ")
    .replace(/\s+/g, " ")
    .replace(/^[,，、;；:：\s-]+|[,，、;；:：\s-]+$/g, "")
    .trim();
  const companyTitleRe = /(有限公司|有限责任公司|科技有限公司|贸易有限公司|商贸有限公司|塑料厂|模具厂|玩具厂|制品厂|加工厂|工厂|商行|经营部|销售部|营业部)$/;
  const isBadTitle = (value) => {
    if (!value || value.length < 4) return true;
    if (/^(?:1688(?:\.com)?|阿里巴巴|Alibaba|Alibaba\.com|商品详情|商品详情页|详情页)$/i.test(value)) return true;
    if (/^(?:https?:\/\/)?(?:www\.)?(?:1688|alibaba)\.com(?:[/?#].*)?$/i.test(value)) return true;
    if (serviceTextRe.test(value) && value.length <= 90) return true;
    if (value.length <= 50 && (companyTitleRe.test(value) || /厂$/.test(value))) return true;
    if (/^[\u4e00-\u9fa5]{2,8}$/.test(value)) return true;
    if (/有限公司|有限责任公司|经营部|商行|旺铺|诚信通|供应商/.test(value) && value.length <= 60) return true;
    if (/搜索|找本店|首页|购物车|我的订单|客服|官方服务|下裁插件|采购车|消息/.test(value) && value.length <= 80) return true;
    return false;
  };

  const titleCandidates = [];
  const addTitle = (value, score, source) => {
    value = cleanProductTitle(text(value)
      .replace(/\s*[-_].*(1688|阿里巴巴|Alibaba).*$/i, "")
      .replace(/^\s*商品标题[:：]\s*/, "")
      .trim());
    if (isBadTitle(value)) return;
    let adjusted = score + Math.min(value.length / 20, 6);
    if (is1688 && /document\.title|og:title|twitter:title|h1/i.test(source)) adjusted += 4;
    if (value.length >= 12) adjusted += 3;
    if (!pageProductIdMatch) adjusted -= 60;
    titleCandidates.push({
      value: value.slice(0, 180),
      score: adjusted,
      source,
      url: location.href,
      product_id: pageProductId,
      expected_product_id: expectedId,
      product_id_match: pageProductIdMatch
    });
  };
  const titleSegmentStopRe = /商品复购率|已售|热销|新人价|首单|起批|库存|颜色|规格|款式|型号|立即下单|加入采购车|跨境铺货|收藏|客服|店铺|评价|保障|运费|发货|物流/i;
  const titleTextSegments = (value) => {
    const raw = String(value || "").replace(/\r/g, "\n");
    const output = [];
    for (const line of raw.split(/\n+/)) {
      const compact = text(line);
      if (!compact) continue;
      output.push(compact);
      const cut = text(compact.split(titleSegmentStopRe)[0] || "");
      if (cut && cut !== compact) output.push(cut);
    }
    return Array.from(new Set(output)).filter((item) => item.length >= 4 && item.length <= 220);
  };
  const addTitleText = (value, score, source) => {
    const segments = titleTextSegments(value);
    if (!segments.length) {
      addTitle(value, score, source);
      return;
    }
    segments.forEach((segment, index) => addTitle(segment, score - Math.min(index, 2), source));
  };
  const addTitleElement = (element, score, source) => {
    if (!element) return;
    const rect = element.getBoundingClientRect?.() || { width: 0, height: 0, top: 9999 };
    let adjusted = score;
    if (visible(element)) adjusted += 2;
    if (rect.top >= -40 && rect.top < Math.max(window.innerHeight || 800, 600)) adjusted += 2;
    if (rect.width >= 240) adjusted += 1;
    addTitleText(element.innerText || element.textContent || element.getAttribute?.("title") || element.getAttribute?.("aria-label") || "", adjusted, source);
  };
  const addVisibleTextBlockTitles = () => {
    const selectors = [
      "h1",
      "h2",
      "[role='heading']",
      "[data-title]",
      "[data-spm*='title']",
      "[title]",
      "[class*='name']",
      "[class*='subject']",
      "div",
      "span",
      "p",
      "a"
    ].join(",");
    const seen = new Set();
    const elements = Array.from(document.querySelectorAll(selectors)).slice(0, 1200);
    elements.forEach((element) => {
      if (!visible(element)) return;
      const rect = element.getBoundingClientRect?.() || { width: 0, height: 0, top: 9999 };
      if (rect.top < -40 || rect.top > Math.max(window.innerHeight || 800, 800)) return;
      if (rect.width < 120 || rect.height < 12 || rect.height > 260) return;
      const raw = String(element.innerText || element.textContent || element.getAttribute?.("title") || element.getAttribute?.("aria-label") || "").replace(/\r/g, "\n");
      const lines = raw.split(/\n+/).map(text).filter(Boolean);
      if (!lines.length || lines.length > 8) return;
      for (const line of lines) {
        const compact = text(line);
        if (compact.length < 8 || compact.length > 220) continue;
        const key = compact.slice(0, 220);
        if (seen.has(key)) continue;
        seen.add(key);
        let score = 4;
        if (rect.top >= -40 && rect.top < 420) score += 2;
        if (rect.width >= 240) score += 1;
        if (/[¥￥]\s*\d|CA\$|US\$|\$\s*\d/.test(compact)) score -= 4;
        addTitleText(compact, score, "visible_text_block");
      }
    });
  };

  addTitleText(document.querySelector("h1")?.innerText, 12, "h1");
  addTitleText(attr("meta[property='og:title']", "content"), 10, "og:title");
  addTitleText(attr("meta[name='twitter:title']", "content"), 8, "twitter:title");
  addTitleText(attr("meta[name='keywords']", "content"), 6, "keywords");
  [
    ".title-text",
    ".title-content",
    ".main-title",
    ".offer-title",
    ".offer-title-content",
    ".product-title",
    ".product-main-title",
    ".goods-title",
    ".detail-title",
    ".detail-title-content",
    ".d-title",
    ".mod-detail-title",
    "[class*='offer-title']",
    "[class*='product-title']",
    "[class*='goods-title']",
    "[class*='item-title']",
    "[class*='detail-title']",
    "[class*='title']",
    "[class*='subject']",
    "[data-testid*='title']"
  ].forEach((selector) => {
    document.querySelectorAll(selector).forEach((element) => addTitleElement(element, visible(element) ? 7 : 3, selector));
  });
  document.querySelectorAll("[title]").forEach((element) => {
    if (String(element.tagName || "").toUpperCase() === "IMG") return;
    const value = element.getAttribute("title");
    if (value && value.length >= 8) addTitleElement(element, visible(element) ? 6 : 3, "[title]");
  });
  addVisibleTextBlockTitles();
  addTitleText(document.title, 12, "document.title");
  titleCandidates.sort((a, b) => b.score - a.score);

  const imageCandidates = [];
  const titleLooksLikePackagingProduct = (value) => {
    return /pack(?:ag|ing)\s+bag|shipping\s+bag|mail(?:er|ing)\s+bag|poly\s+mailer|courier\s+bag|opp\s+bag|ziplock\s+bag|self\s*seal\s+bag|快递袋|包装袋|打包袋|邮寄袋|物流袋|封口袋|自封袋|opp袋/i.test(String(value || ""));
  };
  const imageQualityFlagsForCandidate = (candidateImage, candidateTitle) => {
    const flags = [];
    const nearby = String(candidateImage?.nearbyText || "");
    const source = String(candidateImage?.source || "");
    const url = String(candidateImage?.url || "");
    const evidence = `${nearby} ${source} ${url}`;
    if (/logo|avatar|sprite|icon|service|guarantee|refund|return|客服|保障|退货|运费险/i.test(evidence)) {
      flags.push("page_service_or_icon_image");
    }
    if (/packaging\s+only|packing\s+only|poly\s+mailer|courier\s+bag|仅包装|只有包装|仅展示包装|只展示包装|包装袋图/i.test(evidence) && !titleLooksLikePackagingProduct(candidateTitle)) {
      flags.push("packaging_only_not_product");
    }
    return Array.from(new Set(flags));
  };
  const elementLooksLikeVideoMedia = (element, nearbyText = "") => {
    if (!element) return false;
    const snippets = [];
    let current = element;
    for (let depth = 0; current && depth < 4; depth += 1) {
      snippets.push(
        current.className || "",
        current.id || "",
        current.getAttribute?.("class") || "",
        current.getAttribute?.("id") || "",
        current.getAttribute?.("data-spm") || "",
        current.getAttribute?.("data-testid") || "",
        current.getAttribute?.("aria-label") || "",
        current.getAttribute?.("title") || ""
      );
      current = current.parentElement || null;
    }
    snippets.push(nearbyText || "");
    const evidence = text(snippets.join(" "));
    if (/video|play|player|poster|movie|media[-_]?video|视频|播放|主图视频|视频封面/i.test(evidence)) return true;
    try {
      const holder = element.closest?.("li, div, a, section") || element;
      if (holder?.querySelector?.("video, [class*='video'], [class*='play'], [aria-label*='播放'], [title*='播放']")) return true;
    } catch (_err) {
      // DOM mocks and unusual pages may not support complex selectors.
    }
    return false;
  };
  const imageUrlLooksUsableForProduct = (url) => {
    const value = text(url);
    if (!/^https?:\/\//i.test(value)) return false;
    if (/\/(?:undefined|null|NaN)(?:[?#/]|$)/i.test(value)) return false;
    if (/(?:logo|avatar|sprite|icon|loading|placeholder|blank)/i.test(value)) return false;
    if (/-2-tps-\d+-\d+\.png(?:[?#]|$)/i.test(value)) return false;
    return /\.(?:jpg|jpeg|png|webp)(?:[?#]|$)/i.test(value);
  };
  const addImage = (url, score, source, element, nearbyText = "") => {
    url = normalizeImageUrl(url);
    if (!url || /\.(svg|gif)(\?|$)/i.test(url)) return;
    if (/logo|avatar|sprite|icon|loading|placeholder/i.test(url)) return;
    if (!imageUrlLooksUsableForProduct(url)) return;
    let adjusted = score;
    const videoCover = elementLooksLikeVideoMedia(element, nearbyText);
    if (videoCover) adjusted -= 45;
    if (/alicdn|cbu01|imgextra|alicdn\.com\/img|alicdn\.com\/bao/i.test(url)) adjusted += 8;
    if (/[_./-](?:30|40|50|60|80|100|120)x(?:30|40|50|60|80|100|120)|(?:icon|service|guarantee|refund|return)/i.test(url)) adjusted -= 18;
    if (serviceTextRe.test(nearbyText)) adjusted -= 30;
    if (element) {
      const rect = element.getBoundingClientRect();
      const area = Math.max(rect.width || element.naturalWidth || 0, 0) * Math.max(rect.height || element.naturalHeight || 0, 0);
      if (area < 6000) adjusted -= 18;
      if (rect.top >= -20 && rect.top < Math.max(window.innerHeight || 800, 600)) adjusted += 6;
      if (rect.width >= 260 && rect.height >= 260) adjusted += 12;
      if (rect.width < 180 || rect.height < 180) adjusted -= 10;
    }
    imageCandidates.push({ url, score: adjusted, source, nearbyText, media_type: videoCover ? "video_cover" : "image", video_cover: videoCover });
  };
  const addImageElement = (element, score) => {
    if (!element) return;
    const source = element.tagName ? `${element.tagName.toLowerCase()}${element.className ? "." + String(element.className).slice(0, 40) : ""}` : "element";
    const nearbyText = text(element.alt || element.title || element.closest("a, li, div")?.innerText || "").slice(0, 120);
    addImage(element.currentSrc || element.src, score + 2, source, element, nearbyText);
    addImage(element.getAttribute("data-src"), score, source, element, nearbyText);
    addImage(element.getAttribute("data-original"), score, source, element, nearbyText);
    addImage(element.getAttribute("data-lazy-src"), score, source, element, nearbyText);
    addImage(element.getAttribute("data-img"), score, source, element, nearbyText);
    addImage(element.getAttribute("data-image"), score, source, element, nearbyText);
    addImage(srcsetLast(element.getAttribute("srcset")), score, source, element, nearbyText);
    addImage(bgUrl(element), score, source, element, nearbyText);
  };
  [
    ".detail-gallery-turn-wrapper img",
    ".detail-gallery-img img",
    ".offer-image img",
    ".mod-detail-gallery img",
    ".detail-gallery img",
    "[class*='main'] img",
    "[class*='preview'] img",
    "[class*='gallery'] img",
    "[class*='image-view'] img",
    "[class*='imageView'] img",
    "[class*='slider'] img",
    "[class*='carousel'] img"
  ].forEach((selector) => {
    document.querySelectorAll(selector).forEach((img) => {
      if (visible(img)) addImageElement(img, is1688 ? 34 : 24);
    });
  });
  addImage(attr("meta[property='og:image']", "content"), 12, "og:image");
  addImage(attr("meta[property='og:image:secure_url']", "content"), 12, "og:image:secure_url");
  addImage(attr("meta[name='twitter:image']", "content"), 8, "twitter:image");
  document.querySelectorAll("img").forEach((img) => {
    const rect = img.getBoundingClientRect();
    const area = Math.max(rect.width || img.naturalWidth || 0, 0) * Math.max(rect.height || img.naturalHeight || 0, 0);
    if (area >= 8000 || visible(img)) addImageElement(img, Math.min(12, 2 + area / 40000));
  });
  document.querySelectorAll("[style*='background-image']").forEach((element) => {
    if (visible(element)) addImage(bgUrl(element), 4, "background-image", element);
  });
  const seenImages = new Set();
  for (let index = imageCandidates.length - 1; index >= 0; index -= 1) {
    const key = imageCandidates[index].url.replace(/([?&])(x-oss-process|image_process|_.+).*$/i, "");
    if (seenImages.has(key)) {
      imageCandidates.splice(index, 1);
      continue;
    }
    seenImages.add(key);
  }
  imageCandidates.sort((a, b) => b.score - a.score);

  const platform = /1688\.com|alibaba\.com/.test(host)
    ? "1688"
    : (/pinduoduo\.com|yangkeduo\.com/.test(host) ? "pinduoduo" : (/temu\.com/.test(host) ? "temu" : "generic"));
  const bodyText = text(document.body?.innerText || "");
  const priceMeta = pick(
    attr("meta[property='product:price:amount']", "content"),
    attr("meta[itemprop='price']", "content")
  );
  const currencyMeta = pick(
    attr("meta[property='product:price:currency']", "content"),
    attr("meta[itemprop='priceCurrency']", "content")
  );
  const normalizeCurrency = (value) => {
    const raw = String(value || "").trim().toUpperCase();
    if (/^(CNY|RMB|¥|￥)$/.test(raw)) return "CNY";
    if (/^(CAD|CA\$|C\$)$/.test(raw)) return "CAD";
    if (/^(USD|US\$|\$)$/.test(raw)) return "USD";
    return "";
  };
  const priceMatches = Array.from(bodyText.matchAll(/(CA\$|C\$|US\$|USD|CAD|CNY|RMB|¥|￥|\$)\s*([0-9]+(?:\.[0-9]{1,2})?)/gi))
    .map((match) => ({ currency: normalizeCurrency(match[1]), value: `${match[1]}${match[2]}`, amount: Number(match[2]) }))
    .filter((item) => item.amount > 0);
  const temuPriceCandidates = platform === "temu"
    ? (() => {
      const priceRoot = document.querySelector("#goods_price");
      const priceRows = Array.from(priceRoot?.children || [])
        .filter(visible)
        .filter((element) => /(?:CA\$|C\$|US\$|USD|CAD|CNY|RMB|¥|￥|\$)\s*\d/i.test(text(element.innerText || element.textContent || "")));
      const directPriceCandidates = priceRows
        .slice(0, 2)
        .flatMap((element) => {
          const rowIndex = priceRows.indexOf(element);
          const numericSpans = Array.from(element.querySelectorAll("span"))
            .filter((span) => !span.querySelector("span"))
            .filter((span) => /\d/.test(text(span.innerText || span.textContent || "")));
          const struck = numericSpans.length > 0 && numericSpans.every((span) => {
            const decoration = `${span.style?.textDecorationLine || ""} ${getComputedStyle(span).textDecorationLine || ""}`;
            return /line-through/i.test(decoration);
          });
          return Array.from(text(element.innerText || element.textContent || "").matchAll(/(CA\$|C\$|US\$|USD|CAD|CNY|RMB|¥|￥|\$)\s*([0-9]+(?:\.[0-9]{1,2})?)/gi))
            .map((match) => ({
              currency: normalizeCurrency(match[1]),
              value: `${match[1]}${match[2]}`,
              amount: Number(match[2]),
              source: "temu_goods_price",
              score: struck || rowIndex === 0 && priceRows.length > 1 ? -100 : 100
            }))
            .filter((item) => item.amount > 0);
        });
      const genericCandidates = Array.from(document.querySelectorAll("[data-testid*='price'], [data-testid*='Price'], [class*='price'], [class*='Price'], del, s, strike"))
      .filter(visible)
      .flatMap((element) => {
        const classText = text(`${element.className || ""} ${element.getAttribute?.("data-testid") || ""}`).toLowerCase();
        const nearbyText = text(`${element.innerText || element.textContent || ""} ${element.parentElement?.innerText || ""}`).slice(0, 240).toLowerCase();
        let score = /sale|discount|deal|flash|promo|current/.test(classText) ? 40 : 10;
        if (/original|market|was|line-through/.test(classText) || /^(DEL|S|STRIKE)$/i.test(String(element.tagName || ""))) score -= 100;
        if (/today\s*pay|today\s*payment|klarna|afterpay|installment|今天支付|分期/.test(nearbyText)) score -= 60;
        return Array.from(text(element.innerText || element.textContent || "").matchAll(/(CA\$|C\$|US\$|USD|CAD|CNY|RMB|¥|￥|\$)\s*([0-9]+(?:\.[0-9]{1,2})?)/gi))
          .map((match) => ({
            currency: normalizeCurrency(match[1]),
            value: `${match[1]}${match[2]}`,
            amount: Number(match[2]),
            source: "temu_price_dom",
            score
          }))
          .filter((item) => item.amount > 0);
      });
      return [...directPriceCandidates, ...genericCandidates];
    })()
    : [];
  const selectedTemuPrice = temuPriceCandidates
    .filter((item) => item.score > 0)
    .sort((left, right) => right.score - left.score)[0] || null;
  const priceFromText = selectedTemuPrice || (priceMatches.length ? priceMatches[0] : null);
  const normalizedPriceMeta = (() => {
    const match = String(priceMeta || "").replace(/,/g, "").match(/([0-9]+(?:\.[0-9]{1,4})?)/);
    return match ? match[1] : "";
  })();
  const normalizedCurrencyMeta = normalizeCurrency(currencyMeta);
  const capturedPrice = selectedTemuPrice
    ? selectedTemuPrice.value
    : (normalizedPriceMeta && Number(normalizedPriceMeta) > 0 && (normalizedCurrencyMeta || platform !== "temu")
    ? `${normalizedCurrencyMeta ? `${normalizedCurrencyMeta} ` : ""}${normalizedPriceMeta}`
    : (priceFromText?.value || ""));
  const capturedCurrency = selectedTemuPrice?.currency || (normalizedPriceMeta && Number(normalizedPriceMeta) > 0 && normalizedCurrencyMeta
    ? normalizedCurrencyMeta
    : (priceFromText?.currency || ""));
  const pricePromoRe = /(?:今天支付|pay\s*today|现价|到手价|新人价|首单价|热销新人价|sale\s*price|current\s*price|price)/i;
  const selected_sku_or_current_price = priceFromText?.source === "selected_sku_or_current_price" ? priceFromText : null;
  const price_confidence = capturedPrice
    ? (selectedTemuPrice ? "high" : (normalizedPriceMeta && normalizedCurrencyMeta ? "meta" : (selected_sku_or_current_price ? "high" : "text")))
    : "";
  const price_candidates = [...temuPriceCandidates, ...priceMatches]
    .slice(0, 12)
    .map((item) => ({
      amount: item.amount,
      currency: item.currency,
      value: item.value,
      source: item.source || (pricePromoRe.test(bodyText) ? "promo_or_page_text" : "page_text"),
      confidence: item === selectedTemuPrice || selected_sku_or_current_price ? "high" : "text"
    }));
  const specTexts = Array.from(document.querySelectorAll("[class*='sku'], [class*='prop'], [class*='attribute'], [class*='spec']"))
    .filter(visible)
    .map((element) => text(element.innerText || element.textContent))
    .filter((value) => value.length >= 2 && value.length <= 160)
    .slice(0, 20);
  const temu_capture_diagnostics = platform === "temu" ? (() => {
    const summarize = (element) => ({
      tag: String(element?.tagName || "").toLowerCase(),
      class_name: text(element?.className || "").slice(0, 160),
      test_id: text(element?.getAttribute?.("data-testid") || "").slice(0, 100),
      text: text(element?.innerText || element?.textContent || "").slice(0, 180),
      child_count: Number(element?.children?.length || 0)
    });
    const priceNodes = Array.from(document.querySelectorAll("[data-testid*='price'], [data-testid*='Price'], [class*='price'], [class*='Price'], del, s, strike"))
      .filter(visible)
      .filter((element) => /(?:CA\$|C\$|US\$|USD|CAD|CNY|RMB|¥|￥|\$)\s*\d/i.test(text(element.innerText || element.textContent || "")))
      .slice(0, 12)
      .map(summarize);
    const modelLabels = Array.from(document.querySelectorAll("span, div, p, label"))
      .filter(visible)
      .filter((element) => text(element.innerText || element.textContent || "") === "手机型号")
      .slice(0, 3)
      .map((element) => summarize(element.parentElement || element));
    return { price_nodes: priceNodes, model_sections: modelLabels };
  })() : {};
  const cleanSpecValue = (value) => text(value)
    .replace(/^(?:\u989c\u8272|\u89c4\u683c|\u6b3e\u5f0f|\u578b\u53f7|\u5bb9\u91cf|\u5c3a\u5bf8|\u5c3a\u7801|\u6570\u91cf|\u5305\u88c5|\u5957\u88c5|Color|Size|Style|Capacity|Pack)[:：\s]*/i, "")
    .replace(/^(颜色|规格|款式|型号|容量|尺寸|尺码|数量|包装|套装|Color|Size|Style|Capacity|Pack)[:：\s]*/i, "")
    .replace(/\s*(已选|请选择|选择|库存|起批|¥|￥|\$).*/i, "")
    .replace(/\s*(?:\u5df2\u9009|\u8bf7\u9009\u62e9|\u9009\u62e9|\u5e93\u5b58|\u8d77\u6279|¥|￥|\$).*/i, "")
    .trim();
  const badSpecValueRe = /官方|退货|客服|收藏|好评|店铺|评价|保障|包邮|发货|物流|起批|库存|已售|加购|选择|请选择|加入|立即|下单|采购车|跨境铺货|分销代发|\u5168\u90e8\u53c2\u6570|\u5546\u54c1\u53c2\u6570|\u4ea7\u54c1\u53c2\u6570|\u89c4\u683c\u53c2\u6570|\u8be6\u7ec6\u53c2\u6570|\u57fa\u672c\u53c2\u6570/i;
  const colorVariantWordRe = /黑色?|白色?|灰色?|银色?|金色?|蓝色?|红色?|绿色?|黄色?|粉色?|紫色?|透明色?|橙色?|棕色?|米白|玫红|black|white|gray|grey|silver|gold|blue|red|green|yellow|pink|purple|clear|orange|brown/i;
  const attributeNoiseNameRe = /attribute|parameter|detail|\u4ea7\u54c1\u5c5e\u6027|\u5546\u54c1\u5c5e\u6027|\u57fa\u672c\u4fe1\u606f|\u8be6\u7ec6\u53c2\u6570|\u54c1\u724c|\u6750\u8d28|\u4ea7\u5730|\u8d27\u53f7|\u6267\u884c\u6807\u51c6|\u9002\u7528\u573a\u666f/i;
  const parameterNoiseNameRe = /all\s*parameters?|product\s*(?:parameters?|attributes?|specifications?)|specification\s*(?:parameters?|details?|table|list)|parameter\s*(?:details?|table|list)|\u5168\u90e8\u53c2\u6570|\u5546\u54c1\u53c2\u6570|\u4ea7\u54c1\u53c2\u6570|\u89c4\u683c\u53c2\u6570|\u8be6\u7ec6\u53c2\u6570|\u57fa\u672c\u53c2\u6570|\u8be6\u60c5\u53c2\u6570|\u4ea7\u54c1\u5c5e\u6027|\u5546\u54c1\u5c5e\u6027/i;
  const parameterNoiseValueRe = /(?:\u5168\u90e8\u53c2\u6570|\u5546\u54c1\u53c2\u6570|\u4ea7\u54c1\u53c2\u6570|\u89c4\u683c\u53c2\u6570|\u8be6\u7ec6\u53c2\u6570|\u57fa\u672c\u53c2\u6570)|(?:\u6750\u8d28|\u54c1\u724c|\u4ea7\u5730|\u8d27\u53f7|\u662f\u5426|\u6267\u884c\u6807\u51c6|\u9002\u7528\u573a\u666f)[\s\S]{0,140}(?:\u662f\u5426|\u6750\u8d28|\u54c1\u724c|\u4ea7\u5730|\u8d27\u53f7|\u529f\u80fd|\u6267\u884c\u6807\u51c6|\u9002\u7528\u573a\u666f)/i;
  const imageEvidenceKey = (url) => normalizeImageUrl(url).replace(/([?&])(x-oss-process|image_process|_.+).*$/i, "").replace(/[?#].*$/, "");
  const variantOptionEvidenceKey = (sourceName, item) => {
    const source = text(sourceName || item?.source_name || item?.name || "").toLowerCase();
    const value = cleanSpecValue(item?.value || item).toLowerCase();
    const image = imageEvidenceKey(item?.image_url || item?.imageUrl || "");
    const sku = text(item?.source_sku_id || item?.sourceSkuId || item?.sku || item?.skuId || "").toLowerCase();
    const propPath = text(item?.propPath || item?.skuAttr || item?.specPath || "").toLowerCase();
    return JSON.stringify({ source, value, image, sku, propPath });
  };
  const variantOptionHasBoundEvidence = (item) => Boolean(
    normalizeImageUrl(item?.image_url || item?.imageUrl || "") ||
    text(item?.price) ||
    text(item?.stock) ||
    text(item?.source_sku_id || item?.sourceSkuId || item?.sku || item?.skuId || item?.sku_id || item?.skuCode || item?.offerSkuId) ||
    text(item?.propPath || item?.salePropPath || item?.skuAttr || item?.specPath)
  );
  const variantOptionHasSalesEvidence = (item) => Boolean(
    variantOptionHasBoundEvidence(item) || item?.selectable
  );
  const variantOptionIsSelectableControl = (element) => {
    if (!element) return false;
    const tag = String(element.tagName || "").toUpperCase();
    const role = text(element.getAttribute?.("role")).toLowerCase();
    const inputType = text(element.getAttribute?.("type")).toLowerCase();
    if (tag === "BUTTON" || tag === "SELECT" || tag === "OPTION") return true;
    if (tag === "INPUT" && /^(?:button|radio|checkbox)$/i.test(inputType)) return true;
    if (/^(?:button|radio|option|tab)$/i.test(role)) return true;
    if (text(element.getAttribute?.("aria-selected")) || text(element.getAttribute?.("aria-checked"))) return true;
    if (Number(element.tabIndex) >= 0) return true;
    if (typeof element.onclick === "function" || text(element.getAttribute?.("onclick"))) return true;
    return false;
  };
  const variantOptionSelector = platform === "temu"
    ? "button, input, option, li, dd, [role='button'], [role='radio'], [role='option'], [class*='item'], [class*='option'], [class*='value'], [data-testid*='sku'], [data-testid*='Sku'], [data-testid*='option'], [data-testid*='Option'], [aria-label]"
    : "button, input, option, li, dd, [role='button'], [role='radio'], [role='option'], [class*='item'], [class*='option'], [class*='value']";
  const isLeafVariantOptionElement = (element) => {
    const nested = Array.from(element.querySelectorAll?.(variantOptionSelector) || [])
      .filter((child) => child !== element && visible(child));
    return nested.length === 0;
  };
  const variantOptionImageUrl = (element) => {
    if (!element) return "";
    const selector = [
      "img",
      "[style*='background']",
      "[data-img]",
      "[data-image]",
      "[data-image-url]",
      "[data-img-url]",
      "[data-src]",
      "[data-original]",
      "[data-lazy-src]",
      "[data-thumb]",
      "[data-url]"
    ].join(",");
    const nodes = [element, ...Array.from(element.querySelectorAll?.(selector) || [])].slice(0, 20);
    for (const node of nodes) {
      const isImageNode = String(node?.tagName || "").toLowerCase() === "img";
      const candidates = [
        isImageNode ? node.currentSrc : "",
        isImageNode ? node.src : "",
        isImageNode ? srcsetLast(node.getAttribute?.("srcset")) : "",
        node.getAttribute?.("src"),
        node.getAttribute?.("data-src"),
        node.getAttribute?.("data-original"),
        node.getAttribute?.("data-lazy-src"),
        node.getAttribute?.("data-img"),
        node.getAttribute?.("data-image"),
        node.getAttribute?.("data-image-url"),
        node.getAttribute?.("data-img-url"),
        node.getAttribute?.("data-thumb"),
        node.getAttribute?.("data-url"),
        bgUrl(node),
        inlineStyleImageUrl(node)
      ].filter(Boolean);
      for (const candidate of candidates) {
        const normalized = normalizeImageUrl(candidate);
        if (imageUrlLooksUsableForProduct(normalized)) return normalized;
      }
    }
    return "";
  };
  const variantGroupLooksLikeParameterNoise = (sourceName, classText = "", fullText = "") => {
    const sample = text(`${sourceName || ""} ${classText || ""} ${String(fullText || "").slice(0, 220)}`);
    return parameterNoiseNameRe.test(sample) || parameterNoiseValueRe.test(sample);
  };
  const looksLikeModelCodeVariantValues = (value) => {
    const sample = text(value);
    if (!sample || colorVariantWordRe.test(sample)) return false;
    if (/件|只|支|个|片|套|装|包|pcs?|pieces?|pack|set|cm|mm|inch|英寸|ml|毫升/i.test(sample)) return false;
    const explicitValues = Array.from(sample.matchAll(/["']value["']\s*:\s*["']([^"']+)["']/gi)).map((match) => match[1]);
    const tokens = explicitValues.length
      ? explicitValues
      : sample.split(/[\s,，;；|/【】\[\]{}()（）"']+/).filter(Boolean);
    const codes = tokens.filter((token) => /^[A-Za-z]?\d{2,5}[A-Za-z]?$/.test(token));
    return codes.length >= 2;
  };
  const normalizeGroupName = (value, fallbackText = "") => {
    const sourceName = text(value);
    const sample = `${sourceName} ${fallbackText || ""}`;
    const hasDimensionEvidence = /\d+(?:\.\d+)?\s*(?:[x×*]\s*\d+(?:\.\d+)?\s*){1,2}(?:cm|mm|inch|in\b|m\b|厘米|公分|毫米|英寸|寸|米)|\d+(?:\.\d+)?\s*(?:cm|mm|inch|in\b|m\b|厘米|公分|毫米|英寸|寸|米)|package\s*(?:size|dimension)|dimensions?|\u5305\u88c5\u5c3a\u5bf8/i.test(sample);
    const hasPackQuantityEvidence = /(?:\d+|[一二两三四五六七八九十]{1,3})\s*(?:pcs?|pieces?|packs?|sets?|kits?|\u4ef6|\u4e2a|\u53ea|\u652f|\u7247|\u5957|\u5305|\u53cc|\u5bf9)(?:\s*(?:\u88c5|set|pack))?/i.test(sample);
    if (hasDimensionEvidence && !hasPackQuantityEvidence) return "Size";
    if (/\u5305\u88c5|\u5957\u88c5|\u6570\u91cf/i.test(sample)) return "Pack";
    if (/\u989c\u8272/i.test(sample)) return "Color";
    if (/\u5bb9\u91cf/i.test(sample)) return "Capacity";
    if (/\u5c3a\u5bf8|\u5c3a\u7801|\u5927\u5c0f/i.test(sample)) return "Size";
    if (/\u89c4\u683c|\u6b3e\u5f0f|\u578b\u53f7/i.test(sample)) return "Style";
    if (looksLikeModelCodeVariantValues(fallbackText) && /color|colour|颜色|色|规格|型号|款式|style|model/i.test(sample)) return "Style";
    if (/pack|pcs?|pieces?|套|装|件|只|支|个|片|数量|包装/i.test(sample)) return hasDimensionEvidence && !hasPackQuantityEvidence ? "Size" : "Pack";
    if (/color|colour|颜色|色/i.test(sample)) return "Color";
    if (/capacity|volume|容量|毫升|\bml\b|\bl\b/i.test(sample)) return "Capacity";
    if (/size|尺寸|尺码|大小|cm|mm|inch|英寸/i.test(sample)) return "Size";
    if (/style|model|款式|型号|款|图案/i.test(sample)) return "Style";
    return "Style";
  };
  const extractVariantGroups = () => {
    const groups = [];
    const seenGroupKeys = new Set();
    const temuVariantContainersFromLabels = () => {
      if (platform !== "temu") return [];
      const labels = Array.from(document.querySelectorAll("span, div, p, label"))
        .filter(visible)
        .filter((element) => /^(颜色|手机型号|规格|尺寸|尺码|款式|Color|Model|Size|Style)[:：]?$/i.test(text(element.innerText || element.textContent || "")));
      const containers = [];
      for (const label of labels) {
        let candidate = label.parentElement;
        for (let depth = 0; candidate && depth < 5; depth += 1, candidate = candidate.parentElement) {
          const controls = Array.from(candidate.querySelectorAll("[role='button'], [role='radio'], [tabindex='0']")).filter(visible);
          if (controls.length >= 2) {
            containers.push(candidate);
            break;
          }
        }
      }
      return containers;
    };
    const genericContainers = Array.from(document.querySelectorAll(
      platform === "temu"
        ? "[class*='sku'], [class*='prop'], [class*='attribute'], [class*='spec']"
        : "[class*='sku'], [class*='prop'], [class*='attribute'], [class*='spec'], [class*='option'], [class*='selector'], [class*='variant']"
    ));
    const temuContainers = platform === "temu"
      ? Array.from(document.querySelectorAll("[data-testid*='sku'], [data-testid*='Sku'], [data-testid*='variant'], [data-testid*='Variant'], [data-testid*='option'], [data-testid*='Option'], [class*='variant'], [class*='Variant'], [class*='option'], [class*='Option']"))
      : [];
    const containers = Array.from(new Set([...genericContainers, ...temuContainers, ...temuVariantContainersFromLabels()]))
      .filter(visible)
      .sort((left, right) => text(left.innerText || left.textContent).length - text(right.innerText || right.textContent).length)
      .slice(0, 40);
    for (const container of containers) {
      const fullText = text(container.innerText || container.textContent);
      if (!fullText || fullText.length > 500 || badSpecValueRe.test(fullText) && fullText.length < 20) continue;
      const saleLabelCount = (fullText.match(/(?:颜色|规格|款式|型号|容量|尺寸|尺码|数量|包装|套装|Color|Size|Style|Capacity|Pack)/g) || []).length;
      if (saleLabelCount > 1 && container.querySelectorAll?.("[class*='sku'], [class*='prop'], [class*='spec']").length > 1) continue;
      const labelText = text(container.querySelector("[class*='title'], [class*='label'], [class*='name'], dt")?.innerText || "");
      const labelMatch = fullText.match(/^(颜色|规格|款式|型号|容量|尺寸|尺码|数量|包装|套装|Color|Size|Style|Capacity|Pack)[:：\s]/i);
      const temuLabel = platform === "temu"
        ? Array.from(container.querySelectorAll("span, div, p, label"))
          .map((element) => text(element.innerText || element.textContent))
          .find((value) => /^(颜色|规格|款式|型号|容量|尺寸|尺码|数量|包装|套装|Color|Size|Style|Capacity|Pack)[:：]?$/i.test(value)) || ""
        : "";
      const sourceName = labelText || temuLabel || (labelMatch ? labelMatch[1] : "");
      const classText = text(`${container.className || ""} ${container.getAttribute?.("class") || ""} ${container.getAttribute?.("data-testid") || ""} ${container.getAttribute?.("data-spm") || ""}`);
      const parameterNoiseGroup = variantGroupLooksLikeParameterNoise(sourceName, classText, fullText);
      const optionElements = Array.from(container.querySelectorAll(variantOptionSelector))
        .filter((element) => visible(element) && isLeafVariantOptionElement(element));
      let values = optionElements
        .map((element) => {
          const value = cleanSpecValue(element.innerText || element.textContent || element.getAttribute("title") || element.getAttribute("aria-label"));
          const imageUrl = variantOptionImageUrl(element);
          const classText = `${element.className || ""} ${element.getAttribute("aria-selected") || ""} ${element.getAttribute("aria-checked") || ""}`;
          const selected = /selected|active|current|checked|true/i.test(classText);
          const price = element.getAttribute("data-price") || element.getAttribute("data-sale-price") || element.getAttribute("data-sku-price") || "";
          const stock = element.getAttribute("data-stock") || element.getAttribute("data-inventory") || "";
          const sku = element.getAttribute("data-sku-id") || element.getAttribute("data-skuid") || element.getAttribute("data-sku") || "";
          const propPath = element.getAttribute("data-prop-path") || element.getAttribute("data-sale-prop-path") || "";
          const skuAttr = element.getAttribute("data-sku-attr") || "";
          const specPath = element.getAttribute("data-spec-path") || "";
          const selectable = variantOptionIsSelectableControl(element) || (
            platform === "temu" && Boolean(sourceName) && Boolean(imageUrl) && colorVariantWordRe.test(value)
          );
          return value ? {
            source_name: sourceName || "",
            value,
            image_url: imageUrl,
            price,
            stock,
            sku,
            source_sku_id: sku,
            propPath,
            skuAttr,
            specPath,
            selected,
            selectable
          } : null;
        })
        .filter((item) => item && item.value.length >= 1 && item.value.length <= 80 && !badSpecValueRe.test(item.value));
      const hasBoundOptionEvidence = values.some(variantOptionHasBoundEvidence);
      let hasSalesOptionEvidence = values.some(variantOptionHasSalesEvidence);
      // Temu/1688 规格选项常是纯文本元素（无价格/库存/SKU 属性且无 selectable 标志），
      // 若容器带规格标签且存在 ≥2 个短规格值，按可选项保留，避免只采到默认单规格。
      // 1688 部分页面规格区没有显式标签文本（如「颜色」只渲染在图标里），此时
      // 只要容器类名带 sku/spec/option 等规格痕迹也按可选项保留。
      const hasSkuClassHint = /sku|spec|prop|option|selector|variant/i.test(
        text(`${container.className || ""} ${container.getAttribute?.("data-spm") || ""} ${container.getAttribute?.("data-testid") || ""}`)
      );
      const bareTextGroup = (platform === "temu" || platform === "1688" || /1688|alibaba/i.test(host))
        && values.length >= 2
        && (Boolean(sourceName) || hasSkuClassHint)
        && values.every((item) => text(item.value).length <= 40);
      if (bareTextGroup && !hasSalesOptionEvidence) {
        values = values.map((item) => ({ ...item, selectable: true, source_name: sourceName || "规格" }));
        hasSalesOptionEvidence = true;
      }
      if (!hasSalesOptionEvidence) continue;
      if (parameterNoiseGroup && !hasBoundOptionEvidence && !bareTextGroup) continue;
      if (attributeNoiseNameRe.test(`${sourceName} ${classText} ${fullText.slice(0, 100)}`) && !hasBoundOptionEvidence && !bareTextGroup) continue;
      const deduped = [];
      const seenValues = new Set();
      for (const item of values) {
        const key = variantOptionEvidenceKey(sourceName || "", item);
        if (seenValues.has(key)) continue;
        seenValues.add(key);
        deduped.push(item);
      }
      if (!deduped.length) continue;
      const name = normalizeGroupName(sourceName, fullText);
      const groupKey = name;
      if (seenGroupKeys.has(groupKey)) continue;
      seenGroupKeys.add(groupKey);
      groups.push({ name, source_name: sourceName || name, values: deduped.slice(0, 50) });
      if (groups.length >= 4) break;
    }
    return groups;
  };

  // 兜底：1688 新版详情页（od-* SPA）部分 SKU 区的容器类名不含 sku/prop/spec
  // 等痕迹（如仅用 flex/od- 前缀），容器级提取会漏掉规格选项（表现为首采有规格、
  // 换页/复采为 0）。这里直接页面级扫描“类名带 sku/sale-prop 的可选项”，按最近
  // 带规格标签的祖先分组；标签缺失时归入“规格”组。
  const pageWideSkuGroups = (() => {
    if (platform !== "1688" && !/1688|alibaba/i.test(host)) return [];
    const optionSelector = [
      "button[class*='sku']",
      "li[class*='sku']",
      "[class*='sku-filter']",
      "[class*='sku-select']",
      "[class*='sku-option']",
      "[class*='sku-item']",
      "[class*='sale-prop']"
    ].join(",");
    const items = [];
    const seenValues = new Set();
    for (const element of Array.from(document.querySelectorAll(optionSelector))) {
      if (!visible(element) || !isLeafVariantOptionElement(element)) continue;
      const value = cleanSpecValue(element.innerText || element.textContent || element.getAttribute("title") || element.getAttribute("aria-label"));
      if (!value || value.length < 1 || value.length > 40 || badSpecValueRe.test(value)) continue;
      if (seenValues.has(value)) continue;
      seenValues.add(value);
      const tagName = String(element.tagName || "").toUpperCase();
      const classText = `${element.className || ""} ${element.getAttribute("aria-selected") || ""} ${element.getAttribute("aria-checked") || ""}`;
      const selected = /selected|active|current|checked|true/i.test(classText);
      const price = element.getAttribute("data-price") || element.getAttribute("data-sale-price") || element.getAttribute("data-sku-price") || "";
      const stock = element.getAttribute("data-stock") || element.getAttribute("data-inventory") || "";
      const sku = element.getAttribute("data-sku-id") || element.getAttribute("data-skuid") || element.getAttribute("data-sku") || "";
      let label = "";
      let strongSkuHint = false;
      let ancestor = element.parentElement;
      for (let depth = 0; ancestor && depth < 5; depth += 1, ancestor = ancestor.parentElement) {
        const ancestorClass = `${ancestor.className || ""}`;
        strongSkuHint = strongSkuHint || /sku-filter|sku-select|sale-prop|sku-option|selector|variant/i.test(ancestorClass);
        const ancestorText = text(ancestor.innerText || ancestor.textContent || "").slice(0, 120);
        const labelMatch = ancestorText.match(/^(颜色|规格|款式|型号|容量|尺寸|尺码|数量|包装|套装|Color|Size|Style|Capacity|Pack)\s*[:：]?\s*(.*)$/i);
        if (labelMatch) {
          label = labelMatch[1];
          break;
        }
        const directChildLabel = Array.from(ancestor.children)
          .map((child) => text(child.innerText || child.textContent || ""))
          .find((childText) => /^(颜色|规格|款式|型号|容量|尺寸|尺码|数量|包装|套装|Color|Size|Style|Capacity|Pack)[:：]?$/i.test(childText));
        if (directChildLabel) {
          label = directChildLabel.replace(/[:：]/g, "");
          break;
        }
      }
      // 只接受“明确是 SKU 选项”的元素：按钮标签、或能找到规格标签、或祖先带
      // 强 SKU 痕迹类名；避免把推荐位/商品卡片等类名带 sku 的元素误当规格值。
      if (!label && !strongSkuHint && tagName !== "BUTTON") continue;
      items.push({
        source_name: label,
        value,
        image_url: variantOptionImageUrl(element),
        price,
        stock,
        sku,
        source_sku_id: sku,
        selected,
        selectable: true
      });
    }
    const grouped = new Map();
    for (const item of items) {
      const name = normalizeGroupName(item.source_name || "规格", item.value);
      const list = grouped.get(name) || [];
      list.push(item);
      grouped.set(name, list);
    }
    const output = [];
    for (const [name, values] of grouped) {
      if (values.length < 2) continue;
      output.push({ name, source_name: name, values: values.slice(0, 50) });
    }
    return output;
  })();

  const compactImageKey = imageEvidenceKey;
  const moneyValue = (value) => {
    if (value == null || value === "") return "";
    if (typeof value === "number" && Number.isFinite(value)) {
      if (value > 10000 && Number.isInteger(value)) return String(Math.round(value) / 100);
      return String(value);
    }
    const match = String(value).replace(/,/g, "").match(/([0-9]+(?:\.[0-9]{1,4})?)/);
    return match ? match[1] : "";
  };
  const firstObjectValue = (object, keys) => {
    if (!object || typeof object !== "object") return "";
    for (const key of keys) {
      if (object[key] != null && object[key] !== "") return object[key];
    }
    return "";
  };
  const imageFromObject = (object, seen = null) => {
    if (!object || typeof object !== "object") return "";
    // Temu 等页面的全局 JS 数据普遍存在循环引用（self/parent 自引用），
    // 必须带环检测，否则 Object.values 递归会无限自调用导致栈溢出。
    const visited = seen || new WeakSet();
    if (visited.has(object)) return "";
    visited.add(object);
    const direct = firstObjectValue(object, ["image_url", "imageUrl", "img", "imgUrl", "picUrl", "skuImageUrl", "skuPicUrl", "thumbUrl", "originalImage", "originImage", "url"]);
    if (direct && /\.(jpg|jpeg|png|webp)(?:[?#].*)?$/i.test(String(direct))) return normalizeImageUrl(direct);
    for (const value of Object.values(object)) {
      if (typeof value === "string" && /\.(jpg|jpeg|png|webp)(?:[?#].*)?$/i.test(value)) return normalizeImageUrl(value);
      if (value && typeof value === "object" && !Array.isArray(value)) {
        const nested = imageFromObject(value, visited);
        if (nested) return nested;
      }
    }
    return "";
  };
  const priceFromObject = (object) => {
    const value = firstObjectValue(object, ["price", "salePrice", "skuPrice", "discountPrice", "offerPrice", "promotionPrice", "priceValue", "priceCent", "priceInCent"]);
    return moneyValue(value);
  };
  const stockFromObject = (object) => {
    const value = firstObjectValue(object, ["stock", "stockNum", "inventory", "quantity", "canBookCount", "availableQuantity"]);
    const parsed = moneyValue(value);
    return parsed ? String(Math.max(0, Math.floor(Number(parsed)))) : "";
  };
  const structuredSourceSkuIdFromObject = (object) => text(firstObjectValue(object, [
    "source_sku_id",
    "sourceSkuId",
    "skuId",
    "skuID",
    "sku_id",
    "skuIdStr",
    "sku_id_str",
    "skuCode",
    "offerSkuId"
  ]));
  const sourceSkuIdFromObject = (object, skuRecordContext = false) => {
    const structured = structuredSourceSkuIdFromObject(object);
    if (structured && (structured !== pageProductId || skuRecordContext)) return structured;
    const plainSku = skuRecordContext ? text(object?.sku) : "";
    return plainSku && plainSku !== pageProductId ? plainSku : "";
  };
  const skuPathFieldsFromObject = (object, fallbackKey = "") => {
    if (!object || typeof object !== "object") return {};
    const fields = {};
    const put = (target, value) => {
      const cleaned = text(value);
      if (cleaned && cleaned !== pageProductId && !fields[target]) fields[target] = cleaned;
    };
    put("propPath", firstObjectValue(object, ["propPath", "salePropPath"]));
    put("skuAttr", firstObjectValue(object, ["skuAttr", "skuAttrs"]));
    put("specPath", firstObjectValue(object, ["specPath", "specKey"]));
    put("props_name", firstObjectValue(object, ["props_name", "propsName", "properties_name", "propertiesName"]));
    const fallback = text(fallbackKey);
    if (!fields.propPath && !fields.skuAttr && fallback && fallback !== pageProductId && !/^\d+$/.test(fallback)) {
      fields.propPath = fallback;
    }
    return fields;
  };
  const variantIdentityPath = (value) => text(value?.propPath || value?.skuAttr || value?.specPath || "");
  const normalizeAttrPair = (name, value, extra = {}) => {
    const cleanValue = cleanSpecValue(value);
    if (!cleanValue || badSpecValueRe.test(cleanValue) || /^全部$/i.test(cleanValue)) return null;
    if (variantGroupLooksLikeParameterNoise(name, "", cleanValue) && !variantOptionHasSalesEvidence(extra)) return null;
    const normalizedName = normalizeGroupName(name || "", cleanValue);
    return { name: normalizedName, source_name: text(name || normalizedName), value: cleanValue, ...extra };
  };
  const attrsFromObject = (object, propValueMap = {}) => {
    const attrs = {};
    if (!object || typeof object !== "object") return attrs;
    // Temu 单轴商品 SKU 项常直接携带纯文本规格（spec: "10 rolls"）。
    if (typeof object.spec === "string") {
      const pair = normalizeAttrPair("spec", object.spec);
      if (pair) attrs[pair.source_name || pair.name] = pair.value;
    }
    if (typeof object.saleSpec === "string") {
      const pair = normalizeAttrPair("spec", object.saleSpec);
      if (pair) attrs[pair.source_name || pair.name] = pair.value;
    }
    const candidates = [
      object.attributes,
      object.attribute,
      object.attrs,
      object.props,
      object.skuProps,
      object.sku_props,
      object.specAttrs,
      object.specs,
      object.properties,
      object.values,
      object.specList
    ];
    for (const candidate of candidates) {
      if (!candidate) continue;
      if (Array.isArray(candidate)) {
        for (const item of candidate) {
          if (!item || typeof item !== "object") continue;
          const name = text(firstObjectValue(item, ["name", "title", "label", "prop", "propName", "propertyName", "attributeName"]));
          const value = text(firstObjectValue(item, ["value", "text", "label", "nameValue", "valueName", "propertyValueName", "attributeValue"]));
          const pair = normalizeAttrPair(name, value, { image_url: imageFromObject(item) });
          if (pair) attrs[pair.source_name || pair.name] = pair.value;
        }
      } else if (typeof candidate === "object") {
        for (const [key, value] of Object.entries(candidate)) {
          const pair = normalizeAttrPair(key, typeof value === "object" ? firstObjectValue(value, ["value", "text", "label", "name", "valueName"]) : value);
          if (pair) attrs[pair.source_name || pair.name] = pair.value;
        }
      }
    }
    const keyText = text(firstObjectValue(object, ["skuAttr", "skuAttrs", "specKey", "specPath", "propPath", "salePropPath"]));
    if (keyText) {
      [keyText, ...keyText.split(/[^A-Za-z0-9_\u4e00-\u9fff]+/).filter(Boolean)].forEach((id) => {
        const mapped = propValueMap[id];
        if (mapped) attrs[mapped.source_name || mapped.name] = mapped.value;
      });
    }
    return attrs;
  };
  const walkObjects = (root, visit, maxDepth = 8, maxNodes = 5000) => {
    const seen = new Set();
    let count = 0;
    const walk = (value, depth, path) => {
      if (!value || typeof value !== "object" || count >= maxNodes || depth > maxDepth || seen.has(value)) return;
      seen.add(value);
      count += 1;
      if (visit(value, path) === false) return;
      const entries = Array.isArray(value) ? value.map((child, index) => [String(index), child]) : Object.entries(value);
      for (const [key, child] of entries) walk(child, depth + 1, [...path, key]);
    };
    walk(root, 0, []);
  };
  const extractBalancedLiteral = (raw, startIndex) => {
    const opener = raw[startIndex];
    const closer = opener === "{" ? "}" : "]";
    let depth = 0;
    let quote = "";
    let escaped = false;
    for (let index = startIndex; index < raw.length; index += 1) {
      const char = raw[index];
      if (quote) {
        if (escaped) {
          escaped = false;
        } else if (char === "\\") {
          escaped = true;
        } else if (char === quote) {
          quote = "";
        }
        continue;
      }
      if (char === "\"" || char === "'") {
        quote = char;
        continue;
      }
      if (char === opener) depth += 1;
      if (char === closer) {
        depth -= 1;
        if (depth === 0) return raw.slice(startIndex, index + 1);
      }
    }
    return "";
  };
  const parseAssignedJson = (raw, marker) => {
    const markerIndex = raw.indexOf(marker);
    if (markerIndex < 0) return null;
    const equalIndex = raw.indexOf("=", markerIndex + marker.length);
    if (equalIndex < 0 || equalIndex - markerIndex > 120) return null;
    const rest = raw.slice(equalIndex + 1);
    const literalOffset = rest.search(/[\[{]/);
    if (literalOffset < 0) return null;
    const literal = extractBalancedLiteral(rest, literalOffset);
    if (!literal || literal.length > 8000000) return null;
    try {
      return JSON.parse(literal);
    } catch (_error) {
      return null;
    }
  };
  const deepParseJson = (value) => {
    let current = value;
    for (let step = 0; step < 3; step += 1) {
      if (typeof current === "string" && /^[\s\r\n]*[\[{]/.test(current)) {
        try {
          current = JSON.parse(current);
        } catch (_error) {
          break;
        }
      } else {
        break;
      }
    }
    return current;
  };
  const jsonSourcesFromPage = () => {
    const sources = [];
    const probe = window.__temuWorkbenchNetworkProbe;
    try {
      const records = probe?.getCaptures?.("product_capture_to_workbench", "", 50) || [];
      records.forEach((record) => {
        if (record?.responseJson) sources.push({ source: `network:${record.endpoint || record.url || ""}`, value: record.responseJson });
      });
    } catch (_error) {}
    // 1688 新版详情页（od-* SPA）的 SKU/价格接口可能不在 URL 过滤规则内，
    // 额外把探针捕获的全部 JSON 响应纳入扫描，由 walkObjects 自行识别规格结构。
    try {
      const allRecords = probe?.getAllJsonCaptures?.(200) || [];
      allRecords.forEach((record) => {
        if (record?.responseJson) sources.push({ source: `network-all:${record.endpoint || record.url || ""}`, value: record.responseJson });
      });
    } catch (_error) {}
    const windowKeys = [
      "__INIT_DATA__",
      "__INITIAL_STATE__",
      "__APOLLO_STATE__",
      "__NEXT_DATA__",
      "__NUXT__",
      "__DATA__",
      "__GLOBAL_DATA__",
      "__PAGE_DATA__",
      "__PAGE_DATA",
      "__page__data",
      "__ICE_APP_CONTEXT__",
      "__STORE_DATA__",
      "FE_GLOBALS",
      "GLOBAL_DATA",
      "offerDetailData",
      "productDetailData",
      "pageData",
      "offerData",
      "globalData",
      "detailData",
      "skuModel"
    ];
    for (const key of windowKeys) {
      try {
        const value = window[key];
        if (value && typeof value === "object") sources.push({ source: `window:${key}`, value });
      } catch (_error) {}
    }
    // 动态发现 window 上其它承载商品/规格数据的键（不依赖固定变量名），
    // 覆盖 1688 等平台随版本变化的全局挂载点；walkObjects 有循环/深度/节点上限保护。
    try {
      const fixed = new Set(windowKeys);
      const dynamicKeys = Object.keys(window)
        .filter((key) => key.length <= 60 && !fixed.has(key) && /sku|offer|goods|detail|product|init|page|global|data|state/i.test(key))
        .slice(0, 80);
      for (const key of dynamicKeys) {
        try {
          const value = window[key];
          if (value && typeof value === "object") sources.push({ source: `window-dynamic:${key}`, value });
        } catch (_error) {}
      }
    } catch (_error) {}
    // 复用上次成功提取的 SKU JSON 数据源：1688 JSONP 响应在探针捕获缓冲滑窗
    // 中可能已被挤出（缓冲上限 200 条），首次采集成功后缓存该 JSON，同页再次
    // 采集即使网络/全局数据丢失也能重新推导完整规格与组合。
    try {
      const cachedSku = window.__workbenchSkuJsonCache;
      if (cachedSku && typeof cachedSku === "string" && cachedSku.length > 0 && cachedSku.length <= 8 * 1024 * 1024) {
        const value = JSON.parse(cachedSku);
        if (value && typeof value === "object") sources.push({ source: "cached-sku", value });
      }
    } catch (_error) {}
    document.querySelectorAll("script[type='application/json'], script").forEach((script, index) => {
      const raw = script.textContent || "";
      if (!/(sku|offer|price|stock|inventory|spec|prop)/i.test(raw) || raw.length > 8000000) return;
      const trimmed = raw.trim();
      try {
        if (/^[\[{]/.test(trimmed)) sources.push({ source: `script:${index}`, value: deepParseJson(JSON.parse(trimmed)) });
      } catch (_error) {}
      [
        "window.__page__data",
        "window.__PAGE_DATA__",
        "window.__PAGE_DATA",
        "window.__GLOBAL_DATA__",
        "window.__INIT_DATA__",
        "window.FE_GLOBALS",
        "window.GLOBAL_DATA",
        "window.offerDetailData",
        "window.productDetailData",
        "window.pageData",
        "window.offerData",
        "window.globalData",
        "window.detailData"
      ].forEach((marker) => {
        const value = parseAssignedJson(raw, marker);
        if (value) sources.push({ source: `script-assignment:${marker}:${index}`, value: deepParseJson(value) });
      });
      const nextMatch = trimmed.match(/<script[^>]*id=["']__NEXT_DATA__["'][^>]*>([\s\S]*?)<\/script>/i);
      if (nextMatch) {
        try { sources.push({ source: `script-next:${index}`, value: deepParseJson(JSON.parse(nextMatch[1])) }); } catch (_error) {}
      }
    });
    // 1688 新版详情页常在超大内联脚本里嵌入完整商品/规格 JSON（依赖不固定的全局
    // 变量名），这里直接在脚本文本中定位规格关键字段并提取所在对象，交给
    // walkObjects 识别 skuProps/skuInfoMap 等结构。
    const embeddedSkuKeys = ["skuProps", "sku_props", "saleProps", "salePropList", "skuInfoMap", "skuMap", "skuInfos", "skuList", "skuItems", "goodsSkus", "skuInfoList", "goodsSkuList", "skuAttrs", "skuAttrList", "skuRecords", "saleSkuList"];
    let embeddedAttempts = 0;
    for (const script of document.querySelectorAll("script")) {
      if (embeddedAttempts >= 12) break;
      const rawText = script.textContent || "";
      if (rawText.length > 8000000 || !/sku|offer|goods/i.test(rawText)) continue;
      let searchFrom = 0;
      while (embeddedAttempts < 12) {
        let nearest = -1;
        let hitKey = "";
        for (const key of embeddedSkuKeys) {
          const found = rawText.indexOf(`"${key}"`, searchFrom);
          if (found >= 0 && (nearest < 0 || found < nearest)) {
            nearest = found;
            hitKey = key;
          }
        }
        if (nearest < 0) break;
        const openObject = rawText.lastIndexOf("{", nearest);
        const openArray = rawText.lastIndexOf("[", nearest);
        const start = openObject >= 0 ? openObject : openArray;
        if (start < 0 || nearest - start > 6000 || rawText.slice(start, nearest).includes(";")) {
          searchFrom = nearest + hitKey.length + 2;
          continue;
        }
        const literal = extractBalancedLiteral(rawText, start);
        if (literal) {
          try {
            const parsed = deepParseJson(JSON.parse(literal));
            if (parsed && typeof parsed === "object") {
              sources.push({ source: `script-embedded:${hitKey}:${index}`, value: parsed });
              embeddedAttempts += 1;
            }
          } catch (_error) {}
        }
        searchFrom = nearest + hitKey.length + 2;
      }
    }
    return sources;
  };
  const cleanSourceAttributeName = (value) => {
    const cleaned = text(value)
      .replace(/^[\s:：-]+|[\s:：-]+$/g, "")
      .replace(/\s+/g, " ");
    if (!cleaned || cleaned.length > 80) return "";
    if (/^(?:key|value|name|label|text|id|sku|skuid|sku id)$/i.test(cleaned)) return "";
    if (/price|stock|inventory|freight|shipping|logistics|coupon|discount|saleprice|sku/i.test(cleaned)) return "";
    if (/价格|库存|现货|可售|起批|已售|销量|运费|物流|快递|优惠|折扣/.test(cleaned)) return "";
    return cleaned;
  };
  const sourceAttributeUnitFromName = (name) => {
    const raw = text(name).toLowerCase();
    if (!raw) return "";
    if (/(容量|容积|capacity|volume)/i.test(raw)) {
      if (/毫升|(?:^|[^a-z])ml(?:$|[^a-z])/i.test(raw)) return "ml";
      if (/升|(?:^|[^a-z])l(?:$|[^a-z])/i.test(raw)) return "L";
      if (/fl\s*oz|盎司/i.test(raw)) return "fl oz";
      if (/oz/i.test(raw)) return "oz";
    }
    if (/(重量|克重|weight)/i.test(raw)) {
      if (/千克|公斤|(?:^|[^a-z])kg(?:$|[^a-z])/i.test(raw)) return "kg";
      if (/克|(?:^|[^a-z])g(?:$|[^a-z])/i.test(raw)) return "g";
    }
    if (/(袖长|长度|宽度|高度|尺寸|length|width|height|sleeve)/i.test(raw)) {
      if (/毫米|(?:^|[^a-z])mm(?:$|[^a-z])/i.test(raw)) return "mm";
      if (/厘米|(?:^|[^a-z])cm(?:$|[^a-z])/i.test(raw)) return "cm";
      if (/英寸|inch|inches|(?:^|[^a-z])in(?:$|[^a-z])/i.test(raw)) return "in";
    }
    if (/(包装数量|件数|数量|package\s*quantity|pack\s*quantity|quantity|count)/i.test(raw)) {
      if (/件|个|只|片|pcs?|pieces?|pack|set/i.test(raw)) return "pcs";
    }
    return "";
  };
  const cleanSourceAttributeValue = (value, name = "") => {
    let cleaned = cleanSpecValue(value)
      .replace(/^[\s:：-]+|[\s:：-]+$/g, "")
      .replace(/\s+/g, " ");
    if (!cleaned || cleaned.length > 140) return "";
    if (/^(?:全部参数|商品参数|产品参数|规格参数|详细参数|基本参数)$/i.test(cleaned)) return "";
    if (/^(?:¥|￥|\$)?\d+(?:\.\d+)?$/.test(cleaned)) {
      const unit = sourceAttributeUnitFromName(name);
      return unit && !/^(?:¥|￥|\$)/.test(cleaned) ? `${cleaned} ${unit}` : "";
    }
    if (/(?:库存|现货|可售|价格|起批|已售|销量|¥|￥|\$)\s*\d|\d+\s*(?:库存|现货|可售|起批|已售|销量)/i.test(cleaned)) return "";
    return cleaned;
  };
  const extractSourceAttributeData = () => {
    const attributes = {};
    const pairs = [];
    const table = [];
    const seen = new Set();
    const addPair = (name, value, source) => {
      const cleanName = cleanSourceAttributeName(name);
      const cleanValue = cleanSourceAttributeValue(value, cleanName);
      if (!cleanName || !cleanValue || cleanName === cleanValue) return;
      const key = cleanName.toLowerCase();
      if (seen.has(key)) return;
      seen.add(key);
      attributes[cleanName] = cleanValue;
      pairs.push({ name: cleanName, value: cleanValue, source });
      table.push({ key: cleanName, value: cleanValue, source });
    };
    const childTexts = (element) => Array.from(element?.children || [])
      .map((child) => text(child.innerText || child.textContent))
      .filter(Boolean);
    const addRowFromCells = (element, source) => {
      const cells = childTexts(element);
      if (cells.length >= 2) addPair(cells[0], cells.slice(1).join(" "), source);
    };
    const parameterSignalRe = /parameter|attribute|property|specification|product\s*detail|产品参数|商品参数|规格参数|详细参数|基本参数|产品属性|商品属性/i;
    const skuContainerRe = /sku|sale[-_]?prop|variation|variant/i;
    const containers = Array.from(document.querySelectorAll("table, tr, dl, ul, ol, [class*='param'], [class*='attribute'], [class*='property'], [class*='specification'], [data-testid*='attribute'], [data-spm*='attribute']"))
      .filter(visible)
      .slice(0, 120);
    for (const container of containers) {
      const tagName = String(container.tagName || "").toUpperCase();
      const classText = text(`${container.className || ""} ${container.getAttribute?.("class") || ""} ${container.getAttribute?.("data-testid") || ""} ${container.getAttribute?.("data-spm") || ""}`);
      const fullText = text(container.innerText || container.textContent);
      const parameterLike = ["TABLE", "TR", "DL"].includes(tagName) || parameterSignalRe.test(`${classText} ${fullText.slice(0, 160)}`);
      if (!parameterLike || skuContainerRe.test(classText) && !parameterSignalRe.test(`${classText} ${fullText.slice(0, 160)}`)) continue;
      if (tagName === "TR") addRowFromCells(container, "dom-parameter-table");
      Array.from(container.querySelectorAll?.("tr") || []).slice(0, 80).forEach((row) => addRowFromCells(row, "dom-parameter-table"));
      const children = Array.from(container.children || []);
      for (let index = 0; index < children.length; index += 1) {
        const child = children[index];
        addRowFromCells(child, "dom-parameter-list");
        const childTag = String(child.tagName || "").toUpperCase();
        if (childTag === "DT" && children[index + 1] && String(children[index + 1].tagName || "").toUpperCase() === "DD") {
          addPair(child.innerText || child.textContent, children[index + 1].innerText || children[index + 1].textContent, "dom-parameter-dl");
        }
      }
      fullText.split(/\n+/).map(text).filter(Boolean).slice(0, 80).forEach((line) => {
        const matched = line.match(/^([^:：]{2,40})[:：]\s*(.{1,140})$/);
        if (matched) addPair(matched[1], matched[2], "dom-parameter-text");
      });
    }
    const attrContainerKeyRe = /attributes?|properties|parameters?|specs?|productProps?|productProperties|productFeatures|featureList|detailList|attrList|propList/i;
    const skuOrCommerceKeyRe = /sku|skuProps?|skuInfo|saleProps?|inventory|stock|price|freight|shipping|logistics|coupon|promotion/i;
    const nameKeys = ["name", "title", "label", "key", "attrName", "attributeName", "propertyName", "propName", "specName"];
    const valueKeys = ["value", "text", "content", "attrValue", "attributeValue", "propertyValue", "propValue", "valueName", "propertyValueName", "specValue"];
    const addFromContainer = (value, source) => {
      if (Array.isArray(value)) {
        value.slice(0, 200).forEach((item) => {
          if (!item || typeof item !== "object") return;
          const name = firstObjectValue(item, nameKeys);
          const attrValue = firstObjectValue(item, valueKeys);
          if (name && attrValue !== "") addPair(name, attrValue, source);
        });
      } else if (value && typeof value === "object") {
        Object.entries(value).slice(0, 200).forEach(([key, item]) => {
          if (item && typeof item === "object") {
            const name = firstObjectValue(item, nameKeys) || key;
            const attrValue = firstObjectValue(item, valueKeys);
            if (name && attrValue !== "") addPair(name, attrValue, source);
          } else {
            addPair(key, item, source);
          }
        });
      }
    };
    for (const source of jsonSourcesFromPage()) {
      walkObjects(source.value, (object) => {
        if (!object || typeof object !== "object" || Array.isArray(object)) return;
        Object.entries(object).forEach(([key, value]) => {
          if (!attrContainerKeyRe.test(key) || skuOrCommerceKeyRe.test(key)) return;
          addFromContainer(value, `${source.source}:${key}`);
        });
      });
    }
    return { attributes, pairs: pairs.slice(0, 80), table: table.slice(0, 80) };
  };
  const propValueMapFromSkuProps = (skuProps) => {
    const map = {};
    const groups = [];
    if (!Array.isArray(skuProps)) return { map, groups };
    for (const prop of skuProps) {
      if (!prop || typeof prop !== "object") continue;
      const sourceName = text(firstObjectValue(prop, ["name", "title", "label", "prop", "propName", "propertyName", "specName", "optionName", "salePropName", "attrName"]));
      const values = firstObjectValue(prop, ["values", "value", "children", "items", "props", "options", "propertyValues", "propValues", "valueList", "specValueList", "optionList", "specValues", "valueItems", "saleValueList"]);
      const groupValues = [];
      const list = Array.isArray(values) ? values : [];
      for (const item of list) {
        if (!item || typeof item !== "object") continue;
        const value = text(firstObjectValue(item, ["value", "text", "label", "name", "valueName", "propertyValueName", "specValueName", "optionValue", "valueText"]));
        const sourceSkuId = sourceSkuIdFromObject(item, true);
        const pair = normalizeAttrPair(sourceName, value, {
          image_url: imageFromObject(item),
          price: priceFromObject(item),
          stock: stockFromObject(item),
          sku: sourceSkuId,
          source_sku_id: sourceSkuId,
          ...skuPathFieldsFromObject(item)
        });
        if (!pair) continue;
        groupValues.push(pair);
        [
          item.id,
          item.valueId,
          item.vid,
          item.fid,
          item.propertyValueId,
          item.specId,
          value
        ].filter(Boolean).forEach((id) => { map[String(id)] = pair; });
      }
      if (groupValues.length > 1 || groupValues.some(variantOptionHasSalesEvidence)) {
        groups.push({ name: normalizeGroupName(sourceName, groupValues.map((item) => item.value)), source_name: sourceName, values: groupValues });
      }
    }
    return { map, groups };
  };
  const imageForAttributes = (attributes, propValueMap) => {
    const pairs = Object.values(propValueMap || {}).filter((pair) => pair && typeof pair === "object" && pair.image_url);
    if (!pairs.length || !attributes || typeof attributes !== "object") return "";
    for (const [name, value] of Object.entries(attributes)) {
      const attrName = text(name).toLowerCase();
      const attrValue = cleanSpecValue(value).toLowerCase();
      if (!attrValue) continue;
      const matched = pairs.find((pair) => {
        const pairValue = cleanSpecValue(pair.value).toLowerCase();
        const pairName = text(pair.source_name || pair.name || "").toLowerCase();
        return pairValue === attrValue && (!attrName || !pairName || pairName === attrName);
      });
      if (matched?.image_url) return normalizeImageUrl(matched.image_url);
    }
    return "";
  };
  const propPairsFromSkuObject = (object, propValueMap) => {
    const pairs = [];
    const seen = new Set();
    const addMapped = (id) => {
      const mapped = propValueMap?.[String(id)];
      if (!mapped) return;
      const key = variantOptionEvidenceKey(mapped.source_name || mapped.name || "", mapped);
      if (seen.has(key)) return;
      seen.add(key);
      pairs.push(mapped);
    };
    const scanIds = (value) => {
      // Temu 全局对象可能带循环引用，必须用 WeakSet 防环，避免无限递归。
      const visited = new WeakSet();
      const scan = (current) => {
        if (Array.isArray(current)) {
          current.forEach(scan);
          return;
        }
        if (current && typeof current === "object") {
          if (visited.has(current)) return;
          visited.add(current);
          Object.values(current).forEach(scan);
          return;
        }
        const raw = text(current);
        [raw, ...raw.split(/[^A-Za-z0-9_\u4e00-\u9fff]+/).filter(Boolean)].filter(Boolean).forEach(addMapped);
      };
      scan(value);
    };
    [
      object?.skuAttr,
      object?.skuAttrs,
      object?.specKey,
      object?.specPath,
      object?.propPath,
      object?.salePropPath,
      object?.propIds,
      object?.propValueIds,
      object?.skuPropIds,
      object?.skuPropValueIds
    ].forEach(scanIds);
    return pairs;
  };
  const imageForSkuObject = (object, attributes, propValueMap) => {
    const matchedById = propPairsFromSkuObject(object, propValueMap).find((pair) => pair?.image_url);
    if (matchedById?.image_url) return normalizeImageUrl(matchedById.image_url);
    return imageForAttributes(attributes, propValueMap);
  };
  const extractJsonVariantData = () => {
    const groups = [];
    const combos = [];
    const sources = jsonSourcesFromPage();
    const contributingSources = new Set();
    for (const source of sources) {
      walkObjects(source.value, (object, path) => {
        if (!object || typeof object !== "object" || Array.isArray(object)) return;
        const skuProps = object.skuProps || object.sku_props || object.saleProps || object.salePropList || object.props || object.specList || object.specs || object.saleSpecs || object.optionList || object.saleOptions || object.saleAttrs || object.saleAttr || object.skuAttrs || object.skuAttrList;
        const mapSource = object.skuInfoMap || object.skuMap || object.skuInfo || object.skuInfos || object.skuList || object.skus || object.skuItems || object.sku_records || object.skuRecords || object.skuRecordList || object.sku_record_list || object.skuInfoList || object.goodsSkuList || object.goodsSkus || object.skuStockList || object.skuSpecs || object.skuSpec || object.saleSkuList;
        const propData = propValueMapFromSkuProps(skuProps);
        if (propData.groups.length) {
          contributingSources.add(source.source);
          groups.push(...propData.groups.map((group) => ({ ...group, source: source.source })));
        }
        if (mapSource && typeof mapSource === "object") {
          const entries = Array.isArray(mapSource) ? mapSource.map((item, index) => [String(index), item]) : Object.entries(mapSource);
          for (const [key, item] of entries) {
            if (!item || typeof item !== "object") continue;
            const attributes = attrsFromObject(item, propData.map);
            if (!Object.keys(attributes).length) {
              String(key).split(/[^A-Za-z0-9_\u4e00-\u9fff]+/).filter(Boolean).forEach((id) => {
                const mapped = propData.map[id];
                if (mapped) attributes[mapped.source_name || mapped.name] = mapped.value;
              });
            }
            if (!Object.keys(attributes).length) continue;
            const sourceSkuId = sourceSkuIdFromObject(item, true);
            const pathFields = skuPathFieldsFromObject(item, key);
            const price = priceFromObject(item);
            const stock = stockFromObject(item);
            const imageUrl = imageFromObject(item) || imageForSkuObject(item, attributes, propData.map) || "";
            if (!sourceSkuId && !variantIdentityPath(pathFields) && !price && !stock && !imageUrl) continue;
            contributingSources.add(source.source);
            combos.push({
              attributes,
              price,
              stock,
              sku: sourceSkuId,
              source_sku_id: sourceSkuId,
              image_url: imageUrl,
              source: source.source,
              confidence: "high",
              ...pathFields
            });
          }
        }
        const genericAttrs = attrsFromObject(object);
        const pathText = (path || []).join(".");
        const skuRecordContext = /(?:^|\.)(?:sku(?:info|infos|map|list|items?|records?|recordlist)?|variants?|combinations?)(?:\.|$)/i.test(pathText);
        const sourceSkuId = sourceSkuIdFromObject(object, skuRecordContext);
        const pathFields = skuPathFieldsFromObject(object);
        const price = priceFromObject(object);
        const stock = stockFromObject(object);
        const imageUrl = imageFromObject(object);
        const hasVariantSignal = Object.keys(genericAttrs).length && (
          sourceSkuId ||
          variantIdentityPath(pathFields) ||
          skuRecordContext && (price || stock || imageUrl)
        );
        if (hasVariantSignal) {
          contributingSources.add(source.source);
          combos.push({
            attributes: genericAttrs,
            price,
            stock,
            sku: sourceSkuId,
            source_sku_id: sourceSkuId,
            image_url: imageUrl,
            source: source.source,
            confidence: "medium",
            ...pathFields
          });
        }
      });
    }
    // 把贡献了规格/组合数据的 JSON 源缓存到页面，供同页后续采集复用
    // （探针捕获缓冲有上限，滑窗会挤掉早期 1688 JSONP 的 SKU 响应）。
    if (contributingSources.size) {
      try {
        const preferred = sources.find((source) => contributingSources.has(source.source)
          && /sku|offer|detail|goods|product|mtop|h5api|laputa/i.test(source.source));
        const best = preferred || sources.find((source) => contributingSources.has(source.source));
        if (best && best.value && typeof best.value === "object") {
          window.__workbenchSkuJsonCache = JSON.stringify(best.value);
        }
      } catch (_error) {}
    }
    return { groups, combos };
  };
  const dedupeGroups = (groups) => {
    const output = [];
    const indexedGroups = new Map();
    for (const group of groups) {
      const sourceName = group.source_name || group.name || "";
      const name = normalizeGroupName(sourceName, JSON.stringify(group.values || []));
      const groupKey = `${text(sourceName || name).toLowerCase()}|${name}`;
      let target = indexedGroups.get(groupKey);
      if (!target) {
        if (output.length >= 4) continue;
        target = { name, source_name: group.source_name || group.name || name, values: [], valueIndex: new Map() };
        indexedGroups.set(groupKey, target);
        output.push(target);
      }
      for (const item of group.values || []) {
        const value = typeof item === "object" ? item.value : item;
        const cleaned = cleanSpecValue(value);
        if (!cleaned || badSpecValueRe.test(cleaned) || /^全部$/i.test(cleaned)) continue;
        const normalized = typeof item === "object"
          ? { ...item, source_name: item.source_name || sourceName || "", value: cleaned, image_url: normalizeImageUrl(item.image_url || item.imageUrl || "") }
          : { source_name: sourceName || "", value: cleaned, image_url: "" };
        const sameValueIndex = target.values.findIndex((existing) => cleanSpecValue(existing.value).toLowerCase() === cleaned.toLowerCase());
        if (sameValueIndex >= 0 && (!variantOptionHasSalesEvidence(normalized) || !variantOptionHasSalesEvidence(target.values[sameValueIndex]))) {
          const existing = target.values[sameValueIndex];
          target.values[sameValueIndex] = {
            ...existing,
            image_url: existing.image_url || normalized.image_url || "",
            price: existing.price || normalized.price || "",
            stock: existing.stock || normalized.stock || "",
            sku: existing.sku || normalized.sku || "",
            selected: Boolean(existing.selected || normalized.selected)
          };
          continue;
        }
        const key = variantOptionEvidenceKey(sourceName || "", normalized);
        if (target.valueIndex.has(key)) {
          const existing = target.values[target.valueIndex.get(key)];
          target.values[target.valueIndex.get(key)] = {
            ...existing,
            image_url: existing.image_url || normalized.image_url || "",
            price: existing.price || normalized.price || "",
            stock: existing.stock || normalized.stock || "",
            sku: existing.sku || normalized.sku || "",
            selected: Boolean(existing.selected || normalized.selected)
          };
          continue;
        }
        target.valueIndex.set(key, target.values.length);
        target.values.push(normalized);
      }
    }
    const finalGroups = output
      .filter((group) => group.values.length)
      .map((group) => ({ name: group.name, source_name: group.source_name, values: group.values.slice(0, 50) }));
    return finalGroups.filter((group, index, list) => {
      if (group.name !== "Color" || group.values.length !== 1) return true;
      const colorValue = cleanSpecValue(group.values[0].value).toLowerCase();
      return !list.some((other, otherIndex) => (
        otherIndex !== index &&
        other.name !== "Color" &&
        (other.values || []).some((item) => cleanSpecValue(item.value).toLowerCase() === colorValue)
      ));
    });
  };
  const variantGroupHasSalesEvidence = (group) => (group.values || []).some(variantOptionHasSalesEvidence);
  const variantGroupHasBoundEvidence = (group) => (group.values || []).some(variantOptionHasBoundEvidence);
  const comboHasSalesEvidence = (combo) => Boolean(
    text(combo?.source_sku_id || combo?.sourceSkuId) ||
    variantIdentityPath(combo) ||
    normalizeImageUrl(combo?.image_url || combo?.imageUrl || "") ||
    text(combo?.price) ||
    text(combo?.stock)
  );
  const variantGroupHasComboEvidence = (group, combos) => {
    const sourceName = text(group?.source_name || group?.name || "").toLowerCase();
    const normalizedName = normalizeGroupName(group?.source_name || group?.name || "", "");
    const values = new Set((group?.values || []).map((item) => cleanSpecValue(typeof item === "object" ? item.value : item).toLowerCase()).filter(Boolean));
    if (!values.size) return false;
    return (combos || []).some((combo) => {
      if (!comboHasSalesEvidence(combo)) return false;
      return Object.entries(combo?.attributes || {}).some(([name, value]) => {
        const attributeName = text(name).toLowerCase();
        const namesMatch = attributeName === sourceName || normalizeGroupName(name, value) === normalizedName;
        return namesMatch && values.has(cleanSpecValue(value).toLowerCase());
      });
    });
  };
  const filterParameterNoiseGroups = (groups, combos = []) => {
    return (groups || []).filter((group) => {
      if (!group || typeof group !== "object") return false;
      const values = Array.isArray(group.values) ? group.values : [];
      if (!values.length) return false;
      const directEvidence = variantGroupHasSalesEvidence(group);
      const comboEvidence = variantGroupHasComboEvidence(group, combos);
      if (!directEvidence && !comboEvidence) return false;
      const valueText = (group.values || [])
        .map((item) => typeof item === "object" ? `${item.source_name || ""} ${item.value || ""}` : String(item || ""))
        .join(" ");
      return !(
        variantGroupLooksLikeParameterNoise(group.source_name || group.name || "", group.source || "", valueText) &&
        !variantGroupHasBoundEvidence(group) &&
        !comboEvidence
      );
    });
  };
  const dedupeCombos = (combos) => {
    const output = [];
    const indexedCombos = new Map();
    for (const combo of combos) {
      const sourceText = String(combo.source || "").toLowerCase();
      if (/receiveaddress|address|logistics|freight|coupon|cart|member|login/.test(sourceText)) continue;
      const attributes = {};
      for (const [name, value] of Object.entries(combo.attributes || {})) {
        const pair = normalizeAttrPair(name, value);
        if (pair) attributes[pair.source_name || pair.name] = pair.value;
      }
      if (!Object.keys(attributes).length) continue;
      const imageUrl = normalizeImageUrl(combo.image_url || combo.imageUrl || "");
      const sourceSkuId = text(combo.source_sku_id || combo.sourceSkuId || structuredSourceSkuIdFromObject(combo));
      const legacySku = text(combo.sku);
      const sku = sourceSkuId || (legacySku && legacySku !== pageProductId ? legacySku : "");
      const pathFields = skuPathFieldsFromObject(combo, combo.key || "");
      const identityPath = variantIdentityPath(pathFields);
      const price = moneyValue(combo.price);
      const stock = stockFromObject(combo);
      if (!sourceSkuId && !identityPath && !imageUrl && !price && !stock && !combo.selectable) continue;
      const attributeEvidence = Object.entries(attributes)
        .map(([name, value]) => ({ source_name: text(name), value: cleanSpecValue(value) }))
        .sort((left, right) => `${left.source_name}:${left.value}`.localeCompare(`${right.source_name}:${right.value}`));
      const key = sourceSkuId
        ? `source-sku:${sourceSkuId.toLowerCase()}`
        : (identityPath
          ? `source-path:${identityPath.toLowerCase()}`
          : JSON.stringify({
              attributes: attributeEvidence,
              image_url: compactImageKey(imageUrl),
              sku: sku.toLowerCase()
            }).toLowerCase());
      const normalized = {
        attributes,
        price,
        stock,
        sku,
        source_sku_id: sourceSkuId,
        image_url: imageUrl,
        selected: Boolean(combo.selected),
        selectable: Boolean(combo.selectable),
        source: combo.source || "page",
        confidence: combo.confidence || "medium",
        ...pathFields
      };
      if (indexedCombos.has(key)) {
        const index = indexedCombos.get(key);
        const existing = output[index];
        const mergedAttributes = { ...existing.attributes };
        const attributeConflicts = Array.isArray(existing.attribute_conflicts) ? [...existing.attribute_conflicts] : [];
        for (const [name, value] of Object.entries(normalized.attributes)) {
          const existingName = Object.keys(mergedAttributes).find((candidate) => candidate.toLowerCase() === name.toLowerCase());
          if (!existingName) {
            mergedAttributes[name] = value;
          } else if (cleanSpecValue(mergedAttributes[existingName]).toLowerCase() !== cleanSpecValue(value).toLowerCase()) {
            attributeConflicts.push({ name: existingName, values: [mergedAttributes[existingName], value] });
          }
        }
        const merged = {
          ...existing,
          attributes: mergedAttributes,
          price: existing.price || normalized.price,
          stock: existing.stock || normalized.stock,
          sku: existing.sku || normalized.sku,
          source_sku_id: existing.source_sku_id || normalized.source_sku_id,
          image_url: existing.image_url || normalized.image_url,
          selected: Boolean(existing.selected || normalized.selected),
          selectable: Boolean(existing.selectable || normalized.selectable),
          confidence: existing.confidence === "high" || normalized.confidence !== "high" ? existing.confidence : normalized.confidence,
          source: existing.source || normalized.source,
          propPath: existing.propPath || normalized.propPath,
          skuAttr: existing.skuAttr || normalized.skuAttr,
          specPath: existing.specPath || normalized.specPath,
          props_name: existing.props_name || normalized.props_name
        };
        if (attributeConflicts.length) merged.attribute_conflicts = attributeConflicts;
        output[index] = merged;
        continue;
      }
      if (output.length >= 200) continue;
      indexedCombos.set(key, output.length);
      output.push(normalized);
    }
    return output;
  };
  const selectedAttributesFromGroups = (groups) => {
    const selected = {};
    for (const group of groups) {
      const item = (group.values || []).find((value) => value && typeof value === "object" && value.selected);
      if (item) selected[group.source_name || group.name] = cleanSpecValue(item.value);
    }
    return selected;
  };
  const buildCombosFromGroups = (groups) => {
    const cartesianProduct = (arrays) => arrays.reduce(
      (acc, list) => acc.flatMap((item) => list.map((value) => [...item, value])),
      [[]]
    );
    const candidateGroups = (groups || [])
      .filter((group) => (group.values || []).some(variantOptionHasSalesEvidence))
      .slice(0, 4);
    if (!candidateGroups.length) return [];
    if (candidateGroups.length === 1) {
      const group = candidateGroups[0];
      return (group.values || [])
        .filter((item) => item && typeof item === "object" && variantOptionHasSalesEvidence(item))
        .map((item) => {
          const sourceSkuId = text(item.source_sku_id || item.sourceSkuId || item.sku || "");
          return {
            attributes: { [group.source_name || group.name]: item.value },
            price: item.price || "",
            stock: item.stock || "",
            sku: sourceSkuId,
            source_sku_id: sourceSkuId,
            image_url: item.image_url || "",
            selected: Boolean(item.selected),
            selectable: Boolean(item.selectable),
            source: "dom-group",
            confidence: item.image_url || item.price || item.stock || sourceSkuId ? "medium" : "low",
            ...skuPathFieldsFromObject(item)
          };
        });
    }
    // 多规格组（如 颜色×度数）时没有逐 SKU 的映射，退化为各规格值的笛卡尔积，
    // 属性组合作为规格身份，价格/库存/货号留空由用户在草稿池补充。
    const valueLists = candidateGroups.map((group) => (group.values || [])
      .filter((item) => item && typeof item === "object")
      .map((item) => ({ group, item })));
    const combos = [];
    for (const combination of cartesianProduct(valueLists)) {
      if (combos.length >= 200) break;
      const attributes = {};
      for (const entry of combination) {
        attributes[entry.group.source_name || entry.group.name] = entry.item.value;
      }
      const firstWithImage = combination.find((entry) => text(entry.item.image_url || ""));
      combos.push({
        attributes,
        price: "",
        stock: "",
        sku: "",
        source_sku_id: "",
        image_url: firstWithImage?.item?.image_url || "",
        selected: false,
        selectable: true,
        source: "dom-group",
        confidence: "low"
      });
    }
    return combos;
  };
  const jsonVariantData = extractJsonVariantData();
  const sourceAttributeData = extractSourceAttributeData();
  const variantGroups = [...extractVariantGroups(), ...pageWideSkuGroups];
  let rawVariantGroups = filterParameterNoiseGroups([...jsonVariantData.groups, ...variantGroups], jsonVariantData.combos);
  let mergedVariantGroups = dedupeGroups(rawVariantGroups);
  const rawVariantCombinationFragments = [
    ...jsonVariantData.combos,
    ...buildCombosFromGroups(mergedVariantGroups)
  ];
  let variantCombinations = dedupeCombos(rawVariantCombinationFragments);
  // 首次采集成功后把结果缓存到页面 window：1688 等 SPA 的 SKU 数据由 JSONP 在
  // 页面加载早期返回，探针捕获缓冲（200 条滑窗）会被后续请求挤出，导致同页第二次
  // 采集 JSON 路径失效；此时回退到上次成功结果，保证重复采集结果稳定。
  const SKU_RESULT_CACHE_KEY = "__workbenchSkuResultCache";
  const skuCacheKey = pageProductId || canonicalProductLink(location.href, "") || location.href;
  let skuCache = {};
  try {
    const cachedRaw = window[SKU_RESULT_CACHE_KEY];
    skuCache = cachedRaw && typeof cachedRaw === "object" ? cachedRaw : {};
  } catch (_error) {}
  const cachedSkuResult = (skuCache && skuCache[skuCacheKey]) || null;
  if (variantCombinations.length || mergedVariantGroups.length) {
    // 本次结果若明显比缓存差（如 JSON 缓冲被挤出，只剩无价格/货号/规格图的
    // DOM 规格），保留并复用上次更完整的缓存结果，避免富数据被劣质结果覆盖。
    const cachedEvidence = cachedSkuResult
      ? (cachedSkuResult.combos || []).filter(comboHasSalesEvidence).length
      : 0;
    const currentEvidence = variantCombinations.filter(comboHasSalesEvidence).length;
    if (cachedSkuResult && cachedEvidence > currentEvidence) {
      variantCombinations = cachedSkuResult.combos || [];
      mergedVariantGroups = cachedSkuResult.groups || [];
      rawVariantGroups = cachedSkuResult.rawGroups || [];
    } else {
      try {
        const entries = Object.entries(skuCache || {}).slice(-4);
        entries.push([skuCacheKey, {
          groups: mergedVariantGroups,
          combos: variantCombinations,
          rawGroups: rawVariantGroups,
          ts: Date.now()
        }]);
        window[SKU_RESULT_CACHE_KEY] = Object.fromEntries(entries);
      } catch (_error) {}
    }
  } else if (cachedSkuResult) {
    // 本次空提取：复用上次成功结果（同页第二次采集、缓冲被挤出等场景）。
    variantCombinations = cachedSkuResult.combos || [];
    mergedVariantGroups = cachedSkuResult.groups || [];
    rawVariantGroups = cachedSkuResult.rawGroups || [];
  }
  const rawVariantCombinations = variantCombinations;
  const selectedAttributes = selectedAttributesFromGroups(mergedVariantGroups);
  const title = titleCandidates[0]?.value || "";
  const image = imageCandidates[0] || {};
  const productImageUrls = imageCandidates
    .filter((item) => item && item.url && item.score >= 20 && item.media_type !== "video_cover" && !item.video_cover && imageUrlLooksUsableForProduct(item.url))
    .map((item) => item.url)
    .filter((url, index, list) => list.indexOf(url) === index)
    .slice(0, 6);
  const imageQualityFlags = imageQualityFlagsForCandidate(image, title);
  const productId = pageProductId;

  return {
    title,
    category: "其他",
    image_url: image.url || "",
    imageUrl: image.url || "",
    image_urls: productImageUrls,
    product_image_urls: productImageUrls,
    price: capturedPrice,
    currency: capturedCurrency,
    product_link: canonicalProductLink(location.href, productId),
    link: canonicalProductLink(location.href, productId),
    product_id: productId,
    source_product_id: productId,
    sku: productId, // Legacy product-level output; sale variants use source_sku_id.
    expected_product_id: expectedId,
    capture_product_id: productId,
    capture_product_id_match: pageProductIdMatch,
    variant_groups: mergedVariantGroups,
    variant_combinations: variantCombinations,
    raw_variant_groups: rawVariantGroups,
    raw_variant_combinations: rawVariantCombinations,
    selected_attributes: selectedAttributes,
    source_attributes: sourceAttributeData.attributes,
    source_attribute_pairs: sourceAttributeData.pairs,
    source_attribute_table: sourceAttributeData.table,
    source: host,
    platform,
    captured_fields: {
      specs: specTexts,
      source_attributes: sourceAttributeData.attributes,
      source_attribute_pairs: sourceAttributeData.pairs,
      source_attribute_table: sourceAttributeData.table,
      variant_groups: mergedVariantGroups,
      variant_combinations_count: variantCombinations.length,
      raw_variant_groups_count: rawVariantGroups.length,
      raw_variant_combinations_count: rawVariantCombinations.length,
      price_source: capturedPrice ? (normalizedPriceMeta && normalizedCurrencyMeta ? "meta" : (selected_sku_or_current_price ? "selected_sku_or_current_price" : "text")) : "",
      price_currency: capturedCurrency,
      price_confidence,
      price_candidates,
      temu_capture_diagnostics,
      document_title: document.title || "",
      page_title: document.title || "",
      title_candidates: titleCandidates.slice(0, 4),
      image_candidates: imageCandidates.slice(0, 4).map((item) => ({ url: item.url, score: Math.round(item.score), source: item.source, media_type: item.media_type || "image" })),
      product_image_urls: productImageUrls,
      image_quality_flags: imageQualityFlags,
      image_score: Math.round(Number(image.score || 0)),
      capture_url: canonicalProductLink(location.href, productId),
      expected_product_id: expectedId,
      capture_product_id: productId,
      source_product_id: productId,
      capture_product_id_match: pageProductIdMatch
    },
    quality: {
      title_ok: Boolean(title),
      image_ok: Boolean(image.url),
      title_source: titleCandidates[0]?.source || "",
      image_source: image.source || "",
      image_score: Math.round(Number(image.score || 0)),
      image_quality_flags: imageQualityFlags
    }
  };
}

async function captureNetworkResponses(command) {
  const tab = await getActiveBusinessTab({ allowAny: false });
  const waitMs = Number(command.payload?.wait_ms || 8000);
  const limit = Number(command.payload?.limit || 50);
  const lookbackMs = Number(command.payload?.lookback_ms || 120000);
  const since = new Date(Date.now() - Math.max(0, Math.min(lookbackMs, 10 * 60 * 1000))).toISOString();

  await injectNetworkProbe(tab.id);

  await delay(Math.max(1000, Math.min(waitMs, 30000)));

  const records = await getProbeCaptures(tab.id, command.command_type, since, limit);

  return {
    command_type: command.command_type,
    url: tab.url || "",
    title: tab.title || "",
    wait_ms: waitMs,
    lookback_ms: lookbackMs,
    matched_count: records.length,
    records,
    capturedAt: new Date().toISOString()
  };
}

async function injectNetworkProbe(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId, allFrames: true },
      files: ["network_probe_utils.js", "page_probe.js"],
      world: "MAIN"
    });
  } catch (_error) {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["network_probe_utils.js", "page_probe.js"],
      world: "MAIN"
    });
  }
}

async function getProbeCaptures(tabId, captureType, since, limit) {
  const execute = async (target) => chrome.scripting.executeScript({
    target,
    world: "MAIN",
    args: [captureType, since, limit],
    func: (captureType, startedAt, maxItems) => {
      const probe = window.__temuWorkbenchNetworkProbe;
      if (!probe?.getCaptures) return [];
      return probe.getCaptures(captureType, startedAt, maxItems);
    }
  });
  let results = [];
  try {
    results = await execute({ tabId, allFrames: true });
  } catch (_error) {
    results = await execute({ tabId });
  }
  const merged = [];
  for (const item of results || []) {
    if (Array.isArray(item?.result)) {
      merged.push(...item.result);
    }
  }
  merged.sort((left, right) => String(left?.capturedAt || "").localeCompare(String(right?.capturedAt || "")));
  return merged.slice(Math.max(0, merged.length - Math.min(limit || 50, 120)));
}

// 检测页面最近被拦截/失败的商品数据请求（诊断“其他插件拦截导致入池失败”）。
async function detectBlockedProductApis(tabId, lookbackMs = 120000) {
  if (!tabId) return [];
  try {
    await injectNetworkProbe(tabId);
  } catch (_error) {
    return [];
  }
  let results = [];
  try {
    results = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      args: [lookbackMs],
      func: (sinceMs) => {
        const probe = window.__temuWorkbenchNetworkProbe;
        const utils = window.WorkbenchNetworkProbeUtils;
        if (!probe?.getBlockedCaptures || !utils?.isProductDataApi) return [];
        return probe.getBlockedCaptures(sinceMs)
          .filter((record) => utils.isProductDataApi(String(record?.url || "")))
          .slice(0, 5);
      }
    });
  } catch (_error) {
    return [];
  }
  const blocked = [];
  for (const item of results || []) {
    if (Array.isArray(item?.result)) blocked.push(...item.result);
  }
  return blocked.slice(0, 3);
}

// 采集失败时若检测到“商品数据请求被其他插件拦截”，生成明确原因提示。
const BLOCKED_BY_EXTENSION_HELP =
  "检测到浏览器里的其他插件（常见为广告拦截类，如 AdBlock、uBlock、AdGuard）拦截了平台加载商品数据的请求，导致采集不到商品。请在浏览器扩展列表里关闭或卸载这些插件（或给 temu.com / 1688.com 添加白名单），刷新页面后重新采集。";

async function blockedByOtherExtensionFailure(tab, statusText) {
  const blockedApis = await detectBlockedProductApis(tab?.id);
  if (!blockedApis.length) return null;
  return {
    ok: false,
    error: "product_api_blocked_by_extension",
    statusText,
    help: BLOCKED_BY_EXTENSION_HELP,
    blocked_product_apis: blockedApis.map((item) => item.url || "")
  };
}

async function getRecentEndpointDiagnostics(tabId, since, limit = 30) {
  const records = await getProbeCaptures(tabId, "temu_endpoint_discovery", since, limit);
  return (records || []).map((record) => {
    const requestJson = record?.requestJson && typeof record.requestJson === "object" && !Array.isArray(record.requestJson)
      ? record.requestJson
      : null;
    return {
      endpoint: record?.endpoint || "",
      method: record?.method || "",
      status: Number(record?.status || 0),
      contentType: record?.contentType || "",
      capturedAt: record?.capturedAt || "",
      has_request_json: Boolean(requestJson),
      request_keys: requestJson ? Object.keys(requestJson).slice(0, 20) : []
    };
  });
}

async function getLatestFluxRequestTemplate(tabId, periodText, timeDimension) {
  const since = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  const records = await getProbeCaptures(tabId, "temu_flux_by_spu", since, 80);
  const candidates = (records || [])
    .filter((record) => String(record?.endpoint || record?.url || "").includes("/api/flow/analysis/list"))
    .filter((record) => record?.requestJson && typeof record.requestJson === "object" && !Array.isArray(record.requestJson))
    .sort((left, right) => String(left.capturedAt || "").localeCompare(String(right.capturedAt || "")));
  if (!candidates.length) return null;

  const matchingPeriod = candidates.filter((record) => Number(extractRequestValue(record, "timeDimension")) === Number(timeDimension));
  const selected = matchingPeriod[matchingPeriod.length - 1] || candidates[candidates.length - 1];
  return {
    requestJson: selected.requestJson,
    capturedAt: selected.capturedAt || "",
    periodText,
    observed_time_dimension: extractRequestValue(selected, "timeDimension") ?? null
  };
}

async function learnFluxRequestTemplate(tabId, periodText, timeDimension) {
  const since = new Date(Date.now() - 2000).toISOString();
  const action = await clickFluxPeriodAndQuery(tabId, periodText);
  if (!action?.ok) {
    return null;
  }
  await delay(8000);
  const records = await getProbeCaptures(tabId, "temu_flux_by_spu", since, 80);
  const candidates = (records || [])
    .filter((record) => String(record?.endpoint || record?.url || "").includes("/api/flow/analysis/list"))
    .filter((record) => record?.requestJson && typeof record.requestJson === "object" && !Array.isArray(record.requestJson))
    .sort((left, right) => String(left.capturedAt || "").localeCompare(String(right.capturedAt || "")));
  if (!candidates.length) return null;

  const matchingPeriod = candidates.filter((record) => Number(extractRequestValue(record, "timeDimension")) === Number(timeDimension));
  const selected = matchingPeriod[matchingPeriod.length - 1] || candidates[candidates.length - 1];
  return {
    requestJson: selected.requestJson,
    capturedAt: selected.capturedAt || "",
    periodText,
    observed_time_dimension: extractRequestValue(selected, "timeDimension") ?? null,
    learned_by: "page_query_click",
    action
  };
}

function isSuccessfulBusinessRecord(record) {
  const json = record?.responseJson;
  const status = Number(record?.status || 0);
  if (status >= 400) return false;
  if (!json || typeof json !== "object") return false;
  if (json.success === false) return false;
  if (json.errorCode && Number(json.errorCode) !== 1000000) return false;
  return Boolean(json.result || json.pageItems || json.items || json.list || json.records);
}

function isRateLimitedRecord(record) {
  if (Number(record?.status || 0) === 429) return true;
  const json = record?.responseJson;
  if (!json || typeof json !== "object") return false;
  return Number(json.errorCode) === 4000004 || /too many visitors|try again later/i.test(String(json.errorMsg || ""));
}

function recordBusinessError(record) {
  const status = Number(record?.status || 0);
  if (status === 403) {
    return "HTTP 403：登录态或页面动态参数失效，请刷新 TEMU 商品流量页后重试";
  }
  if (status === 429) {
    return "HTTP 429：平台限流，已自动降批；仍失败请稍后重试";
  }
  if (status >= 400) {
    return `HTTP ${status}：TEMU 接口返回失败`;
  }
  if (status === 0 && record?.responseText) {
    return `页面请求失败：${String(record.responseText).slice(0, 200)}`;
  }
  const json = record?.responseJson;
  if (json && typeof json === "object") {
    const code = json.errorCode ?? json.code ?? "";
    const message = json.errorMsg || json.message || json.msg || "";
    if (Number(code) === 4000004 || /too many visitors|try again later/i.test(String(message))) {
      return "平台限流，已自动降批；仍失败请稍后重试";
    }
    if (code || message) {
      return `${code ? `错误码 ${code}` : "接口异常"}${message ? `：${message}` : ""}`;
    }
  }
  return String(record?.responseText || "");
}

function buildFluxBatchEvidence({ periodText, batchNumber, batchIds, record, retryCount, requestVerification, coverage }) {
  const json = record?.responseJson && typeof record.responseJson === "object" ? record.responseJson : {};
  const requestMatched = requestVerification?.matched || {};
  return {
    period: periodText,
    batch_index: batchNumber,
    queried_ids: batchIds.length,
    query_ids: batchIds,
    returned_count: coverage.returned_count,
    missing_count: coverage.missing_count,
    missing_ids: coverage.missing_ids,
    error_code: json.errorCode ?? json.code ?? null,
    error_message: json.errorMsg || json.message || json.msg || "",
    time_dimension: requestMatched.time_dimension ?? extractRequestValue(record, "timeDimension") ?? null,
    page_size: requestMatched.page_size ?? extractRequestValue(record, "pageSize") ?? null,
    retry_count: retryCount,
    request_verification: requestVerification,
    coverage
  };
}

function verifyFluxBatchCoverage(record, expectedIds) {
  const expected = new Set((expectedIds || []).map((item) => String(item || "").trim()).filter(Boolean));
  const returned = returnedFluxProductIds(record);
  const missing = Array.from(expected).filter((id) => !returned.has(id));
  return {
    ok: expected.size > 0 && missing.length === 0,
    expected_count: expected.size,
    returned_count: Array.from(returned).filter((id) => expected.has(id)).length,
    missing_count: missing.length,
    missing_ids: missing.slice(0, 50),
    error: missing.length ? `流量接口返回结果缺失 ${missing.length} 个 SPU，不能按 0 流量处理` : ""
  };
}

function isFlowCaptureType(captureType) {
  return captureType === "capture_temu_flux" || captureType === "temu_flux_by_spu";
}

function requestProductIdSet(record) {
  const raw = findNestedValue(record?.requestJson, "productIdList")
    ?? findNestedValue(record?.requestJson, "productIds")
    ?? findNestedValue(record?.requestJson, "spuIds")
    ?? [];
  const values = Array.isArray(raw)
    ? raw
    : String(raw || "").replace(/,/g, " ").split(/\s+/);
  return new Set(values.map((item) => String(item || "").trim()).filter(Boolean));
}

function setsOverlap(left, right) {
  if (!left || !right || left.size === 0 || right.size === 0) return false;
  for (const item of left) {
    if (right.has(item)) return true;
  }
  return false;
}

function filterBatchRecordsForCommand(records, { captureType, periodText, batchIds }) {
  if (!isFlowCaptureType(captureType)) {
    return records || [];
  }
  const expectedIds = new Set((batchIds || []).map((item) => String(item || "").trim()).filter(Boolean));
  return (records || []).filter((record) => {
    const endpointText = `${record?.endpoint || ""} ${record?.url || ""} ${record?.path || ""}`;
    if (!endpointText.includes("/api/flow/analysis/list")) return false;
    const requestIds = requestProductIdSet(record);
    if (!setsOverlap(requestIds, expectedIds)) return false;
    const requestCheck = verifyCapturedRequest(record, periodText, batchIds.length);
    return requestCheck.period_ok;
  });
}

function verifyFluxRecordsCoverage(records, expectedIds) {
  const expected = new Set((expectedIds || []).map((item) => String(item || "").trim()).filter(Boolean));
  const returned = new Set();
  for (const record of records || []) {
    if (!isSuccessfulBusinessRecord(record)) continue;
    for (const id of returnedFluxProductIds(record)) {
      if (expected.has(id)) returned.add(id);
    }
  }
  const missing = Array.from(expected).filter((id) => !returned.has(id));
  return {
    ok: expected.size > 0 && missing.length === 0,
    expected_count: expected.size,
    returned_count: returned.size,
    missing_count: missing.length,
    missing_ids: missing,
    error: missing.length ? `Traffic response is missing ${missing.length} SPU; missing data must not be treated as zero traffic` : ""
  };
}

function firstBusinessError(records) {
  for (const record of records || []) {
    const message = recordBusinessError(record);
    if (message) return message;
  }
  return "";
}

function returnedFluxProductIds(record) {
  const ids = new Set();
  for (const item of responsePageItems(record?.responseJson)) {
    const raw = item?.productId ?? item?.spu ?? item?.spuId;
    const value = String(raw ?? "").trim();
    if (value) ids.add(value);
  }
  return ids;
}

function responsePageItems(responseJson) {
  if (!responseJson || typeof responseJson !== "object") return [];
  const result = responseJson.result && typeof responseJson.result === "object" ? responseJson.result : {};
  const candidates = [
    result.pageItems,
    result.items,
    result.list,
    result.records,
    responseJson.pageItems,
    responseJson.items,
    responseJson.list,
    responseJson.records
  ];
  for (const candidate of candidates) {
    if (Array.isArray(candidate)) {
      return candidate.filter((item) => item && typeof item === "object");
    }
  }
  return [];
}

function verifyCapturedRequestBatch(records, { captureType, periodText, batchSize }) {
  if (captureType !== "capture_temu_flux" && captureType !== "temu_flux_by_spu") {
    return { ok: true, required: false };
  }
  if (!periodText) {
    return { ok: true, required: false };
  }
  if (!records.length) {
    return {
      ok: false,
      error: `没有捕获到 ${periodText} 的流量接口响应，不能确认真实查询周期`,
      periodText,
      batchSize
    };
  }
  const checks = records.map((record) => verifyCapturedRequest(record, periodText, batchSize));
  const matching = checks.find((check) => check.period_ok);
  if (!matching) {
    return {
      ok: false,
      error: `TEMU 真实请求参数没有确认是 ${periodText}`,
      periodText,
      observed: checks.slice(0, 5)
    };
  }
  if (matching.page_size_ok === false) {
    return {
      ok: false,
      error: `TEMU 当前每页条数为 ${matching.page_size || "未知"}，小于本批 ${Math.min(batchSize, 100)} 个 ID，可能漏数`,
      periodText,
      observed: checks.slice(0, 5)
    };
  }
  return { ok: true, periodText, matched: matching };
}

function verifyCapturedRequest(record, periodText, batchSize) {
  const expectedTimeDimension = periodText === "近30日" ? 5 : periodText === "近7日" ? 4 : null;
  const timeDimension = extractRequestValue(record, "timeDimension");
  const pageSize = extractRequestValue(record, "pageSize");
  const periodOk = expectedTimeDimension == null
    || Number(timeDimension) === expectedTimeDimension
    || requestTextIncludesPeriod(record, periodText);
  const requiredPageSize = Math.min(Number(batchSize || 0), 100);
  const numericPageSize = Number(pageSize || 0);
  return {
    period: periodText || "",
    expected_time_dimension: expectedTimeDimension,
    time_dimension: timeDimension ?? null,
    period_ok: Boolean(periodOk),
    page_size: pageSize ?? null,
    page_size_ok: numericPageSize ? numericPageSize >= requiredPageSize : null,
    has_request_body: Boolean(record?.requestJson || record?.requestText)
  };
}

function extractRequestValue(record, key) {
  const found = findNestedValue(record?.requestJson, key);
  if (found !== undefined && found !== null && found !== "") return found;
  const text = String(record?.requestText || "");
  if (!text) return undefined;
  const jsonPattern = new RegExp(`"${key}"\\s*:\\s*"?([^",}\\]]+)`, "i");
  const jsonMatch = text.match(jsonPattern);
  if (jsonMatch) return jsonMatch[1];
  const queryPattern = new RegExp(`${key}=([^&\\s]+)`, "i");
  const queryMatch = text.match(queryPattern);
  return queryMatch ? decodeURIComponent(queryMatch[1]) : undefined;
}

function findNestedValue(value, key) {
  if (!value || typeof value !== "object") return undefined;
  if (Object.prototype.hasOwnProperty.call(value, key)) return value[key];
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findNestedValue(item, key);
      if (found !== undefined) return found;
    }
    return undefined;
  }
  for (const item of Object.values(value)) {
    const found = findNestedValue(item, key);
    if (found !== undefined) return found;
  }
  return undefined;
}

function requestTextIncludesPeriod(record, periodText) {
  const text = `${JSON.stringify(record?.requestJson || {})} ${record?.requestText || ""}`;
  return Boolean(periodText && text.includes(periodText));
}

async function executeMainWorld(tabId, args, pageFunction, options = {}) {
  const attempts = Math.max(1, Number(options.attempts || 2));
  const timeoutMs = Math.max(1000, Number(options.timeoutMs || DEFAULT_MAIN_WORLD_SCRIPT_TIMEOUT_MS));
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const results = await withTimeout(
      chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        args,
        func: pageFunction
      }),
      timeoutMs,
      "page_script_timeout"
    );
    const firstResult = Array.isArray(results) ? results.find((item) => item && "result" in item) : null;
    if (firstResult && firstResult.result !== undefined) {
      return firstResult.result;
    }
    await delay(700);
  }
  return {
    ok: false,
    error: "页面脚本没有返回结果",
    diagnostic: await readPageDiagnostic(tabId)
  };
}

async function readPageDiagnostic(tabId) {
  try {
    const [result] = await withTimeout(
      chrome.scripting.executeScript({
        target: { tabId },
        world: "MAIN",
        func: () => {
          const text = (document.body?.innerText || document.documentElement?.innerText || "").replace(/\s+/g, " ").trim();
          return {
            url: location.href,
            title: document.title,
            readyState: document.readyState,
            textSample: text.slice(0, 500)
          };
        }
      }),
      5000,
      "page_diagnostic_timeout"
    );
    return result?.result || {};
  } catch (error) {
    return { error: String(error?.message || error) };
  }
}

async function findOrOpenBusinessTab(url) {
  const target = new URL(url);
  const tabs = await chrome.tabs.query({ currentWindow: true });
  const existing = tabs.find((tab) => {
    try {
      const parsed = new URL(tab.url || "");
      return parsed.hostname === target.hostname && parsed.pathname === target.pathname;
    } catch (_error) {
      return false;
    }
  });
  if (existing?.id) {
    await chrome.tabs.update(existing.id, { active: true, url });
    return { id: existing.id };
  }
  const created = await chrome.tabs.create({ url, active: true });
  return { id: created.id };
}

async function waitForTabReady(tabId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const tab = await chrome.tabs.get(tabId);
    if (tab.status === "complete") return;
    await delay(500);
  }
}

async function waitForPageText(tabId, requiredTexts, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let lastText = "";
  while (Date.now() < deadline) {
    const [result] = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      args: [requiredTexts],
      func: (texts) => {
        const text = (document.body?.innerText || document.documentElement?.innerText || "").replace(/\s+/g, " ").trim();
        return {
          ok: texts.every((item) => text.includes(item)),
          textSample: text.slice(0, 500),
          url: location.href,
          title: document.title
        };
      }
    });
    const payload = result?.result || {};
    lastText = payload.textSample || lastText;
    if (payload.ok) return payload;
    await delay(700);
  }
  return { ok: false, textSample: lastText };
}

async function clickFluxPeriodAndQuery(tabId, periodText) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [periodText],
    func: (targetPeriod) => {
      function visible(element) {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
      }
      function textOf(element) {
        return (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
      }
      function findByExactText(text, selector) {
        const candidates = Array.from(document.querySelectorAll(selector));
        return candidates.find((element) => visible(element) && textOf(element) === text)
          || candidates.find((element) => visible(element) && textOf(element).includes(text));
      }
      function findPeriodButton(text) {
        const candidates = Array.from(document.querySelectorAll("button, span, div, a, label"))
          .filter((element) => visible(element) && textOf(element) === text);
        const inPeriodGroup = candidates.find((element) => {
          let current = element;
          for (let index = 0; index < 4 && current; index += 1) {
            const blockText = textOf(current);
            if (blockText.includes("实时") && blockText.includes("近1日") && blockText.includes("近7日") && blockText.includes("近30日")) {
              return true;
            }
            current = current.parentElement;
          }
          return false;
        });
        return inPeriodGroup || candidates[0] || findByExactText(text, "button, span, div, a, label");
      }
      function clickElement(element) {
        const target = element.closest("button, a, label") || element;
        target.scrollIntoView({ block: "center", inline: "center" });
        for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
          target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        }
      }
      function periodSnapshot(text) {
        const element = findPeriodButton(text);
        if (!element) return { ok: false, error: `没有找到 ${text} 按钮` };
        const target = element.closest("button, a, label, div, span") || element;
        const style = window.getComputedStyle(target);
        const className = `${target.className || ""} ${target.parentElement?.className || ""}`;
        const ariaSelected = target.getAttribute("aria-selected") || target.parentElement?.getAttribute("aria-selected") || "";
        const selected = ariaSelected === "true"
          || /active|selected|current|checked/i.test(className)
          || /rgb\\(37, 99, 235\\)|rgb\\(22, 119, 255\\)|#1677ff|#2563eb/i.test(`${style.color} ${style.borderColor} ${style.backgroundColor}`);
        return {
          ok: true,
          text,
          className: String(className),
          ariaSelected,
          color: style.color,
          borderColor: style.borderColor,
          backgroundColor: style.backgroundColor,
          selected
        };
      }
      const period = findPeriodButton(targetPeriod);
      if (!period) {
        return { ok: false, error: `没有找到 ${targetPeriod} 按钮` };
      }
      clickElement(period);
      const query = findByExactText("查询", "button, span, div, a");
      if (!query) {
        return { ok: false, error: "没有找到 查询 按钮" };
      }
      clickElement(query);
      return { ok: true, clicked: [targetPeriod, "查询"], url: location.href, title: document.title };
    }
  });
  return result?.result || { ok: false, error: "页面脚本没有返回结果" };
}

async function clickQuery(tabId) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: () => {
      function visible(element) {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
      }
      function textOf(element) {
        return (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
      }
      const candidates = Array.from(document.querySelectorAll("button, span, div, a"));
      const query = candidates.find((element) => visible(element) && textOf(element) === "查询")
        || candidates.find((element) => visible(element) && textOf(element).includes("查询"));
      if (!query) {
        return { ok: false, error: "没有找到 查询 按钮", url: location.href, title: document.title };
      }
      query.click();
      return { ok: true, clicked: ["查询"], url: location.href, title: document.title };
    }
  });
  return result?.result || { ok: false, error: "页面脚本没有返回结果" };
}

async function fillDefaultIdQueryAndClick(tabId, ids, periodText = "", expectedIdLabel = "", targetPageSize = 0, preQueryStatusTab = "") {
  return executeMainWorld(tabId, [ids, periodText, expectedIdLabel, targetPageSize, preQueryStatusTab], async (queryIds, targetPeriod, expectedLabel, requestedPageSize, targetStatusTab) => {
      function visible(element) {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
      }
      function textOf(element) {
        return (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
      }
      function delay(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
      }
      let queryInputAnchor = null;
      function findByExactText(text, selector) {
        const candidates = Array.from(document.querySelectorAll(selector));
        return candidates.find((element) => visible(element) && textOf(element) === text)
          || candidates.find((element) => visible(element) && textOf(element).includes(text));
      }
      function findPeriodGroup(anchorInput = queryInputAnchor) {
        const anchorRect = anchorInput?.getBoundingClientRect?.() || null;
        const candidates = Array.from(document.querySelectorAll("div, section, article, form"))
          .filter((element) => visible(element));
        return candidates
          .map((element) => ({ element, text: textOf(element), rect: element.getBoundingClientRect() }))
          .filter((item) => item.text.includes("实时") && item.text.includes("近1日") && item.text.includes("近7日") && item.text.includes("近30日"))
          .map((item) => {
            let score = 0;
            if (item.text.includes("统计时间")) score += 600;
            if (item.text.includes("商品明细")) score += 180;
            if (item.text.includes("商品ID查询")) score += 120;
            if (anchorRect) {
              const anchorY = anchorRect.top + anchorRect.height / 2;
              const itemY = item.rect.top + item.rect.height / 2;
              const distance = Math.abs(itemY - anchorY);
              if (item.rect.top >= anchorRect.top - 180 && item.rect.top <= anchorRect.bottom + 620) score += 260;
              if (item.rect.bottom >= anchorRect.top && item.rect.top <= anchorRect.bottom + 620) score += 120;
              score -= Math.min(distance, 1400) / 8;
            }
            score -= Math.min(item.text.length, 5000) / 1000;
            score -= Math.min(item.rect.width * item.rect.height, 2000000) / 1000000;
            return { ...item, score };
          })
          .sort((a, b) => (b.score - a.score) || (a.text.length - b.text.length) || (a.rect.width * a.rect.height - b.rect.width * b.rect.height))[0]?.element || null;
      }
      const DETAIL_PERIOD_LABELS = [
        "\u5b9e\u65f6",
        "\u8fd11\u65e5",
        "\u8fd17\u65e5",
        "\u8fd130\u65e5",
        "\u672c\u5468",
        "\u672c\u6708"
      ];
      function containsPeriodCluster(text) {
        const normalized = String(text || "");
        return DETAIL_PERIOD_LABELS.slice(0, 4).every((label) => normalized.includes(label));
      }
      function findPeriodScope(element) {
        let current = element;
        for (let index = 0; index < 8 && current; index += 1) {
          const blockText = textOf(current);
          if (containsPeriodCluster(blockText)) return current;
          current = current.parentElement;
        }
        return null;
      }
      function scorePeriodCandidate(element) {
        const rect = element.getBoundingClientRect();
        const anchorRect = queryInputAnchor?.getBoundingClientRect?.() || null;
        const scope = findPeriodScope(element);
        const scopeText = scope ? textOf(scope) : "";
        const scopeRect = scope?.getBoundingClientRect?.() || rect;
        let score = 0;
        if (scopeText.includes("\u5546\u54c1\u660e\u7ec6")) score += 520;
        if (scopeText.includes("\u5546\u54c1ID\u67e5\u8be2")) score += 420;
        if (scopeText.includes("\u7edf\u8ba1\u65f6\u95f4")) score += 260;
        if (scopeText.includes("\u6c47\u603b\u5206\u6790")) score -= 700;
        if (scopeText.includes("\u8d8b\u52bf\u5206\u6790")) score -= 450;
        if (anchorRect) {
          const anchorY = anchorRect.top + anchorRect.height / 2;
          const centerY = rect.top + rect.height / 2;
          const distanceY = Math.abs(centerY - anchorY);
          if (rect.top >= anchorRect.top - 60) score += 900;
          else score -= 1400;
          if (rect.top <= anchorRect.bottom + 560) score += 340;
          else score -= Math.min(rect.top - anchorRect.bottom, 1600) / 2;
          if (scopeRect.bottom >= anchorRect.top - 120 && scopeRect.top <= anchorRect.bottom + 760) score += 180;
          if (rect.left >= anchorRect.right - 520) score += 180;
          score -= Math.min(distanceY, 1600) / 2;
        }
        score -= Math.min(scopeText.length, 5000) / 1000;
        return { element, score };
      }
      function findPeriodButton(text) {
        const exactCandidates = Array.from(document.querySelectorAll("button, span, div, a, label"))
          .filter((element) => visible(element) && textOf(element) === text);
        const ranked = exactCandidates
          .map((element) => scorePeriodCandidate(element))
          .sort((a, b) => b.score - a.score);
        if (ranked[0] && ranked[0].score > -300) return ranked[0].element;
        const group = findPeriodGroup(queryInputAnchor);
        if (group) {
          const scoped = Array.from(group.querySelectorAll("button, span, div, a, label"))
            .filter((element) => visible(element) && textOf(element) === text);
          if (scoped.length > 0) return scoped[0];
        }
        const candidates = Array.from(document.querySelectorAll("button, span, div, a, label"))
          .filter((element) => visible(element) && textOf(element) === text);
        const inPeriodGroup = candidates.find((element) => {
          let current = element;
          for (let index = 0; index < 4 && current; index += 1) {
            const blockText = textOf(current);
            if (blockText.includes("实时") && blockText.includes("近1日") && blockText.includes("近7日") && blockText.includes("近30日")) {
              return true;
            }
            current = current.parentElement;
          }
          return false;
        });
        return inPeriodGroup || candidates[0] || findByExactText(text, "button, span, div, a, label");
      }
      function findQueryButton(anchorInput) {
        const candidates = Array.from(document.querySelectorAll("button, span, div, a"))
          .filter((element) => visible(element) && textOf(element) === "查询")
          .map((element) => ({ element, rect: element.getBoundingClientRect() }));
        if (!candidates.length) return null;
        const anchorRect = anchorInput?.getBoundingClientRect?.() || null;
        if (!anchorRect) return candidates[0].element;
        const anchorY = anchorRect.top + anchorRect.height / 2;
        const ranked = candidates
          .map((item) => {
            const centerY = item.rect.top + item.rect.height / 2;
            let score = Math.abs(centerY - anchorY) * 3;
            if (item.rect.left >= anchorRect.right - 40) score -= 160;
            if (item.rect.top >= anchorRect.top - 100 && item.rect.top <= anchorRect.bottom + 180) score -= 120;
            if (item.rect.left < anchorRect.left - 80) score += 120;
            return { ...item, score };
          })
          .sort((a, b) => a.score - b.score);
        return ranked[0]?.element || candidates[0].element;
      }
      function clickElement(element) {
        const target = element.closest("button, a, label") || element;
        target.scrollIntoView({ block: "center", inline: "center" });
        for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
          target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        }
      }
      function optionSelectedScore(element) {
        const chain = [
          element,
          element.closest("button, a, label"),
          element.parentElement,
          element.parentElement?.parentElement
        ].filter(Boolean);
        let score = 0;
        const evidence = [];
        for (const target of chain) {
          const style = window.getComputedStyle(target);
          const className = `${target.className || ""}`;
          const ariaSelected = target.getAttribute("aria-selected") || target.getAttribute("aria-checked") || "";
          const dataSelected = target.getAttribute("data-selected") || target.getAttribute("data-active") || "";
          const styleText = `${style.color} ${style.borderColor} ${style.backgroundColor}`;
          if (ariaSelected === "true" || dataSelected === "true") {
            score += 100;
            evidence.push("aria/data selected");
          }
          if (/active|selected|current|checked/i.test(className)) {
            score += 60;
            evidence.push(`class=${className}`);
          }
          if (/rgb\(22, 119, 255\)|rgb\(37, 99, 235\)|#1677ff|#2563eb/i.test(styleText)) {
            score += 20;
            evidence.push(`blue=${styleText}`);
          }
        }
        return { score, evidence };
      }
      function periodSnapshot(text) {
        const element = findPeriodButton(text);
        const group = findPeriodScope(element) || findPeriodGroup(queryInputAnchor);
        const scope = group || document;
        const labels = DETAIL_PERIOD_LABELS;
        const options = labels.map((label) => {
          const optionElement = Array.from(scope.querySelectorAll("button, span, div, a, label"))
            .find((candidate) => visible(candidate) && textOf(candidate) === label);
          if (!optionElement) return null;
          const selected = optionSelectedScore(optionElement);
          return { text: label, score: selected.score, evidence: selected.evidence };
        }).filter(Boolean);
        const selectedOption = options.slice().sort((a, b) => b.score - a.score)[0] || null;
        if (!element) return { ok: false, error: `没有找到 ${text} 按钮`, options };
        return {
          ok: true,
          text,
          selected: Boolean(selectedOption && selectedOption.text === text && selectedOption.score > 0),
          selectedText: selectedOption?.score > 0 ? selectedOption.text : "",
          options
        };
      }
      function findPageSizeControl(targetSize) {
        const targetText = String(targetSize || 100);
        const pageLabels = Array.from(document.querySelectorAll("span, div, label"))
          .filter((element) => visible(element) && textOf(element).includes("每页"));
        const numeric = Array.from(document.querySelectorAll("button, span, div, a, input"))
          .filter((element) => visible(element))
          .map((element) => {
            const text = element.tagName === "INPUT" ? String(element.value || "") : textOf(element);
            return { element, text, rect: element.getBoundingClientRect() };
          })
          .filter((item) => /^(5|10|20|30|40|50|80|100|200|500)$/.test(item.text));
        if (pageLabels.length > 0) {
          const labelRect = pageLabels[pageLabels.length - 1].getBoundingClientRect();
          const nearby = numeric
            .filter((item) => Math.abs((item.rect.top + item.rect.height / 2) - (labelRect.top + labelRect.height / 2)) < 80)
            .sort((a, b) => {
              if (a.text === targetText && b.text !== targetText) return -1;
              if (b.text === targetText && a.text !== targetText) return 1;
              return Math.abs(a.rect.left - labelRect.right) - Math.abs(b.rect.left - labelRect.right);
            })[0];
          if (nearby) return nearby;
        }
        return numeric
          .filter((item) => item.rect.top > window.innerHeight * 0.45 && item.rect.left > window.innerWidth * 0.55)
          .sort((a, b) => {
            if (a.text === targetText && b.text !== targetText) return -1;
            if (b.text === targetText && a.text !== targetText) return 1;
            return b.rect.top - a.rect.top || b.rect.left - a.rect.left;
          })[0] || null;
      }
      async function selectTargetPageSize(targetSize) {
        const targetText = String(targetSize || 100);
        const originalX = window.scrollX;
        const originalY = window.scrollY;

        async function restoreScroll() {
          window.scrollTo({ left: originalX, top: originalY, behavior: "auto" });
          await delay(300);
        }

        async function selectNative(scrolled) {
          const nativeSelect = Array.from(document.querySelectorAll("select"))
            .filter(visible)
            .find((select) => Array.from(select.options || []).some((option) => option.textContent?.trim() === targetText || option.value === targetText));
          if (!nativeSelect) return null;
          const option = Array.from(nativeSelect.options).find((item) => item.textContent?.trim() === targetText || item.value === targetText);
          if (option && nativeSelect.value !== option.value) {
            nativeSelect.value = option.value;
            nativeSelect.dispatchEvent(new Event("input", { bubbles: true }));
            nativeSelect.dispatchEvent(new Event("change", { bubbles: true }));
            await delay(700);
          }
          return { ok: true, method: "select", selectedText: targetText, scrolled };
        }

        async function selectCustom(scrolled) {
          const control = findPageSizeControl(targetSize);
          if (!control) return null;
          if (control.text === targetText) {
            return { ok: true, method: "custom", selectedText: targetText, already: true, scrolled };
          }
          clickElement(control.element);
          await delay(450);
          const option = Array.from(document.querySelectorAll("button, span, div, a, li"))
            .filter((element) => visible(element) && textOf(element) === targetText)
            .map((element) => ({ element, rect: element.getBoundingClientRect() }))
            .sort((a, b) => b.rect.top - a.rect.top || b.rect.left - a.rect.left)[0];
          if (!option) {
            return { ok: false, error: `已打开每页条数控件，但没有找到 ${targetText} 选项`, before: control.text, scrolled };
          }
          clickElement(option.element);
          await delay(900);
          return { ok: true, method: "custom", before: control.text, selectedText: targetText, scrolled };
        }

        let result = await selectNative(false) || await selectCustom(false);
        if (!result || result.ok === false) {
          const scrollTarget = Math.max(
            document.documentElement?.scrollHeight || 0,
            document.body?.scrollHeight || 0
          );
          window.scrollTo({ left: originalX, top: scrollTarget, behavior: "auto" });
          await delay(700);
          result = await selectNative(true) || await selectCustom(true);
        }
        await restoreScroll();
        return result || {
          ok: false,
          skipped: true,
          error: `没有找到每页条数控件；请确认列表底部可见，并把每页切到 ${targetText} 后重试`,
          scrolled: true
        };
      }
      async function selectOrderStatusTab(tabText) {
        const target = String(tabText || "").trim();
        if (!target) return { ok: true, skipped: true };
        const statusLabels = [
          "\u5168\u90e8",
          "\u5e73\u53f0\u5904\u7406\u4e2d",
          "\u5f85\u53d1\u8d27",
          "\u5df2\u53d1\u8d27",
          "\u5df2\u7b7e\u6536",
          "\u5df2\u53d6\u6d88"
        ];
        const targetPattern = new RegExp(`^${target}(?:\\(\\d+\\))?$`);
        const anchorRect = queryInputAnchor?.getBoundingClientRect?.() || null;
        const candidates = Array.from(document.querySelectorAll("button, span, div, a, label"))
          .filter((element) => visible(element) && targetPattern.test(textOf(element)))
          .map((element) => {
            const rect = element.getBoundingClientRect();
            let current = element;
            let bestScope = element;
            let bestScore = -9999;
            for (let depth = 0; depth < 8 && current; depth += 1) {
              const scopeText = textOf(current);
              const labelHits = statusLabels.filter((label) => scopeText.includes(label)).length;
              let score = labelHits * 180;
              if (scopeText.includes("\u5546\u54c1ID\u67e5\u8be2")) score += 80;
              if (scopeText.includes("\u5356\u5bb6\u5c65\u7ea6\u8ba2\u5355")) score -= 420;
              if (scopeText.includes("\u5408\u4f5c\u5bf9\u63a5\u4ed3\u5c65\u7ea6\u8ba2\u5355")) score -= 260;
              if (scopeText.includes("\u5168\u90e8\u8ba2\u5355")) score -= 260;
              if (anchorRect) {
                if (rect.top >= anchorRect.bottom) score += 260;
                else score -= 260;
                score -= Math.min(Math.abs(rect.top - anchorRect.bottom), 900) / 4;
              }
              if (score > bestScore) {
                bestScore = score;
                bestScope = current;
              }
              current = current.parentElement;
            }
            return { element, rect, scope: bestScope, score: bestScore, text: textOf(element) };
          })
          .sort((a, b) => b.score - a.score);
        const selected = candidates[0];
        if (!selected || selected.score < 250) {
          return {
            ok: false,
            error: `No reliable order status tab found for ${target}`,
            candidates: candidates.slice(0, 5).map((item) => ({ text: item.text, score: item.score }))
          };
        }
        const selectedState = optionSelectedScore(selected.element);
        if (selectedState.score <= 0) {
          clickElement(selected.element);
          await delay(700);
        }
        return {
          ok: true,
          selectedText: selected.text,
          score: selected.score,
          already: selectedState.score > 0
        };
      }
      async function selectPageSize100() {
        return selectTargetPageSize(100);
      }
      function setNativeValue(element, value) {
        const setter = Object.getOwnPropertyDescriptor(element.__proto__, "value")?.set;
        const prototype = Object.getPrototypeOf(element);
        const prototypeSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
        const valueSetter = setter || prototypeSetter;
        if (valueSetter) {
          valueSetter.call(element, value);
        } else {
          element.value = value;
        }
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
      }
      function uniqueIdTypes(text) {
        const matches = String(text || "").toUpperCase().match(/\b(SPU|SKU|SKC)\b/g) || [];
        return Array.from(new Set(matches));
      }
      function idTypeCandidateText(element) {
        if (!element) return "";
        if (element.tagName === "SELECT") {
          return element.selectedOptions?.[0]?.textContent || element.value || textOf(element);
        }
        return textOf(element);
      }
      function findNearbyIdTypeControl(input) {
        const inputRect = input.getBoundingClientRect();
        const inputCenterY = inputRect.top + inputRect.height / 2;
        const candidates = Array.from(document.querySelectorAll("select, button, span, div, a, label"))
          .filter((element) => {
            if (!visible(element) || element === input) return false;
            const text = idTypeCandidateText(element);
            const types = uniqueIdTypes(text);
            if (types.length === 0) return false;
            if (text.length > 80) return false;
            const rect = element.getBoundingClientRect();
            const centerY = rect.top + rect.height / 2;
            const sameRow = Math.abs(centerY - inputCenterY) <= Math.max(48, inputRect.height * 2);
            const nearLeft = rect.left <= inputRect.left + 24 && rect.right >= inputRect.left - 320;
            return sameRow && nearLeft;
          })
          .map((element) => {
            const rect = element.getBoundingClientRect();
            const centerY = rect.top + rect.height / 2;
            return {
              element,
              text: idTypeCandidateText(element),
              types: uniqueIdTypes(idTypeCandidateText(element)),
              score: Math.abs((inputRect.left - rect.right)) + Math.abs(centerY - inputCenterY) * 3
            };
          })
          .sort((a, b) => a.score - b.score);
        return candidates[0] || null;
      }
      async function chooseExpectedIdType(candidate, expected) {
        const normalizedExpected = String(expected || "").toUpperCase();
        const control = candidate?.element;
        if (!control || !normalizedExpected) return { ok: false, error: "没有可切换的商品 ID 类型控件" };
        if (control.tagName === "SELECT") {
          const option = Array.from(control.options || []).find((item) => {
            const text = `${item.textContent || ""} ${item.value || ""}`.toUpperCase();
            return uniqueIdTypes(text).includes(normalizedExpected) || text.includes(normalizedExpected);
          });
          if (!option) return { ok: false, error: `下拉框里没有 ${normalizedExpected}` };
          control.value = option.value;
          control.dispatchEvent(new Event("input", { bubbles: true }));
          control.dispatchEvent(new Event("change", { bubbles: true }));
          await delay(500);
          return { ok: true, method: "select", selectedText: option.textContent || option.value };
        }
        const rect = control.getBoundingClientRect();
        clickElement(control);
        await delay(450);
        const options = Array.from(document.querySelectorAll("button, span, div, a, li, label"))
          .filter((element) => {
            if (!visible(element)) return false;
            const text = idTypeCandidateText(element).toUpperCase();
            const types = uniqueIdTypes(text);
            return types.includes(normalizedExpected) || text.includes(normalizedExpected);
          })
          .map((element) => {
            const optionRect = element.getBoundingClientRect();
            return {
              element,
              text: idTypeCandidateText(element),
              score: Math.abs((optionRect.left + optionRect.width / 2) - (rect.left + rect.width / 2))
                + Math.max(0, optionRect.top - rect.bottom)
                + Math.abs((optionRect.top + optionRect.height / 2) - (rect.top + rect.height / 2)) / 4
            };
          })
          .sort((a, b) => a.score - b.score);
        if (!options[0]) return { ok: false, error: `已打开商品 ID 类型控件，但没有找到 ${normalizedExpected} 选项` };
        clickElement(options[0].element);
        await delay(700);
        return { ok: true, method: "custom", selectedText: options[0].text };
      }
      async function ensureExpectedIdType(input, expected) {
        const normalizedExpected = String(expected || "").toUpperCase();
        if (!normalizedExpected) return { ok: true, expected: "", source: "not_required" };
        const candidate = findNearbyIdTypeControl(input);
        if (!candidate) {
          return {
            ok: false,
            expected: normalizedExpected,
            error: `无法确认商品 ID 查询条件是 ${normalizedExpected}`,
            help: `请在当前页面把商品 ID 查询下拉框切换为 ${normalizedExpected} 后重试。`
          };
        }
        const selectedText = candidate.text;
        const types = candidate.types;
        if (types.length === 1 && types[0] === normalizedExpected) {
          return {
            ok: true,
            expected: normalizedExpected,
            selectedText,
            source: candidate.element.tagName
          };
        }
        const switchResult = await chooseExpectedIdType(candidate, normalizedExpected);
        if (switchResult.ok) {
          const refreshed = findNearbyIdTypeControl(input);
          const refreshedText = refreshed?.text || "";
          const refreshedTypes = uniqueIdTypes(refreshedText);
          if (refreshedTypes.length === 1 && refreshedTypes[0] === normalizedExpected) {
            return {
              ok: true,
              expected: normalizedExpected,
              selectedText: refreshedText,
              source: refreshed?.element?.tagName || "switched",
              switched: true,
              switchResult
            };
          }
        }
        return {
          ok: false,
          expected: normalizedExpected,
          selectedText,
          detectedTypes: types,
          error: `商品 ID 查询条件不是 ${normalizedExpected}，当前识别为：${selectedText || "未知"}`,
          switchResult,
          help: `请把商品 ID 查询下拉框切换为 ${normalizedExpected} 后重试。`
        };
      }
      function inputContext(input) {
        const pieces = [input.getAttribute("placeholder") || "", input.getAttribute("aria-label") || ""];
        let current = input.parentElement;
        for (let index = 0; index < 5 && current; index += 1) {
          pieces.push(textOf(current));
          current = current.parentElement;
        }
        const rect = input.getBoundingClientRect();
        const sameRowLabels = Array.from(document.querySelectorAll("span, div, label, button, select"))
          .filter((element) => {
            if (!visible(element) || element === input) return false;
            const elementRect = element.getBoundingClientRect();
            const sameRow = Math.abs((elementRect.top + elementRect.height / 2) - (rect.top + rect.height / 2)) < 55;
            const near = elementRect.right <= rect.left + 30 && elementRect.right >= rect.left - 420;
            return sameRow && near;
          })
          .map((element) => idTypeCandidateText(element))
          .filter(Boolean)
          .join(" ");
        pieces.push(sameRowLabels);
        return pieces.join(" ");
      }
      function findPreferredIdInput(inputs, expected) {
        const normalizedExpected = String(expected || "").toUpperCase();
        return inputs
          .map((input) => {
            const placeholder = input.getAttribute("placeholder") || "";
            const context = inputContext(input);
            const rect = input.getBoundingClientRect();
            let score = 0;
            if (/多个查询|空格|逗号|商品ID/i.test(placeholder)) score += 260;
            if (context.includes("商品ID查询")) score += 520;
            if (normalizedExpected && uniqueIdTypes(context.toUpperCase()).includes(normalizedExpected)) score += 180;
            if (/订单号|子订单号/.test(context) && !context.includes("商品ID查询")) score -= 620;
            if (input.tagName === "TEXTAREA") score += 30;
            score -= Math.min(Math.abs(rect.top - window.innerHeight * 0.35), 900) / 10;
            return { input, score, placeholder, context: context.slice(0, 240) };
          })
          .sort((a, b) => b.score - a.score)[0] || null;
      }
      const value = queryIds.join(" ");
      const inputs = Array.from(document.querySelectorAll("textarea, input")).filter(visible);
      const preferredCandidate = findPreferredIdInput(inputs, expectedLabel);
      const preferred = preferredCandidate?.input
        || inputs.find((input) => input.tagName === "TEXTAREA")
        || inputs.find((input) => !["button", "submit", "checkbox", "radio"].includes((input.getAttribute("type") || "text").toLowerCase()));
      if (!preferred) {
        return { ok: false, error: "没有找到可输入商品 ID 的默认输入框", url: location.href, title: document.title };
      }
      queryInputAnchor = preferred;
      const preQueryTabSelection = targetStatusTab
        ? await selectOrderStatusTab(targetStatusTab)
        : { ok: true, skipped: true };
      if (preQueryTabSelection.ok === false) {
        return {
          ok: false,
          error: preQueryTabSelection.error || "No reliable order status tab found",
          preQueryTabSelection,
          entered_count: 0,
          url: location.href,
          title: document.title
        };
      }
      const requestedSize = Number(requestedPageSize || 0);
      const pageSizeSelection = requestedSize > 0
        ? await selectTargetPageSize(requestedSize)
        : { ok: true, skipped: true };
      preferred.scrollIntoView({ block: "center", inline: "center" });
      await delay(350);
      const idTypeCheck = await ensureExpectedIdType(preferred, expectedLabel);
      if (!idTypeCheck.ok) {
        return {
          ok: false,
          error: idTypeCheck.error,
          help: idTypeCheck.help,
          idTypeCheck,
          inputCandidate: preferredCandidate ? { score: preferredCandidate.score, placeholder: preferredCandidate.placeholder, context: preferredCandidate.context } : null,
          entered_count: 0,
          url: location.href,
          title: document.title
        };
      }
      preferred.focus();
      setNativeValue(preferred, value);

      if (targetPeriod) {
        const period = findPeriodButton(targetPeriod);
        if (!period) {
          return { ok: false, error: `没有找到 ${targetPeriod} 按钮`, entered_count: queryIds.length, url: location.href, title: document.title };
        }
        clickElement(period);
        let periodSelection = periodSnapshot(targetPeriod);
        const deadline = Date.now() + 5000;
        while (!periodSelection.selected && Date.now() < deadline) {
          await delay(150);
          periodSelection = periodSnapshot(targetPeriod);
        }
        if (!periodSelection.selected) {
          return {
            ok: false,
            error: `页面没有确认选中 ${targetPeriod}，当前高亮：${periodSelection.selectedText || "未知"}`,
            entered_count: queryIds.length,
            periodSelection,
            pageSizeSelection,
            url: location.href,
            title: document.title
          };
        }
        await delay(400);
      }

      const query = findQueryButton(preferred) || findByExactText("查询", "button, span, div, a");
      if (!query) {
        return { ok: false, error: "没有找到 查询 按钮", entered_count: queryIds.length, url: location.href, title: document.title };
      }
      clickElement(query);
      return {
        ok: true,
        entered_count: queryIds.length,
        clicked: targetPeriod ? ["默认ID输入框", targetPeriod, "查询"] : ["默认ID输入框", "查询"],
        inputPlaceholder: preferred.getAttribute("placeholder") || "",
        idTypeCheck,
        preQueryTabSelection,
        pageSizeSelection,
        inputCandidate: preferredCandidate ? { score: preferredCandidate.score, placeholder: preferredCandidate.placeholder, context: preferredCandidate.context } : null,
        periodSelection: targetPeriod ? periodSnapshot(targetPeriod) : null,
        url: location.href,
        title: document.title
      };
    }, { attempts: 3 });
}

async function fillGoodsSkcQueryAndMaybeDelist(tabId, skcs, { execute }) {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [skcs, execute],
    func: async (targetSkcs, shouldExecute) => {
      function delay(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
      }
      function visible(element) {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
      }
      function textOf(element) {
        return (element.innerText || element.textContent || "").replace(/\s+/g, " ").trim();
      }
      function setNativeValue(element, value) {
        const setter = Object.getOwnPropertyDescriptor(element.__proto__, "value")?.set;
        const prototype = Object.getPrototypeOf(element);
        const prototypeSetter = Object.getOwnPropertyDescriptor(prototype, "value")?.set;
        const valueSetter = setter || prototypeSetter;
        if (valueSetter) {
          valueSetter.call(element, value);
        } else {
          element.value = value;
        }
        element.dispatchEvent(new Event("input", { bubbles: true }));
        element.dispatchEvent(new Event("change", { bubbles: true }));
      }
      function findByText(text, selector = "button, span, div, a") {
        const candidates = Array.from(document.querySelectorAll(selector));
        return candidates.find((element) => visible(element) && textOf(element) === text)
          || candidates.find((element) => visible(element) && textOf(element).includes(text));
      }
      function clickElement(element) {
        const target = element.closest("button, a, label") || element;
        target.scrollIntoView({ block: "center", inline: "center" });
        for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
          target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window }));
        }
      }
      const inputs = Array.from(document.querySelectorAll("textarea, input")).filter(visible);
      const idInput = inputs.find((input) => /多个查询|空格|逗号|商品ID|SKC/i.test(input.getAttribute("placeholder") || ""))
        || inputs.find((input) => input.tagName === "TEXTAREA")
        || inputs.find((input) => !["button", "submit", "checkbox", "radio"].includes((input.getAttribute("type") || "text").toLowerCase()));
      if (!idInput) {
        return { ok: false, error: "没有找到商品列表的 SKC 查询输入框", url: location.href, title: document.title };
      }
      setNativeValue(idInput, targetSkcs.join(" "));
      const query = findByText("查询");
      if (!query) {
        return { ok: false, error: "没有找到商品列表查询按钮", url: location.href, title: document.title };
      }
      clickElement(query);
      await delay(4000);

      const batchButton = findByText("批量下架产品") || findByText("批量下架");
      if (!batchButton) {
        return { ok: false, error: "没有找到店小秘“批量下架产品”按钮", url: location.href, title: document.title };
      }
      clickElement(batchButton);
      await delay(3000);

      const modalText = textOf(document.body);
      if (!/批量下架|开始下架|下架确认/.test(modalText)) {
        return { ok: false, error: "已点击批量下架，但没有出现下架确认弹窗", url: location.href, title: document.title };
      }
      if (!shouldExecute) {
        return {
          ok: true,
          prepared: true,
          entered_count: targetSkcs.length,
          clicked: ["SKC查询", "查询", "批量下架产品"],
          final_submit_required: "human",
          url: location.href,
          title: document.title
        };
      }

      const confirmed = window.confirm(`AI工作台准备下架 ${targetSkcs.length} 个已审批 SKC。确认后会点击店小秘“开始下架”。`);
      if (!confirmed) {
        return { ok: false, canceled: true, error: "员工取消了浏览器二次确认", url: location.href, title: document.title };
      }

      const startButton = findByText("开始下架") || findByText("确认下架") || findByText("确定");
      if (!startButton) {
        return { ok: false, error: "没有找到“开始下架”按钮", url: location.href, title: document.title };
      }
      clickElement(startButton);
      await delay(5000);
      return {
        ok: true,
        executed: true,
        entered_count: targetSkcs.length,
        clicked: ["SKC查询", "查询", "批量下架产品", "开始下架"],
        url: location.href,
        title: document.title
      };
    }
  });
  return result?.result || { ok: false, error: "页面脚本没有返回结果" };
}

function failedItemResult(item, error) {
  return {
    item_id: item?.item_id,
    skc: item?.skc,
    status: "failed",
    error
  };
}

async function captureTabScreenshot() {
  try {
    return await chrome.tabs.captureVisibleTab(undefined, { format: "jpeg", quality: 50 });
  } catch (error) {
    return "";
  }
}

function normalizeIds(rawIds) {
  const values = Array.isArray(rawIds) ? rawIds : String(rawIds || "").split(/[\s,，;；]+/);
  const seen = new Set();
  const output = [];
  for (const item of values) {
    const value = String(item || "").trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    output.push(value);
  }
  return output;
}

function normalizePeriods(rawPeriods) {
  const values = Array.isArray(rawPeriods) ? rawPeriods : String(rawPeriods || "").split(/[\s,，;；]+/);
  const seen = new Set();
  const output = [];
  for (const item of values) {
    const value = String(item || "").trim();
    if (!value || seen.has(value)) continue;
    seen.add(value);
    output.push(value);
  }
  return output;
}

async function getActiveBusinessTab({ allowAny }) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    throw new Error("no active tab");
  }
  if (allowAny) {
    return tab;
  }
  const rawUrl = tab.url || "";
  let hostname = "";
  try {
    hostname = new URL(rawUrl).hostname;
  } catch (_error) {
    hostname = "";
  }
  if (!hostname || !BUSINESS_HOST_RE.test(hostname)) {
    const error = new Error("unsupported_page");
    error.details = { url: rawUrl };
    throw error;
  }
  return tab;
}

function isBusinessTab(tab) {
  const rawUrl = tab?.url || "";
  try {
    return BUSINESS_HOST_RE.test(new URL(rawUrl).hostname);
  } catch (_error) {
    return false;
  }
}

function priceQuoteTabScore(tab) {
  const text = `${tab?.url || ""} ${tab?.title || ""}`.toLowerCase();
  let score = 0;
  if (/seller|agentseller|sellercentral|seller-central/.test(text)) score += 20;
  if (/life|lifecycle|生命周期|上市生命周期|price|quote|review|申报|核价|价格|priceorder/.test(text)) score += 16;
  if (/goods|product|sku|skc|商品/.test(text)) score += 6;
  if (/temu\.com/i.test(text)) score += 4;
  if (tab?.active) score += 3;
  return score;
}

async function findPriceQuoteBusinessTab() {
  const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const currentWindowTabs = await chrome.tabs.query({ currentWindow: true });
  const currentCandidates = currentWindowTabs
    .filter((tab) => tab?.id && isBusinessTab(tab))
    .sort((left, right) => priceQuoteTabScore(right) - priceQuoteTabScore(left));
  if (currentCandidates.length) {
    const selected = currentCandidates[0];
    return {
      ...selected,
      selectedReason: selected.id === activeTab?.id ? "active_scored_business_tab" : "current_window_business_tab",
      selectedScore: priceQuoteTabScore(selected)
    };
  }

  const allTabs = await chrome.tabs.query({});
  const allCandidates = allTabs
    .filter((tab) => tab?.id && isBusinessTab(tab))
    .sort((left, right) => priceQuoteTabScore(right) - priceQuoteTabScore(left));
  if (allCandidates.length) {
    const selected = allCandidates[0];
    return { ...selected, selectedReason: "other_window_business_tab", selectedScore: priceQuoteTabScore(selected) };
  }

  const error = new Error("unsupported_page");
  error.details = { url: activeTab?.url || "" };
  throw error;
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function withTimeout(promise, timeoutMs, errorMessage) {
  let timer = null;
  const limit = Math.max(1000, Number(timeoutMs || 10000));
  return Promise.race([
    Promise.resolve(promise),
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new Error(errorMessage || "operation_timeout")), limit);
    })
  ]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

async function describeError(error) {
  let url = "";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    url = tab?.url || "";
  } catch (_ignored) {
    url = "";
  }
  return {
    error: error instanceof Error ? error.message : String(error),
    url,
    capturedAt: new Date().toISOString()
  };
}

function compactPluginProgressResultForPost(status, result) {
  if (!result || typeof result !== "object") return result;
  if (!["sent", "running"].includes(String(status || ""))) return result;
  if (result.command_type !== "source_browser_image_search") return result;
  const items = Array.isArray(result.items) ? result.items : [];
  const counts = result.counts && typeof result.counts === "object"
    ? { ...result.counts }
    : sourceBrowserCounts(items, result.total_tasks || items.length);
  const processedQuotes = Math.max(Number(counts.processed_quotes || 0), items.length);
  const totalTasks = Number(counts.total_tasks || 0);
  if (totalTasks > 0 && processedQuotes >= totalTasks) return result;
  counts.processed_quotes = processedQuotes;
  counts.total_tasks = Math.max(totalTasks, processedQuotes);
  return {
    command_type: result.command_type,
    status: result.status || status,
    mode: result.mode,
    statusText: result.statusText,
    error: result.error,
    counts,
    item_count: items.length,
    source_task_worker_count: result.source_task_worker_count,
    safety: result.safety,
    capturedAt: result.capturedAt,
    progress_compacted: true
  };
}

async function postResult(baseUrl, sessionToken, commandId, status, result) {
  baseUrl = normalizeBaseUrl(baseUrl);
  if (!isAllowedWorkbenchUrl(baseUrl)) return;
  const connection = trustedConnectionForBase(baseUrl);
  if (connection.session_token !== sessionToken) {
    throw new tenantContext.TenantContextError(
      "connection_context_mismatch",
      "Result session does not match the active tenant connection."
    );
  }
  const transportResult = compactPluginProgressResultForPost(status, result);
  const body = JSON.stringify({
    session_token: sessionToken,
    tenant_context: publicTenantContext(connection),
    command_id: commandId,
    status,
    result: transportResult
  });
  let lastError = null;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const controller = typeof AbortController !== "undefined" ? new AbortController() : null;
    let timer = null;
    try {
      if (controller) {
        timer = setTimeout(() => controller.abort(), WORKBENCH_RESULT_POST_TIMEOUT_MS);
      }
      const response = await withTimeout(fetch(tenantContext.buildHttpUrl(connection, "/plugin/result"), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body,
        signal: controller?.signal
      }), WORKBENCH_RESULT_POST_TIMEOUT_MS + 1000, "plugin_result_post_timeout");
      if (!response.ok) {
        if ([401, 403, 404].includes(response.status)) {
          await clearConnectionState();
          throw new tenantContext.TenantContextError(
            "plugin_session_revoked",
            `Result endpoint rejected the plugin session with HTTP ${response.status}.`
          );
        }
        throw new Error(`plugin_result_post_http_${response.status}`);
      }
      const payload = await response.json();
      tenantContext.assertServerTenantContext(connection, payload.tenant_context);
      return;
    } catch (error) {
      lastError = error;
      if (error instanceof tenantContext.TenantContextError) {
        await clearConnectionState();
        throw error;
      }
      if (attempt === 0) {
        await delay(500);
      }
    } finally {
      if (timer) clearTimeout(timer);
    }
  }
  throw lastError || new Error("plugin_result_post_failed");
}
