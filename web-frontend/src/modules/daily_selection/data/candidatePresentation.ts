import type { DailySelectionCandidate } from "../types";

/** 候选卡片的排序口径：综合分由后端给，其余在前端本地排序。 */
export type CandidateSortKey = "score" | "sales" | "price" | "moq";

export const CANDIDATE_SORT_OPTIONS: ReadonlyArray<{ value: CandidateSortKey; label: string }> = [
  { value: "score", label: "综合排序" },
  { value: "sales", label: "销量优先" },
  { value: "price", label: "价格从低到高" },
  { value: "moq", label: "起订量从低到高" },
];

const SALES_UNITS: Record<string, number> = {
  万: 10000,
  w: 10000,
  W: 10000,
  千: 1000,
  k: 1000,
  K: 1000,
};

/**
 * 把上游的销量文案解析成数字，用于排序。
 * 1688/淘宝会返回「已售 1.2万」「5000+」「成交 3.5k」这类混合文案。
 */
export function parseSalesCount(value: string | null | undefined): number | null {
  if (!value) return null;
  const text = value.replace(/[,，\s]/g, "");
  const match = /(\d+(?:\.\d+)?)(万|w|W|千|k|K)?/.exec(text);
  if (!match) return null;
  const base = Number(match[1]);
  if (!Number.isFinite(base)) return null;
  const unit = match[2];
  return Math.round(base * (unit ? SALES_UNITS[unit] : 1));
}

/**
 * 候选的销量：优先用商品级销量文案，缺失时退化为各 SKU 销量之和。
 * 1688 详情接口对部分类目不返回商品级销量，但 SKU 行仍可能带销量。
 */
export function candidateSalesCount(candidate: DailySelectionCandidate): number | null {
  const textCount = parseSalesCount(candidate.sales_text);
  if (textCount !== null) return textCount;
  const variantSales = (candidate.source_variant_records ?? [])
    .map((record) => Number(record.sales))
    .filter((value) => Number.isFinite(value) && value > 0);
  if (variantSales.length === 0) return null;
  return variantSales.reduce((total, value) => total + value, 0);
}

export function candidateSalesLabel(candidate: DailySelectionCandidate): string {
  const count = candidateSalesCount(candidate);
  return count === null ? "—" : formatSalesCount(count);
}

function formatSalesCount(count: number): string {
  if (count >= 10000) {
    const wan = Math.round((count / 10000) * 10) / 10;
    return `${wan}万`;
  }
  return count.toLocaleString("zh-CN");
}

/**
 * 候选是否具备「可直接进入产品处理」的三件套：价格、主图、SKU 记录。
 * 用于结果区的「只看完整数据」开关，避免用户选中后才发现缺资料。
 */
export function isCandidateComplete(candidate: DailySelectionCandidate): boolean {
  const hasPrice = candidate.price_cny !== null && candidate.price_cny !== "";
  const hasMainImage = Boolean(candidate.main_image_url);
  const hasSku = (candidate.source_variant_records?.length ?? 0) > 0;
  return hasPrice && hasMainImage && hasSku;
}

/**
 * 保持后端综合分顺序的稳定排序；其余口径把缺失值排在最后，
 * 数值相同时沿用原顺序（Array.prototype.sort 在 ES2019 起稳定）。
 */
export function sortCandidates(
  candidates: DailySelectionCandidate[],
  key: CandidateSortKey,
): DailySelectionCandidate[] {
  if (key === "score") return [...candidates];
  const sortable = [...candidates];
  sortable.sort((left, right) => compareByKey(left, right, key));
  return sortable;
}

function compareByKey(
  left: DailySelectionCandidate,
  right: DailySelectionCandidate,
  key: Exclude<CandidateSortKey, "score">,
): number {
  if (key === "sales") {
    const leftSales = candidateSalesCount(left);
    const rightSales = candidateSalesCount(right);
    return compareDescending(leftSales, rightSales);
  }
  if (key === "price") {
    return compareAscending(toNumber(left.price_cny), toNumber(right.price_cny));
  }
  return compareAscending(left.min_order_quantity, right.min_order_quantity);
}

