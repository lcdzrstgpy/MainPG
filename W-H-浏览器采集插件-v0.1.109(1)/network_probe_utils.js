(function attachNetworkProbeUtils(root) {
  const SENSITIVE_KEY_RE = /token|cookie|authorization|password|passwd|secret|csrf|session/i;
  const MAX_RESPONSE_CHARS = 4 * 1024 * 1024;
  const MAX_RECORDS = 120;

  function endpointFromUrl(url) {
    try {
      return new URL(url, location.href).pathname;
    } catch (_error) {
      return "";
    }
  }

  function parsedBusinessUrl(url) {
    try {
      const parsed = new URL(url, location.href);
      const isTemu = /(^|\.)temu\.com$/i.test(parsed.hostname);
      const isDxm = /(^|\.)dianxiaomi\.com$/i.test(parsed.hostname);
      const isProductSource = /(^|\.)(1688|alibaba|pinduoduo|yangkeduo)\.com$/i.test(parsed.hostname);
      const hasSensitivePath = /cookie|token|authorization|password|passwd|secret|csrf|session/i.test(parsed.pathname);
      return { parsed, isBusinessHost: (isTemu || isDxm || isProductSource) && !hasSensitivePath };
    } catch (_error) {
      return { parsed: null, isBusinessHost: false };
    }
  }

  function isLikelyJsonBusinessEndpoint(url) {
    const { parsed, isBusinessHost } = parsedBusinessUrl(url);
    if (!parsed || !isBusinessHost) return false;
    return /^\/api\//i.test(parsed.pathname)
      || /^\/mms\//i.test(parsed.pathname)
      || /^\/mmsos\//i.test(parsed.pathname)
      || /^\/ms\//i.test(parsed.pathname)
      || /^\/oms\//i.test(parsed.pathname)
      || /\/visage-|\/bg\//i.test(parsed.pathname);
  }

  function currentPagePath() {
    try {
      return new URL(location.href).pathname;
    } catch (_error) {
      return "";
    }
  }

  function shouldCaptureUrl(url, captureType) {
    const endpoint = endpointFromUrl(url);
    if (captureType === "capture_temu_goods") {
      return endpoint.includes("/visage-agent-seller/product/skc/pageQuery");
    }
    if (captureType === "capture_temu_flux" || captureType === "temu_flux_by_spu") {
      return endpoint.includes("/api/flow/analysis/list");
    }
    if (captureType === "temu_endpoint_discovery") {
      return isLikelyJsonBusinessEndpoint(url);
    }
    if (captureType === "temu_price_quote_discovery") {
      const { parsed, isBusinessHost } = parsedBusinessUrl(url);
      if (!parsed || !isBusinessHost || !isLikelyJsonBusinessEndpoint(url)) return false;
      const endpointText = `${endpoint} ${parsed.search}`.toLowerCase();
      const pageText = currentPagePath().toLowerCase();
      const text = `${endpointText} ${pageText}`;
      if (/accept|reject|negotiate|submit|publish|remove|delete|batch\.review|change\.sku\.price|confirm\.order|commit|save/.test(text)) return false;
      if (/price\/review|price\/re-price-review|bargain-no-bom\/batch\/info\/query|priceorder\/query|recommendedprice\.query/.test(endpointText)) return true;
      if (/action=bg\.(?:semi\.price\.review\.page\.query\.order|local\.goods\.priceorder\.query|glo\.product\.search)/.test(endpointText)) return true;
      return false;
    }
    if (captureType === "capture_temu_orders" || captureType === "temu_orders_by_sku") {
      return (/order/i.test(endpoint) && isLikelyJsonBusinessEndpoint(url))
        || (/order/i.test(currentPagePath()) && isLikelyJsonBusinessEndpoint(url));
    }
    if (captureType === "product_capture_current_page" || captureType === "product_capture_to_workbench") {
      const { parsed, isBusinessHost } = parsedBusinessUrl(url);
      if (!parsed || !isBusinessHost) return false;
      const text = `${parsed.hostname} ${parsed.pathname} ${parsed.search}`.toLowerCase();
      if (/receiveaddress|address|logistics|freight|coupon|cart|member|login/.test(text)) return false;
      return /offer|detail|product|goods|item|sku|spec|price|stock|inventory|sale|trade|widget|apollo|mtop|h5/.test(text);
    }
    return false;
  }

  function redactSensitive(value) {
    if (Array.isArray(value)) {
      return value.map((item) => redactSensitive(item));
    }
    if (!value || typeof value !== "object") {
      return value;
    }
    const output = {};
    for (const [key, item] of Object.entries(value)) {
      output[key] = SENSITIVE_KEY_RE.test(key) ? "[REDACTED]" : redactSensitive(item);
    }
    return output;
  }

  function parseAndSanitizeResponse(text, contentType) {
    const truncated = text.length > MAX_RESPONSE_CHARS;
    const sample = truncated ? text.slice(0, MAX_RESPONSE_CHARS) : text;
    const looksJson = /json/i.test(contentType || "") || /^[\s\r\n]*[\[{]/.test(sample);
    if (!looksJson) {
      return { responseText: sample, truncated };
    }
    try {
      const parsed = JSON.parse(sample);
      const redacted = redactSensitive(parsed);
      const serialized = JSON.stringify(redacted);
      if (serialized.length > MAX_RESPONSE_CHARS) {
        return { responseText: serialized.slice(0, MAX_RESPONSE_CHARS), truncated: true };
      }
      return { responseJson: redacted, truncated };
    } catch (_error) {
      return { responseText: sample, truncated };
    }
  }

  function parseAndSanitizeRequest(text) {
    const truncated = text.length > MAX_RESPONSE_CHARS;
    const sample = truncated ? text.slice(0, MAX_RESPONSE_CHARS) : text;
    const looksJson = /^[\s\r\n]*[\[{]/.test(sample);
    if (!looksJson) {
      return { requestText: sample, requestTruncated: truncated };
    }
    try {
      const parsed = JSON.parse(sample);
      const redacted = redactSensitive(parsed);
      const serialized = JSON.stringify(redacted);
      if (serialized.length > MAX_RESPONSE_CHARS) {
        return { requestText: serialized.slice(0, MAX_RESPONSE_CHARS), requestTruncated: true };
      }
      return { requestJson: redacted, requestTruncated: truncated };
    } catch (_error) {
      return { requestText: sample, requestTruncated: truncated };
    }
  }

  function normalizeRecord(input, captureType) {
    const contentType = input.contentType || "";
    const parsed = parseAndSanitizeResponse(String(input.responseText || ""), contentType);
    const request = input.requestText ? parseAndSanitizeRequest(String(input.requestText || "")) : {};
    return {
      url: String(input.url || ""),
      endpoint: endpointFromUrl(String(input.url || "")),
      method: String(input.method || "GET").toUpperCase(),
      status: Number(input.status || 0),
      contentType,
      captureType,
      capturedAt: input.capturedAt || new Date().toISOString(),
      ...request,
      ...parsed
    };
  }

  function trimRecords(records, limit) {
    return records.slice(Math.max(0, records.length - Math.min(limit || MAX_RECORDS, MAX_RECORDS)));
  }

  function buildSafeFlowAnalysisRequest(template, overrides) {
    const source = template && typeof template === "object" && !Array.isArray(template)
      ? redactSensitive(template)
      : {};
    const output = {};
    for (const [key, value] of Object.entries(source)) {
      if (SENSITIVE_KEY_RE.test(key)) continue;
      output[key] = redactSensitive(value);
    }

    if (overrides && Object.prototype.hasOwnProperty.call(overrides, "pageNumber")) {
      output.pageNumber = Number(overrides.pageNumber || 1);
    }
    if (overrides && Object.prototype.hasOwnProperty.call(overrides, "pageSize")) {
      output.pageSize = Number(overrides.pageSize || 100);
    }
    if (overrides && Object.prototype.hasOwnProperty.call(overrides, "timeDimension")) {
      output.timeDimension = Number(overrides.timeDimension);
    }
    if (overrides && Object.prototype.hasOwnProperty.call(overrides, "productIdList")) {
      const ids = Array.isArray(overrides.productIdList) ? overrides.productIdList : [];
      if (ids.length > 0) {
        output.productIdList = ids;
      } else {
        delete output.productIdList;
      }
    }
    return output;
  }

  root.WorkbenchNetworkProbeUtils = {
    MAX_RECORDS,
    MAX_RESPONSE_CHARS,
    buildSafeFlowAnalysisRequest,
    endpointFromUrl,
    isLikelyJsonBusinessEndpoint,
    normalizeRecord,
    parseAndSanitizeRequest,
    parseAndSanitizeResponse,
    parsedBusinessUrl,
    redactSensitive,
    shouldCaptureUrl,
    trimRecords
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = root.WorkbenchNetworkProbeUtils;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
