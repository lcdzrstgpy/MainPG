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
  PodStyleTitleStatus,
  PodStyleTitleSource,
  SpecCardConfig,
  SpecCardCorner,
  SpecCardStyle,
} from "../types";

export type PodStyleRow = {
  index: number;
  title: string;
  title_status?: PodStyleTitleStatus;
  title_source?: PodStyleTitleSource;
  listing_ready?: boolean;
  export_selected: boolean;
  title_error_message?: string;
  results: Array<PodBatchItem | undefined>;
  status: "queued" | "generating" | "completed" | "partial_failure" | "failed";
};

export const POD_BATCH_COUNTS = [2, 10, 20, 40, 60, 100] as const satisfies readonly PodBatchCount[];
export const MAX_POD_SKU_COUNT = 100;
export const MAX_POD_SKU_NAME_LENGTH = 120;

// 业务字段字符上限：与后端 contracts.py 的 BusinessFields 对齐，超限会被后端 422 拒绝。
// 单值字段（产品名称/品类/市场/人群/主题）与文案限制为整段上限；
// 多值字段（卖点/元素/配色/禁用）提交前按分隔符拆成数组，限制「单条 + 条数」。
export const POD_BUSINESS_TEXT_MAX_LENGTH = 500;
export const POD_COPY_RESTRICTIONS_MAX_LENGTH = 2000;
export const POD_BUSINESS_LIST_ITEM_MAX_LENGTH = 200;
export const POD_BUSINESS_LIST_MAX_ITEMS = 100;
const POD_STYLE_PRESENTATION_ROLES = ["lifestyle", "detail_a", "detail_b", "hero"] as const;

export const EMPTY_POD_BUSINESS_FIELDS: PodBusinessFieldsDraft = {
  product_name: "",
  product_category: "",
  target_market: "",
  target_audience: "",
  core_selling_points: "",
  design_theme: "",
  fabric_craft: "",
  scene_mood: "",
  style_keywords: "",
  color_preferences: "",
  excluded_elements: "",
  copy_restrictions: "",
};

export const EMPTY_POD_LISTING_FIELDS: PodListingFieldsDraft = {
  title_mode: "long",
  category_name: "",
  skus: [{ name: "", declared_price: "", weight_g: "" }],
};

// 尺寸详情（第 4 张图上的规格卡）是结构化表格，不再是自由表格：
// 第 1 行为表头（SKU / Length / Width / Height，英文，会原样印到图上），之后每个 SKU 一行，固定 4 列；
// 表头与第 1 列由 SKU 预设自动映射，属强制只读单元格。
export const SPEC_CARD_DIMENSION_HEADER = ["SKU", "Length", "Width", "Height"] as const;
export const SPEC_CARD_COLUMNS = SPEC_CARD_DIMENSION_HEADER.length;
export const SPEC_CARD_DIMENSION_LABELS = ["长（cm）", "宽（cm）", "高（cm）"] as const;
export const SPEC_CARD_MIN_ROWS = 1;
// 行数跟随 SKU 数量：1 行表头 + 每个 SKU 一行。
export const SPEC_CARD_MAX_ROWS = MAX_POD_SKU_COUNT + 1;
export const SPEC_CARD_MIN_COLUMNS = 1;
export const SPEC_CARD_MAX_COLUMNS = 6;
export const SPEC_CARD_CELL_MAX_LENGTH = 120;

export const SPEC_CARD_STYLE_LABELS: Record<SpecCardStyle, string> = {
  light: "浅色卡片",
  dark: "深色卡片",
};

export const SPEC_CARD_CORNER_LABELS: Record<SpecCardCorner, string> = {
  "bottom-right": "右下角",
  "bottom-left": "左下角",
  "top-right": "右上角",
  "top-left": "左上角",
};

/** 已有单元格里匹配该 SKU 的行；匹配不到时退回按位置对齐（重命名后仍保留已填尺寸）。 */
function findDimensionRow(
  cells: readonly (readonly string[])[],
  name: string,
  index: number,
): readonly string[] | undefined {
  const trimmed = name.trim();
  const matched = trimmed ? cells.find((row) => (row[0] ?? "").trim() === trimmed) : undefined;
  return matched ?? cells[index + 1];
}

