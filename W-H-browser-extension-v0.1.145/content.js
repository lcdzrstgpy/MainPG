(() => {
  if (window.__temuWorkbenchConnector) return;
  window.__temuWorkbenchConnector = true;
  const tenantContext = globalThis.WorkbenchTenantContext;
  const PRODUCT_CAPTURE_HOST_RE = /(^|\.)(temu|1688|alibaba|pinduoduo|yangkeduo|amazon|taobao|tmall)\.com$/i;
  let productListCaptureBusy = false;
  let productListCaptureKey = "";
  let productCaptureBusy = false;
  const CAPTURE_LEDGER_KEY = "twCaptureLedger";
  const CAPTURE_LEDGER_LIMIT = 120;
  let captureLedgerCache = null;
  let captureLedgerDirtyUntil = 0;
  let captureFeedbackEl = null;
  let captureFeedbackBaseline = null;
  let captureFeedbackTransientTimer = 0;
  let captureFeedbackLastText = "";
  let priceQuoteCaptureBusy = false;
  let orderProfitCaptureBusy = false;
  let lastOrderProfitResult = null;
  let oneboundPageCapture = null;
  let oneboundPageCaptureGeneration = 0;
  let extensionContextInvalidated = false;
  const FLOAT_POSITION_KEY = "temuWorkbenchFloatPositionV2";
  const FLOAT_HANDLE_SIZE = 60;
  let floatHandleEl = null;
  let floatPanelEl = null;
  let floatOpen = true; // 默认展开，用户点击手柄后收起
  let floatDrag = { active: false, moved: false, startX: 0, startY: 0, originLeft: 0, originTop: 0 };

  initFloatingControls();
  renderConnectorBadge();
  refreshConnectorBadge();
  syncProductCaptureButton();
  syncProductListCaptureButton();
  syncPriceQuoteCaptureButton();
  syncOrderProfitCaptureButton();
  safeInterval(syncProductCaptureButton, 1500);
  safeInterval(syncProductListCaptureButton, 1800);
  safeInterval(syncPriceQuoteCaptureButton, 1800);
  safeInterval(syncOrderProfitCaptureButton, 2200);
  window.addEventListener("popstate", syncProductListCaptureButton);
  window.addEventListener("hashchange", syncProductListCaptureButton);
  window.addEventListener("pagehide", closeOneboundPageCaptureForPageExit);

  try {
    if (chrome.storage?.onChanged) {
      chrome.storage.onChanged.addListener((changes, areaName) => {
        if (areaName !== "local") return;
        if (changes.connectionContext || changes.sessionId || changes.sessionToken || changes.baseUrl) {
          refreshConnectorBadge();
          syncProductCaptureButton();
          syncProductListCaptureButton();
          syncPriceQuoteCaptureButton();
          syncOrderProfitCaptureButton();
        }
      });
    }
  } catch (error) {
    handleExtensionContextError(error);
  }

  try {
    chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
      if (message?.type === "READ_PAGE_CONTEXT") {
        sendResponse(readPageContext());
        return true;
      }
      if (message?.type === "ONEBOUND_PAGE_CAPTURE_PROGRESS") {
        applyOneboundPageCaptureProgress(message);
        return false;
      }
      return false;
    });
  } catch (error) {
    handleExtensionContextError(error);
  }

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.data?.source !== "temu-workbench") return;
    if (event.data.type === "READ_PAGE_CONTEXT") {
      window.postMessage({
        source: "temu-workbench-content",
        type: "PAGE_CONTEXT",
        payload: readPageContext()
      }, "*");
    }
  });

  function readPageContext() {
    const textSource = document.body?.innerText || document.documentElement?.innerText || "";
    return {
      url: location.href,
      title: document.title,
      textSample: textSource.slice(0, 1000),
      capturedAt: new Date().toISOString()
    };
  }

  function isExtensionContextError(error) {
    const message = String(error?.message || error || "");
    return /Extension context invalidated|context invalidated|message (?:port|channel) closed|Receiving end does not exist/i.test(message);
  }

  function extensionContextReady() {
    if (extensionContextInvalidated) return false;
    try {
      return Boolean(chrome?.runtime?.id);
    } catch (error) {
      return !handleExtensionContextError(error);
    }
  }

  function handleExtensionContextError(error) {
    if (!isExtensionContextError(error)) return false;
    extensionContextInvalidated = true;
    removeConnectorControls();
    return true;
  }

  function removeConnectorControls() {
    for (const id of [
      "temu-workbench-connector-badge",
      "temu-workbench-product-capture",
      "temu-workbench-product-recapture",
      "temu-workbench-product-list-capture",
      "temu-workbench-product-list-recapture",
      "temu-workbench-product-list-cancel",
      "temu-workbench-page-capture-status",
      "temu-workbench-order-profit-capture",
      "temu-workbench-order-profit-status",
      "temu-workbench-order-profit-copy"
    ]) {
      document.getElementById(id)?.remove();
    }
  }

  function safeInterval(callback, delayMs) {
    let timerId = 0;
    timerId = window.setInterval(() => {
      if (!extensionContextReady()) {
        window.clearInterval(timerId);
        return;
      }
      try {
        callback();
      } catch (error) {
        if (handleExtensionContextError(error)) {
          window.clearInterval(timerId);
          return;
        }
        console.warn("workbench content timer failed", error);
      }
    }, delayMs);
    return timerId;
  }

  function safeStorageGet(keys, callback) {
    if (!extensionContextReady()) return;
    try {
      chrome.storage.local.get(keys, (settings) => {
        const error = chrome.runtime?.lastError;
        if (error && handleExtensionContextError(error)) return;
        if (error) {
          console.warn("workbench storage read failed", error);
          return;
        }
        try {
          callback(settings || {});
        } catch (callbackError) {
          if (!handleExtensionContextError(callbackError)) {
            console.warn("workbench storage callback failed", callbackError);
          }
        }
      });
    } catch (error) {
      if (!handleExtensionContextError(error)) {
        console.warn("workbench storage read failed", error);
      }
    }
  }

  async function safeSendRuntimeMessage(message) {
    if (!extensionContextReady()) {
      throw new Error("extension_context_invalidated");
    }
    try {
      return await chrome.runtime.sendMessage(message);
    } catch (error) {
      if (handleExtensionContextError(error)) {
        throw new Error("extension_context_invalidated");
      }
      throw error;
    }
  }

  function safeStorageSet(values, callback) {
    if (!extensionContextReady()) return;
    try {
      chrome.storage.local.set(values, () => {
        const error = chrome.runtime?.lastError;
        if (error && handleExtensionContextError(error)) return;
        if (error) {
          console.warn("workbench storage write failed", error);
          return;
        }
        try {
          callback?.();
        } catch (callbackError) {
          console.warn("workbench storage write callback failed", callbackError);
        }
      });
    } catch (error) {
      if (!handleExtensionContextError(error)) {
        console.warn("workbench storage write failed", error);
      }
    }
  }

  // ===== 采集状态机：按页面持久化“已采集 / 失败”，刷新或重开页面仍能看到上次结果 =====
  function capturePageKey(kind) {
    let parsed;
    try {
      parsed = new URL(location.href);
    } catch (_error) {
      return "";
    }
    const host = parsed.hostname.toLowerCase();
    if (kind === "product") {
      const queryId = parsed.search.match(
        /[?&](?:goods_id|goodsId|product_id|productId|item_id|itemId|spu_id|spuId|offerId|offer_id)=(\d+)/i
      );
      const pathId = parsed.pathname.match(/(?:-g-|offer\/|goods\/|product\/|item\/)(\d+)/i);
      return `${host}:product:${queryId?.[1] || pathId?.[1] || parsed.pathname}`;
    }
    // 列表页忽略翻页参数，翻页后仍归到同一个列表登记记录。
    const pagingKeyRe = /^(?:page|pageno|pagenum|page_no|p|offset|start|current|pageindex|pagesize|limit)$/i;
    const query = [...parsed.searchParams.entries()]
      .filter(([key]) => !pagingKeyRe.test(key))
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([key, value]) => `${key}=${value}`)
      .join("&");
    return `${host}:list:${parsed.pathname}${query ? `?${query}` : ""}`;
  }

  function captureLedgerFrom(settings) {
    // 刚写入的 2 秒内以内存为准，避免异步写入未落盘时被旧存储覆盖。
    if (captureLedgerCache && Date.now() < captureLedgerDirtyUntil) return captureLedgerCache;
    const ledger = settings?.[CAPTURE_LEDGER_KEY];
    if (ledger && typeof ledger === "object") captureLedgerCache = ledger;
    return captureLedgerCache || {};
  }

  function captureTimeLabel(at) {
    const date = new Date(Number(at) || Date.now());
    const pad = (value) => String(value).padStart(2, "0");
    const sameDay = date.toDateString() === new Date().toDateString();
    const time = `${pad(date.getHours())}:${pad(date.getMinutes())}`;
    return sameDay ? time : `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${time}`;
  }

  function writeCaptureRecord(kind, record) {
    const key = capturePageKey(kind);
    if (!key || !extensionContextReady()) return;
    const entry = { ...record, at: Number(record?.at) || Date.now(), url: location.href };
    const commit = (ledger) => {
      const next = { ...ledger, [key]: entry };
      const sorted = Object.entries(next).sort((a, b) => Number(b[1]?.at || 0) - Number(a[1]?.at || 0));
      captureLedgerCache = Object.fromEntries(sorted.slice(0, CAPTURE_LEDGER_LIMIT));
      captureLedgerDirtyUntil = Date.now() + 2000;
      safeStorageSet({ [CAPTURE_LEDGER_KEY]: captureLedgerCache });
      refreshCaptureFeedbackBaseline();
    };
    if (captureLedgerCache) {
      commit(captureLedgerCache);
      return;
    }
    // 首次写入：先占住内存状态保证按钮立刻可见，再合并存储里的历史记录。
    captureLedgerCache = { [key]: entry };
    safeStorageGet([CAPTURE_LEDGER_KEY], (settings) => commit({ ...captureLedgerFrom(settings), [key]: entry }));
  }

  function clearCaptureRecord(kind, callback) {
    const key = capturePageKey(kind);
    if (!key || !extensionContextReady()) {
      callback?.();
      return;
    }
    const commit = (ledger) => {
      const next = { ...ledger };
      delete next[key];
      captureLedgerCache = next;
      captureLedgerDirtyUntil = Date.now() + 2000;
      safeStorageSet({ [CAPTURE_LEDGER_KEY]: next }, callback);
      refreshCaptureFeedbackBaseline();
    };
    if (captureLedgerCache) {
      commit(captureLedgerCache);
      return;
    }
    safeStorageGet([CAPTURE_LEDGER_KEY], (settings) => commit(captureLedgerFrom(settings)));
  }

  function paintCaptureFeedback(text, tone = "info") {
    const element = captureFeedbackEl || document.getElementById("temu-workbench-float-feedback");
    if (!element) return;
    captureFeedbackEl = element;
    const value = String(text || "").trim();
    if (!value) {
      element.style.display = "none";
      return;
    }
    element.textContent = value;
    element.style.display = "block";
    const failed = tone === "error";
    const done = tone === "success";
    element.style.borderColor = failed ? "rgba(220,38,38,.28)" : done ? "rgba(15,157,110,.28)" : "#eef1f5";
    element.style.background = failed ? "rgba(220,38,38,.06)" : done ? "rgba(15,157,110,.06)" : "#f5f7f9";
    element.style.color = failed ? "#b91c1c" : done ? "#047857" : "#5b6675";
  }

  function paintCaptureFeedbackBaseline() {
    if (!captureFeedbackBaseline) {
      paintCaptureFeedback("");
      return;
    }
    paintCaptureFeedback(captureFeedbackBaseline.text, captureFeedbackBaseline.tone);
  }

  function showCaptureFeedback(text, tone = "info", holdMs = 8000) {
    captureFeedbackLastText = String(text || "").trim();
    paintCaptureFeedback(text, tone);
    window.clearTimeout(captureFeedbackTransientTimer);
    captureFeedbackTransientTimer = window.setTimeout(() => {
      captureFeedbackTransientTimer = 0;
      paintCaptureFeedbackBaseline();
    }, holdMs);
  }

  function captureRecordFeedback(record) {
    if (!record) return null;
    const time = captureTimeLabel(record.at);
    if (record.status === "success") {
      const summary = String(record.summary || record.label || "已采集").trim();
      return { text: `上次采集成功 · ${time} · ${summary}`, tone: "success" };
    }
    const reason = String(record.reason || record.summary || "请重试").trim();
    return { text: `上次采集失败 · ${time} · ${reason}`, tone: "error" };
  }

  function refreshCaptureFeedbackBaseline() {
    if (!extensionContextReady()) return;
    const apply = (ledger) => {
      const productKey = capturePageKey("product");
      const listKey = capturePageKey("list");
      const records = [ledger[productKey], ledger[listKey]]
        .filter(Boolean)
        .sort((a, b) => Number(b?.at || 0) - Number(a?.at || 0));
      captureFeedbackBaseline = captureRecordFeedback(records[0]);
      if (!captureFeedbackTransientTimer) paintCaptureFeedbackBaseline();
    };
    if (captureLedgerCache) {
      apply(captureLedgerCache);
      return;
    }
    safeStorageGet([CAPTURE_LEDGER_KEY], (settings) => apply(captureLedgerFrom(settings)));
  }

  function renderConnectorBadge() {
    const content = floatContentRoot();
    if (!content) return;
    if (content.querySelector("#temu-workbench-connector-badge")) return;
    const badge = document.createElement("div");
    badge.id = "temu-workbench-connector-badge";
    badge.setAttribute("role", "status");
    badge.setAttribute("aria-live", "polite");
    badge.style.cssText = [
      "display:flex",
      "align-items:center",
      "gap:7px",
      "height:28px",
      "box-sizing:border-box",
      "padding:0 10px",
      "border:1px solid #eef1f5",
      "border-radius:8px",
      "background:#fafbfc",
      "color:#3a4453",
      "font:11.5px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "pointer-events:none",
      "margin-bottom:8px"
    ].join(";");

    const dot = document.createElement("span");
    dot.dataset.role = "dot";
    dot.style.cssText = [
      "width:8px",
      "height:8px",
      "border-radius:999px",
      "background:#d97706",
      "box-shadow:0 0 0 3px rgba(217,119,6,0.12)",
      "flex:0 0 auto"
    ].join(";");

    const text = document.createElement("span");
    text.dataset.role = "text";
    text.textContent = "界野助手 · 待连接";
    text.style.cssText = [
      "overflow:hidden",
      "text-overflow:ellipsis",
      "white-space:nowrap",
      "font-weight:700"
    ].join(";");

    badge.append(dot, text);
    content.appendChild(badge);
  }

  function refreshConnectorBadge() {
    if (!chrome.storage?.local) {
      updateConnectorBadge(null);
      return;
    }
    safeStorageGet(["connectionContext"], (settings) => {
      let connection = null;
      try {
        connection = tenantContext.validateConnectionContext(settings?.connectionContext);
      } catch (_error) {
        connection = null;
      }
      updateConnectorBadge(connection);
    });
  }

  function updateConnectorBadge(connectionContext) {
    const badge = document.getElementById("temu-workbench-connector-badge");
    if (!badge) return;
    const connected = Boolean(connectionContext);
    const companyCode = connected && /^[0-9]{3}$/.test(String(connectionContext.company_code || ""))
      ? String(connectionContext.company_code)
      : "";
    const dot = badge.querySelector("[data-role='dot']");
    const text = badge.querySelector("[data-role='text']");
    if (dot) {
      dot.style.background = connected ? "#7c8a3f" : "#d97706";
      dot.style.boxShadow = connected ? "0 0 0 3px rgba(124,138,63,0.14)" : "0 0 0 3px rgba(217,119,6,0.12)";
    }
    if (text) {
      text.textContent = connected ? `界野助手 · 公司 ${companyCode || "—"} 已连接` : "界野助手 · 待连接";
    }
    badge.title = connected
      ? `界野采集助手 已连接公司 ${companyCode}`
      : "界野采集助手已注入页面，打开扩展弹窗连接工作台";
  }

  function productDetailLinkElements() {
    const selectors = [
      'a[href*="/offer/"]',
      'a[href*="detail.1688.com"]',
      'a[href*="-g-"]',
      'a[href*="/goods.html"]',
      'a[href*="/goods/"]',
      'a[href*="/product/"]',
      'a[href*="offerId="]',
      'a[href*="offerid="]',
      'a[href*="offer_id="]',
      'a[href*="productId="]',
      'a[href*="productid="]',
      'a[href*="product_id="]',
      'a[href*="goods_id="]',
      'a[href*="goodsId="]',
      'a[href*="/goods/detail"]',
      'a[data-offer-id]',
      'a[data-offerid]',
      'a[data-goods-id]',
      'a[data-goodsid]',
      'a[data-item-id]',
      'a[data-product-id]',
      "[data-offer-id] a",
      "[data-offerid] a",
      "[data-goods-id] a",
      "[data-goodsid] a",
      "[data-item-id] a",
      "[data-product-id] a"
    ].join(",");
    const productUrlRe = /\/offer\/\d+\.html|goods\.html|goods\/|\/product\/|-g-\d+(?:\.html|[/?#]|$)|[?&](?:offerid|offer_id|productid|product_id|goods_id|goodsId|productId|spuId|spu_id|item_id|itemId)=\d+/i;
    return Array.from(document.querySelectorAll(selectors))
      .filter((link) => productUrlRe.test(String(link.href || link.getAttribute("href") || "")));
  }

  function visibleProductImageCount() {
    return Array.from(document.images).filter((img) => {
      const rect = img.getBoundingClientRect();
      const src = String(img.currentSrc || img.src || img.getAttribute("data-src") || "");
      return rect.width >= 72 && rect.height >= 72 && !/logo|avatar|icon|sprite|blank|placeholder/i.test(src);
    }).length;
  }

  function hasTemuRelatedProductListEvidence() {
    let parsed;
    try {
      parsed = new URL(location.href);
    } catch (_error) {
      return false;
    }
    if (!/(^|\.)temu\.com$/i.test(parsed.hostname)) return false;
    const offerLinks = productDetailLinkElements();
    const bodyText = String(document.body?.innerText || "").slice(0, 10000);
    const relatedContainerSelectors = [
      "[class*='similar']",
      "[class*='recommend']",
      "[class*='recommendation']",
      "[class*='related']",
      "[class*='related-products']",
      "[class*='猜你喜欢']",
      "[class*='also-like']",
      "[class*='related-goods']",
      "[class*='similar-items']",
      "[class*='similar-product']",
      "[class*='similar_products']"
    ].join(",");
    const hasRelatedContainers = document.querySelector(relatedContainerSelectors) != null;
    const hasRelatedText = /鐩镐技鍟嗗搧|绫讳技鍟嗗搧|鐩稿叧鎺ㄨ崘|鐚滀綘鍠滄|鎺ㄨ崘鍟嗗搧|Similar items|Similar products|Related products|You may also like/i.test(bodyText);
    const hasProductGridSignals = visibleProductImageCount() >= 2
      && /(?:CA\$|US\$|\$)\s*\d|宸插敭|Sold|淇冮攢|骞垮憡|璇勫垎|Reviews?|鏈湴浠?/i.test(bodyText);
    const hasRelatedIdText = /(?:goods_id|goodsId|product_id|productId|item_id|itemId|spu_id|spuId|offerId|offerid|offer_id)[:=]\d{6,}/i.test(bodyText);
    return hasRelatedText || hasRelatedContainers || hasProductGridSignals || offerLinks.length >= 2 || (hasRelatedIdText && visibleProductImageCount() >= 2);
  }

  function isExcludedProductCapturePage(parsed) {
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
    if (/ocms-fusion|web_im|aliim|wangwang|旺旺|聊天|客服|messenger|instant-message/i.test(decodedHref)) return true;
    return false;
  }

  function isSupportedProductPage() {
    let parsed;
    try {
      parsed = new URL(location.href);
    } catch (_error) {
      return false;
    }
    if (!PRODUCT_CAPTURE_HOST_RE.test(parsed.hostname)) return false;
    if (isExcludedProductCapturePage(parsed)) return false;
    if (/(^|\.)amazon\.com$/i.test(parsed.hostname)) {
      return /\/(?:dp|gp\/product|product)\/[A-Z0-9]{10}(?:[/?#]|$)/i.test(parsed.pathname)
        || /[?&](?:asin|ASIN)=([A-Z0-9]{10})(?:&|$)/.test(parsed.search)
        || Boolean(document.querySelector("#productTitle, #dp"));
    }
    if (/(^|\.)(taobao|tmall)\.com$/i.test(parsed.hostname)) {
      return isTaobaoItemDetailPage(parsed);
    }
    const isTemu = /(^|\.)temu\.com$/i.test(parsed.hostname);
    if (isTemu) {
      const explicitDetail = /-g-\d+(?:\.html|[/?#]|$)/i.test(parsed.pathname)
        || /[?&](goods_id|goodsId|product_id|productId|item_id|itemId|spu_id|spuId|offerId|offer_id)=\d+/i.test(parsed.search)
        || (/\/goods\.html|\/detail|\/item|\/goods|\/product/i.test(parsed.pathname) && productDetailLinkElements().length <= 1);
      if (explicitDetail) return true;
      if (productDetailLinkElements().length >= 2) return false;
      if (/[?&](goods_id|goodsId|product_id|productId|item_id|itemId|spu_id|spuId|offerId|offer_id)=\d+/i.test(parsed.search)) return true;
      if (/\/goods\.html|\/detail|\/item|\/goods|\/product/i.test(parsed.pathname)) return true;
      const bodyText = String(document.body?.innerText || "").slice(0, 5000);
      const hasProductAction = /添加到购物车|加入购物车|立即加购|Add to cart|add to cart|buy now|已售|Sold|数量|颜色|尺码|CA\$|\$\s*\d/i.test(bodyText);
      const hasProductImage = visibleProductImageCount() === 1
        ? Array.from(document.querySelectorAll("img")).some((img) => {
          const rect = img.getBoundingClientRect();
          return rect.width >= 240 && rect.height >= 240;
        })
        : false;
      return hasProductAction && hasProductImage;
    }
    if (/(^|\.)1688\.com$/i.test(parsed.hostname)) {
      return /\/offer\/\d+\.html/i.test(parsed.pathname) || /[?&](offerid|offer_id|productid|product_id)=\d+/i.test(parsed.search);
    }
    if (/(^|\.)alibaba\.com$/i.test(parsed.hostname)) {
      return /\/offer\/\d+\.html|\/product-detail\//i.test(parsed.pathname) || /[?&](offerid|offer_id|productid|product_id)=\d+/i.test(parsed.search);
    }
    return /\/offer\/|\/goods\.html|\/detail|\/item|\/goods|\/product/i.test(parsed.pathname);
  }

  function syncProductCaptureButton() {
    const button = document.getElementById("temu-workbench-product-capture");
    const recapture = document.getElementById("temu-workbench-product-recapture");
    if (!isSupportedProductPage()) {
      if (button) button.remove();
      if (recapture) recapture.remove();
      return;
    }
    renderProductCaptureButton();
    refreshProductCaptureButton();
  }

  function renderProductCaptureButton() {
    if (!isSupportedProductPage()) return;
    const content = floatContentRoot();
    if (!content) return;
    if (content.querySelector("#temu-workbench-product-capture")) return;
    const button = document.createElement("button");
    button.id = "temu-workbench-product-capture";
    button.type = "button";
    button.textContent = "采集到工作台";
    button.style.cssText = panelButtonStyle("#0f9f9a", "rgba(15,159,154,0.52)");
    button.addEventListener("click", captureProductToWorkbench);
    content.appendChild(button);

    const recapture = document.createElement("button");
    recapture.id = "temu-workbench-product-recapture";
    recapture.type = "button";
    recapture.className = "tw-ghost";
    recapture.textContent = "重新采集";
    recapture.style.cssText = ghostButtonStyle();
    recapture.style.display = "none";
    recapture.addEventListener("click", (event) => {
      event.stopPropagation();
      clearCaptureRecord("product", () => {
        recapture.style.display = "none";
        setCaptureButtonState("采集到工作台", false);
        refreshProductCaptureButton();
        void captureProductToWorkbench();
      });
    });
    content.appendChild(recapture);
    refreshProductCaptureButton();
  }

  function refreshProductCaptureButton() {
    const button = document.getElementById("temu-workbench-product-capture");
    if (!button || productCaptureBusy) return;
    const key = capturePageKey("product");
    safeStorageGet(["connectionContext", CAPTURE_LEDGER_KEY], (settings) => {
      if (productCaptureBusy) return;
      let connected = false;
      try {
        connected = Boolean(tenantContext.validateConnectionContext(settings?.connectionContext));
      } catch (_error) {
        connected = false;
      }
      const record = captureLedgerFrom(settings)[key] || null;
      applyProductCaptureButton(button, connected, record);
    });
  }

  function applyProductCaptureButton(button, connected, record) {
    const recapture = document.getElementById("temu-workbench-product-recapture");
    button.style.cssText = panelButtonStyle();
    if (record?.status === "success") {
      const at = Number(record.at) || Date.now();
      button.textContent = `${record.label || "已采集"} · ${captureTimeLabel(at)}`;
      button.disabled = true;
      button.style.opacity = "1";
      button.style.background = "#eef2f6";
      button.style.borderColor = "#dfe5ec";
      button.style.color = "#3a4453";
      button.style.cursor = "default";
      button.title = `${String(record.summary || record.label || "已采集")} · ${new Date(at).toLocaleString("zh-CN")}`;
      if (recapture) recapture.style.display = "block";
      return;
    }
    const failed = record?.status === "failed";
    button.textContent = failed ? "采集失败 · 点击重试" : "采集到工作台";
    button.disabled = !connected;
    button.style.opacity = connected ? "1" : "0.72";
    button.title = failed
      ? String(record.reason || record.summary || "上次采集失败，点击重试")
      : connected
        ? "采集当前商品标题和主图，直接加入产品处理待处理池"
        : "正在自动连接本机工作台，连接成功后按钮自动可用";
    if (recapture) recapture.style.display = "none";
  }

  function setCaptureButtonState(text, busy = false, help = "") {
    const button = document.getElementById("temu-workbench-product-capture");
    if (!button) return;
    button.textContent = text;
    button.disabled = busy;
    button.title = String(help || text || "").trim();
  }

  async function captureProductToWorkbench() {
    if (productCaptureBusy) return;
    productCaptureBusy = true;
    setCaptureButtonState("正在采集...", true, "正在读取当前商品并提交到工作台");
    try {
      const response = await safeSendRuntimeMessage({ type: "CAPTURE_PRODUCT_TO_WORKBENCH" });
      if (!response?.ok) {
        const reason = String(response?.statusText || response?.help || response?.error || "采集失败").trim();
        productCaptureBusy = false;
        writeCaptureRecord("product", { status: "failed", reason });
        setCaptureButtonState("采集失败 · 点击重试", false, reason);
        showCaptureFeedback(`采集失败：${reason}`, "error");
        window.setTimeout(refreshProductCaptureButton, 2400);
        return;
      }
      const summary = String(response?.statusText || "已加入待处理").trim();
      const count = Number(response?.item_count || response?.captured_count || 0);
      productCaptureBusy = false;
      writeCaptureRecord("product", { status: "success", label: "已采集", summary, count });
      setCaptureButtonState(`已采集 · ${captureTimeLabel(Date.now())}`, true, summary);
      const recapture = document.getElementById("temu-workbench-product-recapture");
      if (recapture) recapture.style.display = "block";
      showCaptureFeedback(summary, "success");
    } catch (error) {
      const message = String(error?.message || error || "后台消息无响应").replace(/\s+/g, " ").trim();
      productCaptureBusy = false;
      writeCaptureRecord("product", { status: "failed", reason: message });
      setCaptureButtonState("采集失败 · 点击重试", false, message);
      showCaptureFeedback(`采集失败：${message.slice(0, 80)}`, "error");
      window.setTimeout(refreshProductCaptureButton, 2400);
    }
  }

  function isSupportedProductListPage() {
    let parsed;
    try {
      parsed = new URL(location.href);
    } catch (_error) {
      return false;
    }
    if (!/(^|\.)((temu)|(1688)|(alibaba))\.com$/i.test(parsed.hostname)) return false;
    if (isExcludedProductCapturePage(parsed)) return false;
    if (isSupportedPriceQuotePage()) return false;
    const isTemu = /(^|\.)temu\.com$/i.test(parsed.hostname);
    const productPage = isSupportedProductPage();
    const offerLinks = productDetailLinkElements();
    if (productPage) {
      if (isTemu) {
        if (offerLinks.length >= 2) {
          return true;
        }
        return hasTemuRelatedProductListEvidence();
      }
      return false;
    }
    if (offerLinks.length >= 1) return true;
    const path = `${parsed.hostname}${parsed.pathname}`.toLowerCase();
    const bodyText = String(document.body?.innerText || "").slice(0, 8000);
    const productImages = Array.from({ length: visibleProductImageCount() });
    const dataOfferNodes = document.querySelectorAll(
      "[data-offer-id], [data-offerid], [offer-id], [offerid], [data-item-id], [data-product-id]"
    );
    const hasPriceSignal = /(?:CA\$|US\$|￥|¥|\$)\s*\d|起批|起订量|成交|销量|已售|Sold|拿样价|批发价/i.test(bodyText);
    const listLikeProductSignalCount = productImages.length + dataOfferNodes.length + (hasPriceSignal ? 1 : 0);
    const urlLooksLikeList = /(^|\.)(www|s|p4psearch|search|show|ye)\.1688\.com|(^|\.)(www|s|search|app)\.alibaba\.com|(^|\.)temu\.com|offer_search|search|market|huo|page|p4p|list|result|supplier|factory|wholesale/i.test(path);
    const textLooksLikeList = /找货源|精选货源|综合|销量|价格|起订量|店铺商品数|新人价|起批|包邮|跨境|源头厂家|严选|材质|风格|工厂|实力商家|同款|相似|采购|批发|粉销商品|5星好评|新品|分类|畅销商品|CA\$/.test(bodyText);
    return (urlLooksLikeList && listLikeProductSignalCount >= 1) || (textLooksLikeList && listLikeProductSignalCount >= 2);
  }

  function is1688ProductListPage() {
    if (!isSupportedProductListPage()) return false;
    try {
      return /(^|\.)1688\.com$/i.test(new URL(location.href).hostname);
    } catch (_error) {
      return false;
    }
  }

  function isTaobaoItemDetailPage(parsed) {
    const hostname = String(parsed.hostname || "").toLowerCase();
    if (/(^|\.)(item|detail|world|main)\.(taobao|tmall)\.com$/i.test(hostname)) return true;
    if (/\/item\.htm(?:$|[\?#])/i.test(parsed.pathname)) return true;
    if (/\/i\d{5,}\.htm(?:$|[\?#])/i.test(parsed.pathname)) return true;
    return false;
  }

  function isTaobaoProductListPage() {
    let parsed;
    try {
      parsed = new URL(location.href);
    } catch (_error) {
      return false;
    }
    if (!/(^|\.)(taobao|tmall)\.com$/i.test(parsed.hostname)) return false;
    if (isExcludedProductCapturePage(parsed)) return false;
    if (isTaobaoItemDetailPage(parsed)) return false;
    // 新版淘宝搜索/列表页商品卡：锚点 id="item_id_<num_iid>" 或 data-spm-anchor-id="aItem_id_<num_iid>_..."。
    const cardAnchors = document.querySelectorAll(
      'a[id^="item_id_"], a[data-spm-anchor-id^="aItem_id_"]'
    );
    if (cardAnchors.length >= 1) return true;
    // 兜底：多数商品链接指向 item.taobao.com / detail.tmall.com 且页面含价格/销量信号。
    const itemLinks = document.querySelectorAll(
      'a[href*="item.htm?id="], a[href*="item.taobao.com"], a[href*="detail.tmall.com"]'
    );
    if (itemLinks.length < 1) return false;
    const bodyText = String(document.body?.innerText || "").slice(0, 8000);
    const hasPriceSignal = /(?:￥|¥|\$)\s*\d|销量|已售|月销|评价|包邮|满减|到手价/i.test(bodyText);
    return hasPriceSignal || visibleProductImageCount() >= 4;
  }

  // 整页采集覆盖：1688、淘宝、天猫列表/搜索页
  function isOneboundCaptureListPage() {
    return is1688ProductListPage() || isTaobaoProductListPage();
  }

  // 列表页采集按钮的显示门控：老链路（批量采集本页）或整页采集任一命中即显示。
  function isListCaptureSupportedPage() {
    return isSupportedProductListPage() || isTaobaoProductListPage();
  }

  function isCurrentOneboundPageCapture(state, batchToken = null) {
    return isOneboundCaptureListPage()
      && canUseOneboundPageCaptureState(state, location.href, oneboundPageCaptureGeneration, batchToken);
  }

  function clearOneboundPageCaptureState() {
    oneboundPageCapture = null;
    productListCaptureBusy = false;
    document.getElementById("temu-workbench-page-capture-status")?.remove();
    setProductListCancelButtonState(false);
  }

  function invalidateOneboundPageCaptureForNavigation() {
    if (!oneboundPageCapture || isCurrentOneboundPageCapture(oneboundPageCapture)) return;
    const stale = oneboundPageCapture;
    oneboundPageCaptureGeneration += 1;
    clearOneboundPageCaptureState();
    if (stale.batchToken && !["finished", "registered"].includes(stale.phase)) {
      void safeSendRuntimeMessage(oneboundPreparedCancelRequest(stale.batchToken)).catch(() => {});
    }
  }

  function closeOneboundPageCaptureForPageExit() {
    const state = oneboundPageCapture;
    if (!state?.batchToken || ["finished", "registered"].includes(state.phase)) return;
    void safeSendRuntimeMessage(oneboundPreparedCancelRequest(state.batchToken)).catch(() => {});
  }

  function isSupportedPriceQuotePage() {
    let parsed;
    try {
      parsed = new URL(location.href);
    } catch (_error) {
      return false;
    }
    if (!/(^|\.)(temu|kuajingmaihuo)\.com$/i.test(parsed.hostname)) return false;
    // 不做特定页面隐藏：仅按页面标题/文本识别核价相关页，稳定显示按钮。
    // 核价弹窗是挂在 body 末尾的浮层，标题文本常在 1200 字之后；只搜开头会把
    // 弹窗页误判成商品列表，导致「采集核价本页」按钮不出现（2026-10-29 用户反馈）。
    const pageText = String(document.body?.innerText || "").replace(/\s+/g, " ").slice(0, 24000);
    const haystack = `${document.title || ""} ${pageText}`;
    return /批量查看并确认申报价|批量查看并确认申报价格/.test(haystack);
  }

  function syncPriceQuoteCaptureButton() {
    const button = document.getElementById("temu-price-quote-capture");
    if (!isSupportedPriceQuotePage()) {
      if (button) button.remove();
      return;
    }
    renderPriceQuoteCaptureButton();
    refreshPriceQuoteCaptureButton();
  }

  function renderPriceQuoteCaptureButton() {
    if (!isSupportedPriceQuotePage()) return;
    const content = floatContentRoot();
    if (!content) return;
    if (content.querySelector("#temu-price-quote-capture")) return;
    const button = document.createElement("button");
    button.id = "temu-price-quote-capture";
    button.type = "button";
    button.textContent = "采集核价本页";
    button.style.cssText = panelButtonStyle("#7c3aed", "rgba(124,58,237,0.52)");
    button.addEventListener("click", capturePriceQuotePageToWorkbench);
    content.appendChild(button);
  }

  function refreshPriceQuoteCaptureButton() {
    const button = document.getElementById("temu-price-quote-capture");
    if (!button) return;
    if (priceQuoteCaptureBusy) return;
    safeStorageGet(["connectionContext"], (settings) => {
      if (priceQuoteCaptureBusy) return;
      let connected = false;
      try {
        connected = Boolean(tenantContext.validateConnectionContext(settings?.connectionContext));
      } catch (_error) {
        connected = false;
      }
      button.disabled = !connected;
      button.textContent = connected ? "采集核价本页" : "先连接工作台";
      button.style.opacity = connected ? "1" : "0.72";
      button.title = connected
        ? "只读取当前核价页的官方报价并写入工作台当前核价批次，不会提交或修改 Temu"
        : "请先打开扩展弹窗连接本地工作台";
    });
  }

  function setPriceQuoteCaptureButtonState(text, busy = false, help = "") {
    const button = document.getElementById("temu-price-quote-capture");
    if (!button) return;
    priceQuoteCaptureBusy = Boolean(busy);
    button.textContent = text;
    button.disabled = busy;
    button.title = help || text;
  }

  async function capturePriceQuotePageToWorkbench() {
    if (priceQuoteCaptureBusy) return;
    setPriceQuoteCaptureButtonState("正在采集核价...", true);
    try {
      const response = await safeSendRuntimeMessage({ type: "CAPTURE_TEMU_PRICE_QUOTE_PAGE" });
      if (!response?.ok) {
        setPriceQuoteCaptureButtonState(response?.statusText || "核价采集失败", false, response?.help || response?.error || "");
        window.setTimeout(refreshPriceQuoteCaptureButton, 3000);
        return;
      }
      setPriceQuoteCaptureButtonState(response.statusText || `已入库${Number(response.item_count || 0)}条`, false);
      window.setTimeout(refreshPriceQuoteCaptureButton, 3000);
    } catch (error) {
      const message = String(error?.message || error || "");
      if (/extension_context_invalidated/.test(message)) {
        // 扩展重新加载后，已打开页面的内容脚本会失效，刷新页面即可恢复。
        setPriceQuoteCaptureButtonState("请刷新当前 Temu 页面后再采集", false, "扩展已更新，刷新页面后采集按钮会自动恢复");
      } else {
        setPriceQuoteCaptureButtonState("核价采集失败", false, message);
      }
      window.setTimeout(refreshPriceQuoteCaptureButton, 3000);
    }
  }

  function syncProductListCaptureButton() {
    invalidateOneboundPageCaptureForNavigation();
    const button = document.getElementById("temu-workbench-product-list-capture");
    const recapture = document.getElementById("temu-workbench-product-list-recapture");
    const cancelButton = document.getElementById("temu-workbench-product-list-cancel");
    if (!isListCaptureSupportedPage()) {
      if (button) button.remove();
      if (recapture) recapture.remove();
      if (cancelButton) cancelButton.remove();
      document.getElementById("temu-workbench-page-capture-status")?.remove();
      return;
    }
    renderProductListCaptureButton();
    refreshProductListCaptureButton();
  }

  function renderProductListCaptureButton() {
    if (!isListCaptureSupportedPage()) return;
    const content = floatContentRoot();
    if (!content) return;
    if (content.querySelector("#temu-workbench-product-list-capture")) {
      renderProductListCancelButton();
      return;
    }
    const button = document.createElement("button");
    button.id = "temu-workbench-product-list-capture";
    button.type = "button";
    button.textContent = isOneboundCaptureListPage() ? "整页采集" : "批量采集本页";
    button.style.cssText = panelButtonStyle("#2563eb", "rgba(37,99,235,0.48)");
    button.addEventListener("click", () => {
      if (isOneboundCaptureListPage()) {
        prepare1688OneboundPageCapture();
        return;
      }
      captureProductListToWorkbench();
    });
    content.appendChild(button);

    const recapture = document.createElement("button");
    recapture.id = "temu-workbench-product-list-recapture";
    recapture.type = "button";
    recapture.className = "tw-ghost";
    recapture.textContent = isOneboundCaptureListPage() ? "重新扫描" : "重新采集本页";
    recapture.style.cssText = ghostButtonStyle();
    recapture.style.display = "none";
    recapture.addEventListener("click", (event) => {
      event.stopPropagation();
      clearCaptureRecord("list", () => {
        recapture.style.display = "none";
        setProductListButtonState(isOneboundCaptureListPage() ? "整页采集" : "批量采集本页", false);
        refreshProductListCaptureButton();
        if (isOneboundCaptureListPage()) {
          prepare1688OneboundPageCapture();
          return;
        }
        void captureProductListToWorkbench();
      });
    });
    content.appendChild(recapture);
    renderProductListCancelButton();
  }

  function renderProductListCancelButton() {
    if (document.getElementById("temu-workbench-product-list-cancel")) return;
    const button = document.createElement("button");
    button.id = "temu-workbench-product-list-cancel";
    button.type = "button";
    button.textContent = "中断采集";
    button.style.cssText = [
      "position:fixed",
      "right:14px",
      "bottom:138px",
      "z-index:2147483647",
      "height:34px",
      "max-width:180px",
      "box-sizing:border-box",
      "padding:0 13px",
      "border:1px solid rgba(220,38,38,0.38)",
      "border-radius:10px",
      "background:#ffffff",
      "box-shadow:0 8px 20px rgba(17,24,39,0.12)",
      "color:#dc2626",
      "font:600 12.5px/1 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "cursor:pointer",
      "display:none"
    ].join(";");
    button.addEventListener("click", cancelProductListCapture);
    document.documentElement.appendChild(button);
  }

  function refreshProductListCaptureButton() {
    const button = document.getElementById("temu-workbench-product-list-capture");
    if (!button) return;
    const key = capturePageKey("list");
    if (productListCaptureBusy) {
      if (productListCaptureKey === key) return;
      // 页面已切换：旧页面的采集状态不再锁住新页面的按钮。
      productListCaptureBusy = false;
    }
    safeStorageGet(["connectionContext", CAPTURE_LEDGER_KEY], (settings) => {
      if (productListCaptureBusy) return;
      let connected = false;
      try {
        connected = Boolean(tenantContext.validateConnectionContext(settings?.connectionContext));
      } catch (_error) {
        connected = false;
      }
      applyProductListCaptureButton(button, connected, captureLedgerFrom(settings)[key] || null);
    });
  }

  function applyProductListCaptureButton(button, connected, record) {
    const recapture = document.getElementById("temu-workbench-product-list-recapture");
    button.style.cssText = panelButtonStyle();
    if (record?.status === "success") {
      const at = Number(record.at) || Date.now();
      button.textContent = `${record.label || "已登记"} · ${captureTimeLabel(at)}`;
      button.disabled = true;
      button.style.opacity = "1";
      button.style.background = "#eef2f6";
      button.style.borderColor = "#dfe5ec";
      button.style.color = "#3a4453";
      button.style.cursor = "default";
      button.title = `${String(record.summary || record.label || "已采集")} · ${new Date(at).toLocaleString("zh-CN")}`;
      if (recapture) recapture.style.display = "block";
      return;
    }
    const failed = record?.status === "failed";
    button.textContent = failed ? "采集失败 · 点击重试" : (isOneboundCaptureListPage() ? "整页采集" : "批量采集本页");
    button.disabled = !connected;
    button.style.opacity = connected ? "1" : "0.72";
    button.title = failed
      ? String(record.reason || record.summary || "上次采集失败，点击重试")
      : connected
        ? isOneboundCaptureListPage()
          ? "先扫描并识别当前列表页商品卡；确认后再到工作台启动采集"
          : "采集当前列表页商品链接，逐个打开详情页复用单品采集，自动跳过广告、推广和店铺卡片"
        : "正在自动连接本机工作台，连接成功后按钮自动可用";
    if (recapture) recapture.style.display = "none";
  }

  function setProductListButtonState(text, busy = false, help = "") {
    const button = document.getElementById("temu-workbench-product-list-capture");
    if (!button) return;
    productListCaptureBusy = Boolean(busy);
    if (busy) productListCaptureKey = capturePageKey("list");
    const value = String(text || "");
    button.textContent = value.length > 28 ? `${value.slice(0, 26)}...` : value;
    button.title = String(help || value || "").trim();
    button.disabled = busy;
  }

  function setProductListCancelButtonState(visible, text = "中断采集", disabled = false) {
    const button = document.getElementById("temu-workbench-product-list-cancel");
    if (!button) return;
    button.style.display = visible ? "block" : "none";
    button.textContent = text;
    button.disabled = disabled;
    button.style.opacity = disabled ? "0.78" : "1";
  }

  async function cancelProductListCapture() {
    setProductListCancelButtonState(true, "中断中...", true);
    try {
      const response = await safeSendRuntimeMessage({ type: "CANCEL_PRODUCT_BATCH_CAPTURE" });
      if (oneboundPageCapture?.phase === "running") {
        oneboundPageCapture.statusText = response?.statusText || "正在中断整页采集";
        renderOneboundPageCaptureStatus();
      } else {
        setProductListButtonState(response?.statusText || "正在中断批量采集", true);
      }
    } catch (_error) {
      setProductListCancelButtonState(true, "中断失败", false);
    }
  }

  // ONEBOUND_STATE_HELPERS_START
  function pageCaptureCount(value) {
    const count = Number(value);
    return Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
  }

  function pageCaptureFailedUrls(value) {
    return [...new Set((Array.isArray(value) ? value : [])
      .map((item) => String(item || "").trim())
      .filter(Boolean))];
  }

  function pageCaptureSuccessCount(value) {
    const captured = Number(value?.captured_count);
    if (Number.isFinite(captured)) return pageCaptureCount(captured);
    return pageCaptureCount(value?.created_count) + pageCaptureCount(value?.refreshed_count);
  }

  function createOneboundPageCaptureState(sourceUrl, generation) {
    return {
      phase: "preparing", sourceUrl: String(sourceUrl || ""), generation: Number(generation), batchToken: null,
      scanTotal: 0, workTotal: 0, pendingTotal: 0, existingCount: 0, completedCount: 0,
      createdCount: 0, refreshedCount: 0, capturedCount: 0, skippedCount: 0, failedCount: 0,
      unprocessedCount: 0, failedUrls: [], statusText: "正在扫描并识别当前列表页商品", cancelled: false
    };
  }

  function canUseOneboundPageCaptureState(state, sourceUrl, generation, batchToken = null) {
    return Boolean(
      state
      && state.sourceUrl === String(sourceUrl || "")
      && state.generation === Number(generation)
      && (batchToken === null || state.batchToken === String(batchToken || ""))
    );
  }

  function applyOneboundPageCapturePrepareState(state, response) {
    state.scanTotal = pageCaptureCount(response?.total_count);
    state.existingCount = pageCaptureCount(response?.existing_count);
    state.pendingTotal = pageCaptureCount(response?.pending_count);
    state.workTotal = state.pendingTotal;
    if (response?.statusText) state.statusText = String(response.statusText);
    return state;
  }

  function applyOneboundPageCaptureProgressState(state, message) {
    state.workTotal = pageCaptureCount(message?.total) || state.workTotal;
    state.completedCount = pageCaptureCount(message?.completed);
    state.createdCount = pageCaptureCount(message?.created_count);
    state.refreshedCount = pageCaptureCount(message?.refreshed_count);
    state.capturedCount = pageCaptureSuccessCount(message);
    state.skippedCount = pageCaptureCount(message?.skipped_count);
    state.failedCount = pageCaptureCount(message?.failed_count);
    state.unprocessedCount = pageCaptureCount(message?.unprocessed_count);
    state.statusText = String(message?.statusText || "");
    return state;
  }

  function applyOneboundPageCaptureFinalState(state, response) {
    state.cancelled = Boolean(response?.cancelled);
    state.createdCount = pageCaptureCount(response?.created_count);
    state.refreshedCount = pageCaptureCount(response?.refreshed_count);
    state.capturedCount = pageCaptureSuccessCount(response);
    state.skippedCount = pageCaptureCount(response?.skipped_count);
    state.failedCount = pageCaptureCount(response?.failed_count);
    state.unprocessedCount = pageCaptureCount(response?.unprocessed_count);
    state.failedUrls = pageCaptureFailedUrls(response?.failed_urls);
    state.statusText = response?.statusText || "整页采集完成";
    return state;
  }

  function oneboundFinalHasUsableSummary(response) {
    if (response?.ok !== false || response?.cancelled) return true;
    return ["onebound_page_capture_finish_failed", "onebound_page_capture_fatal_item_failed"]
      .includes(String(response?.error || ""));
  }

  function oneboundPreparedCancelRequest(batchToken) {
    return { type: "CANCEL_PRODUCT_BATCH_CAPTURE", batch_token: String(batchToken || "") };
  }

  function oneboundRetryPrepareRequest(failedUrls) {
    return {
      type: "PREPARE_1688_ONEBOUND_PAGE_CAPTURE",
      source_urls: pageCaptureFailedUrls(failedUrls)
    };
  }
  // ONEBOUND_STATE_HELPERS_END

  function setOneboundPageCaptureButtonState(text, busy = false, help = "") {
    setProductListButtonState(text, busy, help);
    setProductListCancelButtonState(busy && oneboundPageCapture?.phase === "running", "中断采集", false);
  }

  function renderOneboundPageCaptureStatus() {
    const existing = document.getElementById("temu-workbench-page-capture-status");
    if (!oneboundPageCapture) {
      existing?.remove();
      return;
    }
    existing?.remove();

    const card = document.createElement("section");
    card.id = "temu-workbench-page-capture-status";
    card.setAttribute("role", "status");
    card.setAttribute("aria-live", "polite");
    card.style.cssText = [
      "position:fixed", "right:14px", "bottom:14px", "z-index:2147483647",
      "width:min(320px,calc(100vw - 28px))", "box-sizing:border-box", "padding:11px 12px",
      "border:1px solid rgba(37,99,235,0.34)", "border-radius:9px", "background:rgba(255,255,255,0.98)",
      "box-shadow:0 10px 28px rgba(16,26,43,0.2)", "color:#102033",
      "font:12px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif"
    ].join(";");

    const heading = document.createElement("div");
    heading.style.cssText = "font-weight:800;margin-bottom:4px";
    heading.textContent = oneboundPageCapture.phase === "registered"
      ? "商品链接登记完成"
      : oneboundPageCapture.phase === "ready"
        ? "整页采集已就绪"
        : oneboundPageCapture.phase === "running"
        ? "整页采集进行中"
        : oneboundPageCapture.phase === "finished"
          ? "整页采集完成"
          : oneboundPageCapture.phase === "error"
            ? "整页采集失败"
            : "正在扫描整页";
    card.appendChild(heading);

    const summary = document.createElement("div");
    summary.textContent = `扫描/识别总数 ${oneboundPageCapture.scanTotal} · 已存在 ${oneboundPageCapture.existingCount} · 待采集 ${oneboundPageCapture.pendingTotal}`;
    card.appendChild(summary);

    const details = document.createElement("div");
    details.style.cssText = "margin-top:3px;color:#475569";
    if (oneboundPageCapture.phase === "running") {
      details.textContent = `进度 ${oneboundPageCapture.completedCount}/${oneboundPageCapture.workTotal} · 新建 ${oneboundPageCapture.createdCount} · 刷新 ${oneboundPageCapture.refreshedCount} · 跳过 ${oneboundPageCapture.skippedCount} · 失败 ${oneboundPageCapture.failedCount} · 未处理 ${oneboundPageCapture.unprocessedCount}`;
    } else if (oneboundPageCapture.phase === "finished") {
      details.textContent = `${oneboundPageCapture.cancelled ? "已中断" : "成功"} ${oneboundPageCapture.capturedCount} · 新建 ${oneboundPageCapture.createdCount} · 刷新 ${oneboundPageCapture.refreshedCount} · 跳过 ${oneboundPageCapture.skippedCount} · 失败 ${oneboundPageCapture.failedCount} · 未处理 ${oneboundPageCapture.unprocessedCount}`;
    } else {
      details.textContent = oneboundPageCapture.statusText || "正在识别当前列表页商品";
    }
    card.appendChild(details);

    if (oneboundPageCapture.phase === "finished" && oneboundPageCapture.failedUrls.length) {
      const actions = document.createElement("div");
      actions.style.cssText = "display:flex;gap:7px;margin-top:9px";
      const primary = document.createElement("button");
      primary.type = "button";
      primary.style.cssText = "border:0;border-radius:6px;padding:6px 9px;background:#2563eb;color:#fff;font:700 12px/1 inherit;cursor:pointer";
      primary.textContent = "仅重试失败项";
      primary.addEventListener("click", () => prepare1688OneboundPageCapture(oneboundPageCapture.failedUrls));
      actions.appendChild(primary);
      card.appendChild(actions);
    }
    document.documentElement.appendChild(card);
  }

  async function prepare1688OneboundPageCapture(sourceUrls = null) {
    if (!isOneboundCaptureListPage() || oneboundPageCapture?.phase === "preparing" || oneboundPageCapture?.phase === "running") return;
    const state = createOneboundPageCaptureState(location.href, ++oneboundPageCaptureGeneration);
    oneboundPageCapture = state;
    setOneboundPageCaptureButtonState("正在扫描...", true);
    renderOneboundPageCaptureStatus();
    const request = Array.isArray(sourceUrls) && sourceUrls.length
      ? oneboundRetryPrepareRequest(sourceUrls)
      : { type: "PREPARE_1688_ONEBOUND_PAGE_CAPTURE" };
    try {
      const response = await safeSendRuntimeMessage(request);
      if (!isCurrentOneboundPageCapture(state) || oneboundPageCapture !== state) {
        invalidateOneboundPageCaptureForNavigation();
        return;
      }
      applyOneboundPageCapturePrepareState(state, response);
      if (!response?.ok || !response?.batch_token) {
        state.phase = "error";
        state.statusText = response?.statusText || response?.help || response?.error || "整页扫描失败";
        writeCaptureRecord("list", { status: "failed", reason: state.statusText });
        setOneboundPageCaptureButtonState("采集失败 · 点击重试", false, state.statusText);
        renderOneboundPageCaptureStatus();
        return;
      }
      state.phase = "registered";
      state.batchToken = String(response.batch_token);
      if (!isCurrentOneboundPageCapture(state, state.batchToken) || oneboundPageCapture !== state) {
        invalidateOneboundPageCaptureForNavigation();
        return;
      }
      state.statusText = "链接已登记，请到工作台点击“启动采集”";
      const scanTotal = pageCaptureCount(state.scanTotal);
      const registeredLabel = scanTotal ? `已登记 ${scanTotal} 条链接` : "链接已登记";
      writeCaptureRecord("list", {
        status: "success",
        label: registeredLabel,
        summary: state.statusText,
        count: scanTotal
      });
      setOneboundPageCaptureButtonState(`${registeredLabel} · ${captureTimeLabel(Date.now())}`, true, state.statusText);
      const registeredRecapture = document.getElementById("temu-workbench-product-list-recapture");
      if (registeredRecapture) registeredRecapture.style.display = "block";
      showCaptureFeedback(state.statusText, "success");
      renderOneboundPageCaptureStatus();
    } catch (error) {
      if (!isCurrentOneboundPageCapture(state) || oneboundPageCapture !== state) {
        invalidateOneboundPageCaptureForNavigation();
        return;
      }
      state.phase = "error";
      state.statusText = String(error?.message || error || "整页扫描失败");
      writeCaptureRecord("list", { status: "failed", reason: state.statusText });
      setOneboundPageCaptureButtonState("采集失败 · 点击重试", false, state.statusText);
      showCaptureFeedback(`采集失败：${state.statusText.slice(0, 80)}`, "error");
      renderOneboundPageCaptureStatus();
    }
  }

  async function cancelPrepared1688OneboundPageCapture() {
    const state = oneboundPageCapture;
    if (state?.phase !== "ready" || !isCurrentOneboundPageCapture(state, state.batchToken)) return;
    state.phase = "cancelling";
    state.statusText = "正在取消本次整页采集";
    renderOneboundPageCaptureStatus();
    try {
      const response = await safeSendRuntimeMessage(oneboundPreparedCancelRequest(state.batchToken));
      if (!isCurrentOneboundPageCapture(state, state.batchToken) || oneboundPageCapture !== state) {
        invalidateOneboundPageCaptureForNavigation();
        return;
      }
      if (!response?.ok) {
        throw new Error(response?.statusText || response?.help || response?.error || "后端未接受取消请求");
      }
      clearOneboundPageCaptureState();
      refreshProductListCaptureButton();
    } catch (error) {
      if (!isCurrentOneboundPageCapture(state, state.batchToken) || oneboundPageCapture !== state) {
        invalidateOneboundPageCaptureForNavigation();
        return;
      }
      state.phase = "ready";
      state.statusText = `取消失败：${String(error?.message || error || "请重试")}`;
      renderOneboundPageCaptureStatus();
    }
  }

  function applyOneboundPageCaptureProgress(message) {
    const state = oneboundPageCapture;
    if (!isCurrentOneboundPageCapture(state, message?.batch_token) || oneboundPageCapture !== state) return;
    state.phase = "running";
    applyOneboundPageCaptureProgressState(state, message);
    setOneboundPageCaptureButtonState(`采集中 ${state.completedCount}/${state.workTotal}`, true, state.statusText);
    renderOneboundPageCaptureStatus();
  }

  async function start1688OneboundPageCapture() {
    if (oneboundPageCapture?.phase !== "ready" || !oneboundPageCapture.batchToken) return;
    const state = oneboundPageCapture;
    if (!isCurrentOneboundPageCapture(state, state.batchToken)) return;
    state.phase = "running";
    state.statusText = "正在采集待处理商品";
    setOneboundPageCaptureButtonState("正在采集...", true, state.statusText);
    renderOneboundPageCaptureStatus();
    try {
      const response = await safeSendRuntimeMessage({
        type: "START_1688_ONEBOUND_PAGE_CAPTURE",
        batch_token: state.batchToken
      });
      if (!isCurrentOneboundPageCapture(state, state.batchToken) || oneboundPageCapture !== state) {
        invalidateOneboundPageCaptureForNavigation();
        return;
      }
      applyOneboundPageCaptureFinal(state, response || {});
    } catch (error) {
      if (!isCurrentOneboundPageCapture(state, state.batchToken) || oneboundPageCapture !== state) {
        invalidateOneboundPageCaptureForNavigation();
        return;
      }
      state.phase = "error";
      state.statusText = String(error?.message || error || "整页采集失败");
      writeCaptureRecord("list", { status: "failed", reason: state.statusText });
      setOneboundPageCaptureButtonState("采集失败 · 点击重试", false, state.statusText);
      setProductListCancelButtonState(false);
      showCaptureFeedback(`采集失败：${state.statusText.slice(0, 80)}`, "error");
      renderOneboundPageCaptureStatus();
    }
  }

  function applyOneboundPageCaptureFinal(state, response) {
    if (!isCurrentOneboundPageCapture(state, state?.batchToken) || oneboundPageCapture !== state) return;
    if (!oneboundFinalHasUsableSummary(response)) {
      state.phase = "error";
      state.statusText = response?.statusText || response?.help || response?.error || "整页采集失败";
      writeCaptureRecord("list", { status: "failed", reason: state.statusText });
      setOneboundPageCaptureButtonState("采集失败 · 点击重试", false, state.statusText);
      setProductListCancelButtonState(false);
      showCaptureFeedback(`采集失败：${state.statusText.slice(0, 80)}`, "error");
      renderOneboundPageCaptureStatus();
      return;
    }
    state.phase = "finished";
    applyOneboundPageCaptureFinalState(state, response);
    if (state.cancelled) {
      clearCaptureRecord("list");
      setOneboundPageCaptureButtonState("已中断整页采集", false, state.statusText);
    } else {
      const label = state.capturedCount ? `已登记 ${state.capturedCount} 条` : "整页采集完成";
      writeCaptureRecord("list", { status: "success", label, summary: state.statusText, count: state.capturedCount });
      setOneboundPageCaptureButtonState(`${label} · ${captureTimeLabel(Date.now())}`, true, state.statusText);
      const recapture = document.getElementById("temu-workbench-product-list-recapture");
      if (recapture) recapture.style.display = "block";
      showCaptureFeedback(state.statusText, "success");
    }
    setProductListCancelButtonState(false);
    renderOneboundPageCaptureStatus();
    window.setTimeout(refreshProductListCaptureButton, 3600);
  }

  async function captureProductListToWorkbench() {
    setProductListButtonState("正在采集...", true);
    setProductListCancelButtonState(true, "中断采集", false);
    try {
      const response = await safeSendRuntimeMessage({ type: "CAPTURE_VISIBLE_PRODUCTS_TO_WORKBENCH" });
      if (!response?.ok) {
        const reason = String(response?.statusText || response?.help || response?.error || "批量采集失败").trim();
        writeCaptureRecord("list", { status: "failed", reason });
        setProductListButtonState("采集失败 · 点击重试", false, reason);
        setProductListCancelButtonState(false);
        showCaptureFeedback(`采集失败：${reason}`, "error");
        window.setTimeout(refreshProductListCaptureButton, 2400);
        return;
      }
      const captured = Number(response.captured_count || 0);
      const skipped = Number(response.skipped_count || 0);
      const deferred = Number(response.deferred_detail_count || 0);
      if (response.cancelled) {
        const reason = String(response.statusText || "已中断批量采集").trim();
        clearCaptureRecord("list");
        setProductListButtonState(reason, false, reason);
        setProductListCancelButtonState(false);
        showCaptureFeedback(reason, "info");
        window.setTimeout(refreshProductListCaptureButton, 2600);
        return;
      }
      if (response.risk_control_blocked) {
        const reason = String(response.statusText || "平台风控拦截，请稍后在页面重试").trim();
        writeCaptureRecord("list", { status: "failed", reason });
        setProductListButtonState("采集失败 · 点击重试", false, reason);
        setProductListCancelButtonState(false);
        showCaptureFeedback(reason, "error");
        window.setTimeout(refreshProductListCaptureButton, 2600);
        return;
      }
      const summary = [
        `已入池 ${captured} 个`,
        skipped ? `跳过 ${skipped} 个` : "",
        deferred ? `待下批 ${deferred} 个` : ""
      ].filter(Boolean).join(" · ");
      const label = `已登记 ${captured} 条`;
      writeCaptureRecord("list", { status: "success", label, summary, count: captured });
      setProductListButtonState(`${label} · ${captureTimeLabel(Date.now())}`, true, summary);
      const recapture = document.getElementById("temu-workbench-product-list-recapture");
      if (recapture) recapture.style.display = "block";
      setProductListCancelButtonState(false);
      showCaptureFeedback(summary, "success");
    } catch (error) {
      const message = String(error?.message || error || "后台消息无响应").replace(/\s+/g, " ").trim();
      writeCaptureRecord("list", { status: "failed", reason: message });
      setProductListButtonState("采集失败 · 点击重试", false, message);
      setProductListCancelButtonState(false);
      showCaptureFeedback(`采集失败：${message.slice(0, 80)}`, "error");
      window.setTimeout(refreshProductListCaptureButton, 2400);
    }
  }

  // ===== 订单利润采集（Temu 订单列表） =====
  function isOrderProfitPage() {
    let parsed;
    try {
      parsed = new URL(location.href);
    } catch (_error) {
      return false;
    }
    if (!/(^|\.)temu\.com$/i.test(parsed.hostname)) return false;
    const path = parsed.pathname.toLowerCase();
    const pageText = String(document.body?.innerText || "").replace(/\s+/g, " ").slice(0, 12000);
    if (/批量查看并确认申报价|批量查看并确认申报价格/.test(pageText)) return false;
    const hasOrderPath = /(?:^|\/)(?:orders?|order-list|orderlist|order_list|order-detail|orderdetail)(?:[\/.]|$)/.test(path);
    const hasOrderText = /订单列表|全部订单|订单详情|订单信息|子订单号|子订单信息|申报价格（CNY）/.test(pageText);
    return hasOrderText && (hasOrderPath || /订单列表|订单详情/.test(pageText));
  }

  function syncOrderProfitCaptureButton() {
    if (!isOrderProfitPage()) {
      document.getElementById("temu-workbench-order-profit-capture")?.remove();
      document.getElementById("temu-workbench-order-profit-status")?.remove();
      document.getElementById("temu-workbench-order-profit-copy")?.remove();
      return;
    }
    renderOrderProfitCaptureButton();
    refreshOrderProfitCaptureButton();
  }

  function renderOrderProfitCaptureButton() {
    if (!isOrderProfitPage()) return;
    const content = floatContentRoot();
    if (!content) return;
    if (content.querySelector("#temu-workbench-order-profit-capture")) return;

    const button = document.createElement("button");
    button.id = "temu-workbench-order-profit-capture";
    button.type = "button";
    button.textContent = "采集订单利润";
    button.title = "读取当前订单列表页的订单与子订单，生成利润预估所需数据";
    button.style.cssText = panelButtonStyle("#6d28d9", "rgba(109,40,217,0.48)");
    button.addEventListener("click", captureOrderProfitToPreview);
    content.appendChild(button);

    const status = document.createElement("div");
    status.id = "temu-workbench-order-profit-status";
    status.style.cssText = [
      "margin:-2px 0 8px",
      "font-size:11px",
      "line-height:1.5",
      "color:#64748b",
      "word-break:break-all"
    ].join(";");
    status.textContent = "读取当前页订单与子订单，供利润预估使用";
    content.appendChild(status);

    renderOrderProfitCopyButton();
  }

  function refreshOrderProfitCaptureButton() {
    const button = document.getElementById("temu-workbench-order-profit-capture");
    if (!button) return;
    button.disabled = orderProfitCaptureBusy;
    if (orderProfitCaptureBusy) {
      button.textContent = "正在采集订单…";
      button.style.opacity = "0.76";
      return;
    }
    button.style.opacity = "1";
    button.textContent = lastOrderProfitResult?.ok ? "重新采集订单" : "采集订单利润";
  }

  function setOrderProfitStatus(message, tone) {
    const status = document.getElementById("temu-workbench-order-profit-status");
    if (!status) return;
    status.textContent = message;
    status.style.color = tone === "ok" ? "#5f6d2e" : (tone === "error" ? "#b91c1c" : "#6b7280");
  }

  function renderOrderProfitCopyButton() {
    if (!lastOrderProfitResult?.ok) return;
    const content = floatContentRoot();
    if (!content || document.getElementById("temu-workbench-order-profit-copy")) return;
    const button = document.createElement("button");
    button.id = "temu-workbench-order-profit-copy";
    button.type = "button";
    button.textContent = "复制采集结果";
    button.className = "tw-ghost";
    button.style.cssText = [
      "display:block",
      "width:100%",
      "box-sizing:border-box",
      "padding:9px 12px",
      "margin-bottom:8px",
      "border-radius:10px",
      "border:1px solid #dde3ea",
      "background:#ffffff",
      "color:#3a4453",
      "text-align:left",
      "font:600 12px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "cursor:pointer"
    ].join(";");
    button.addEventListener("click", copyOrderProfitResult);
    content.appendChild(button);
  }

  async function captureOrderProfitToPreview() {
    if (orderProfitCaptureBusy) return;
    orderProfitCaptureBusy = true;
    refreshOrderProfitCaptureButton();
    setOrderProfitStatus("正在读取订单接口…", "busy");
    try {
      const response = await safeSendRuntimeMessage({ type: "CAPTURE_TEMU_ORDER_PROFIT" });
      if (!response?.ok) {
        lastOrderProfitResult = null;
        document.getElementById("temu-workbench-order-profit-copy")?.remove();
        setOrderProfitStatus(response?.statusText || "没有识别到订单数据", "error");
        return;
      }
      lastOrderProfitResult = response;
      const missing = response.missing || {};
      const parts = [
        `已采集 ${Number(response.order_count || 0)} 单 / ${Number(response.sub_order_count || 0)} 条子订单 / ${Number(response.item_count || 0)} 件`
      ];
      if (Number(response.gmv_total || 0) > 0) {
        parts.push(`GMV ${response.gmv_currency === "USD" ? "$" : "¥"}${Number(response.gmv_total).toFixed(2)}`);
      }
      if (Number(response.declared_total || 0) > 0) {
        parts.push(`申报价 ¥${Number(response.declared_total).toFixed(2)}`);
      }
      const financials = response.financial_totals || {};
      if (Number(financials.sales_payback || 0) > 0) {
        parts.push(`销售回款 ¥${Number(financials.sales_payback).toFixed(2)}`);
      }
      if (Number(missing.declared_price || 0) > 0) parts.push(`申报价待补 ${missing.declared_price}`);
      if (Number(missing.gmv || 0) > 0) parts.push(`GMV待补 ${missing.gmv}`);
      if (Number(missing.tail_freight || 0) > 0) parts.push(`尾程待补 ${missing.tail_freight}`);
      setOrderProfitStatus(parts.join(" · "), "ok");
      renderOrderProfitCopyButton();
    } catch (error) {
      const message = String(error?.message || error || "");
      if (/extension_context_invalidated/.test(message)) {
        setOrderProfitStatus("扩展已更新，请刷新当前 Temu 页面后再采集", "error");
      } else {
        setOrderProfitStatus(`采集失败：${message.slice(0, 80)}`, "error");
      }
    } finally {
      orderProfitCaptureBusy = false;
      refreshOrderProfitCaptureButton();
    }
  }

  async function copyOrderProfitResult() {
    if (!lastOrderProfitResult?.ok) return;
    const payload = {
      ...lastOrderProfitResult,
      items: (lastOrderProfitResult.items || []).slice(0, 500)
    };
    const text = JSON.stringify(payload, null, 2);
    try {
      await navigator.clipboard.writeText(text);
      setOrderProfitStatus("采集结果已复制到剪贴板", "ok");
    } catch (_error) {
      try {
        const area = document.createElement("textarea");
        area.value = text;
        area.style.cssText = "position:fixed;left:-9999px;top:0";
        document.documentElement.appendChild(area);
        area.select();
        document.execCommand("copy");
        area.remove();
        setOrderProfitStatus("采集结果已复制到剪贴板", "ok");
      } catch (copyError) {
        setOrderProfitStatus("复制失败，请打开插件弹窗查看结果", "error");
      }
    }
  }

  // ===== 悬浮抽屉式操作面板 =====
  // 统一「界野」墨色主按钮：白字、深墨底，悬停由注入的样式统一提亮。
  function panelButtonStyle() {
    return [
      "display:block",
      "width:100%",
      "box-sizing:border-box",
      "padding:10px 12px",
      "margin-bottom:8px",
      "border-radius:10px",
      "border:1px solid #1b2430",
      "background:#1b2430",
      "color:#ffffff",
      "text-align:left",
      "font:600 12.5px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "cursor:pointer",
      "transition:background .16s ease,border-color .16s ease,opacity .16s ease"
    ].join(";");
  }

  function ghostButtonStyle() {
    return [
      "display:block",
      "width:100%",
      "box-sizing:border-box",
      "padding:7px 12px",
      "margin:-2px 0 8px",
      "border-radius:9px",
      "border:1px solid #dfe5ec",
      "background:#ffffff",
      "color:#5b6675",
      "text-align:center",
      "font:600 11.5px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "cursor:pointer"
    ].join(";");
  }

  function extensionAsset(path) {
    try {
      return chrome.runtime.getURL(path);
    } catch (_error) {
      return path;
    }
  }

  /** 页面内注入的品牌样式：只负责悬停/焦点等无法内联的状态。 */
  function ensureFloatStyle() {
    if (document.getElementById("temu-workbench-float-style")) return;
    const style = document.createElement("style");
    style.id = "temu-workbench-float-style";
    style.textContent = [
      "#temu-workbench-float-panel button:not(:disabled):hover{background:#111a24 !important;border-color:#111a24 !important;}",
      "#temu-workbench-float-panel button.tw-ghost:not(:disabled):hover{background:#f4f6f9 !important;border-color:#c9d2de !important;color:#1b2430 !important;}",
      "#temu-workbench-float-panel button:disabled{opacity:.55;cursor:not-allowed;}",
      "#temu-workbench-float-panel button:focus-visible,#temu-workbench-float-handle:focus-visible{outline:2px solid rgba(27,36,48,.35);outline-offset:2px;}",
      "#temu-workbench-float-handle:hover{transform:translateY(-1px);box-shadow:0 10px 24px rgba(17,24,39,.22);}",
      "#temu-workbench-float-handle img{width:36px;height:36px;object-fit:contain;display:block;pointer-events:none;}",
      "#temu-workbench-float-handle{transition:transform .16s ease,box-shadow .16s ease;}"
    ].join("\n");
    (document.head || document.documentElement).appendChild(style);
  }

  function floatContentRoot() {
    return document.getElementById("temu-workbench-float-content");
  }

  function initFloatingControls() {
    if (document.getElementById("temu-workbench-float-handle")) return;
    ensureFloatStyle();

    const handle = document.createElement("button");
    handle.id = "temu-workbench-float-handle";
    handle.type = "button";
    handle.setAttribute("aria-label", "界野采集助手");
    handle.setAttribute("aria-expanded", "true");
    handle.title = "界野采集助手（点击展开/收起，拖拽可移动位置）";
    const handleIcon = document.createElement("img");
    handleIcon.src = extensionAsset("assets/jieye-char.png");
    handleIcon.alt = "界野";
    handle.appendChild(handleIcon);
    handle.style.cssText = [
      "position:fixed",
      "right:20px",
      "bottom:80px",
      "z-index:2147483647",
      "width:" + FLOAT_HANDLE_SIZE + "px",
      "height:" + FLOAT_HANDLE_SIZE + "px",
      "box-sizing:border-box",
      "padding:0",
      "border-radius:18px",
      "border:1px solid #e2e8f0",
      "background:#ffffff",
      "display:flex",
      "align-items:center",
      "justify-content:center",
      "cursor:grab",
      "box-shadow:0 8px 22px rgba(17,24,39,.16)",
      "user-select:none"
    ].join(";");

    const panel = document.createElement("div");
    panel.id = "temu-workbench-float-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "界野采集助手");
    panel.style.cssText = [
      "position:fixed",
      "right:74px",
      "bottom:80px",
      "z-index:2147483647",
      "width:min(268px,calc(100vw - 104px))",
      "box-sizing:border-box",
      "padding:12px",
      "border:1px solid #e8edf3",
      "border-radius:14px",
      "background:#ffffff",
      "box-shadow:0 14px 36px rgba(17,24,39,.16)",
      "color:#1b2430",
      "font:12px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif"
    ].join(";");

    const head = document.createElement("div");
    head.style.cssText = [
      "display:flex",
      "align-items:center",
      "gap:8px",
      "padding-bottom:10px",
      "margin-bottom:10px",
      "border-bottom:1px solid #eef1f5"
    ].join(";");
    const logoBox = document.createElement("span");
    logoBox.style.cssText = [
      "display:flex",
      "align-items:center",
      "justify-content:center",
      "flex:0 0 24px",
      "width:24px",
      "height:24px",
      "border-radius:7px",
      "background:#1b2430"
    ].join(";");
    const logoImg = document.createElement("img");
    logoImg.src = extensionAsset("assets/jieye-char-white.png");
    logoImg.alt = "界野";
    logoImg.style.cssText = "width:17px;height:17px;object-fit:contain;display:block;";
    logoBox.appendChild(logoImg);

    const title = document.createElement("span");
    title.textContent = "界野 · 采集助手";
    title.style.cssText = "flex:1;min-width:0;font:700 12.5px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;color:#1b2430;letter-spacing:.02em;white-space:nowrap;";

    const actionButton = (label, path) => {
      const button = document.createElement("button");
      button.type = "button";
      button.title = label;
      button.setAttribute("aria-label", label);
      button.style.cssText = [
        "display:grid",
        "place-items:center",
        "flex:0 0 22px",
        "width:22px",
        "height:22px",
        "padding:0",
        "border:1px solid #e8edf3",
        "border-radius:7px",
        "background:#ffffff",
        "color:#5b6675",
        "cursor:pointer"
      ].join(";");
      button.innerHTML = '<svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
        + path + "</svg>";
      return button;
    };
    const closeButton = actionButton("收起面板",
      '<path d="M6 6l12 12M18 6 6 18"/>');
    closeButton.addEventListener("click", (event) => {
      event.stopPropagation();
      if (floatOpen) toggleFloatPanel();
    });

    head.append(logoBox, title, closeButton);

    // 横向两个主操作：启动/回到界野、采集到工作台。
    const actionStyle = (primary) => [
      "flex:1",
      "min-width:0",
      "height:34px",
      "padding:0 10px",
      "border-radius:9px",
      primary ? "border:1px solid #1b2430" : "border:1px solid #d6dde6",
      primary ? "background:#1b2430" : "background:#ffffff",
      primary ? "color:#ffffff" : "color:#1b2430",
      "font:600 12px/1 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "cursor:pointer",
      "white-space:nowrap",
      "overflow:hidden",
      "text-overflow:ellipsis"
    ].join(";");
    const actionBar = document.createElement("div");
    actionBar.style.cssText = "display:flex;gap:8px;margin-bottom:10px;";
    const launchButton = document.createElement("button");
    launchButton.type = "button";
    launchButton.textContent = "启动界野";
    launchButton.style.cssText = actionStyle(false);
    launchButton.title = "打开或回到本机界野工作台";
    launchButton.addEventListener("click", (event) => {
      event.stopPropagation();
      void chrome.runtime.sendMessage({ type: "OPEN_WORKBENCH_TAB" }).catch(() => {});
    });
    const captureToWorkbenchButton = document.createElement("button");
    captureToWorkbenchButton.type = "button";
    captureToWorkbenchButton.textContent = "采集到工作台";
    captureToWorkbenchButton.style.cssText = actionStyle(true);
    captureToWorkbenchButton.title = "把当前页面的数据采集到本机工作台";
    captureToWorkbenchButton.addEventListener("click", (event) => {
      event.stopPropagation();
      const buttons = [...content.querySelectorAll("button")];
      // 已完成采集的页面优先走“重新采集”，避免误以为还能重复采集。
      const recapture = buttons.find((item) =>
        /重新采集|重新扫描|重新采集本页/.test(item.textContent || "") && getComputedStyle(item).display !== "none"
      );
      const target = recapture || buttons.find((item) =>
        /批量采集本页|整页采集|采集核价本页|采集订单利润|采集到工作台/.test(item.textContent || "")
      );
      if (target && !target.disabled) {
        target.click();
        return;
      }
      const done = buttons.some((item) => /已采集|已登记|链接已登记/.test(item.textContent || ""));
      captureToWorkbenchButton.textContent = done ? "本页已采集完成" : target ? "正在自动连接工作台" : "当前页面无可用采集";
      window.setTimeout(() => {
        captureToWorkbenchButton.textContent = "采集到工作台";
      }, 1800);
    });
    actionBar.append(launchButton, captureToWorkbenchButton);

    // 连接 / 配对同样放进浮窗，横向一行，不再需要打开插件弹窗。
    const smallGhost = (label) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.style.cssText = "flex:0 0 auto;height:28px;padding:0 10px;border:1px solid #d6dde6;border-radius:8px;background:#ffffff;color:#1b2430;font:600 11.5px/1 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;cursor:pointer;white-space:nowrap;";
      return button;
    };
    const connBar = document.createElement("div");
    connBar.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:8px;";
    const connDot = document.createElement("span");
    connDot.style.cssText = "flex:0 0 7px;width:7px;height:7px;border-radius:999px;background:#d97706;box-shadow:0 0 0 3px rgba(217,119,6,.12);";
    const connText = document.createElement("span");
    connText.textContent = "待连接";
    connText.style.cssText = "flex:1;min-width:0;font:12px/1.3 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;color:#5b6675;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;";
    const connectButton = smallGhost("连接");
    const pairToggle = smallGhost("配对");
    connBar.append(connDot, connText, connectButton, pairToggle);

    const pairRow = document.createElement("div");
    pairRow.style.cssText = "display:none;gap:8px;margin-bottom:8px;";
    const pairInput = document.createElement("input");
    pairInput.maxLength = 6;
    pairInput.placeholder = "6 位配对码";
    pairInput.inputMode = "numeric";
    pairInput.style.cssText = "flex:1;min-width:0;height:32px;padding:0 10px;border:1px solid #d6dde6;border-radius:8px;background:#ffffff;color:#1b2430;font:12px/1 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;";
    pairInput.addEventListener("click", (event) => event.stopPropagation());
    const pairSubmit = smallGhost("确认配对");
    pairSubmit.style.height = "32px";
    pairRow.append(pairInput, pairSubmit);

    // 未绑定时只在刚打开的几秒里显示“正在自动连接”，之后给明确的绑定入口，不长期挂着中间态。
    const connPendingSince = Date.now();
    const updateConnState = () => safeStorageGet(["connectionContext"], (settings) => {
      let connected = false;
      try {
        connected = Boolean(tenantContext.validateConnectionContext(settings?.connectionContext));
      } catch (_error) {
        connected = false;
      }
      connDot.style.background = connected ? "#0f9d6e" : "#d97706";
      connDot.style.boxShadow = connected ? "0 0 0 3px rgba(15,157,110,.12)" : "0 0 0 3px rgba(217,119,6,.12)";
      const pending = Date.now() - connPendingSince < 8000;
      connText.textContent = connected
        ? "已绑定账号 · 自动同步"
        : pending
          ? "正在自动连接本机工作台…"
          : "未绑定账号 · 点「配对」绑定";
      // 连接完全自动化：不再让用户手动点“连接/重连”；未绑定时只留一个轻量“配对”入口。
      connectButton.style.display = "none";
      pairToggle.style.display = connected ? "none" : "inline-flex";
    });
    updateConnState();
    window.setInterval(updateConnState, 5000);

    connectButton.addEventListener("click", (event) => {
      event.stopPropagation();
      connectButton.disabled = true;
      connText.textContent = "正在连接…";
      chrome.runtime.sendMessage({ type: "AUTO_CONNECT_WORKBENCH" }).then((result) => {
        connectButton.disabled = false;
        connText.textContent = result?.ok ? "已连接工作台" : "连接失败，请确认工作台已启动";
        updateConnState();
      }, () => {
        connectButton.disabled = false;
        connText.textContent = "连接失败";
      });
    });
    pairToggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const visible = pairRow.style.display === "flex";
      pairRow.style.display = visible ? "none" : "flex";
      if (!visible) pairInput.focus();
    });
    pairSubmit.addEventListener("click", (event) => {
      event.stopPropagation();
      const code = String(pairInput.value || "").trim();
      if (!/^\d{6}$/.test(code)) {
        pairInput.focus();
        return;
      }
      pairSubmit.disabled = true;
      connText.textContent = "正在配对…";
      chrome.runtime.sendMessage({ type: "PAIR_DEVICE", code }).then((result) => {
        pairSubmit.disabled = false;
        if (result?.ok) {
          pairInput.value = "";
          pairRow.style.display = "none";
          connText.textContent = result.connected ? "配对成功，已连接" : "配对成功，正在连接…";
          updateConnState();
        } else {
          connText.textContent = result?.error || "配对失败";
        }
      }, () => {
        pairSubmit.disabled = false;
        connText.textContent = "配对失败";
      });
    });
    const refreshLaunchLabel = async () => {
      try {
        const status = await chrome.runtime.sendMessage({ type: "GET_WORKBENCH_STATUS" });
        launchButton.textContent = status?.reachable ? "回到界野" : "启动界野";
      } catch (_error) {
        launchButton.textContent = "启动界野";
      }
    };
    void refreshLaunchLabel();
    window.setInterval(() => void refreshLaunchLabel(), 8000);

    const content = document.createElement("div");
    content.id = "temu-workbench-float-content";

    // 采集反馈：实时状态（采集中 3/10、已登记、失败原因）优先，空闲时回落到本页上次采集结果。
    const feedback = document.createElement("p");
    feedback.id = "temu-workbench-float-feedback";
    feedback.setAttribute("role", "status");
    feedback.setAttribute("aria-live", "polite");
    feedback.style.cssText = "display:none;margin:0 0 8px;padding:7px 9px;border-radius:8px;border:1px solid #eef1f5;background:#f5f7f9;color:#5b6675;font:11.5px/1.45 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif;word-break:break-all;";
    captureFeedbackEl = feedback;
    const staticLabelRe = /^(?:采集到工作台|批量采集本页|整页采集|采集订单利润|重新采集|重新扫描|重新采集本页|请先连接工作台)$/;
    const captureStateRe = /采集中|正在采集|已入库|已入池|已登记|链接已登记|采集完成|整页采集完成|已中断|已采集|采集失败|入库失败|扫描失败/;
    const feedbackObserver = new MutationObserver(() => {
      const texts = [...content.querySelectorAll("button")]
        .map((item) => String(item.textContent || "").trim())
        .filter((text) => text && !staticLabelRe.test(text) && captureStateRe.test(text));
      const text = texts[texts.length - 1] || "";
      if (!text) {
        if (!captureFeedbackTransientTimer) paintCaptureFeedbackBaseline();
        return;
      }
      if (text === captureFeedbackLastText) return;
      const tone = /失败|错误|无法/.test(text) ? "error" : /成功|完成|已入库|已入池|已登记|已采集/.test(text) ? "success" : "info";
      showCaptureFeedback(text, tone);
    });
    feedbackObserver.observe(content, { childList: true, subtree: true, characterData: true });
    refreshCaptureFeedbackBaseline();

    panel.append(head, actionBar, connBar, pairRow, feedback, content);

    document.documentElement.appendChild(handle);
    document.documentElement.appendChild(panel);

    floatHandleEl = handle;
    floatPanelEl = panel;

    // 默认展开，并压过店小秘等同样使用 2147483647 的浮层：
    // 同级 z-index 时后出现的在上，所以定期把自己重新追加到 html 末尾。
    panel.style.display = floatOpen ? "block" : "none";
    const keepFloatTopmost = () => {
      if (!floatHandleEl || !floatPanelEl) return;
      const root = document.documentElement;
      if (root.lastElementChild !== floatPanelEl) {
        root.appendChild(floatHandleEl);
        root.appendChild(floatPanelEl);
      }
    };
    window.setInterval(keepFloatTopmost, 1200);

    // 恢复拖拽位置（转成 left/top 定位）；没有记录时默认停靠最左侧、上下居中。
    let restoredPosition = false;
    try {
      const saved = JSON.parse(localStorage.getItem(FLOAT_POSITION_KEY) || "null");
      if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
        positionHandle(saved.x, saved.y);
        restoredPosition = true;
      }
    } catch (_error) {
      localStorage.removeItem(FLOAT_POSITION_KEY);
    }
    if (!restoredPosition) {
      positionHandle(16, Math.max(8, Math.round((window.innerHeight - FLOAT_HANDLE_SIZE) / 2)));
    }
    positionPanelRelativeToHandle();

    handle.addEventListener("click", (event) => {
      event.stopPropagation();
      if (floatDrag.moved) {
        floatDrag.moved = false;
        return;
      }
      toggleFloatPanel();
    });
    handle.addEventListener("pointerdown", startHandleDrag);
    document.addEventListener("pointermove", onHandleDrag);
    document.addEventListener("pointerup", endHandleDrag);
    document.addEventListener("click", onDocumentClickForFloat);
    window.addEventListener("resize", positionPanelRelativeToHandle);
  }

  function toggleFloatPanel() {
    floatOpen = !floatOpen;
    if (!floatPanelEl) return;
    floatPanelEl.style.display = floatOpen ? "block" : "none";
    if (floatHandleEl) floatHandleEl.setAttribute("aria-expanded", String(floatOpen));
    if (floatOpen) positionPanelRelativeToHandle();
  }

  function onDocumentClickForFloat(event) {
    if (!floatOpen || !floatPanelEl) return;
    if (!floatPanelEl.contains(event.target) && event.target !== floatHandleEl) {
      toggleFloatPanel();
    }
  }

  function startHandleDrag(event) {
    if (event.button !== 0 || !floatHandleEl) return;
    const rect = floatHandleEl.getBoundingClientRect();
    floatDrag.active = true;
    floatDrag.moved = false;
    floatDrag.startX = event.clientX;
    floatDrag.startY = event.clientY;
    floatDrag.originLeft = rect.left;
    floatDrag.originTop = rect.top;
    floatHandleEl.style.cursor = "grabbing";
    event.preventDefault();
  }

  function onHandleDrag(event) {
    if (!floatDrag.active || !floatHandleEl) return;
    const dx = event.clientX - floatDrag.startX;
    const dy = event.clientY - floatDrag.startY;
    if (Math.abs(dx) > 5 || Math.abs(dy) > 5) floatDrag.moved = true;
    const left = clampHandlePosition(floatDrag.originLeft + dx, window.innerWidth - FLOAT_HANDLE_SIZE);
    const top = clampHandlePosition(floatDrag.originTop + dy, window.innerHeight - FLOAT_HANDLE_SIZE);
    positionHandle(left, top);
    positionPanelRelativeToHandle();
  }

  function endHandleDrag() {
    if (!floatDrag.active) return;
    floatDrag.active = false;
    if (floatHandleEl) floatHandleEl.style.cursor = "grab";
    try {
      localStorage.setItem(FLOAT_POSITION_KEY, JSON.stringify({ x: floatHandleEl.offsetLeft, y: floatHandleEl.offsetTop }));
    } catch (_error) {
      // 存储不可用时忽略，仅本次页面内移动
    }
  }

  function positionHandle(left, top) {
    if (!floatHandleEl) return;
    floatHandleEl.style.left = String(Math.round(left)) + "px";
    floatHandleEl.style.top = String(Math.round(top)) + "px";
    floatHandleEl.style.right = "auto";
    floatHandleEl.style.bottom = "auto";
  }

  function clampHandlePosition(value, max) {
    return Math.max(0, Math.min(Math.round(value), Math.max(0, max)));
  }

  function positionPanelRelativeToHandle() {
    if (!floatHandleEl || !floatPanelEl) return;
    const rect = floatHandleEl.getBoundingClientRect();
    const panelWidth = Math.min(268, window.innerWidth - 104);
    const gap = 8;
    let left = rect.right + gap;
    if (left + panelWidth > window.innerWidth - 8) {
      left = Math.max(8, rect.left - panelWidth - gap);
    }
    const top = Math.max(8, Math.min(rect.top, window.innerHeight - 240));
    floatPanelEl.style.left = String(Math.round(left)) + "px";
    floatPanelEl.style.top = String(Math.round(top)) + "px";
    floatPanelEl.style.right = "auto";
    floatPanelEl.style.bottom = "auto";
  }
})();
