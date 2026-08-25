import type {
  PodBatch,
  PodBatchCount,
  PodBatchItem,
  PodBatchItemStatus,
  PodBatchStatus,
  PodBusinessFields,
  PodBusinessFieldsDraft,
  PodListingFields,
  PodListingFieldsDraft,
  PodTemplateCalibration,
  PodStyleTitleStatus,
} from "../types";

export type PodStyleRow = {
  index: number;
  title: string;
  title_status?: PodStyleTitleStatus;
  listing_ready?: boolean;
  title_error_message?: string;
  results: Array<PodBatchItem | undefined>;
  status: "queued" | "generating" | "completed" | "partial_failure" | "failed";
};

export const POD_BATCH_COUNTS = [2, 10, 20, 40, 60, 100] as const satisfies readonly PodBatchCount[];
export const MAX_POD_SKU_COUNT = 100;
export const MAX_POD_SKU_NAME_LENGTH = 120;

export const EMPTY_POD_BUSINESS_FIELDS: PodBusinessFieldsDraft = {
  product_name: "",
  product_category: "",
  target_market: "",
  target_audience: "",
  core_selling_points: "",
  design_theme: "",
  style_keywords: "",
  color_preferences: "",
  excluded_elements: "",
};

export const EMPTY_POD_LISTING_FIELDS: PodListingFieldsDraft = {
  title_mode: "long",
  declared_price: "",
  suggested_price_usd: "",
  category_name: "",
  skus: [{ name: "", length_cm: "", width_cm: "", height_cm: "", weight_g: "" }],
};

const ACTIVE_BATCH_STATUSES = new Set<PodBatchStatus>([
  "queued",
  "generating_patterns",
  "compositing",
  "generating_titles",
  "settlement_pending",
]);
const ACTIVE_ITEM_STATUSES = new Set<PodBatchItemStatus>([
  "queued",
  "generating_pattern",
  "compositing",
  "optimizing_scene",
]);
const ACTIVE_TITLE_STATUSES = new Set<PodStyleTitleStatus>(["queued", "generating"]);
const SETTLED_BATCH_STATUSES = new Set<PodBatchStatus>(["completed", "partial_failure", "failed"]);

function valueOrFallback(value: string): string {
  return value.trim() || "未填写";
}

export function buildPromptV1(fields: PodBusinessFieldsDraft): string {
  return [
    "[POD DIRECT LISTING PROMPT v1]",
    "模板只用于识别产品结构、轮廓、材质和可印刷区域，不作为生成底图。",
    `产品名称：${valueOrFallback(fields.product_name)}`,
    `产品品类：${valueOrFallback(fields.product_category)}`,
    `目标市场：${valueOrFallback(fields.target_market)}`,
    `目标人群：${valueOrFallback(fields.target_audience)}`,
    `核心卖点：${valueOrFallback(fields.core_selling_points)}`,
    `设计主题：${valueOrFallback(fields.design_theme)}`,
    `风格关键词：${valueOrFallback(fields.style_keywords)}`,
    `偏好配色：${valueOrFallback(fields.color_preferences)}`,
    `禁用元素：${valueOrFallback(fields.excluded_elements)}`,
    "硬性规则：",
    "1. 每款正常只生成一次 2×2 成组图片；生成、拆分或去重失败时最多重试一次。",
    "2. 四格顺序固定为主图、细节图 A、细节图 B、场景图。",
    "3. 同一款四张图必须保持产品、结构、底色、图案内容、图案尺寸与位置完全一致。",
    "4. 不同款式必须使用不同图案、构图和创意配方，禁止复用上一款图案。",
    "5. 禁止复制模板原有图案、产品颜色、背景或场景；必须重新设计产品表面与展示环境。",
    "6. 不得添加未授权品牌、商标、版权角色、文字、水印或与禁用元素冲突的内容。",
  ].join("\n");
}

export function resolveCreativePrompt(fields: PodBusinessFieldsDraft, currentBatchEdit: string): string {
  return currentBatchEdit.trim() || buildPromptV1(fields);
}

function splitBusinessField(value: string): string[] {
  return value.split(/[、，,;；\n]+/).map((part) => part.trim()).filter(Boolean);
}

export function businessFieldsForApi(fields: PodBusinessFieldsDraft): PodBusinessFields {
  return {
    product_name: fields.product_name.trim(),
    product_category: fields.product_category.trim(),
    target_market: fields.target_market.trim(),
    target_audience: fields.target_audience.trim(),
    core_selling_points: splitBusinessField(fields.core_selling_points),
    design_theme: fields.design_theme.trim(),
    style_keywords: splitBusinessField(fields.style_keywords),
    color_preferences: splitBusinessField(fields.color_preferences),
    excluded_elements: splitBusinessField(fields.excluded_elements),
  };
}