/**
 * 按 SKU 列表重建尺寸详情表格：表头固定，每个 SKU 一行，第 1 列写 SKU 名称。
 * 已有表格里同一 SKU 的长/宽/高会被保留，避免重建时清掉用户已填的尺寸。
 */
export function buildSpecCardCells(
  skuNames: readonly string[],
  previous: readonly (readonly string[])[] = [],
): string[][] {
  const names = skuNames.length ? [...skuNames] : [""];
  return [
    [...SPEC_CARD_DIMENSION_HEADER],
    ...names.map((name, index) => {
      const source = findDimensionRow(previous, name, index);
      return [name, source?.[1] ?? "", source?.[2] ?? "", source?.[3] ?? ""];
    }),
  ];
}

export const EMPTY_SPEC_CARD: SpecCardConfig = {
  enabled: true,
  style: "light",
  corner: "bottom-right",
  display_unit: "cm",
  cells: buildSpecCardCells([""]),
};

export function cloneSpecCardConfig(config: SpecCardConfig): SpecCardConfig {
  return {
    enabled: config.enabled,
    style: config.style,
    corner: config.corner,
    display_unit: config.display_unit ?? "cm",
    cells: config.cells.map((row) => [...row]),
  };
}

export function createEmptySpecCard(): SpecCardConfig {
  return cloneSpecCardConfig(EMPTY_SPEC_CARD);
}

export function isSpecCardStyle(value: unknown): value is SpecCardStyle {
  return value === "light" || value === "dark";
}

export function isSpecCardCorner(value: unknown): value is SpecCardCorner {
  return value === "bottom-right" || value === "bottom-left" || value === "top-right" || value === "top-left";
}

export function isSpecCardConfig(value: unknown): value is SpecCardConfig {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const candidate = value as Partial<SpecCardConfig>;
  return typeof candidate.enabled === "boolean"
    && isSpecCardStyle(candidate.style)
    && isSpecCardCorner(candidate.corner)
    && (candidate.display_unit === undefined || candidate.display_unit === "cm" || candidate.display_unit === "in")
    && Array.isArray(candidate.cells)
    && candidate.cells.every((row) => Array.isArray(row) && row.every((cell) => typeof cell === "string"));
}

/**
 * 必填判定口径：尺寸详情是结构化表格，配置完成 = 每个 SKU 行都填了长、宽、高。
 * 表头行与第 1 列由 SKU 预设自动映射，不参与判定。
 * `enabled` 由批次冻结快照承载（当前 UI 恒为 true），不参与提交拦截。
 */
export function isSpecCardConfigured(config: SpecCardConfig | null | undefined): boolean {
  if (!config || !Array.isArray(config.cells)) return false;
  const rows = config.cells.slice(1);
  if (!rows.length) return false;
  return rows.every((row) => Array.isArray(row)
    && [1, 2, 3].every((column) => typeof row[column] === "string" && row[column].trim().length > 0));
}

/** 配置摘要，例如「3 行 · 浅色卡片 · 右下角」；未配置时为「未配置」。 */
export function specCardSummaryText(config: SpecCardConfig | null | undefined): string {
  if (!config || !isSpecCardConfigured(config)) return "未配置";
  // 行数 = 1 行表头 + 每个 SKU 一行。
  const rows = config.cells.length;
  const style = SPEC_CARD_STYLE_LABELS[config.style] ?? SPEC_CARD_STYLE_LABELS.light;
  const corner = SPEC_CARD_CORNER_LABELS[config.corner] ?? SPEC_CARD_CORNER_LABELS["bottom-right"];
  // 关掉「印到图上」时补一句，避免用户在页面上看不出素材图会不会带卡片。
  const printing = config.enabled ? "" : " · 不印图";
  return `${rows} 行 · ${style} · ${corner}${printing}`;
}

/** 提交载荷里带的规格卡快照（单元格内容原样透传，不做任何加工）。 */
export function specCardForApi(config: SpecCardConfig): SpecCardConfig {
  return cloneSpecCardConfig(config);
}

const ACTIVE_BATCH_STATUSES = new Set<PodBatchStatus>([
  "queued",
  "generating_patterns",
  "compositing",
  "generating_titles",
  "pausing",
  "cancelling",
]);

