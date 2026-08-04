/* DOM-only evidence extraction. This file does not fetch, mutate, or submit pages. */
(function exposePageProbe(root, factory) {
  const api = factory(root.PriceVerificationNetworkProbeUtils || {});
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.PriceVerificationPageProbe = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function createPageProbe(networkUtils) {
  const redactUrl = typeof networkUtils.redactUrl === "function" ? networkUtils.redactUrl : localRedactUrl;

  function extractTemuQuoteRows(root, options) {
    const popupConfirmed = Boolean(options && options.popupConfirmed);
    const rows = queryAll(root, "[data-price-quote-row], [role='dialog'] tr, table tr");
    return rows.map((row) => quoteRow(row, popupConfirmed)).filter((row) => Object.keys(row.cellsByHeader).length >= 2);
  }

  function quoteRow(row, popupConfirmed) {
    const text = textOf(row);
    const cellsByHeader = {};
    copyLabel(text, "SKC ID", cellsByHeader, "SKC ID");
    copyLabel(text, "SKU ID", cellsByHeader, "SKU ID");
    copyLabel(text, "原申报价格(CNY)", cellsByHeader, "原申报价格(CNY)", true);
    if (popupConfirmed) copyLabel(text, "调整后申报价格(CNY)", cellsByHeader, "调整后申报价格(CNY)", true);
    if (popupConfirmed) copyLabel(text, "新申报价格(CNY)", cellsByHeader, "新申报价格(CNY)", true);
    return { source: safeText(row && row.dataset && row.dataset.source) || "temu_quote_dom", cellsByHeader };
  }

  function extractSpecialSaleCards(root) {
    return queryAll(root, "[data-offer-id], [data-offerid], [data-source-card]").map((card) => {
      const data = (card && card.dataset) || {};
      const offerId = safeText(data.offerId || data.offerid);
      const sourceUrl = canonical1688Url(data.url || data.sourceUrl, offerId);
      const candidate = {
        offer_id: offerId || offerIdFromUrl(sourceUrl),
        source_url: sourceUrl,
        source_title: safeText(data.title || data.sourceTitle),
        main_image_url: redactUrl(data.imageUrl || data.image || ""),
        price: numberValue(data.price),
        moq: numberValue(data.moq),
      };
      return candidate.offer_id && candidate.source_url ? candidate : null;
    }).filter(Boolean);
  }

  function verify1688Sku(root) {
    return queryAll(root, "[data-sku-row], [data-variant-row], [data-sku]").map((row) => {
      const data = (row && row.dataset) || {};
      const candidate = {
        sku_attributes: safeText(data.variant || data.skuAttributes || data.attributes),
        price: numberValue(data.price),
        moq: numberValue(data.moq),
        domestic_freight: numberValue(data.freight || data.domesticFreight),
      };
      return candidate.sku_attributes || candidate.price !== null ? candidate : null;
    }).filter(Boolean);
  }

  function quoteEvidenceFromPage(options) {
    return { dom: { dialog_present: Boolean(document.querySelector("[role='dialog']")), rows: extractTemuQuoteRows(document, options) } };
  }

  function sourceEvidenceFromPage(task) {
    const safeTask = task && typeof task === "object" ? task : {};
    return {
      task_key: safeText(safeTask.task_key),
      skc_id: safeText(safeTask.skc_id),
      main_image_url: redactUrl(safeTask.main_image_url || ""),
      source_quote_keys: Array.isArray(safeTask.source_quote_keys) ? safeTask.source_quote_keys.map((key) => safeText(key)).filter(Boolean) : [],
      candidates: extractSpecialSaleCards(document),
      sku_verification: verify1688Sku(document),
    };
  }

  function copyLabel(text, label, target, key, money) {
    const expression = new RegExp(`${escapeRegex(label)}\\s*[:：]\\s*([^\\n\\r]+)`, "i");
    const match = String(text || "").match(expression);
    if (!match) return;
    const value = money ? moneyText(match[1]) : safeText(match[1]);
    if (value) target[key] = value;
  }

  function canonical1688Url(value, offerId) {
    const candidate = redactUrl(value);
    const id = safeText(offerId) || offerIdFromUrl(candidate);
    if (!id) return "";
    try {
      const url = new URL(candidate);
      if (url.hostname === "1688.com" || url.hostname.endsWith(".1688.com")) return `https://detail.1688.com/offer/${id}.html`;
    } catch (_) { return ""; }
    return "";
  }

  function offerIdFromUrl(value) {
    const match = String(value || "").match(/(?:offer\/|offerId=|offer_id=)(\d{3,})/i);
    return match ? match[1] : "";
  }

  function numberValue(value) {
    const match = String(value || "").replace(/,/g, "").match(/\d+(?:\.\d+)?/);
    return match ? Number(match[0]) : null;
  }

  function moneyText(value) {
    const match = String(value || "").replace(/,/g, "").match(/\d+(?:\.\d+)?/);
    return match ? match[0] : "";
  }

  function queryAll(root, selector) {
    return root && typeof root.querySelectorAll === "function" ? Array.from(root.querySelectorAll(selector)) : [];
  }

  function textOf(value) { return safeText(value && (value.innerText || value.textContent), 4000); }
  function safeText(value, maximum) { return typeof value === "string" ? value.trim().slice(0, maximum || 240) : ""; }
  function escapeRegex(value) { return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); }
  function localRedactUrl(value) { try { const url = new URL(value); url.search = ""; url.hash = ""; return url.toString(); } catch (_) { return ""; } }

  return { extractTemuQuoteRows, extractSpecialSaleCards, quoteEvidenceFromPage, sourceEvidenceFromPage, verify1688Sku };
});