type PodListingFieldsResult = { value: PodListingFields; error?: never } | { value?: never; error: string };

function positiveListingNumber(value: string, label: string): number | { error: string } {
  const normalized = value.trim();
  const parsed = Number(normalized);
  if (!normalized || !Number.isFinite(parsed) || parsed <= 0) return { error: `${label}必须是大于 0 的有效数字。` };
  return parsed;
}

export function listingFieldsForApi(fields: PodListingFieldsDraft): PodListingFieldsResult {
  const declaredPrice = positiveListingNumber(fields.declared_price, "申报价");
  if (typeof declaredPrice !== "number") return declaredPrice;
  const suggestedPriceUsd = positiveListingNumber(fields.suggested_price_usd, "建议美元售价");
  if (typeof suggestedPriceUsd !== "number") return suggestedPriceUsd;
  const categoryName = fields.category_name.trim();
  if (!categoryName) return { error: "请填写店小秘类目。" };

  if (!fields.skus.length) return { error: "请至少填写一个 SKU。" };
  if (fields.skus.length > MAX_POD_SKU_COUNT) return { error: `SKU 最多可添加 ${MAX_POD_SKU_COUNT} 个。` };
  const skus = [] as PodListingFields["skus"];
  for (const sku of fields.skus) {
    const name = sku.name.trim();
    if (!name) return { error: "SKU 名称不能为空。" };
    if (name.length > MAX_POD_SKU_NAME_LENGTH) return { error: `SKU 名称不能超过 ${MAX_POD_SKU_NAME_LENGTH} 个字符。` };
    const lengthCm = positiveListingNumber(sku.length_cm, `SKU「${name}」的长度`);
    if (typeof lengthCm !== "number") return lengthCm;
    const widthCm = positiveListingNumber(sku.width_cm, `SKU「${name}」的宽度`);
    if (typeof widthCm !== "number") return widthCm;
    const heightCm = positiveListingNumber(sku.height_cm, `SKU「${name}」的高度`);
    if (typeof heightCm !== "number") return heightCm;
    const weightG = positiveListingNumber(sku.weight_g, `SKU「${name}」的重量`);
    if (typeof weightG !== "number") return weightG;
    skus.push({ name, length_cm: lengthCm, width_cm: widthCm, height_cm: heightCm, weight_g: weightG });
  }

  return {
    value: {
      title_mode: fields.title_mode,
      declared_price: declaredPrice,
      suggested_price_usd: suggestedPriceUsd,
      category_name: categoryName,
      skus,
    },
  };
}

export function isPodBatchCount(value: number): value is PodBatchCount {
  return Number.isInteger(value) && value >= 1 && value <= 200;
}

export function groupPodStyleRows(
  batch: Pick<PodBatch, "items" | "style_grid" | "business_fields" | "style_titles">,
): PodStyleRow[] {
  const productName = batch.business_fields?.product_name?.trim() || "未命名商品";
  const grouped = new Map<number, Array<PodBatchItem | undefined>>();
  const titlesByStyle = new Map((batch.style_titles ?? []).map((title) => [title.style_index, title]));
  for (const styleIndex of titlesByStyle.keys()) grouped.set(styleIndex, [undefined, undefined, undefined, undefined]);
  for (const item of batch.items) {
    const styleIndex = batch.style_grid ? item.style_index ?? item.index : item.index;
    const variantIndex = batch.style_grid ? item.variant_index ?? 1 : 1;
    const results = grouped.get(styleIndex) ?? [undefined, undefined, undefined, undefined];
    results[Math.min(4, Math.max(1, variantIndex)) - 1] = item;
    grouped.set(styleIndex, results);
  }
  return [...grouped.entries()].sort(([left], [right]) => left - right).map(([index, results]) => {
    const styleTitle = titlesByStyle.get(index);
    const settled = results.filter(Boolean);
    const completed = settled.filter((item) => item?.status === "completed").length;
    const failed = settled.filter((item) => item?.status === "failed").length;
    const status = completed === 4 ? "completed"
      : failed === 4 || (!completed && failed > 0) ? "failed"
      : completed || failed ? "partial_failure"
      : settled.some((item) => item && isActivePodItemStatus(item.status)) ? "generating"
      : "queued";
    return {
      index,
      title: styleTitle?.title?.trim() || `${productName} · 款式 #${String(index).padStart(3, "0")}`,
      title_status: styleTitle?.status,
      listing_ready: styleTitle?.listing_ready,
      title_error_message: styleTitle?.error_message,
      results,
      status,
    };
  });
}