// 可发起暂停的运行态，与后端 request_pause 的原子状态门槛保持一致。
const PAUSABLE_BATCH_STATUSES = new Set<PodBatchStatus>([
  "queued",
  "generating_patterns",
  "compositing",
  "generating_titles",
]);
const ACTIVE_ITEM_STATUSES = new Set<PodBatchItemStatus>([
  "queued",
  "generating_pattern",
  "compositing",
  "optimizing_scene",
]);
const ACTIVE_TITLE_STATUSES = new Set<PodStyleTitleStatus>(["queued", "generating"]);
const SETTLED_BATCH_STATUSES = new Set<PodBatchStatus>(["completed", "partial_failure", "failed", "cancelled", "settlement_pending"]);
const RETRYABLE_BATCH_STATUSES = SETTLED_BATCH_STATUSES;
// Generation results are terminal before settlement bookkeeping finishes, so
// retry and manual-title actions use the same terminal batch status set.
const MANUAL_TITLE_EDITABLE_BATCH_STATUSES = new Set<PodBatchStatus>([
  "completed",
  "partial_failure",
  "failed",
  "cancelled",
  "settlement_pending",
]);

export function resolveCreativePrompt(currentBatchEdit: string): string {
  // 现在只有用户手写的主体锚定；不再自动生成 v1 模板。
  return currentBatchEdit.trim();
}

/**
 * 旧模板 / 旧草稿里残留的「禁用元素：」行：加载时直接去掉。
 * 危险违禁内容由生图模型自身审核，品牌/肖像/logo 由后端固定清单约束。
 */
export function stripExcludedElementsLine(text: string): string {
  const lines = text
    .split("\n")
    .filter((line) => !/^\s*禁用元素\s*[：:]/.test(line));
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
  return lines.join("\n");
}

/** 旧版自动生成的 v1 快照检测：命中即视为「没有主体锚定」，加载时直接丢弃。 */
export function isPristineCreativeEdit(text: string | null | undefined): boolean {
  const trimmed = (text ?? "").trim();
  if (!trimmed.startsWith("[POD DIRECT LISTING PROMPT v1]")) return false;
  return ["产品名称：", "产品品类：", "目标市场：", "主题整批统一风格：", "硬性规则："].every(
    (label) => trimmed.includes(label),
  );
}

export function splitBusinessField(value: string): string[] {
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
    // 批级全局字段：整段自然语言原样透传（不切分），留空即空串。
    fabric_craft: fields.fabric_craft.trim(),
    scene_mood: fields.scene_mood.trim(),
    style_keywords: splitBusinessField(fields.style_keywords),
    color_preferences: splitBusinessField(fields.color_preferences),
    excluded_elements: splitBusinessField(fields.excluded_elements),
    // 上架文案限制是整段自然语言，原样透传（不切分、不进 buildPromptV1）。
    copy_restrictions: fields.copy_restrictions.trim(),
  };
}

type PodListingFieldsResult = { value: PodListingFields; error?: never } | { value?: never; error: string };

function positiveListingNumber(value: string, label: string): number | { error: string } {
  const normalized = value.trim();
  const parsed = Number(normalized);
  if (!normalized || !Number.isFinite(parsed) || parsed <= 0) return { error: `${label}必须是大于 0 的有效数字。` };
  return parsed;
}

/**
 * 组装提交载荷里的 listing_fields 快照。
 * 申报价改为每个 SKU 各一个；长/宽/高不在 SKU 上，而是从尺寸详情表格按 SKU 反查校验，
 * 随 specCard 冻结进 `spec_card`；不传配置时载荷结构与旧版本保持一致。
 * 建议售价不作为上架必填项，不再上报（导出时该列留空）。
 */
