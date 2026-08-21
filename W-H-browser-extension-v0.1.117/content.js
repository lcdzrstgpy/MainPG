(() => {
  if (window.__temuWorkbenchConnector) return;
  window.__temuWorkbenchConnector = true;
  const tenantContext = globalThis.WorkbenchTenantContext;
  const PRODUCT_CAPTURE_HOST_RE = /(^|\.)(temu|1688|alibaba|pinduoduo|yangkeduo|amazon)\.com$/i;
  let productListCaptureBusy = false;
  let priceQuoteCaptureBusy = false;
  let oneboundPageCapture = null;
  let oneboundPageCaptureGeneration = 0;
  let extensionContextInvalidated = false;

  renderConnectorBadge();
  refreshConnectorBadge();
  syncProductCaptureButton();
  syncProductListCaptureButton();
  syncPriceQuoteCaptureButton();
  safeInterval(syncProductCaptureButton, 1500);
  safeInterval(syncProductListCaptureButton, 1800);
  safeInterval(syncPriceQuoteCaptureButton, 1800);
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
    return /Extension context invalidated|context invalidated|message port closed|Receiving end does not exist/i.test(message);
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
      "temu-workbench-product-list-capture",
      "temu-workbench-product-list-cancel",
      "temu-workbench-page-capture-status"
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

  function renderConnectorBadge() {
    if (document.getElementById("temu-workbench-connector-badge")) return;
    const badge = document.createElement("div");
    badge.id = "temu-workbench-connector-badge";
    badge.setAttribute("role", "status");
    badge.setAttribute("aria-live", "polite");
    badge.style.cssText = [
      "position:fixed",
      "left:14px",
      "bottom:14px",
      "z-index:2147483647",
      "display:flex",
      "align-items:center",
      "gap:7px",
      "height:32px",
      "max-width:220px",
      "box-sizing:border-box",
      "padding:0 11px",
      "border:1px solid rgba(15,159,154,0.42)",
      "border-radius:7px",
      "background:rgba(255,255,255,0.96)",
      "box-shadow:0 8px 22px rgba(16,26,43,0.18)",
      "color:#102033",
      "font:12px/1.2 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "pointer-events:none"
    ].join(";");

    const dot = document.createElement("span");
    dot.dataset.role = "dot";
    dot.style.cssText = [
      "width:8px",
      "height:8px",
      "border-radius:999px",
      "background:#f59e0b",
      "box-shadow:0 0 0 3px rgba(245,158,11,0.16)",
      "flex:0 0 auto"
    ].join(";");

    const text = document.createElement("span");
    text.dataset.role = "text";
    text.textContent = "AI工作台 待连接";
    text.style.cssText = [
      "overflow:hidden",
      "text-overflow:ellipsis",
      "white-space:nowrap",
      "font-weight:700"
    ].join(";");

    badge.append(dot, text);
    document.documentElement.appendChild(badge);
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
      dot.style.background = connected ? "#0f9f9a" : "#f59e0b";
      dot.style.boxShadow = connected ? "0 0 0 3px rgba(15,159,154,0.16)" : "0 0 0 3px rgba(245,158,11,0.16)";
    }
    if (text) {
      text.textContent = connected ? `AI工作台 公司 ${companyCode} 已连接` : "AI工作台 待连接";
    }
    badge.title = connected
      ? `W-H Workbench Connector 已连接公司 ${companyCode}`
      : "TEMU Y2 AI Workbench Connector 已注入页面，打开扩展弹窗连接工作台";
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
    if (!isSupportedProductPage()) {
      if (button) button.remove();
      return;
    }
    renderProductCaptureButton();
    refreshProductCaptureButton();
  }

  function renderProductCaptureButton() {
    if (!isSupportedProductPage()) return;
    if (document.getElementById("temu-workbench-product-capture")) return;
    const button = document.createElement("button");
    button.id = "temu-workbench-product-capture";
    button.type = "button";
    button.textContent = "采集到工作台";
    button.style.cssText = [
      "position:fixed",
      "left:14px",
      "bottom:54px",
      "z-index:2147483647",
      "height:36px",
      "max-width:180px",
      "box-sizing:border-box",
      "padding:0 13px",
      "border:1px solid rgba(15,159,154,0.52)",
      "border-radius:8px",
      "background:#0f9f9a",
      "box-shadow:0 10px 24px rgba(16,26,43,0.22)",
      "color:#ffffff",
      "font:700 13px/1 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "cursor:pointer"
    ].join(";");
    button.addEventListener("click", captureProductToWorkbench);
    document.documentElement.appendChild(button);
  }

  function refreshProductCaptureButton() {
    const button = document.getElementById("temu-workbench-product-capture");
    if (!button) return;
    safeStorageGet(["connectionContext"], (settings) => {
      let connected = false;
      try {
        connected = Boolean(tenantContext.validateConnectionContext(settings?.connectionContext));
      } catch (_error) {
        connected = false;
      }
      button.disabled = !connected;
      button.textContent = connected ? "采集到工作台" : "先连接工作台";
      button.style.opacity = connected ? "1" : "0.72";
      button.title = connected ? "采集当前商品标题和主图，直接加入产品处理待处理池" : "请先打开扩展弹窗连接本地工作台";
    });
  }

  function setCaptureButtonState(text, busy = false, help = "") {
    const button = document.getElementById("temu-workbench-product-capture");
    if (!button) return;
    button.textContent = text;
    button.disabled = busy;
    button.title = String(help || text || "").trim();
  }

  async function captureProductToWorkbench() {
    setCaptureButtonState("正在采集...", true);
    try {
      const response = await safeSendRuntimeMessage({ type: "CAPTURE_PRODUCT_TO_WORKBENCH" });
      if (!response?.ok) {
        setCaptureButtonState(response?.statusText || "采集失败", false, response?.help || response?.error || "");
        window.setTimeout(refreshProductCaptureButton, 2600);
        return;
      }
      setCaptureButtonState("已加入待处理", false);
      window.setTimeout(refreshProductCaptureButton, 2600);
    } catch (error) {
      setCaptureButtonState(`采集失败`, false);
      window.setTimeout(refreshProductCaptureButton, 2600);
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

  function isCurrentOneboundPageCapture(state, batchToken = null) {
    return is1688ProductListPage()
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
    if (!/(^|\.)temu\.com$/i.test(parsed.hostname)) return false;
    const pageText = String(document.body?.innerText || "").replace(/\s+/g, " ").slice(0, 18000);
    const tableHeaders = Array.from(document.querySelectorAll("th, [role='columnheader'], .ant-table-thead, .semi-table-thead"))
      .map((element) => String(element?.innerText || element?.textContent || "").replace(/\s+/g, " ").trim())
      .join(" ");
    const title = `${document.title || ""} ${pageText.slice(0, 1200)}`;
    if (!/批量查看并确认申报价|批量查看并确认申报价格/.test(title)) return false;
    const headers = ["SKC信息", "SKU属性", "原申报价", "调整后申报价", "新申报价"];
    const headerEvidence = `${pageText} ${tableHeaders}`;
    const headerCount = headers.filter((header) => headerEvidence.includes(header)).length;
    if (headerCount < 3) return false;
    return true;
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
    if (!isSupportedPriceQuotePage() || document.getElementById("temu-price-quote-capture")) return;
    const button = document.createElement("button");
    button.id = "temu-price-quote-capture";
    button.type = "button";
    button.textContent = "采集核价本页";
    button.style.cssText = [
      "position:fixed", "left:14px", "bottom:96px", "z-index:2147483647",
      "height:36px", "max-width:180px", "box-sizing:border-box", "padding:0 13px",
      "border:1px solid rgba(124,58,237,0.52)", "border-radius:8px",
      "background:#7c3aed", "box-shadow:0 10px 24px rgba(16,26,43,0.22)",
      "color:#ffffff", "font:700 13px/1 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "cursor:pointer"
    ].join(";");
    button.addEventListener("click", capturePriceQuotePageToWorkbench);
    document.documentElement.appendChild(button);
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
    const cancelButton = document.getElementById("temu-workbench-product-list-cancel");
    if (!isSupportedProductListPage()) {
      if (button) button.remove();
      if (cancelButton) cancelButton.remove();
      document.getElementById("temu-workbench-page-capture-status")?.remove();
      return;
    }
    renderProductListCaptureButton();
    refreshProductListCaptureButton();
  }

  function renderProductListCaptureButton() {
    if (!isSupportedProductListPage()) return;
    if (document.getElementById("temu-workbench-product-list-capture")) {
      renderProductListCancelButton();
      return;
    }
    const button = document.createElement("button");
    button.id = "temu-workbench-product-list-capture";
    button.type = "button";
    button.textContent = is1688ProductListPage() ? "整页采集（万邦）" : "批量采集本页";
    button.style.cssText = [
      "position:fixed",
      "left:14px",
      "bottom:96px",
      "z-index:2147483647",
      "height:36px",
      "max-width:180px",
      "box-sizing:border-box",
      "padding:0 13px",
      "border:1px solid rgba(37,99,235,0.48)",
      "border-radius:8px",
      "background:#2563eb",
      "box-shadow:0 10px 24px rgba(16,26,43,0.22)",
      "color:#ffffff",
      "font:700 13px/1 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "cursor:pointer"
    ].join(";");
    button.addEventListener("click", () => {
      if (is1688ProductListPage()) {
        prepare1688OneboundPageCapture();
        return;
      }
      captureProductListToWorkbench();
    });
    document.documentElement.appendChild(button);
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
      "left:14px",
      "bottom:138px",
      "z-index:2147483647",
      "height:34px",
      "max-width:180px",
      "box-sizing:border-box",
      "padding:0 13px",
      "border:1px solid rgba(220,38,38,0.5)",
      "border-radius:8px",
      "background:#dc2626",
      "box-shadow:0 10px 24px rgba(16,26,43,0.22)",
      "color:#ffffff",
      "font:700 13px/1 -apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif",
      "cursor:pointer",
      "display:none"
    ].join(";");
    button.addEventListener("click", cancelProductListCapture);
    document.documentElement.appendChild(button);
  }

  function refreshProductListCaptureButton() {
    const button = document.getElementById("temu-workbench-product-list-capture");
    if (!button) return;
    if (productListCaptureBusy) return;
    safeStorageGet(["connectionContext"], (settings) => {
      if (productListCaptureBusy) return;
      let connected = false;
      try {
        connected = Boolean(tenantContext.validateConnectionContext(settings?.connectionContext));
      } catch (_error) {
        connected = false;
      }
      button.disabled = !connected;
      button.textContent = connected ? (is1688ProductListPage() ? "整页采集（万邦）" : "批量采集本页") : "先连接工作台";
      button.style.opacity = connected ? "1" : "0.72";
      button.title = !connected
        ? "请先打开扩展弹窗连接本地工作台"
        : is1688ProductListPage()
          ? "先扫描并识别当前 1688 列表页；确认后再采集待处理商品"
          : "采集当前列表页商品链接，逐个打开详情页复用单品采集，自动跳过广告、推广和店铺卡片";
    });
  }

  function setProductListButtonState(text, busy = false, help = "") {
    const button = document.getElementById("temu-workbench-product-list-capture");
    if (!button) return;
    productListCaptureBusy = Boolean(busy);
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
    if (!is1688ProductListPage() || oneboundPageCapture?.phase === "preparing" || oneboundPageCapture?.phase === "running") return;
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
        setOneboundPageCaptureButtonState("整页采集失败", false, state.statusText);
        renderOneboundPageCaptureStatus();
        return;
      }
      state.phase = "registered";
      state.batchToken = String(response.batch_token);
      if (!isCurrentOneboundPageCapture(state, state.batchToken) || oneboundPageCapture !== state) {
        invalidateOneboundPageCaptureForNavigation();
        return;
      }
      state.statusText = "链接已登记，请到工作台点击“启动万邦采集”";
      setOneboundPageCaptureButtonState("链接已登记", true, state.statusText);
      renderOneboundPageCaptureStatus();
    } catch (error) {
      if (!isCurrentOneboundPageCapture(state) || oneboundPageCapture !== state) {
        invalidateOneboundPageCaptureForNavigation();
        return;
      }
      state.phase = "error";
      state.statusText = String(error?.message || error || "整页扫描失败");
      setOneboundPageCaptureButtonState("整页采集失败", false, state.statusText);
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
      setOneboundPageCaptureButtonState("整页采集失败", false, state.statusText);
      setProductListCancelButtonState(false);
      renderOneboundPageCaptureStatus();
    }
  }

  function applyOneboundPageCaptureFinal(state, response) {
    if (!isCurrentOneboundPageCapture(state, state?.batchToken) || oneboundPageCapture !== state) return;
    if (!oneboundFinalHasUsableSummary(response)) {
      state.phase = "error";
      state.statusText = response?.statusText || response?.help || response?.error || "整页采集失败";
      setOneboundPageCaptureButtonState("整页采集失败", false, state.statusText);
      setProductListCancelButtonState(false);
      renderOneboundPageCaptureStatus();
      return;
    }
    state.phase = "finished";
    applyOneboundPageCaptureFinalState(state, response);
    setOneboundPageCaptureButtonState(state.cancelled ? "已中断整页采集" : "整页采集完成", false, state.statusText);
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
        setProductListButtonState(response?.statusText || "批量采集失败", false, response?.help || response?.error || "");
        setProductListCancelButtonState(false);
        window.setTimeout(refreshProductListCaptureButton, 3200);
        return;
      }
      const captured = Number(response.captured_count || 0);
      const skipped = Number(response.skipped_count || 0);
      const deferred = Number(response.deferred_detail_count || 0);
      if (response.cancelled && response.statusText) {
        setProductListButtonState(response.statusText, false);
      } else if (response.risk_control_blocked && response.statusText) {
        setProductListButtonState(response.statusText, false);
      } else {
        setProductListButtonState(`已入池${captured}个 跳过${skipped}个${deferred ? ` 待下批${deferred}个` : ""}`, false);
      }
      setProductListCancelButtonState(false);
      window.setTimeout(refreshProductListCaptureButton, 3600);
    } catch (_error) {
      setProductListButtonState("批量采集失败", false);
      setProductListCancelButtonState(false);
      window.setTimeout(refreshProductListCaptureButton, 3200);
    }
  }
})();