export function batchProgress(batch: { count: number; processed_count: number }): number {
  if (batch.count <= 0) return 0;
  return Math.min(100, Math.max(0, Math.round((batch.processed_count / batch.count) * 100)));
}

export function formatPodBatchWaitingTime(createdAt: string, now = Date.now()): string {
  const startedAt = Date.parse(createdAt);
  const elapsedSeconds = Number.isFinite(startedAt) ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  const minutes = Math.floor(elapsedSeconds / 60);
  const seconds = elapsedSeconds % 60;
  return minutes ? `${minutes}分${seconds}秒` : `${seconds}秒`;
}

export function isActiveBatchStatus(status: PodBatchStatus): boolean {
  return ACTIVE_BATCH_STATUSES.has(status);
}

export function isActivePodItemStatus(status: PodBatchItemStatus): boolean {
  return ACTIVE_ITEM_STATUSES.has(status);
}

export function isActivePodStyleTitleStatus(status: PodStyleTitleStatus): boolean {
  return ACTIVE_TITLE_STATUSES.has(status);
}

export function canRegeneratePodStyle(
  batchStatus: PodBatchStatus,
  styleStatus: PodStyleRow["status"],
): boolean {
  return SETTLED_BATCH_STATUSES.has(batchStatus) && styleStatus === "failed";
}

export function canRegeneratePodStyleTitle(
  batchStatus: PodBatchStatus,
  titleStatus: PodStyleTitleStatus | undefined,
  results: Array<Pick<PodBatchItem, "status" | "public_url"> | undefined>,
): boolean {
  return SETTLED_BATCH_STATUSES.has(batchStatus)
    && titleStatus === "failed"
    && results.length === 4
    && results.every((item) => item?.status === "completed" && Boolean(item.public_url));
}

export function shouldPollPodBatch(
  isActive: boolean,
  visibility: DocumentVisibilityState,
  status: PodBatchStatus,
  itemStatuses: PodBatchItemStatus[] = [],
  titleStatuses: PodStyleTitleStatus[] = [],
): boolean {
  return isActive
    && visibility === "visible"
    && (isActiveBatchStatus(status) || itemStatuses.some(isActivePodItemStatus) || titleStatuses.some(isActivePodStyleTitleStatus));
}

export function podStyleTitleStatusLabel(status?: PodStyleTitleStatus, listingReady = false): string {
  if (listingReady) return "可上架";
  return ({
    queued: "待补标题",
    generating: "标题生成中",
    completed: "标题已完成",
    failed: "待补标题",
  } as Record<PodStyleTitleStatus, string>)[status ?? "queued"];
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(1, Math.max(0, value));
}

export function clampTemplateCalibration(value: PodTemplateCalibration): PodTemplateCalibration {
  const width = Math.min(1, Math.max(0.02, Number.isFinite(value.mask.width) ? value.mask.width : 0.02));
  const height = Math.min(1, Math.max(0.02, Number.isFinite(value.mask.height) ? value.mask.height : 0.02));
  return {
    mask: {
      x: Math.min(1 - width, clamp01(value.mask.x)),
      y: Math.min(1 - height, clamp01(value.mask.y)),
      width,
      height,
    },
    anchor: {
      x: clamp01(value.anchor.x),
      y: clamp01(value.anchor.y),
    },
  };
}

export function defaultTemplateCalibration(): PodTemplateCalibration {
  return {
    mask: { x: 0.25, y: 0.25, width: 0.5, height: 0.5 },
    anchor: { x: 0.5, y: 0.5 },
  };
}

export function podBatchStatusLabel(status: PodBatchStatus): string {
  return {
    queued: "等待启动",
    generating_patterns: "生成图片",
    compositing: "拆分并发布",
    generating_titles: "生成标题",
    completed: "已完成",
    partial_failure: "部分完成",
    failed: "生成失败",
    settlement_pending: "等待计费结算",
    billing_auth_required: "需要重新授权",
  }[status];
}

export function podItemStatusLabel(status: string): string {
  return ({
    queued: "等待中",
    generating_pattern: "生成图片",
    compositing: "拆分并发布",
    completed: "已完成",
    failed: "失败",
    optimizing_scene: "优化场景",
  } as Record<string, string>)[status] ?? status;
}