export function listingFieldsForApi(
  fields: PodListingFieldsDraft,
  specCard?: SpecCardConfig | null,
): PodListingFieldsResult {
  const categoryName = fields.category_name.trim();
  if (!categoryName) return { error: "请填写店小秘类目。" };

  if (!fields.skus.length) return { error: "请至少填写一个 SKU。" };
  if (fields.skus.length > MAX_POD_SKU_COUNT) return { error: `SKU 最多可添加 ${MAX_POD_SKU_COUNT} 个。` };
  const cells = specCard?.cells ?? [];
  const skus = [] as PodListingFields["skus"];
  for (const [index, sku] of fields.skus.entries()) {
    const name = sku.name.trim();
    if (!name) return { error: "SKU 名称不能为空。" };
    if (name.length > MAX_POD_SKU_NAME_LENGTH) return { error: `SKU 名称不能超过 ${MAX_POD_SKU_NAME_LENGTH} 个字符。` };
    const declaredPrice = positiveListingNumber(sku.declared_price, `SKU「${name}」的申报价`);
    if (typeof declaredPrice !== "number") return declaredPrice;
    const weightG = positiveListingNumber(sku.weight_g, `SKU「${name}」的重量`);
    if (typeof weightG !== "number") return weightG;
    // 长/宽/高从尺寸详情表格反查（先按 SKU 名，再退回按位置），确保导出第 11-13 列不缺值。
    const dimensionRow = findDimensionRow(cells, name, index);
    for (const [offset, label] of SPEC_CARD_DIMENSION_LABELS.entries()) {
      const parsed = positiveListingNumber(dimensionRow?.[offset + 1] ?? "", `SKU「${name}」的${label}`);
      if (typeof parsed !== "number") return parsed;
    }
    skus.push({ name, declared_price: declaredPrice, weight_g: weightG });
  }

  return {
    value: {
      title_mode: fields.title_mode,
      category_name: categoryName,
      skus,
      // 规格卡随批次一起冻结；不传配置时载荷结构与旧版本保持一致。
      ...(specCard ? { spec_card: specCardForApi(specCard) } : {}),
    },
  };
}

export function isPodBatchCount(value: number): value is PodBatchCount {
  return Number.isInteger(value) && value >= 1 && value <= 200;
}

export function groupPodStyleRows(
  batch: Pick<PodBatch, "items" | "style_grid" | "business_fields" | "style_titles">,
  resolveStyleProductName?: (styleIndex: number) => string | undefined,
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
    const resultByRole = new Map(
      results.filter((item): item is PodBatchItem => Boolean(item)).map((item) => [item.role, item]),
    );
    const presentationResults = POD_STYLE_PRESENTATION_ROLES.every((role) => resultByRole.has(role))
      ? POD_STYLE_PRESENTATION_ROLES.map((role) => resultByRole.get(role))
      : results;
    const styleTitle = titlesByStyle.get(index);
    const settled = presentationResults.filter(Boolean);
    const completed = settled.filter((item) => item?.status === "completed").length;
    const failed = settled.filter((item) => item?.status === "failed").length;
    const status = completed === 4 ? "completed"
      : failed === 4 || (!completed && failed > 0) ? "failed"
      : completed || failed ? "partial_failure"
      : settled.some((item) => item && isActivePodItemStatus(item.status)) ? "generating"
      : "queued";
    const resolvedProductName = resolveStyleProductName?.(index)?.trim() || productName;
    return {
      index,
      title: styleTitle?.title?.trim() || `${resolvedProductName} · 款式 #${String(index).padStart(3, "0")}`,
      title_status: styleTitle?.status,
      title_source: styleTitle?.source,
      listing_ready: styleTitle?.listing_ready,
      export_selected: styleTitle?.export_selected ?? true,
      title_error_message: styleTitle?.error_message,
      results: presentationResults,
      status,
    };
  });
}

export function batchProgress(batch: {
  count: number;
  processed_count: number;
  title_completed_count?: number;
  title_failed_count?: number;
}): number {
  if (batch.count <= 0) return 0;
  // Each style needs one image bundle AND one title, so a batch is "100%" only
  // once both the image and title phases settle (done or failed). This keeps
  // the bar from reporting fully complete while titles are still generating.
  const totalUnits = batch.count * 2;
  const imageUnits = Math.min(batch.processed_count, batch.count);
  const titleUnits = Math.min(
    (batch.title_completed_count ?? 0) + (batch.title_failed_count ?? 0),
    batch.count,
  );
  return Math.min(100, Math.max(0, Math.round(((imageUnits + titleUnits) / totalUnits) * 100)));
}

