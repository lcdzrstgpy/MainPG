(function attachTemuOrderProfit(root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  if (root) root.WorkbenchTemuOrderProfit = api;
})(typeof self !== "undefined" ? self : globalThis, function createTemuOrderProfit() {
  "use strict";

  const MAX_ITEMS = 5000;
  const MAX_WALK_DEPTH = 9;
  const MAX_TEXT_ROWS = 60;

  // 订单字段在不同接口里命名不一致（驼峰/下划线/中英文），这里按优先级集中维护。
  const PARENT_KEYS = [
    "parentOrderSn", "parentOrderNo", "parentOrderId", "parentOrderCode",
    "poNo", "poNumber", "poSn", "parentSn",
    "orderSn", "orderNo", "orderNumber", "orderId", "orderCode", "tradeNo", "tradeSn"
  ];
  const SUB_KEYS = [
    "subOrderSn", "subOrderNo", "subOrderId", "subOrderCode",
    "orderItemSn", "orderItemNo", "orderItemId", "orderItemCode",
    "subSn", "itemOrderSn", "tradeItemSn", "orderDetailSn",
    // Temu 订单列表里，父订单在 parentOrderMap.parentOrderSn，子订单在 orderList[].orderSn。
    "orderSn"
  ];
  const SKC_KEYS = [
    "skcId", "skc_id", "skcNo", "skcSn", "skcCode", "productSkcId", "goodsSkcId"
  ];
  const SKU_KEYS = [
    "skuId", "sku_id", "skuNo", "skuSn", "skuCode", "productSkuId", "goodsSkuId",
    "sellerSkuId", "outerSkuId", "sku"
  ];
  const TITLE_KEYS = [
    "goodsName", "goodsTitle", "originalGoodsName", "productName", "productTitle",
    "skuName", "skuTitle", "itemName", "itemTitle", "title", "subject", "productTitleEn"
  ];
  const QTY_KEYS = [
    "quantity", "qty", "goodsNum", "productNum", "itemQuantity", "buyQuantity",
    "skuQuantity", "orderQuantity", "fulfillmentQuantity", "num", "count", "pieceCount"
  ];
  const DECLARED_KEYS = [
    "declaredPriceCent", "declarePriceCent", "supplierPriceCent", "supplyPriceCent",
    "declaredPriceCny", "declarePriceCny", "declaredPrice", "declarePrice",
    "supplierPriceCny", "supplierPrice", "supplyPriceCny", "supplyPrice",
    "priceDeclared", "申报价", "申报价格", "供货价"
  ];
  const GMV_KEYS = [
    "gmvCent", "gmvAmountCent", "saleAmountCent", "orderAmountCent", "totalAmountCent",
    "paymentAmountCent", "gmvCny", "gmvAmountCny", "gmv", "gmvAmount",
    "saleAmountCny", "salesAmountCny", "saleAmount", "salesAmount",
    "orderAmountCny", "orderAmount", "goodsAmount", "totalAmount", "paymentAmount",
    "actualPayment", "transactionAmount", "revenue", "estimatedRevenue", "amount"
  ];
  const TAIL_KEYS = [
    "tailFreightCent", "shippingFeeCent", "freightFeeCent", "lastMileFeeCent",
    "tailFreightCny", "tailFreightFee", "tailFreight", "lastMileFeeCny", "lastMileFee",
    "lastMileFreight", "estimateShippingFee", "estimatedShippingFee", "shippingFeeCny",
    "shippingFee", "freightFee", "deliveryFee", "logisticsFee", "trackingFee",
    "尾程运费", "尾程费用", "预估尾程运费"
  ];
  const TRACKING_KEYS = [
    "trackingNo", "trackingNumber", "tracking_no", "logisticsNo", "logisticsNumber",
    "waybillNo", "waybillNumber", "expressNo", "expressNumber", "shippingNo",
    "运单号", "物流单号"
  ];
  const STATUS_KEYS = [
    "parentOrderStatusDesc", "parentOrderStatusName", "parentOrderStatusText",
    "orderStatusDesc", "orderStatusName", "orderStatusText", "subOrderStatusDesc",
    "subOrderStatusName", "statusDesc", "statusName", "parentOrderStatus",
    "orderStatus", "subOrderStatus", "orderState", "state", "status", "订单状态"
  ];
  const SITE_KEYS = [
    "siteName", "site_name", "siteCode", "site_code", "regionName", "regionCode",
    "countryName", "countryCode", "country", "site", "region", "站点", "国家"
  ];
  const TIME_KEYS = [
    "parentOrderTimeStr", "parentOrderTime", "orderTimeStr", "orderCreateTimeStr",
    "orderTime", "orderCreateTime", "orderCreatedTime", "createTime", "createdAt",
    "gmtCreate", "orderDate", "payTime", "paidAt", "placedAt", "下单时间", "订单创建时间"
  ];
  const CURRENCY_KEYS = ["currency", "currencyCode", "currencyType", "currencyName", "币种"];
  const SHOP_KEYS = [
    "shopName", "storeName", "mallName", "sellerName", "shop_name", "store_name",
    "mall_name", "店铺名称", "店铺"
  ];

  const LINE_ARRAY_KEYS = new Set([
    "orderitems", "orderitemlist", "suborders", "suborderlist",
    "childorders", "childorderlist", "items", "itemlist", "goodslist", "skulist",
    "details", "detaillist", "lines", "linelist", "records", "rows", "list",
    "orderlist", "orders", "pageitems", "data", "result", "pagedata", "content"
  ]);
  const NON_LINE_ARRAY_KEYS = new Set([
    "attributes", "specs", "specifications", "tags", "labels", "images", "imagelist",
    "logs", "loglist", "statuslist", "statushistory", "operations", "buttons", "actions",
    // Temu 订单行上的附属数组：商品信息、包裹/运单、标签、外部编码，不是子订单列表。
    "productinfolist", "productinfo", "skuinfolist", "skuinfo", "goodsinfolist", "goodsinfo",
    "orderpackageinfolist", "packageinfolist", "waybillinfolist", "shippinginfolist",
    "logisticsinfolist", "trackinginfolist", "extcodelist", "orderlabel", "labellist"
  ]);
  // 这些对象只是订单行上的附属信息，flattenFields 已经读过字段，不再向下递归找列表。
  const NON_ORDER_WRAPPER_KEYS = new Set([
    "buyerinfo", "buyer", "addressinfo", "address", "receiverinfo", "receiver",
    "recipientinfo", "recipient", "consigneeinfo", "consignee", "contactinfo",
    "shopinfo", "mallinfo", "storeinfo", "sellerinfo", "productinfo", "goodsinfo",
    "skuinfo", "skcinfo", "priceinfo", "amountinfo", "feeinfo", "logisticsinfo",
    "shippinginfo", "packageinfo", "extrainfo", "extra", "attributes",
    "specs", "specifications", "images", "tags", "logs", "statusinfo", "trackinginfo"
  ]);

  const MONEY_OBJECT_KEYS = ["amount", "value", "price", "cent", "amountCent", "fee", "total"];

  // 不同接口会混用 orderSn / order_sn / order-sn，统一成只保留字母数字的 key 再匹配。
  function canonicalKey(value) {
    return String(value || "").toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g, "");
  }

  function text(value) {
    return value == null ? "" : String(value).replace(/\s+/g, " ").trim();
  }

  function asId(value) {
    if (value == null) return "";
    if (typeof value === "number") return Number.isFinite(value) ? String(value) : "";
    if (typeof value === "string") return value.trim();
    if (typeof value === "object") {
      return text(value.id || value.value || value.code || value.sn || "");
    }
    return "";
  }

  function round2(value) {
    return Math.round((Number(value) + Number.EPSILON) * 100) / 100;
  }

  function isPlainObject(value) {
    return Boolean(value) && typeof value === "object" && !Array.isArray(value);
  }

  function getByPath(object, path) {
    let current = object;
    for (const part of path) {
      if (!isPlainObject(current)) return undefined;
      current = current[part];
    }
    return current;
  }

  // 把对象里的标量字段展平成“小写 key -> {key, value}”。
  // 直连字段优先；嵌套对象只下探两层，避免把父订单的字段覆盖到子订单。
  function flattenFields(object) {
    const fields = new Map();
    const visited = new WeakSet();
    const put = (key, value, currency) => {
      const lower = canonicalKey(key);
      if (!lower || fields.has(lower)) return;
      fields.set(lower, { key: String(key || ""), value: value, currency: text(currency || "") });
    };
    const readDirect = (node) => {
      if (!isPlainObject(node) || visited.has(node)) return;
      visited.add(node);
      for (const [key, value] of Object.entries(node)) {
        if (value == null) continue;
        if (Array.isArray(value)) {
          // 价格/金额对象可能是数组包裹，例如 [{amount: 12.3}]。
          if (value.length === 1 && isPlainObject(value[0])) {
            const money = moneyShape(value[0]);
            if (money) put(key, money.amount, money.currency);
          }
          continue;
        }
        if (isPlainObject(value)) {
          const money = moneyShape(value);
          if (money) {
            put(key, money.amount, money.currency);
          }
          continue;
        }
        put(key, value);
      }
    };
    const readNested = (node, depth) => {
      if (!isPlainObject(node) || depth > 1) return;
      for (const value of Object.values(node)) {
        if (!isPlainObject(value)) continue;
        readDirect(value);
        readNested(value, depth + 1);
      }
    };
    // 直连字段先落位，再用嵌套对象补齐，保证同名字段以直连值为准。
    readDirect(object);
    readNested(object, 0);
    return fields;
  }

  function moneyShape(object) {
    if (!isPlainObject(object)) return null;
    for (const key of MONEY_OBJECT_KEYS) {
      if (object[key] != null && typeof object[key] !== "object") {
        return {
          amount: object[key],
          currency: text(object.currency || object.currencyCode || object.currencyType || "")
        };
      }
    }
    return null;
  }

  function pick(fields, aliases) {
    for (const alias of aliases) {
      const entry = fields.get(canonicalKey(alias));
      if (entry && entry.value != null && entry.value !== "") return entry.value;
    }
    return undefined;
  }

  function pickEntry(fields, aliases) {
    for (const alias of aliases) {
      const entry = fields.get(canonicalKey(alias));
      if (entry && entry.value != null && entry.value !== "") return entry;
    }
    return null;
  }

  function normalizeCurrency(value) {
    const raw = text(value).toUpperCase();
    if (!raw) return "";
    if (/^(USD|US\$|\$|US DOLLAR|美元)$/.test(raw)) return "USD";
    if (/^(CNY|RMB|¥|￥|人民币|元)$/.test(raw)) return "CNY";
    if (/^(EUR|€|欧元)$/.test(raw)) return "EUR";
    if (/^(GBP|£|英镑)$/.test(raw)) return "GBP";
    if (/^(CAD|CA\$|C\$|加元)$/.test(raw)) return "CAD";
    return raw;
  }

  function parseAmount(entry) {
    if (!entry) return { value: null, currency: "" };
    let raw = entry.value;
    if (raw == null || raw === "") return { value: null, currency: normalizeCurrency(entry.currency) };
    let currency = normalizeCurrency(entry.currency);
    if (typeof raw === "string") {
      const symbol = raw.match(/US\$|CA\$|C\$|USD|CNY|RMB|EUR|GBP|¥|￥|\$|€|£/i);
      if (symbol && !currency) currency = normalizeCurrency(symbol[0]);
      raw = raw.replace(/[^\d.\-]/g, "");
    }
    let number = Number(raw);
    if (!Number.isFinite(number)) return { value: null, currency: currency };
    // 金额字段名带 cent/分 时按分转元；Temu 大部分接口直接返回元。
    if (/cent|分/i.test(String(entry.key || "")) && Number.isInteger(number)) {
      number = number / 100;
    }
    return { value: round2(number), currency: currency };
  }

  function parseQuantity(value) {
    if (value == null || value === "") return null;
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    const match = String(value).match(/\d+(?:\.\d+)?/);
    if (!match) return null;
    const number = Number(match[0]);
    return Number.isFinite(number) ? number : null;
  }

  function normalizeSite(value) {
    // Temu 返回的站点文案是“美国站/德国站”，统一去掉末尾“站”再映射。
    const raw = text(value).replace(/\s*站$/, "");
    if (!raw) return "";
    const upper = raw.toUpperCase();
    const map = {
      US: "美国", USA: "美国", "UNITED STATES": "美国", 美国: "美国",
      DE: "德国", DEU: "德国", GERMANY: "德国", 德国: "德国",
      UK: "英国", GB: "英国", GBR: "英国", "UNITED KINGDOM": "英国", 英国: "英国",
      FR: "法国", FRA: "法国", FRANCE: "法国", 法国: "法国",
      CA: "加拿大", CAN: "加拿大", CANADA: "加拿大", 加拿大: "加拿大",
      AU: "澳大利亚", AUS: "澳大利亚", AUSTRALIA: "澳大利亚", 澳大利亚: "澳大利亚",
      JP: "日本", JPN: "日本", JAPAN: "日本", 日本: "日本",
      MX: "墨西哥", MEX: "墨西哥", MEXICO: "墨西哥", 墨西哥: "墨西哥",
      ES: "西班牙", IT: "意大利", NL: "荷兰", PL: "波兰", SE: "瑞典", BE: "比利时"
    };
    return map[upper] || raw;
  }

  function normalizeTime(value) {
    if (value == null || value === "") return "";
    if (typeof value === "number" || /^\d{10,13}$/.test(String(value))) {
      let stamp = Number(value);
      if (String(Math.trunc(stamp)).length === 10) stamp *= 1000;
      const date = new Date(stamp);
      if (!Number.isNaN(date.getTime())) return date.toISOString();
    }
    return text(value);
  }

  // 已通过真实订单页确认：待发货=2、已签收=5；其他状态保留原始 code，避免猜错。
  const STATUS_CODE_TEXT = Object.freeze({ "2": "待发货", "5": "已签收" });

  function statusText(value) {
    const raw = text(value);
    if (!raw) return "";
    return STATUS_CODE_TEXT[raw] || raw;
  }

  function childArraysOf(object) {
    const arrays = [];
    for (const [key, value] of Object.entries(object)) {
      if (!Array.isArray(value) || value.length === 0) continue;
      const lower = canonicalKey(key);
      if (NON_LINE_ARRAY_KEYS.has(lower)) continue;
      const objectItems = value.filter(isPlainObject);
      if (!objectItems.length) continue;
      const looksLikeLineList = objectItems.some((item) => {
        const fields = flattenFields(item);
        return Boolean(pick(fields, SKU_KEYS) || pick(fields, SUB_KEYS) || pick(fields, SKC_KEYS));
      });
      if (LINE_ARRAY_KEYS.has(lower) || looksLikeLineList) {
        arrays.push({ key: key, items: value });
      }
    }
    return arrays;
  }

  function firstObjectFromKeys(node, keys) {
    if (!isPlainObject(node)) return null;
    for (const key of keys) {
      const value = node[key];
      if (Array.isArray(value)) {
        const found = value.find(isPlainObject);
        if (found) return found;
      } else if (isPlainObject(value)) {
        return value;
      }
    }
    return null;
  }

  // Temu 订单行的商品信息在 productInfoList[0] 里，真正的 SKU/SKC 是 productSkuId/productSkcId。
  function extractProductInfo(node) {
    const info = firstObjectFromKeys(node, [
      "productInfoList", "productInfo", "skuInfoList", "skuInfo", "goodsInfoList", "goodsInfo"
    ]);
    if (!info) return { skuId: "", skcId: "", spuId: "" };
    const fields = flattenFields(info);
    return {
      skuId: asId(pick(fields, ["productSkuId", "skuId", "sku_id", "productSku"])),
      skcId: asId(pick(fields, ["productSkcId", "skcId", "skc_id", "productSkc"])),
      spuId: asId(pick(fields, ["productSpuId", "spuId", "spu_id", "productSpu"]))
    };
  }

  function extractTracking(node, fields) {
    let tracking = text(pick(fields, TRACKING_KEYS));
    if (tracking) return tracking;
    if (!isPlainObject(node)) return "";
    for (const key of [
      "waybillInfoList", "orderPackageInfoList", "packageInfoList",
      "shippingInfoList", "logisticsInfoList", "trackingInfoList"
    ]) {
      const list = node[key];
      if (!Array.isArray(list)) continue;
      for (const item of list) {
        if (!isPlainObject(item)) continue;
        const itemFields = flattenFields(item);
        const found = text(pick(itemFields, TRACKING_KEYS))
          || text(item.trackingNumber || item.trackingNo || item.waybillNo || item.logisticsNo || "");
        if (found) return found;
        // Temu 的 orderPackageInfoList 会把子包裹单号放在 subPackageTrackingNumberList。
        const subTracking = item.subPackageTrackingNumberList;
        if (Array.isArray(subTracking)) {
          const first = subTracking.map(text).find(Boolean);
          if (first) return first;
        }
      }
    }
    return "";
  }

  function parseMoneyText(value) {
    const raw = text(value);
    if (!raw) return null;
    const match = raw.match(/(US\$|CA\$|C\$|USD|CNY|RMB|EUR|GBP|¥|￥|\$)\s*([\d,]+(?:\.\d+)?)/i)
      || raw.match(/([\d,]+(?:\.\d+)?)/);
    if (!match) return null;
    const hasCurrency = match.length >= 3;
    const currency = hasCurrency ? normalizeCurrency(match[1]) : "";
    const amount = Number(String(hasCurrency ? match[2] : match[1]).replace(/,/g, ""));
    return Number.isFinite(amount) ? { value: round2(amount), currency: currency } : null;
  }

  // Temu 在 waybillInfoList[].callLabelExtension.estimatedText（如 "$2.70"）里给出面单预估费用，
  // 这就是订单利润里的“预估尾程运费”。多个包裹时累加，避免漏掉分包裹费用。
  function extractParentTailFreight(parentNode, parentFields) {
    if (!isPlainObject(parentNode)) return null;
    const waybills = Array.isArray(parentNode.waybillInfoList) ? parentNode.waybillInfoList : [];
    let total = 0;
    let currency = "";
    for (const waybill of waybills) {
      if (!isPlainObject(waybill)) continue;
      let parsed = parseMoneyText(waybill?.callLabelExtension?.estimatedText);
      if (!parsed) {
        const interlines = Array.isArray(waybill.interlineInfoForAggregationInfo)
          ? waybill.interlineInfoForAggregationInfo
          : [];
        for (const info of interlines) {
          parsed = parseMoneyText(info?.estimatedAmount);
          if (parsed) break;
        }
      }
      if (parsed) {
        total += parsed.value;
        currency = currency || parsed.currency;
      }
    }
    if (total > 0) return { value: round2(total), currency: currency };
    const direct = parseAmount(pickEntry(parentFields, TAIL_KEYS));
    return direct.value == null ? null : { value: direct.value, currency: direct.currency };
  }

  function extractMallId(parentNode) {
    if (!isPlainObject(parentNode)) return "";
    const waybills = Array.isArray(parentNode.waybillInfoList) ? parentNode.waybillInfoList : [];
    for (const waybill of waybills) {
      if (!isPlainObject(waybill)) continue;
      const direct = asId(waybill.fulfillmentMallId);
      if (direct) return direct;
      const related = Array.isArray(waybill.relatedGoodsInfoForAggregation)
        ? waybill.relatedGoodsInfoForAggregation
        : [];
      for (const info of related) {
        const mallId = asId(info?.mallId);
        if (mallId) return mallId;
      }
    }
    return "";
  }

  function lineFromFields(fields, context, node) {
    const explicitParent = asId(pick(fields, PARENT_KEYS));
    const explicitSub = asId(pick(fields, SUB_KEYS));
    let parentOrderSn = explicitParent || asId(context.parentOrderSn);
    let subOrderSn = explicitSub || asId(context.subOrderSn);

    // 订单列表的层级是 parentOrderMap.parentOrderSn > orderList[].orderSn。
    // 已经在父级上下文里时，父单号以上下文为准，行内的 orderSn 视为子订单号。
    if (asId(context.parentOrderSn)) {
      parentOrderSn = asId(context.parentOrderSn);
      if (!subOrderSn && explicitParent && explicitParent !== parentOrderSn) subOrderSn = explicitParent;
    } else if (subOrderSn && subOrderSn === parentOrderSn) {
      // 扁平行只有 orderSn 时不能让父单号同时被当成子单号。
      subOrderSn = "";
    }

    const product = extractProductInfo(node);
    const skuId = product.skuId || asId(pick(fields, SKU_KEYS));
    const skcId = product.skcId || asId(pick(fields, SKC_KEYS));
    const skcCode = asId(pick(fields, ["skcId", "skcCode", "skc_id"]));
    const skcCodeFinal = skcCode && skcCode !== skcId ? skcCode : "";
    if (!skuId && !skcId) return null;
    if (!parentOrderSn && !subOrderSn) return null;

    const declared = parseAmount(pickEntry(fields, DECLARED_KEYS));
    const gmv = parseAmount(pickEntry(fields, GMV_KEYS));
    const tail = parseAmount(pickEntry(fields, TAIL_KEYS));
    const currency = normalizeCurrency(pick(fields, CURRENCY_KEYS)) || gmv.currency || declared.currency || tail.currency;
    const quantity = parseQuantity(pick(fields, QTY_KEYS));
    const goodsName = text(pick(fields, TITLE_KEYS));
    const shopName = text(pick(fields, SHOP_KEYS)) || text(context.shopName);
    const trackingNo = extractTracking(node, fields) || text(context.trackingNo);
    const statusCode = asId(pick(fields, STATUS_KEYS));
    const status = statusText(statusCode) || text(context.orderStatus);
    const site = normalizeSite(pick(fields, SITE_KEYS)) || normalizeSite(context.site);
    const orderTime = normalizeTime(pick(fields, TIME_KEYS)) || text(context.orderTime);
    const extCode = text(pick(fields, ["extCode", "skuCode", "sellerSkuCode", "货号"]))
      || (Array.isArray(node?.extCodeList) ? text(node.extCodeList.find((value) => text(value))) : "");
    const imageUrl = text(pick(fields, ["thumbUrl", "goodsImage", "imageUrl", "mainImageUrl", "skuImageUrl"]));
    const mallId = asId(pick(fields, ["mallId", "fulfillmentMallId"])) || text(context.mallId);
    const parentTailFreight = context.parentTailFreight == null ? null : Number(context.parentTailFreight);

    return {
      parent_order_sn: parentOrderSn,
      sub_order_sn: subOrderSn,
      skc_id: skcId,
      skc_code: skcCodeFinal,
      sku_id: skuId,
      ext_code: extCode,
      goods_name: goodsName,
      image_url: imageUrl,
      quantity: quantity,
      declared_price: declared.value,
      declared_currency: declared.currency || (declared.value == null ? "" : "CNY"),
      gmv: gmv.value,
      gmv_currency: gmv.currency || currency,
      tail_freight: tail.value,
      tail_currency: tail.currency,
      // 父订单面单预估费用先挂在行上，等所有行收集完再按数量分摊，避免多子订单重复计费。
      parent_tail_freight: parentTailFreight,
      parent_tail_currency: text(context.parentTailCurrency),
      tail_freight_source: tail.value != null ? "order_api" : (parentTailFreight != null ? "waybill_estimate_pending" : ""),
      currency: currency,
      tracking_no: trackingNo,
      order_status: status,
      order_status_code: statusCode,
      site: site,
      order_time: orderTime,
      shop_name: shopName,
      mall_id: mallId,
      source: "api",
      missing: {
        declared_price: declared.value == null,
        gmv: gmv.value == null,
        tail_freight: tail.value == null && parentTailFreight == null,
        tracking_no: !trackingNo
      }
    };
  }

  function walkOrders(node, context, depth, items, seenNodes) {
    if (items.length >= MAX_ITEMS || node == null || depth > MAX_WALK_DEPTH) return;
    if (Array.isArray(node)) {
      for (const entry of node) walkOrders(entry, context, depth + 1, items, seenNodes);
      return;
    }
    if (!isPlainObject(node) || seenNodes.has(node)) return;
    seenNodes.add(node);

    const fields = flattenFields(node);
    const parentNode = firstObjectFromKeys(node, [
      "parentOrderMap", "parentOrderInfo", "parentOrder", "parentInfo"
    ]);
    const parentFields = parentNode ? flattenFields(parentNode) : fields;
    const childArrays = childArraysOf(node);
    let parentOrderSn = asId(context.parentOrderSn);
    if (!parentOrderSn) {
      if (parentNode) {
        parentOrderSn = asId(pick(parentFields, PARENT_KEYS));
      } else if (childArrays.length) {
        // 只有父级节点才把自己的订单号下传给子行；叶子行交给 lineFromFields 区分父子。
        parentOrderSn = asId(pick(fields, PARENT_KEYS));
      }
    }
    const subOrderSn = asId(context.subOrderSn)
      || (childArrays.length ? "" : asId(pick(fields, SUB_KEYS)));
    const parentTail = extractParentTailFreight(parentNode, parentFields);
    const mallId = extractMallId(parentNode)
      || asId(pick(parentFields, ["mallId", "fulfillmentMallId"]))
      || text(context.mallId);
    const nextContext = {
      parentOrderSn: parentOrderSn,
      subOrderSn: subOrderSn,
      shopName: text(pick(parentFields, SHOP_KEYS)) || text(pick(fields, SHOP_KEYS)) || context.shopName,
      site: normalizeSite(pick(parentFields, SITE_KEYS)) || normalizeSite(pick(fields, SITE_KEYS)) || context.site || "",
      orderStatus: statusText(pick(parentFields, STATUS_KEYS)) || statusText(pick(fields, STATUS_KEYS)) || context.orderStatus || "",
      orderTime: normalizeTime(pick(parentFields, TIME_KEYS)) || normalizeTime(pick(fields, TIME_KEYS)) || context.orderTime || "",
      trackingNo: extractTracking(parentNode || node, parentFields) || context.trackingNo || "",
      parentTailFreight: parentTail ? parentTail.value : (context.parentTailFreight == null ? null : context.parentTailFreight),
      parentTailCurrency: parentTail ? parentTail.currency : (context.parentTailCurrency || ""),
      mallId: mallId || ""
    };

    let addedLine = false;
    if (!childArrays.length) {
      const line = lineFromFields(fields, nextContext, node);
      if (line) {
        items.push(line);
        addedLine = true;
      }
    }
    for (const child of childArrays) {
      walkOrders(child.items, nextContext, depth + 1, items, seenNodes);
    }
    // 很多接口把列表包在 { success, result: { orderList: [...] } } 这类壳对象里，
    // 只找直接数组会漏掉；这里对确实包含订单字段的壳对象继续下探。
    if (!addedLine && !childArrays.length) {
      for (const [key, value] of Object.entries(node)) {
        if (!isPlainObject(value) || NON_ORDER_WRAPPER_KEYS.has(canonicalKey(key))) continue;
        if (containsOrderKeys(value, 0, new WeakSet()) || childArraysOf(value).length) {
          walkOrders(value, nextContext, depth + 1, items, seenNodes);
        }
      }
    }
  }

  function itemKey(item) {
    if (item.sub_order_sn) return `sub:${item.sub_order_sn}|sku:${item.sku_id || item.skc_id || ""}`;
    return `parent:${item.parent_order_sn}|sku:${item.sku_id || item.skc_id || ""}|${item.goods_name}|${item.quantity == null ? "" : item.quantity}`;
  }

  function mergeItem(target, incoming) {
    for (const key of Object.keys(incoming)) {
      if (key === "missing") continue;
      if (target[key] == null || target[key] === "") target[key] = incoming[key];
    }
    target.missing = target.missing || {};
    for (const key of Object.keys(incoming.missing || {})) {
      target.missing[key] = Boolean(target.missing[key] && incoming.missing[key]);
    }
    if (!target.source || target.source === "dom") target.source = incoming.source || target.source;
    return target;
  }

  function dedupeItems(items) {
    const map = new Map();
    for (const item of items) {
      if (!item) continue;
      const key = itemKey(item);
      if (!key || key === "parent:|sku:|") continue;
      if (map.has(key)) mergeItem(map.get(key), item);
      else map.set(key, item);
    }
    return Array.from(map.values());
  }

  // 面单预估费用是父订单/包裹级别的；按行数量分摊到子订单，保证总额不重复。
  function allocateParentTailFreight(items) {
    const groups = new Map();
    for (const item of items) {
      const key = item.parent_order_sn || item.sub_order_sn || "UNKNOWN";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(item);
    }
    for (const group of groups.values()) {
      if (!group.length) continue;
      // 任一行已有接口尾程时，不再分摊父级预估，避免重复计费。
      if (group.some((item) => item.tail_freight != null)) continue;
      const total = Number(group[0].parent_tail_freight);
      if (!Number.isFinite(total) || total <= 0) continue;
      const currency = group[0].parent_tail_currency || "";
      const quantityTotal = group.reduce((sum, item) => sum + (Number(item.quantity) || 0), 0);
      let allocated = 0;
      group.forEach((item, index) => {
        const quantity = Number(item.quantity) || 0;
        let share;
        if (index === group.length - 1) {
          share = round2(total - allocated);
        } else {
          share = quantityTotal > 0
            ? round2(total * (quantity / quantityTotal))
            : round2(total / group.length);
          allocated = round2(allocated + share);
        }
        item.tail_freight = share;
        item.tail_currency = currency;
        item.tail_freight_source = "waybill_estimate_allocated";
        item.missing = item.missing || {};
        item.missing.tail_freight = false;
      });
    }
  }

  function detailMatchesItem(item, detail) {
    if (!item || !detail) return false;
    if (detail.sub_order_sn && item.sub_order_sn && detail.sub_order_sn === item.sub_order_sn) return true;
    if (detail.parent_order_sn && item.parent_order_sn && detail.parent_order_sn !== item.parent_order_sn) return false;
    if (detail.sku_id && item.sku_id && detail.sku_id === item.sku_id) return true;
    if (detail.ext_code && item.ext_code && detail.ext_code === item.ext_code) return true;
    return false;
  }

  // 详情页补的是列表接口没有的申报价/销售回款/运费回款/预计收入。
  function mergeDetailIntoItems(items, detail) {
    const target = items.find((item) => detailMatchesItem(item, detail));
    if (!target) {
      items.push(detail);
      return items;
    }
    const directKeys = [
      "skc_id", "sku_id", "spu_id", "ext_code", "goods_name", "package_sn", "quantity",
      "declared_price", "declared_currency", "gmv", "gmv_currency",
      "sales_payback", "sales_reversal", "freight_payback", "freight_reversal",
      "estimated_income", "order_payment", "second_payment",
      "tracking_no", "order_status", "order_status_code", "site", "order_time"
    ];
    for (const key of directKeys) {
      if (detail[key] != null && detail[key] !== "") target[key] = detail[key];
    }
    target.financials = detail.financials
      ? { ...(detail.financials || {}), ...(target.financials || {}) }
      : (target.financials || {});
    if (detail.source === "dom-detail") {
      target.detail_sub_order_sn = detail.sub_order_sn || target.detail_sub_order_sn || "";
      target.detail_source = detail.source;
    } else {
      // 列表页的 DOM 只用来补字段，不能把它不完整的子订单号写进 detail_sub_order_sn。
      target.dom_enriched = true;
    }
    const targetSubDigits = text(target.sub_order_sn).replace(/\D/g, "");
    const targetParentDigits = text(target.parent_order_sn).replace(/\D/g, "");
    if (detail.sub_order_sn && (!targetSubDigits || targetSubDigits === targetParentDigits)) {
      target.sub_order_sn = detail.sub_order_sn;
    }
    target.missing = target.missing || {};
    for (const key of Object.keys(detail.missing || {})) {
      if (!detail.missing[key]) target.missing[key] = false;
    }
    return items;
  }

  function containsOrderKeys(value, depth, visited) {
    if (value == null || depth > 4) return false;
    if (Array.isArray(value)) {
      return value.slice(0, 30).some((entry) => containsOrderKeys(entry, depth + 1, visited));
    }
    if (!isPlainObject(value) || visited.has(value)) return false;
    visited.add(value);
    const keys = Object.keys(value).map(canonicalKey);
    if (keys.some((key) => /^(parentordersn|subordersn|orderitemsn|orderlist|suborderlist|orderitemlist|orderstatus|declaredprice|gmv)$/.test(key))) {
      return true;
    }
    return Object.values(value).slice(0, 40).some((entry) => containsOrderKeys(entry, depth + 1, visited));
  }

  function recordKeysSample(json) {
    if (!isPlainObject(json)) return [];
    const keys = Object.keys(json);
    const inner = json.data ?? json.result ?? json.pageData ?? json.content;
    if (isPlainObject(inner)) keys.push(...Object.keys(inner).map((key) => `data.${key}`));
    return keys.slice(0, 24);
  }

  function buildOrders(items) {
    const map = new Map();
    const subKeysByOrder = new Map();
    for (const item of items) {
      const key = item.parent_order_sn || item.sub_order_sn || "UNKNOWN";
      let order = map.get(key);
      if (!order) {
        order = {
          parent_order_sn: key,
          shop_name: "",
          mall_id: "",
          site: "",
          order_status: "",
          order_time: "",
          sub_order_count: 0,
          item_count: 0,
          gmv_total: 0,
          gmv_currency: "",
          declared_total: 0,
          tail_total: 0,
          tail_currency: "",
          tracking_numbers: [],
          missing_declared: 0,
          missing_tail: 0,
          missing_tracking: 0
        };
        map.set(key, order);
        subKeysByOrder.set(key, new Set());
      }
      if (!order.shop_name) order.shop_name = item.shop_name || "";
      if (!order.mall_id) order.mall_id = item.mall_id || "";
      if (!order.site) order.site = item.site || "";
      if (!order.order_status) order.order_status = item.order_status || "";
      if (!order.order_time) order.order_time = item.order_time || "";
      if (item.tail_freight != null && !order.tail_currency) {
        order.tail_currency = item.tail_currency || "";
      }
      const subKeys = subKeysByOrder.get(key);
      const subKey = item.sub_order_sn || `${item.sku_id || item.skc_id || ""}|${item.goods_name}|${item.quantity}`;
      if (!subKeys.has(subKey)) {
        subKeys.add(subKey);
        order.sub_order_count += 1;
      }
      order.item_count += Number(item.quantity) || 0;
      order.gmv_total = round2(order.gmv_total + (Number(item.gmv) || 0));
      if (item.gmv != null && !order.gmv_currency) order.gmv_currency = item.gmv_currency || item.currency || "";
      order.declared_total = round2(order.declared_total + (Number(item.declared_price) || 0));
      order.tail_total = round2(order.tail_total + (Number(item.tail_freight) || 0));
      if (item.tracking_no && order.tracking_numbers.indexOf(item.tracking_no) < 0) {
        order.tracking_numbers.push(item.tracking_no);
      }
      if (item.missing?.declared_price) order.missing_declared += 1;
      if (item.missing?.tail_freight) order.missing_tail += 1;
      if (item.missing?.tracking_no) order.missing_tracking += 1;
    }
    return Array.from(map.values());
  }

  function summarize(items) {
    const orders = buildOrders(items);
    const currencySummary = {};
    const siteSummary = {};
    const statusSummary = {};
    const tailCurrencySummary = {};
    const mallIds = new Set();
    const subOrderKeys = new Set();
    const financialTotals = {
      currency: "CNY",
      sales_payback: 0,
      sales_reversal: 0,
      freight_payback: 0,
      freight_reversal: 0,
      estimated_income: 0,
      order_payment: 0,
      second_payment: 0
    };
    let gmvTotal = 0;
    let declaredTotal = 0;
    let tailTotal = 0;
    let missingDeclared = 0;
    let missingTail = 0;
    let missingTracking = 0;
    let missingGmv = 0;
    let itemCount = 0;
    let gmvCurrency = "";

    for (const item of items) {
      itemCount += Number(item.quantity) || 0;
      subOrderKeys.add(item.sub_order_sn || `${item.parent_order_sn}|${item.sku_id || item.skc_id || ""}|${item.goods_name}|${item.quantity}`);
      if (item.mall_id) mallIds.add(item.mall_id);
      for (const key of ["sales_payback", "sales_reversal", "freight_payback", "freight_reversal", "estimated_income", "order_payment", "second_payment"]) {
        if (item[key] != null) {
          financialTotals[key] = round2(financialTotals[key] + Number(item[key]));
        }
      }
      if (item.financials?.currency) financialTotals.currency = item.financials.currency;
      if (item.gmv != null) {
        gmvTotal = round2(gmvTotal + Number(item.gmv));
        const currency = item.gmv_currency || item.currency || "UNKNOWN";
        currencySummary[currency] = round2((currencySummary[currency] || 0) + Number(item.gmv));
        gmvCurrency = gmvCurrency || currency;
      } else {
        missingGmv += 1;
      }
      declaredTotal = round2(declaredTotal + (Number(item.declared_price) || 0));
      tailTotal = round2(tailTotal + (Number(item.tail_freight) || 0));
      if (item.tail_freight != null) {
        const tailCurrency = item.tail_currency || "UNKNOWN";
        tailCurrencySummary[tailCurrency] = round2((tailCurrencySummary[tailCurrency] || 0) + Number(item.tail_freight));
      }
      if (item.missing?.declared_price) missingDeclared += 1;
      if (item.missing?.tail_freight) missingTail += 1;
      if (item.missing?.tracking_no) missingTracking += 1;
      if (item.site) siteSummary[item.site] = (siteSummary[item.site] || 0) + 1;
      if (item.order_status) statusSummary[item.order_status] = (statusSummary[item.order_status] || 0) + 1;
    }

    const tailCurrencyKeys = Object.keys(tailCurrencySummary);

    return {
      order_count: orders.length,
      sub_order_count: subOrderKeys.size,
      item_count: itemCount,
      gmv_total: gmvTotal,
      gmv_currency: Object.keys(currencySummary).length === 1 ? gmvCurrency : "",
      declared_total: declaredTotal,
      tail_freight_total: tailTotal,
      tail_currency: tailCurrencyKeys.length === 1 ? tailCurrencyKeys[0] : "",
      tail_currency_summary: tailCurrencySummary,
      missing_declared: missingDeclared,
      missing_tail_freight: missingTail,
      missing_tracking: missingTracking,
      missing_gmv: missingGmv,
      currency_summary: currencySummary,
      site_summary: siteSummary,
      status_summary: statusSummary,
      financial_totals: financialTotals,
      mall_ids: Array.from(mallIds)
    };
  }

  function normalizeRecords(records, options) {
    const settings = options || {};
    const items = [];
    const seenNodes = new WeakSet();
    const diagnostics = {
      record_count: Array.isArray(records) ? records.length : 0,
      json_record_count: 0,
      candidate_endpoints: [],
      endpoints: [],
      warnings: []
    };
    const endpointSeen = new Set();

    for (const record of Array.isArray(records) ? records : []) {
      const json = record?.responseJson;
      if (!json) continue;
      diagnostics.json_record_count += 1;
      const endpoint = text(record.endpoint || record.url || "");
      if (endpoint && !endpointSeen.has(endpoint)) {
        endpointSeen.add(endpoint);
        diagnostics.endpoints.push(endpoint);
      }
      const before = items.length;
      walkOrders(json, { parentOrderSn: "", subOrderSn: "", shopName: text(settings.shopName) }, 0, items, seenNodes);
      const added = items.length - before;
      if (added > 0 || containsOrderKeys(json, 0, new WeakSet())) {
        diagnostics.candidate_endpoints.push({
          endpoint: endpoint,
          method: text(record.method || ""),
          status: Number(record.status || 0),
          keys: recordKeysSample(json),
          new_items: added
        });
      }
    }

    let merged = dedupeItems(items);
    const hadApiItems = merged.length > 0;

    const dom = settings.dom && typeof settings.dom === "object" ? settings.dom : null;
    const domDetail = dom?.detail_item && typeof dom.detail_item === "object" ? dom.detail_item : null;
    if (domDetail) {
      merged = mergeDetailIntoItems(merged, domDetail);
      diagnostics.dom_detail_used = true;
    }
    const domItems = [];
    for (const candidate of [].concat(dom?.table_items || [], dom?.text_items || [])) {
      if (candidate) domItems.push(candidate);
    }
    if (domItems.length) {
      if (merged.length) {
        // 接口已经拿到订单行时，DOM 只用来补字段；匹配不上就不追加，避免重复行。
        for (const domItem of domItems) {
          const target = merged.find((item) => detailMatchesItem(item, domItem));
          if (target) mergeDetailIntoItems(merged, domItem);
        }
      } else {
        merged = dedupeItems(domItems);
      }
      diagnostics.warnings.push(hadApiItems ? "dom_enrichment_used" : "dom_fallback_used");
    }

    allocateParentTailFreight(merged);

    if (merged.length > MAX_ITEMS) merged = merged.slice(0, MAX_ITEMS);
    const summary = summarize(merged);
    const shopName = text(settings.shopName) || text(dom?.shop_name) || findFirstShopName(merged);

    if (!merged.length) {
      diagnostics.warnings.push("no_order_line_found");
    }
    if (summary.currency_summary && Object.keys(summary.currency_summary).length > 1) {
      diagnostics.warnings.push("mixed_currency");
    }
    if (summary.missing_gmv === merged.length && merged.length) {
      diagnostics.warnings.push("gmv_missing");
    }

    return {
      ok: merged.length > 0,
      shop_name: shopName,
      page_url: text(settings.pageUrl),
      page_title: text(settings.pageTitle),
      items: merged,
      orders: buildOrders(merged),
      summary: summary,
      diagnostics: {
        ...diagnostics,
        candidate_endpoints: diagnostics.candidate_endpoints.slice(0, 20),
        endpoints: diagnostics.endpoints.slice(0, 40),
        dom_candidate_count: domItems.length,
        dom_text_row_count: Array.isArray(dom?.text_rows) ? dom.text_rows.length : 0
      },
      normalized_at: new Date().toISOString()
    };
  }

  function findFirstShopName(items) {
    for (const item of items) {
      if (item.shop_name) return item.shop_name;
    }
    return "";
  }

  // ================= DOM 兜底 =================

  function elementText(element) {
    return text(element?.innerText || element?.textContent || "");
  }

  function pickShopName(documentRef) {
    const selectors = [
      '[class*="shopName"]', '[class*="storeName"]', '[class*="mallName"]',
      '[class*="shop-name"]', '[class*="store-name"]', '[class*="mall-name"]',
      '[id*="shopName"]', '[id*="storeName"]'
    ];
    for (const selector of selectors) {
      const nodes = documentRef.querySelectorAll(selector);
      for (const node of nodes) {
        const value = elementText(node);
        if (value && value.length <= 60 && !/^(店铺|店铺名称|shop)$/i.test(value)) return value;
      }
    }
    return "";
  }

  function headerFieldKey(label) {
    const value = text(label).toLowerCase();
    if (!value) return "";
    if (/子订单|sub\s*order|order\s*item/.test(value)) return "sub";
    if (/订单号|订单编号|parent\s*order|po\b|订单\s*id/.test(value)) return "parent";
    if (/skc/.test(value)) return "skc";
    if (/sku/.test(value)) return "sku";
    if (/商品|产品|品名|product|goods|title/.test(value)) return "title";
    if (/件数|数量|qty|quantity/.test(value)) return "qty";
    if (/申报价|declared|declare/.test(value)) return "declared";
    if (/gmv|销售额|订单金额|成交额|金额/.test(value)) return "gmv";
    if (/尾程|运费|freight|shipping/.test(value)) return "tail";
    if (/运单|物流|tracking|waybill/.test(value)) return "tracking";
    if (/订单状态|状态|status/.test(value)) return "status";
    if (/站点|国家|site|country|region/.test(value)) return "site";
    if (/时间|日期|time|date/.test(value)) return "time";
    return "";
  }

  function numericText(value) {
    const match = text(value).match(/-?[\d,]+(?:\.\d+)?/);
    if (!match) return null;
    const number = Number(match[0].replace(/,/g, ""));
    return Number.isFinite(number) ? number : null;
  }

  function itemFromRecord(record) {
    if (!record || (!record.parent_order_sn && !record.sub_order_sn)) return null;
    if (!record.sku_id && !record.skc_id) return null;
    return {
      parent_order_sn: text(record.parent_order_sn),
      sub_order_sn: text(record.sub_order_sn),
      skc_id: text(record.skc_id),
      sku_id: text(record.sku_id),
      goods_name: text(record.goods_name),
      quantity: record.quantity == null ? null : Number(record.quantity),
      declared_price: record.declared_price == null ? null : round2(record.declared_price),
      declared_currency: text(record.declared_currency),
      gmv: record.gmv == null ? null : round2(record.gmv),
      gmv_currency: text(record.gmv_currency),
      tail_freight: record.tail_freight == null ? null : round2(record.tail_freight),
      tail_currency: text(record.tail_currency),
      currency: text(record.currency || record.gmv_currency || record.declared_currency),
      tracking_no: text(record.tracking_no),
      order_status: text(record.order_status),
      site: normalizeSite(record.site),
      order_time: normalizeTime(record.order_time),
      shop_name: text(record.shop_name),
      source: text(record.source) || "dom",
      missing: {
        declared_price: record.declared_price == null,
        gmv: record.gmv == null,
        tail_freight: record.tail_freight == null,
        tracking_no: !record.tracking_no
      }
    };
  }

  function extractTableItems(documentRef) {
    const items = [];
    const tables = Array.from(documentRef.querySelectorAll("table"));
    for (const table of tables) {
      const headerCells = Array.from(table.querySelectorAll("thead th, thead td"));
      let headers = headerCells.map(elementText);
      let rows = Array.from(table.querySelectorAll("tbody tr"));
      if (!headers.length) {
        const firstRow = table.querySelector("tr");
        if (!firstRow) continue;
        headers = Array.from(firstRow.children).map(elementText);
        rows = Array.from(table.querySelectorAll("tr")).slice(1);
      }
      if (!headers.length) continue;
      const mapped = headers.map(headerFieldKey);
      if (!mapped.includes("parent") && !mapped.includes("sub")) continue;
      if (!mapped.includes("sku") && !mapped.includes("skc") && !mapped.includes("title")) continue;

      rows.forEach((row) => {
        const cells = Array.from(row.children).map(elementText);
        if (!cells.length) return;
        const record = {
          parent_order_sn: "", sub_order_sn: "", skc_id: "", sku_id: "", goods_name: "",
          quantity: null, declared_price: null, gmv: null, tail_freight: null,
          tracking_no: "", order_status: "", site: "", order_time: "", source: "dom-table"
        };
        mapped.forEach((field, index) => {
          const value = cells[index];
          if (!field || !value) return;
          if (field === "parent") record.parent_order_sn = text(value).match(/[A-Z]{0,4}-?\d{8,}/i)?.[0] || text(value);
          else if (field === "sub") record.sub_order_sn = text(value).match(/\d{8,}/)?.[0] || text(value);
          else if (field === "skc") record.skc_id = text(value).match(/\d{5,}/)?.[0] || text(value);
          else if (field === "sku") record.sku_id = text(value).match(/\d{5,}/)?.[0] || text(value);
          else if (field === "title") record.goods_name = text(value);
          else if (field === "qty") record.quantity = numericText(value);
          else if (field === "declared") record.declared_price = numericText(value);
          else if (field === "gmv") record.gmv = numericText(value);
          else if (field === "tail") record.tail_freight = numericText(value);
          else if (field === "tracking") record.tracking_no = text(value);
          else if (field === "status") record.order_status = text(value);
          else if (field === "site") record.site = text(value);
          else if (field === "time") record.order_time = text(value);
        });
        const item = itemFromRecord(record);
        if (item) items.push(item);
      });
    }
    return items.slice(0, MAX_ITEMS);
  }

  function parseOrderText(blob) {
    const raw = text(blob);
    if (!raw || raw.length > 2000) return null;
    const poMatch = raw.match(/PO[-\s]?\d{3}[-\s]?\d{6,}/i) || raw.match(/\b\d{15,20}\b/);
    if (!poMatch) return null;
    const parent = text(poMatch[0]).replace(/\s+/g, "");
    const allLongNumbers = Array.from(raw.matchAll(/\b\d{10,22}\b/g)).map((match) => match[0]);
    const sub = allLongNumbers.find((value) => value !== parent.replace(/\D/g, "")) || "";
    const skc = raw.match(/SKC\s*(?:ID)?\s*[:：]?\s*(\d{5,})/i)?.[1] || "";
    const sku = raw.match(/SKU\s*(?:ID)?\s*[:：]?\s*(\d{5,})/i)?.[1] || "";
    if (!sku && !skc) return null;
    const quantity = raw.match(/(?:[×xX]\s*(\d+)|(\d+)\s*(?:件|pcs?)\b)/i);
    const declared = raw.match(/申报价(?:格)?\s*(?:\(CNY\)|CNY|¥|￥)?\s*[:：]?\s*([\d,.]+)/i)?.[1];
    const gmv = raw.match(/GMV\s*(?:\(CNY\)|CNY|¥|￥)?\s*[:：]?\s*([\d,.]+)/i)?.[1]
      || raw.match(/(?:销售额|订单金额)\s*(?:\(CNY\)|CNY|¥|￥)?\s*[:：]?\s*([\d,.]+)/i)?.[1];
    const tail = raw.match(/(?:尾程运费|尾程费用)\s*(?:\(CNY\)|CNY|¥|￥)?\s*[:：]?\s*([\d,.]+)/i)?.[1];
    const tracking = raw.match(/\b(?:SPX|YWE|USPS|UPS|FEDEX|DHL)[A-Z0-9]{8,}\b/i)?.[0] || "";
    const record = {
      parent_order_sn: parent,
      sub_order_sn: sub,
      skc_id: skc,
      sku_id: sku,
      goods_name: "",
      quantity: quantity ? Number(quantity[1] || quantity[2]) : null,
      declared_price: declared ? Number(String(declared).replace(/,/g, "")) : null,
      gmv: gmv ? Number(String(gmv).replace(/,/g, "")) : null,
      tail_freight: tail ? Number(String(tail).replace(/,/g, "")) : null,
      tracking_no: tracking,
      order_status: "",
      site: "",
      order_time: "",
      source: "dom-text"
    };
    return itemFromRecord(record);
  }

  function amountNumber(value) {
    if (value == null) return null;
    const number = Number(String(value).replace(/[^\d.-]/g, ""));
    return Number.isFinite(number) ? round2(number) : null;
  }

  // 详情页的金额形如“订单货款 （已减优惠￥0.00）￥75.00”，同一标签后可能有多个金额。
  // stopLabel 用来截断到下一个字段，避免把后面的“预计收入”等金额误当成当前字段。
  function signedAmountsAfter(raw, label, windowSize, stopLabel) {
    const index = raw.indexOf(label);
    if (index < 0) return [];
    const stopIndex = stopLabel ? raw.indexOf(stopLabel, index + label.length) : -1;
    const end = stopIndex > index ? stopIndex : index + (windowSize || 220);
    const segment = raw.slice(index, end);
    return Array.from(segment.matchAll(/(-?)\s*[￥¥]\s*([\d,]+(?:\.\d+)?)/g)).map((match) => {
      const value = amountNumber(match[2]);
      return value == null ? null : (match[1] === "-" ? -value : value);
    }).filter((value) => value != null);
  }

  function firstAmountAfter(raw, label, windowSize, stopLabel) {
    const values = signedAmountsAfter(raw, label, windowSize, stopLabel);
    return values.length ? values[0] : null;
  }

  function lastAmountAfter(raw, label, windowSize, stopLabel) {
    const values = signedAmountsAfter(raw, label, windowSize, stopLabel);
    return values.length ? values[values.length - 1] : null;
  }

  // 订单详情页是订单列表之外唯一能看到申报价和销售回款/运费回款的地方。
  function parseOrderDetailText(rawText) {
    const raw = text(rawText);
    if (!raw || raw.length > 30000) return null;
    // 订单列表页也有“子订单号”表头，必须用详情页独有的申报价/销售回款来判定。
    if (!/申报价格[（(]CNY[）)]|销售回款/.test(raw)) return null;

    const parentOrderSn = text(raw.match(/订单号\s*[:：]\s*(PO-[0-9-]+)/i)?.[1])
      || text(raw.match(/\b(PO-[0-9-]{8,})\b/i)?.[1]);
    const subOrderSn = text(raw.match(/子订单号\s*[:：]\s*([0-9-]+)/i)?.[1]);
    const skuId = text(raw.match(/SKU\s*ID\s*[:：]\s*([0-9]+)/i)?.[1]);
    const skcId = text(raw.match(/SKC\s*ID\s*[:：]\s*([0-9]+)/i)?.[1]);
    const spuId = text(raw.match(/SPU\s*ID\s*[:：]\s*([0-9]+)/i)?.[1]);
    const extCode = text(raw.match(/SKU\s*货号\s*[:：]\s*([^\s]+)/i)?.[1]);
    if (!parentOrderSn && !subOrderSn) return null;
    if (!skuId && !skcId) return null;

    const declaredPrice = amountNumber(raw.match(/申报价格[（(]CNY[）)]\s*[:：]?\s*([\d,.]+)/i)?.[1]);
    const salesPayback = firstAmountAfter(raw, "销售回款", 200, "订单货款");
    const orderPayment = lastAmountAfter(raw, "订单货款", 260, "二次收款");
    const secondPayment = lastAmountAfter(raw, "二次收款", 220, "销售冲回");
    const salesReversal = firstAmountAfter(raw, "销售冲回", 160, "运费回款");
    const freightPayback = firstAmountAfter(raw, "运费回款", 160, "运费冲回");
    const freightReversal = firstAmountAfter(raw, "运费冲回", 160, "预计收入");
    const estimatedIncome = firstAmountAfter(raw, "预计收入", 160, "收货信息");
    const gmv = orderPayment != null ? orderPayment : salesPayback;

    const quantityMatch = raw.match(/[×xX]\s*(\d+)/);
    const quantity = quantityMatch ? Number(quantityMatch[1]) : null;
    const goodsName = text(
      raw.match(/([A-Z][A-Za-z0-9 ,.'&()\-@]{20,220})\s*[×xX]\s*\d+/)?.[1]
    );
    const trackingRaw = text(raw.match(/运单号\s*[:：]\s*([^\s]+)/)?.[1]);
    const trackingCandidates = trackingRaw.match(/[A-Z0-9]{8,}/gi) || [];
    const trackingNo = trackingCandidates.sort((left, right) => right.length - left.length)[0] || trackingRaw;
    const status = text(raw.match(/状态\s*[:：]\s*([^\s]+)/)?.[1]);
    const site = normalizeSite(raw.match(/站点名?\s*[:：]\s*([^\s]+)/)?.[1]);
    const orderTime = text(raw.match(/订单创建时间\s*[:：]\s*([0-9-]+\s+[0-9:]+)/)?.[1]);
    const packageSn = text(raw.match(/包裹\d*\s*[:：]\s*(PK-[0-9]+)/i)?.[1]);

    return {
      parent_order_sn: parentOrderSn,
      sub_order_sn: subOrderSn,
      skc_id: skcId,
      sku_id: skuId,
      spu_id: spuId,
      ext_code: extCode,
      goods_name: goodsName,
      package_sn: packageSn,
      image_url: "",
      quantity: quantity,
      declared_price: declaredPrice,
      declared_currency: declaredPrice == null ? "" : "CNY",
      gmv: gmv,
      gmv_currency: gmv == null ? "" : "CNY",
      sales_payback: salesPayback,
      sales_reversal: salesReversal,
      freight_payback: freightPayback,
      freight_reversal: freightReversal,
      estimated_income: estimatedIncome,
      order_payment: orderPayment,
      second_payment: secondPayment,
      financials: {
        currency: "CNY",
        sales_payback: salesPayback,
        sales_reversal: salesReversal,
        freight_payback: freightPayback,
        freight_reversal: freightReversal,
        estimated_income: estimatedIncome,
        order_payment: orderPayment,
        second_payment: secondPayment
      },
      tail_freight: null,
      tail_currency: "",
      tail_freight_source: "",
      tracking_no: trackingNo,
      order_status: statusText(status),
      order_status_code: status,
      site: site,
      order_time: orderTime,
      shop_name: "",
      mall_id: "",
      source: "dom-detail",
      missing: {
        declared_price: declaredPrice == null,
        gmv: gmv == null,
        tail_freight: true,
        tracking_no: !trackingNo
      }
    };
  }

  function extractTextRows(documentRef) {
    const rows = [];
    const seen = new Set();
    const push = (element) => {
      const value = elementText(element);
      if (!value || value.length < 20 || value.length > 2000) return;
      if (!/PO[-\s]?\d{3}[-\s]?\d{6,}|\b\d{15,20}\b/i.test(value)) return;
      const key = value.slice(0, 300);
      if (seen.has(key)) return;
      seen.add(key);
      rows.push(value);
    };
    const preferred = documentRef.querySelectorAll('tr, [role="row"]');
    if (preferred.length) {
      Array.from(preferred).slice(0, 1200).forEach(push);
    }
    if (!rows.length) {
      const candidates = documentRef.querySelectorAll('[class*="order"], [class*="Order"], [class*="row"], [class*="Row"]');
      Array.from(candidates).slice(0, 2500).forEach(push);
    }
    return rows.slice(0, MAX_TEXT_ROWS);
  }

  function extractFromDom(documentRef) {
    if (!documentRef) return { shop_name: "", table_items: [], text_items: [], text_rows: [], detail_item: null };
    const url = text(documentRef.location?.href);
    const pageText = text(documentRef.body?.innerText || documentRef.documentElement?.innerText || "");
    const detailItem = (/order-detail\.html/i.test(url) || /申报价格[（(]CNY[）)]|销售回款/.test(pageText))
      ? parseOrderDetailText(pageText)
      : null;
    if (detailItem) {
      return {
        url: url,
        title: text(documentRef.title),
        shop_name: pickShopName(documentRef),
        table_items: [],
        text_items: [],
        text_rows: [],
        detail_item: detailItem
      };
    }
    const textRows = extractTextRows(documentRef);
    const textItems = textRows.map(parseOrderText).filter(Boolean);
    return {
      url: url,
      title: text(documentRef.title),
      shop_name: pickShopName(documentRef),
      table_items: extractTableItems(documentRef),
      text_items: textItems,
      text_rows: textRows,
      detail_item: null
    };
  }

  return Object.freeze({
    version: "0.1.0",
    normalizeRecords: normalizeRecords,
    extractFromDom: extractFromDom,
    summarize: summarize,
    buildOrders: buildOrders,
    parseOrderText: parseOrderText,
    parseOrderDetailText: parseOrderDetailText,
    normalizeSite: normalizeSite,
    normalizeTime: normalizeTime,
    parseAmount: parseAmount,
    fieldAliases: Object.freeze({
      parent: PARENT_KEYS,
      sub: SUB_KEYS,
      skc: SKC_KEYS,
      sku: SKU_KEYS,
      title: TITLE_KEYS,
      quantity: QTY_KEYS,
      declared: DECLARED_KEYS,
      gmv: GMV_KEYS,
      tail: TAIL_KEYS,
      tracking: TRACKING_KEYS,
      status: STATUS_KEYS,
      site: SITE_KEYS,
      time: TIME_KEYS
    })
  });
});