function toNumber(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** 缺失值永远排最后：升序（价格/起订量）。 */
function compareAscending(left: number | null, right: number | null): number {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return left - right;
}

/** 缺失值永远排最后：降序（销量）。 */
function compareDescending(left: number | null, right: number | null): number {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return right - left;
}

const RISK_LABELS: Record<string, string> = {
  medical: "医疗/健康类",
  food: "食品类",
  infant: "婴童类",
  dangerous_goods: "危险品",
  ip: "侵权风险",
};

const REASON_LABELS: Record<string, string> = {
  duplicate_source_offer: "重复商品",
  duplicate_source_url: "重复链接",
  missing_main_image: "缺主图",
  missing_price: "价格未取到",
  price_below_min: "低于价格区间",
  price_above_max: "高于价格区间",
  missing_moq: "起订量未知",
  moq_above_limit: "起订量超限",
  missing_sku: "未取到 SKU",
  missing_sku_price: "SKU 缺价格",
  sku_price_below_min: "SKU 价低于下限",
  sku_price_above_max: "SKU 价高于上限",
  sku_count_below_min: "SKU 少于下限",
  sku_count_above_max: "SKU 多于上限",
  missing_sku_stock: "缺 SKU 库存",
  sku_stock_below_min: "SKU 库存偏低",
  sku_stock_above_max: "SKU 库存偏高",
  risk_not_confirmable: "风险项需人工确认",
};

export function riskLabel(tag: string): string {
  const normalized = tag.startsWith("risk_") ? tag.slice("risk_".length) : tag;
  return RISK_LABELS[normalized] ?? "风险项";
}

export function reasonLabel(code: string): string {
  return REASON_LABELS[code] ?? "待复核";
}

/** 风险和筛选原因统一转成中文提示，风险项排在最前。 */
export function candidateWarningTags(candidate: DailySelectionCandidate): string[] {
  const labels: string[] = [];
  for (const tag of candidate.risk_tags ?? []) {
    labels.push(riskLabel(tag));
  }
  for (const code of candidate.selection_reasons ?? []) {
    labels.push(code.startsWith("risk_") ? riskLabel(code) : reasonLabel(code));
  }
  return [...new Set(labels)];
}

/**
 * 用评分分项和已采字段生成「为什么推荐」的中文短语。
 * 分项缺失（历史批次）时退化为字段是否存在，保证老数据也能展示。
 */
export function recommendationTags(candidate: DailySelectionCandidate): string[] {
  const components = candidate.score_components ?? {};
  const hasComponent = (key: string) => Number(components[key] ?? 0) > 0;
  const skuCount = candidate.source_variant_records?.length ?? 0;
  const imageCount = candidate.source_image_urls?.length ?? 0;
  const attributeCount = Object.keys(candidate.source_attributes ?? {}).length;
  const tags: string[] = [];

  if (hasComponent("complete_sku") || (skuCount > 0 && candidate.price_cny !== null)) {
    tags.push(skuCount > 0 ? `SKU ${skuCount} 个` : "SKU 完整");
  }
  if (hasComponent("image_gallery") || imageCount > 1) {
    tags.push(imageCount > 1 ? `图集 ${imageCount} 张` : "图集齐全");
  }
  if (hasComponent("attributes") || attributeCount > 0) {
    tags.push("属性齐全");
  }
  if (hasComponent("match") || candidate.query_keyword) {
    tags.push("关键词命中");
  }
  return tags;
}

/** 主图之外的图集数量，用于图片角标；0 表示没有可展示的图集。 */
export function galleryCount(candidate: DailySelectionCandidate): number {
  return candidate.source_image_urls?.length ?? 0;
}

/**
 * 详情抽屉可展示的全部图片：主图 + 图集 + 详情长图，按顺序去重。
 * 上游三个字段可能互相重复，抽屉里逐张展示前先收敛。
 */
export function candidateGalleryUrls(candidate: DailySelectionCandidate): string[] {
  const urls = [
    candidate.main_image_url,
    ...(candidate.source_image_urls ?? []),
    ...(candidate.source_detail_image_urls ?? []),
  ];
  return [...new Set(urls.filter((url): url is string => Boolean(url)))];
}

export type CandidateAttributeEntry = { label: string; value: string };

/** 商品属性转成便于表格展示的键值对，空值不占行。 */
export function candidateAttributeEntries(candidate: DailySelectionCandidate): CandidateAttributeEntry[] {
  return Object.entries(candidate.source_attributes ?? {})
    .map(([label, value]) => ({ label, value: formatAttributeValue(value) }))
    .filter((entry) => entry.label.trim() !== "" && entry.value !== "");
}

function formatAttributeValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.map((item) => formatAttributeValue(item)).filter(Boolean).join(" / ");
  }
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => {
        const text = formatAttributeValue(item);
        return text ? `${key}: ${text}` : "";
      })
      .filter(Boolean)
      .join(" / ");
  }
  return String(value);
}