export function podBatchProgressCounts(batch: {
  count: number;
  processed_count: number;
  title_completed_count?: number;
  title_failed_count?: number;
}): { image: number; title: number } {
  return {
    image: Math.min(batch.processed_count, batch.count),
    title: Math.min(
      (batch.title_completed_count ?? 0) + (batch.title_failed_count ?? 0),
      batch.count,
    ),
  };
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
  listingReady = false,
): boolean {
  if (styleStatus === "failed") return RETRYABLE_BATCH_STATUSES.has(batchStatus);
  return styleStatus === "completed" && listingReady && !isBillingInterruptedPodBatch(batchStatus) && SETTLED_BATCH_STATUSES.has(batchStatus);
}

export function isBillingInterruptedPodBatch(status: PodBatchStatus): boolean {
  return false;
}

export function canPausePodBatch(status: PodBatchStatus): boolean {
  return PAUSABLE_BATCH_STATUSES.has(status);
}

export function canCancelPodBatch(status: PodBatchStatus): boolean {
  return PAUSABLE_BATCH_STATUSES.has(status) || status === "pausing" || status === "paused";
}

export function canResumePodBatch(status: PodBatchStatus): boolean {
  return status === "paused";
}

// 可删除的终态批次，与后端 delete_batch 的状态门槛保持一致。
const DELETABLE_BATCH_STATUSES = new Set<PodBatchStatus>(["completed", "partial_failure", "failed", "cancelled"]);

export function canDeletePodBatch(status: PodBatchStatus): boolean {
  return DELETABLE_BATCH_STATUSES.has(status);
}

// 结算任务与生成结果独立；结算待处理不能锁死失败项重试。
export function canRetryPodBatchFailed(batchStatus: PodBatchStatus): boolean {
  return RETRYABLE_BATCH_STATUSES.has(batchStatus);
}

export function canRegeneratePodStyleTitle(
  batchStatus: PodBatchStatus,
  titleStatus: PodStyleTitleStatus | undefined,
  results: Array<Pick<PodBatchItem, "status" | "public_url"> | undefined>,
): boolean {
  const completeResults = results.length === 4 && results.every((item) => item?.status === "completed" && Boolean(item.public_url));
  if (!completeResults) return false;
  if (titleStatus === "failed") return RETRYABLE_BATCH_STATUSES.has(batchStatus);
  return titleStatus === "completed" && !isBillingInterruptedPodBatch(batchStatus) && SETTLED_BATCH_STATUSES.has(batchStatus);
}

export function canEditPodStyleTitle(
  batchStatus: PodBatchStatus,
  titleStatus: PodStyleTitleStatus | undefined,
  results: Array<Pick<PodBatchItem, "status" | "public_url"> | undefined>,
): boolean {
  return MANUAL_TITLE_EDITABLE_BATCH_STATUSES.has(batchStatus)
    && (titleStatus === "failed" || titleStatus === "completed")
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

export function podBatchStatusLabel(
  status: PodBatchStatus,
  dianxiaomiReady?: boolean,
  allRequestedProcessed = true,
): string {
  // 结算尚未完成但生成已完整时，以导出资格为准展示。
  if (dianxiaomiReady && allRequestedProcessed && status === "settlement_pending") {
    return "生成已完成，可导出";
  }
  return {
    queued: "等待启动",
    generating_patterns: "生成中",
    compositing: "生成中",
    generating_titles: "生成中",
    pausing: "暂停中",
    paused: "已暂停",
    cancelling: "取消中",
    cancelled: "已取消",
    completed: "已完成",
    partial_failure: "部分完成",
    failed: "生成失败",
    settlement_pending: "等待计费结算",
  }[status];
}

export function podBatchStatusDetail(status: PodBatchStatus, submittedCount?: number): string {
  if (status === "pausing") {
    return submittedCount !== undefined && submittedCount > 0
      ? `已提交款式：${submittedCount}，已提交的款正在完成，其余款不会继续发起。`
      : "已提交的款正在完成，其余款不会继续发起。";
  }
  if (status === "paused") {
    return submittedCount !== undefined && submittedCount > 0
      ? `已提交款式：${submittedCount}，已暂停，可继续剩余款式。`
      : "已暂停，可继续剩余款式。";
  }
  return "";
}

export function podItemStatusLabel(status: string): string {
  return ({
    queued: "等待中",
    generating_pattern: "生成中",
    compositing: "生成中",
    completed: "已完成",
    failed: "失败",
    optimizing_scene: "优化场景",
  } as Record<string, string>)[status] ?? status;
}
