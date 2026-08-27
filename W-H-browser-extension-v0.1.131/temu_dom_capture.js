(function attachTemuDomCapture(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.WorkbenchTemuDomCapture = api;
})(typeof self !== "undefined" ? self : globalThis, function createTemuDomCapture() {
  const SKU_LABELS = Object.freeze({
    "颜色": "Color",
    "色号": "Color",
    "Color": "Color",
    "Colour": "Color",
    "尺寸": "Size",
    "尺码": "Size",
    "大小": "Size",
    "Size": "Size",
    "容量": "Capacity",
    "Capacity": "Capacity",
    "规格": "Style",
    "款式": "Style",
    "型号": "Style",
    "Style": "Style",
    "Model": "Style",
    "包装": "Pack",
    "套装": "Pack",
    "数量": "Quantity",
    "Quantity": "Quantity"
  });
  const BAD_IMAGE_RE = /(?:aftersales|after-sales|service|review|comment|recommend|avatar|logo|sprite|icon|loading|placeholder|blank|video|play)/i;
  const BAD_OPTION_RE = /(?:加入购物车|立即购买|购买|配送|送达|运费|免运费|退货|保障|客服|收藏|评价|已售|库存|数量|推荐|最近的|请选择|查看全部|更多)/i;

  function text(value) {
    return value == null ? "" : String(value).replace(/\s+/g, " ").trim();
  }

  function walk(root) {
    const output = [];
    const visit = (node) => {
      for (const child of Array.from(node?.children || [])) {
        output.push(child);
        visit(child);
      }
    };
    visit(root);
    return output;
  }

  function cssImageUrl(value) {
    const match = String(value || "").match(/url\((['"]?)(.*?)\1\)/i);
    return match ? text(match[2]) : "";
  }

  function normalizeImageUrl(value, baseUrl = "https://www.temu.com/") {
    const raw = text(value);
    if (!raw || /^(?:data|blob):/i.test(raw)) return "";
    let parsed;
    try {
      parsed = new URL(raw.startsWith("//") ? `https:${raw}` : raw, baseUrl);
    } catch (_error) {
      return "";
    }
    if (!/^https?:$/.test(parsed.protocol)) return "";
    if (/\.kwcdn\.com$/i.test(parsed.hostname) && /(?:^|\/)imageView2(?:\/|$)/i.test(parsed.search.slice(1))) {
      parsed.search = "";
    }
    parsed.hash = "";
    const normalized = parsed.href;
    if (BAD_IMAGE_RE.test(normalized)) return "";
    if (!/\.(?:jpe?g|png|webp|avif)(?:[?#]|$)/i.test(normalized)) return "";
    return normalized;
  }

  function styleFor(element, view) {
    try {
      return view?.getComputedStyle ? view.getComputedStyle(element) : (element?.style || {});
    } catch (_error) {
      return element?.style || {};
    }
  }

  function visible(element, view) {
    if (!element) return false;
    const style = styleFor(element, view);
    if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity ?? 1) === 0) return false;
    try {
      const rect = element.getBoundingClientRect();
      return Number(rect.width || 0) > 0 && Number(rect.height || 0) > 0;
    } catch (_error) {
      return true;
    }
  }

  function attr(element, name) {
    try {
      return text(element?.getAttribute?.(name));
    } catch (_error) {
      return "";
    }
  }

  function elementImageUrls(element, view, baseUrl) {
    const urls = [];
    const tag = String(element?.tagName || "").toUpperCase();
    if (tag === "IMG" || tag === "SOURCE") {
      urls.push(element.currentSrc, element.src, attr(element, "src"), attr(element, "data-src"), attr(element, "data-lazy-src"), attr(element, "data-original"));
      const srcset = attr(element, "srcset") || attr(element, "data-srcset");
      if (srcset) urls.push(...srcset.split(",").map((part) => text(part).split(/\s+/)[0]));
    }
    urls.push(
      cssImageUrl(attr(element, "style")),
      cssImageUrl(element?.style?.backgroundImage),
      cssImageUrl(styleFor(element, view).backgroundImage),
      attr(element, "data-url")
    );
    return urls.map((url) => normalizeImageUrl(url, baseUrl)).filter(Boolean);
  }

  function descendantImageUrl(element, view, baseUrl) {
    for (const node of [element, ...walk(element)]) {
      const url = elementImageUrls(node, view, baseUrl)[0];
      if (url) return url;
    }
    return "";
  }

  function carouselIndex(element, galleryRoot) {
    let current = element;
    while (current && current !== galleryRoot) {
      const raw = attr(current, "data-index");
      if (/^\d+$/.test(raw)) return Number(raw);
      current = current.parentElement;
    }
    return null;
  }

  function galleryImages(documentRef, view) {
    const root = documentRef?.getElementById?.("leftContent");
    if (!root) return [];
    const baseUrl = view?.location?.href || documentRef?.location?.href || "https://www.temu.com/";
    const found = [];
    const seen = new Set();
    for (const element of [root, ...walk(root)]) {
      const evidence = `${attr(element, "class")} ${attr(element, "id")} ${attr(element, "aria-label")} ${attr(element, "title")}`;
      if (/(?:video|play|review|comment|recommend)/i.test(evidence)) continue;
      for (const url of elementImageUrls(element, view, baseUrl)) {
        if (seen.has(url)) continue;
        seen.add(url);
        found.push({
          url,
          carousel_index: carouselIndex(element, root),
          source: "temu-semantic-gallery"
        });
      }
    }
    return found
      .sort((left, right) => {
        const a = left.carousel_index == null ? Number.MAX_SAFE_INTEGER : left.carousel_index;
        const b = right.carousel_index == null ? Number.MAX_SAFE_INTEGER : right.carousel_index;
        return a - b;
      })
      .slice(0, 24);
  }

  function elementText(element) {
    return text(element?.innerText || element?.textContent || attr(element, "aria-label") || attr(element, "title"));
  }

  function directCandidateText(element) {
    const raw = elementText(element);
    if (!raw) return "";
    const childTexts = Array.from(element?.children || []).map(elementText).filter(Boolean);
    if (childTexts.length > 1) return "";
    if (childTexts.length === 1 && text(childTexts[0]) === raw) return "";
    return raw;
  }

  function cleanedOptionValue(value) {
    return text(value)
      .replace(/^[^\p{L}\p{N}]+/u, "")
      .replace(/\s*(?:已选|请选择)$/i, "")
      .trim();
  }

  function optionEvidenceElement(element, labelElement) {
    let current = element;
    let best = element;
    const targetText = elementText(element);
    for (let depth = 0; current?.parentElement && depth < 3; depth += 1) {
      const parent = current.parentElement;
      if (parent === labelElement?.parentElement) break;
      if (elementText(parent) && elementText(parent) !== targetText) break;
      best = parent;
      current = parent;
    }
    return best;
  }

  function selectedOption(element, view) {
    let current = element;
    for (let depth = 0; current && depth < 3; depth += 1, current = current.parentElement) {
      const evidence = `${attr(current, "class")} ${attr(current, "aria-selected")} ${attr(current, "aria-checked")} ${attr(current, "data-selected")} ${attr(current, "data-state")}`;
      if (/(?:selected|active|checked|current|\btrue\b)/i.test(evidence)) return true;
      const borderWidth = parseFloat(styleFor(current, view).borderWidth || "0");
      if (Number.isFinite(borderWidth) && borderWidth >= 2) return true;
    }
    return false;
  }

  function currentPrice(documentRef, view) {
    const root = documentRef?.getElementById?.("goods_price");
    if (!root) return null;
    const priceRe = /(CA\$|C\$|US\$|USD|CAD|CNY|RMB|¥|￥|\$)\s*([0-9]+(?:\.[0-9]{1,2})?)/i;
    const badContextRe = /(?:今天支付|pay\s*today|today\s*payment|分期|installment|afterpay|klarna|每月|\/mo\b)/i;
    const currencyFor = (raw) => {
      const value = String(raw || "").toUpperCase();
      if (/^(?:CAD|CA\$|C\$)$/.test(value)) return "CAD";
      if (/^(?:CNY|RMB|¥|￥)$/.test(value)) return "CNY";
      return "USD";
    };
    const isStruck = (element) => {
      let current = element;
      for (let depth = 0; current && depth < 4; depth += 1, current = current.parentElement) {
        if (/^(?:DEL|S|STRIKE)$/i.test(String(current.tagName || ""))) return true;
        const decoration = `${current?.style?.textDecorationLine || ""} ${styleFor(current, view).textDecorationLine || ""}`;
        if (/line-through/i.test(decoration)) return true;
        if (current === root) break;
      }
      return false;
    };
    const candidates = [];
    for (const element of [root, ...walk(root)]) {
      if (!visible(element, view) || attr(element, "aria-hidden") === "true" || isStruck(element)) continue;
      const value = elementText(element);
      const match = value.match(priceRe);
      if (!match || badContextRe.test(value)) continue;
      const amount = Number(match[2]);
      if (!Number.isFinite(amount) || amount <= 0) continue;
      const exactMoney = value.replace(/\s+/g, "").toUpperCase() === `${match[1]}${match[2]}`.replace(/\s+/g, "").toUpperCase();
      const hiddenChild = Array.from(element?.children || []).some((child) => attr(child, "aria-hidden") === "true");
      candidates.push({
        amount,
        currency: currencyFor(match[1]),
        value: `${match[1]}${match[2]}`,
        source: "temu-goods-price-visible",
        score: (exactMoney ? 200 : 50) + (hiddenChild ? 20 : 0) - Math.min(value.length, 80) / 100
      });
    }
    candidates.sort((left, right) => right.score - left.score);
    if (!candidates.length) return null;
    const { score: _score, ...selected } = candidates[0];
    return selected;
  }

  function skuGroups(documentRef, view) {
    const root = documentRef?.getElementById?.("rightContent");
    if (!root) return [];
    const baseUrl = view?.location?.href || documentRef?.location?.href || "https://www.temu.com/";
    const elements = walk(root);
    const labels = [];
    for (let index = 0; index < elements.length; index += 1) {
      const value = elementText(elements[index]).replace(/[:：]$/, "");
      if (!SKU_LABELS[value]) continue;
      const childHasSameLabel = Array.from(elements[index].children || []).some((child) => elementText(child).replace(/[:：]$/, "") === value);
      if (!childHasSameLabel) labels.push({ index, element: elements[index], sourceName: value, name: SKU_LABELS[value] });
    }
    const output = [];
    for (let labelIndex = 0; labelIndex < labels.length; labelIndex += 1) {
      const label = labels[labelIndex];
      if (label.name === "Quantity") continue;
      const stop = labels[labelIndex + 1]?.index ?? elements.length;
      const values = [];
      const seen = new Set();
      const strongOptionIndexes = [];
      for (let index = label.index + 1; index < stop; index += 1) {
        if (/^(?:radio|option)$/i.test(attr(elements[index], "role")) && attr(elements[index], "aria-label")) {
          strongOptionIndexes.push(index);
        }
      }
      const candidateIndexes = strongOptionIndexes.length
        ? strongOptionIndexes
        : Array.from({ length: Math.max(0, stop - label.index - 1) }, (_item, offset) => label.index + offset + 1);
      for (const index of candidateIndexes) {
        const element = elements[index];
        if (!visible(element, view)) continue;
        const strongOption = /^(?:radio|option)$/i.test(attr(element, "role"));
        const rawValue = strongOption ? attr(element, "aria-label") : directCandidateText(element);
        const value = cleanedOptionValue(rawValue);
        if (!value || value.length > 40 || SKU_LABELS[value] || BAD_OPTION_RE.test(value) || /(?:CA\$|US\$|\$|¥|￥)\s*\d/i.test(value)) continue;
        if (seen.has(value.toLowerCase())) continue;
        const evidenceElement = optionEvidenceElement(element, label.element);
        const imageUrl = strongOption
          ? descendantImageUrl(element, view, baseUrl)
          : (elementImageUrls(evidenceElement, view, baseUrl)[0]
            || elementImageUrls(element, view, baseUrl)[0]
            || "");
        seen.add(value.toLowerCase());
        values.push({
          value,
          image_url: imageUrl,
          selected: selectedOption(element, view) || selectedOption(evidenceElement, view),
          selectable: true
        });
      }
      if (values.length) {
        output.push({ name: label.name, source_name: label.sourceName, values: values.slice(0, 50) });
      }
    }
    return output;
  }

  function skuCombinations(groups, price) {
    const usableGroups = Array.from(groups || []).filter((group) =>
      group && Array.isArray(group.values) && group.values.length
    );
    if (!usableGroups.length) return [];

    let combinations = [{ attributes: {}, image_url: "", selected: true }];
    for (const group of usableGroups) {
      const attributeName = text(group.source_name || group.name || "规格");
      const next = [];
      for (const combination of combinations) {
        for (const option of group.values.slice(0, 50)) {
          if (!option?.value) continue;
          next.push({
            attributes: { ...combination.attributes, [attributeName]: text(option.value) },
            image_url: combination.image_url || text(option.image_url),
            selected: Boolean(combination.selected && option.selected)
          });
          if (next.length >= 10000) break;
        }
        if (next.length >= 10000) break;
      }
      // 规格数量过大时依旧继续后续维度，不能在中途截断而生成缺少尺寸/包装等
      // 属性的伪组合。最终仍最多送 200 条，避免插件请求无限膨胀。
      combinations = next.length > 200 ? next.slice(0, 200) : next;
      if (!combinations.length) break;
    }

    return combinations.map((combination) => ({
      ...combination,
      price: text(price?.value),
      currency: text(price?.currency),
      selectable: true,
      source: "temu-semantic-groups",
      confidence: combination.image_url || price?.value ? "medium" : "low"
    }));
  }

  function extract(documentRef, view) {
    const groups = skuGroups(documentRef, view);
    const price = currentPrice(documentRef, view);
    return {
      gallery_images: galleryImages(documentRef, view),
      sku_groups: groups,
      current_price: price,
      variant_combinations: skuCombinations(groups, price)
    };
  }

  return Object.freeze({ extract, galleryImages, skuGroups, skuCombinations, currentPrice, normalizeImageUrl, cssImageUrl });
});
