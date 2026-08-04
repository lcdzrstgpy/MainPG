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
      const sourceSkcId = safeText(data.sourceSkcId || data.skcId);
      const referenceImageUrl = redactUrl(data.referenceImageUrl || data.searchImageUrl || "");
      const candidate = {
        offer_id: offerId || offerIdFromUrl(sourceUrl),
        ...(sourceSkcId ? { source_skc_id: sourceSkcId } : {}),
        ...(referenceImageUrl ? { reference_image_url: referenceImageUrl } : {}),
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
    return bindSourceTaskEvidence(task, {
      search_url: typeof location === "object" ? location.href : "",
      candidates: extractSpecialSaleCards(document),
      sku_verification: verify1688Sku(document),
    });
  }

  function sourceSearchUrl(task) {
    const safeTask = task && typeof task === "object" ? task : {};
    const imageUrl = redactUrl(safeTask.main_image_url || "");
    const skcId = safeText(safeTask.skc_id);
    if (!imageUrl || !skcId) return "";
    const url = new URL("https://s.1688.com/selloffer/offer_search.htm");
    url.searchParams.set("image_url", imageUrl);
    url.searchParams.set("skc_id", skcId);
    return url.toString();
  }

  function bindSourceTaskEvidence(task, evidence) {
    const safeTask = task && typeof task === "object" ? task : {};
    const binding = {
      task_key: safeText(safeTask.task_key),
      skc_id: safeText(safeTask.skc_id),
      main_image_url: redactUrl(safeTask.main_image_url || ""),
      source_quote_keys: Array.isArray(safeTask.source_quote_keys) ? safeTask.source_quote_keys.map((key) => safeText(key)).filter(Boolean) : [],
    };
    const source = evidence && typeof evidence === "object" ? evidence : {};
    const verification = Array.isArray(source.sku_verification) ? source.sku_verification.filter((entry) => entry && typeof entry === "object") : [];
    const navigationBound = searchUrlMatchesTask(source.search_url, binding);
    const candidates = Array.isArray(source.candidates) ? source.candidates.filter((candidate) => candidate && typeof candidate === "object").filter((candidate) => navigationBound || candidateMatchesTask(candidate, binding)).map((candidate) => enrichCandidateWithSkuVerification(candidate, verification)) : [];
    return { ...binding, candidates, sku_verification: verification };
  }

  function candidateMatchesTask(candidate, task) {
    return safeText(candidate.source_skc_id) === task.skc_id || redactUrl(candidate.reference_image_url || "") === task.main_image_url;
  }

  function searchUrlMatchesTask(value, task) {
    try {
      const url = new URL(value);
      return url.hostname === "s.1688.com" && url.searchParams.get("image_url") === task.main_image_url && url.searchParams.get("skc_id") === task.skc_id;
    } catch (_) { return false; }
  }

  function enrichCandidateWithSkuVerification(candidate, verification) {
    const variants = uniqueStrings([...(Array.isArray(candidate.variants) ? candidate.variants : []), ...verification.map((entry) => safeText(entry.sku_attributes)).filter(Boolean)]);
    const matched = verification.find((entry) => !safeText(candidate.sku_attributes) || safeText(entry.sku_attributes) === safeText(candidate.sku_attributes)) || verification[0] || {};
    return {
      ...candidate,
      price: candidate.price == null ? numberValue(matched.price) : candidate.price,
      moq: candidate.moq == null ? numberValue(matched.moq) : candidate.moq,
      domestic_freight: candidate.domestic_freight == null ? numberValue(matched.domestic_freight) : candidate.domestic_freight,
      ...(safeText(candidate.sku_attributes) ? {} : (safeText(matched.sku_attributes) ? { sku_attributes: safeText(matched.sku_attributes) } : {})),
      ...(variants.length ? { variants } : {}),
    };
  }

  function uniqueStrings(values) { return [...new Set(values.map((value) => safeText(value)).filter(Boolean))]; }

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

  return { bindSourceTaskEvidence, extractTemuQuoteRows, extractSpecialSaleCards, quoteEvidenceFromPage, sourceEvidenceFromPage, sourceSearchUrl, verify1688Sku };
});