export type CandidateTierEntry = { minOrderQuantity: number; priceCny: number };

/** 阶梯价按起订量升序；上游没给阶梯价时返回空数组，由界面给占位说明。 */
export function candidateTierEntries(candidate: DailySelectionCandidate): CandidateTierEntry[] {
  const tiers: CandidateTierEntry[] = [];
  for (const tier of candidate.tiered_prices ?? []) {
    const moq = Number(tier.min_order_quantity);
    const price = toNumber(tier.price_cny);
    if (!Number.isFinite(moq) || moq < 1 || price === null) continue;
    tiers.push({ minOrderQuantity: moq, priceCny: price });
  }
  tiers.sort((left, right) => left.minOrderQuantity - right.minOrderQuantity);
  return tiers;
}

/** SKU 之间的价格区间，用于快速判断同一商品内的价格跨度。 */
export function candidateSkuPriceRange(
  candidate: DailySelectionCandidate,
): { min: number; max: number } | null {
  const prices = (candidate.source_variant_records ?? [])
    .map((record) => toNumber(record.price_cny))
    .filter((value): value is number => value !== null && value > 0);
  if (prices.length === 0) return null;
  return { min: Math.min(...prices), max: Math.max(...prices) };
}

export type QuoteInput = {
  priceCny: number | string | null | undefined;
  freightCny?: number | string | null | undefined;
  exchangeRate: number | string | null | undefined;
  salePriceUsd?: number | string | null | undefined;
};

export type QuoteEstimate = {
  landedCostCny: number;
  landedCostUsd: number;
  profitUsd: number | null;
  marginRate: number | null;
  breakEvenUsd: number;
};

/**
 * 比价试算：采购价 + 单件运费按给定汇率折成美元，再与目标售价对比。
 * 采购价或汇率缺失时无法试算，返回 null 让界面显示占位。
 */
export function estimateQuote(input: QuoteInput): QuoteEstimate | null {
  const price = toNumber(input.priceCny);
  const rate = toNumber(input.exchangeRate);
  if (price === null || price <= 0 || rate === null || rate <= 0) return null;
  const freight = Math.max(0, toNumber(input.freightCny) ?? 0);
  const landedCostCny = roundMoney(price + freight);
  const landedCostUsd = roundMoney(landedCostCny / rate);
  const sale = toNumber(input.salePriceUsd);
  const profitUsd = sale === null ? null : roundMoney(sale - landedCostUsd);
  const marginRate = sale !== null && sale > 0 && profitUsd !== null
    ? roundMoney(profitUsd / sale)
    : null;
  return { landedCostCny, landedCostUsd, profitUsd, marginRate, breakEvenUsd: landedCostUsd };
}

function roundMoney(value: number): number {
  return Math.round(value * 100) / 100;
}
