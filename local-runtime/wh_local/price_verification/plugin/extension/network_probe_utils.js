/* Read-only helpers shared by the MV3 worker and page probe. */
(function exposeNetworkProbeUtils(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.PriceVerificationNetworkProbeUtils = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createNetworkProbeUtils() {
  const REDACTED = "***REDACTED***";
  const SENSITIVE_NAMES = new Set([
    "access_token", "access_key", "api_token", "api_key", "api_secret", "apikey", "authorization", "auth_token", "bearer_token", "client_secret", "client_token", "cookie", "cookies", "credential", "credentials", "id_token", "key", "password", "private_key", "refresh_token", "secret", "session", "session_token", "sid", "token",
  ]);
  const WRITE_ACTION = /(?:accept|approve|confirm|submit|save|update|delete|create|edit|write|publish|delist|order|payment|inventory|stock)/i;
  const READ_QUOTE_PATH = /(?:bargain-no-bom\/batch\/info\/query|price|quote|declar)/i;
  const SAFE_RESULT_FIELDS = new Set(["items", "progress", "message", "error", "records", "actions", "dom"]);
  const SAFE_ITEM_FIELDS = new Set(["task_key", "quote_key", "skc_id", "source_quote_keys", "status", "error", "candidates"]);
  const SAFE_CANDIDATE_FIELDS = new Set(["offer_id", "source_url", "source_title", "main_image_url", "price", "moq", "domestic_freight", "weight_kg", "sku_attributes", "variants"]);

  function isAllowedQuoteResponse(record) {
    const url = parseUrl(record && record.url);
    if (!url || !isSubdomain(url.hostname, "temu.com")) return false;
    if (WRITE_ACTION.test(url.pathname)) return false;
    const method = String((record && record.method) || "GET").toUpperCase();
    if (method !== "GET" && method !== "POST") return false;
    return READ_QUOTE_PATH.test(url.pathname);
  }

  function sanitizeQuoteRecord(record) {
    if (!record || typeof record !== "object" || !isAllowedQuoteResponse(record)) return null;
    const responseJson = redactValue(record.responseJson);
    return {
      url: redactUrl(record.url),
      status: finiteInteger(record.status),
      capturedAt: safeText(record.capturedAt),
      responseJson: responseJson && typeof responseJson === "object" ? responseJson : {},
    };
  }

  function sanitizeResult(result) {
    if (!result || typeof result !== "object" || Array.isArray(result)) return {};
    const sanitized = {};
    for (const [key, value] of Object.entries(result)) {
      if (!SAFE_RESULT_FIELDS.has(key)) continue;
      if (key === "items" && Array.isArray(value)) sanitized.items = value.map(sanitizeItem).filter(Boolean);
      else if (key === "records" && Array.isArray(value)) sanitized.records = value.map(sanitizeQuoteRecord).filter(Boolean);
      else if (key === "actions") sanitized.actions = sanitizeActions(value);
      else if (key === "dom") sanitized.dom = sanitizeDom(value);
      else if (key === "progress") sanitized.progress = finiteInteger(value);
      else if (key === "message" || key === "error") sanitized[key] = safeText(value, 500);
    }
    return sanitized;
  }

  function sanitizeActions(actions) {
    const popup = actions && typeof actions === "object" && actions.batch_price_popup;
    return popup && typeof popup === "object" ? { batch_price_popup: { ok: Boolean(popup.ok) } } : {};
  }

  function sanitizeDom(dom) {
    if (!dom || typeof dom !== "object" || Array.isArray(dom)) return { dialog_present: false, rows: [] };
    const rows = Array.isArray(dom.rows) ? dom.rows.slice(0, 200).map((row) => {
      if (!row || typeof row !== "object" || Array.isArray(row)) return null;
      const cells = row.cellsByHeader && typeof row.cellsByHeader === "object" ? redactValue(row.cellsByHeader) : {};
      return {
        source: safeText(row.source),
        ...(safeText(row.capturedAt) ? { capturedAt: safeText(row.capturedAt) } : {}),
        cellsByHeader: cells,
      };
    }).filter(Boolean) : [];
    return { dialog_present: Boolean(dom.dialog_present), rows };
  }

  function sanitizeItem(item) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const sanitized = {};
    for (const [key, value] of Object.entries(item)) {
      if (isSensitiveKey(key)) {
        sanitized[key] = REDACTED;
      } else if (SAFE_ITEM_FIELDS.has(key)) {
        if (key === "candidates" && Array.isArray(value)) sanitized.candidates = value.map(sanitizeCandidate).filter(Boolean);
        else if (key === "source_quote_keys" && Array.isArray(value)) sanitized.source_quote_keys = value.map((entry) => safeText(entry, 240)).filter(Boolean);
        else sanitized[key] = safeJsonValue(value);
      }
    }
    return sanitized;
  }

  function sanitizeCandidate(candidate) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) return null;
    const sanitized = {};
    for (const [key, value] of Object.entries(candidate)) {
      if (isSensitiveKey(key)) {
        sanitized[key] = REDACTED;
      } else if (SAFE_CANDIDATE_FIELDS.has(key)) {
        sanitized[key] = key === "source_url" || key === "main_image_url" ? redactUrl(value) : safeJsonValue(value);
      }
    }
    return sanitized;
  }

  function redactValue(value, depth) {
    const level = depth || 0;
    if (level > 12) return "";
    if (Array.isArray(value)) return value.slice(0, 200).map((entry) => redactValue(entry, level + 1));
    if (value && typeof value === "object") {
      const output = {};
      for (const [key, entry] of Object.entries(value)) output[key] = isSensitiveKey(key) ? REDACTED : redactValue(entry, level + 1);
      return output;
    }
    return safeJsonValue(value);
  }

  function redactUrl(value) {
    const url = parseUrl(value);
    if (!url) return "";
    for (const key of Array.from(url.searchParams.keys())) if (isSensitiveKey(key)) url.searchParams.delete(key);
    url.hash = "";
    return url.toString();
  }

  function parseUrl(value) {
    try { return new URL(value); } catch (_) { return null; }
  }

  function isSubdomain(hostname, suffix) {
    const host = String(hostname || "").toLowerCase().replace(/\.$/, "");
    return host === suffix || host.endsWith(`.${suffix}`);
  }

  function isLocalBridgeUrl(value) {
    const url = parseUrl(value);
    return Boolean(url && url.protocol === "http:" && !url.username && !url.password && (url.hostname === "127.0.0.1" || url.hostname === "localhost"));
  }

  function isSensitiveKey(key) {
    const normalized = String(key || "").replace(/([a-z0-9])([A-Z])/g, "$1_$2").replace(/-/g, "_").trim().toLowerCase();
    if (SENSITIVE_NAMES.has(normalized)) return true;
    return normalized.split("_").some((part) => part === "credential" || part === "credentials" || part === "password" || part === "secret" || part === "token");
  }

  function safeJsonValue(value) {
    if (typeof value === "string") return value.slice(0, 4000);
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    if (typeof value === "boolean" || value === null) return value;
    if (Array.isArray(value)) return value.slice(0, 100).map(safeJsonValue);
    return "";
  }

  function safeText(value, maximum) {
    return typeof value === "string" ? value.trim().slice(0, maximum || 240) : "";
  }

  function finiteInteger(value) {
    const number = Number(value);
    return Number.isFinite(number) ? Math.trunc(number) : 0;
  }

  return { REDACTED, isAllowedQuoteResponse, isLocalBridgeUrl, redactUrl, sanitizeQuoteRecord, sanitizeResult };
});
