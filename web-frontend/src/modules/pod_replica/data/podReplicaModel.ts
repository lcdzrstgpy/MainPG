import {
  cloneSpecCardConfig,
  createEmptySpecCard,
  isSpecCardConfigured,
  listingFieldsForApi,
} from "../../pod_customization/data/podCustomizationModel.ts";
import type {
  PodListingFields,
  PodListingFieldsDraft,
  SpecCardConfig,
} from "../../pod_customization/types";
import type {
  CreateReplicaBatchRequest,
  CreateReplicaTargetRequest,
  ReplicaAnalyzeResponse,
  ReplicaAnalysisStatus,
  ReplicaFactOverrides,
  ReplicaGenerationStrategy,
  ReplicaPresentationPreferences,
  ReplicaSpec,
} from "../types";

export type ReplicaTargetDraft = {
  /** 前端稳定 ID：删除/复制/选中都不随数组下标变化串状态。 */
  clientId: string;
  assetId: string;
  filename: string;
  width?: number;
  height?: number;
  productName: string;
  listingFields: PodListingFieldsDraft;
  specCard: SpecCardConfig;
  // --- 识图主流程（旧草稿缺省时按「待识别」处理，不影响既有编辑能力）---
  analysisId: string;
  analysisStatus: ReplicaAnalysisStatus;
  analysisError: string;
  /** A 原素材事实（同一批次内一致）。 */
  sourceArtworkSpec: ReplicaSpec | null;
  /** B 目标产品事实；展示与修正都基于这份数据。 */
  targetProductSpec: ReplicaSpec | null;
  candidateCapabilities: string[];
  unknownCapabilities: string[];
  /** 用户对事实的修正；提交时随 analysis_id 一起上报。 */
  factOverrides: ReplicaFactOverrides;
  presentationPreferences: ReplicaPresentationPreferences;
};

export type ReplicaTargetAsset = {
  asset_id: string;
  filename: string;
  width: number;
  height: number;
};

export type ReplicaTargetValidation =
  | { ok: true; listingFields: PodListingFields }
  | { ok: false; error: string };

/**
 * 复刻资产的授权预览路径。后端 `preview_url` 就是这条固定规则
 * （`/api/pod-customization/assets/{asset_id}`），前端一律由 asset_id 推导，
 * 不再另存一份 URL —— 否则草稿恢复时 URL 丢失就只剩裸 asset_id，图片会变成坏图。
 */
export function replicaAssetPath(assetId: string): string {
  return assetId ? `/api/pod-customization/assets/${encodeURIComponent(assetId)}` : "";
}

/** 全定制 `pod_random_v1` 画像口径：每个目标产品计为一款，40–50 积分/款的估算区间。 */
export const REPLICA_FEE_PER_PRODUCT_MIN = 40;
export const REPLICA_FEE_PER_PRODUCT_MAX = 50;

function newRandomId(prefix: string): string {
  const random = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}${random}`;
}

export function nextReplicaClientId(): string {
  return newRandomId("pod-replica-");
}

export function nextReplicaClientRequestId(): string {
  return newRandomId("");
}

/** 分析状态展示文案：待识别 / 识别中 / 待确认 / 已确认 / 分析失败。 */
export const REPLICA_ANALYSIS_STATUS_LABELS: Record<ReplicaAnalysisStatus, string> = {
  pending: "待识别",
  analyzing: "识别中",
  needs_confirmation: "待确认",
  confirmed: "已确认",
  failed: "分析失败",
};

/** 目标产品事实的展示顺序与中文标签（识图确认区按此顺序渲染）。 */
export const REPLICA_TARGET_FACT_FIELDS: readonly { key: string; label: string }[] = [
  { key: "product_type", label: "产品类型" },
  { key: "component_count", label: "部件数量" },
  { key: "component_structure", label: "部件结构" },
  { key: "material", label: "材质" },
  { key: "surface_texture", label: "表面质感" },
  { key: "printable_regions", label: "可印区域" },
  { key: "non_printable_regions", label: "不可印区域" },
  { key: "supported_usage_modes", label: "支持使用方式" },
  { key: "occlusion_risk", label: "遮挡风险" },
];
// 可由用户修正的事实字段（与后端 FactOverrides 对齐）；不可印区域只读展示。
export const REPLICA_EDITABLE_FACT_KEYS: readonly string[] = [
  "product_type", "component_count", "component_structure", "material",
  "surface_texture", "printable_regions", "supported_usage_modes",
];
// 数组类事实：编辑框以顿号/逗号分隔。
export const REPLICA_LIST_FACT_KEYS: readonly string[] = [
  "printable_regions", "supported_usage_modes",
];

/** 15 项固定能力的中文标签（候选标签、构图入口共用）。 */
export const REPLICA_CAPABILITY_LABELS: Record<string, string> = {
  flat_print: "平面印花",
  local_print: "局部印花",
  surface_wrap: "整面包裹",
  multi_piece: "多部件",
  standalone_display: "独立陈列",
  hand_worn: "手部佩戴",
  body_worn: "人体穿着",
  wall_mounted: "墙面悬挂",
  macro_detail: "微距细节",
  white_background: "白底",
  fabric_material: "织物材质",
  paper_material: "纸质材质",
  plastic_material: "塑料材质",
  metal_material: "金属材质",
  gel_material: "凝胶材质",
};

export function replicaCapabilityLabel(id: string): string {
  return REPLICA_CAPABILITY_LABELS[id] ?? id;
}

/** 展示偏好开关：能力标签 ≠ 必拍画面，展示类画面必须在这里显式选中。 */
export const REPLICA_PRESENTATION_OPTIONS: readonly {
  key: keyof ReplicaPresentationPreferences;
  label: string;
  hint: string;
}[] = [
  { key: "use_hand_model", label: "手部佩戴", hint: "产品支持手部使用，也不代表本次必须生成手部画面。" },
  { key: "use_body_model", label: "人体穿着", hint: "仅服饰等可穿着品类适用。" },
  { key: "use_wall_display", label: "墙面展示", hint: "仅支持挂墙的品类适用。" },
  { key: "use_white_background", label: "白底图", hint: "纯净背景，突出产品本体。" },
  { key: "use_lifestyle_scene", label: "生活场景", hint: "默认开启，带环境与氛围。" },
];

/** 展示偏好默认值：所有展示类能力默认关闭，生活场景默认开启。 */
export function defaultReplicaPresentationPreferences(): ReplicaPresentationPreferences {
  return {
    use_hand_model: false,
    use_body_model: false,
    use_wall_display: false,
    use_white_background: false,
    use_lifestyle_scene: true,
  };
}

/** 识图相关字段的默认值（新建目标 / 旧草稿缺省时使用）。 */
export function emptyReplicaAnalysisFields(): Pick<
  ReplicaTargetDraft,
  | "analysisId"
  | "analysisStatus"
  | "analysisError"
  | "sourceArtworkSpec"
  | "targetProductSpec"
  | "candidateCapabilities"
  | "unknownCapabilities"
  | "factOverrides"
  | "presentationPreferences"
> {
  return {
    analysisId: "",
    analysisStatus: "pending",
    analysisError: "",
    sourceArtworkSpec: null,
    targetProductSpec: null,
    candidateCapabilities: [],
    unknownCapabilities: [],
    factOverrides: {},
    presentationPreferences: defaultReplicaPresentationPreferences(),
  };
}

export function createEmptyReplicaTarget(clientId = nextReplicaClientId()): ReplicaTargetDraft {
  return {
    clientId,
    assetId: "",
    filename: "",
    productName: "",
    listingFields: {
      title_mode: "long",
      suggested_price_usd: "",
      category_name: "",
      skus: [{ name: "", declared_price: "", weight_g: "" }],
    },
    specCard: createEmptySpecCard(),
    ...emptyReplicaAnalysisFields(),
  };
}

export function deepCopyReplicaTarget(target: ReplicaTargetDraft): ReplicaTargetDraft {
  return {
    clientId: target.clientId,
    assetId: target.assetId,
    filename: target.filename,
    width: target.width,
    height: target.height,
    productName: target.productName,
    listingFields: {
      title_mode: target.listingFields.title_mode,
      suggested_price_usd: target.listingFields.suggested_price_usd,
      category_name: target.listingFields.category_name,
      skus: target.listingFields.skus.map((sku) => ({ ...sku })),
    },
    specCard: cloneSpecCardConfig(target.specCard),
    ...cloneReplicaAnalysisFields(target),
  };
}

function cloneReplicaAnalysisFields(target: ReplicaTargetDraft) {
  const fallback = emptyReplicaAnalysisFields();
  return {
    analysisId: target.analysisId ?? fallback.analysisId,
    analysisStatus: target.analysisStatus ?? fallback.analysisStatus,
    analysisError: target.analysisError ?? fallback.analysisError,
    sourceArtworkSpec: target.sourceArtworkSpec ?? null,
    targetProductSpec: target.targetProductSpec ?? null,
    candidateCapabilities: [...(target.candidateCapabilities ?? [])],
    unknownCapabilities: [...(target.unknownCapabilities ?? [])],
    factOverrides: { ...(target.factOverrides ?? {}) },
    presentationPreferences: {
      ...fallback.presentationPreferences,
      ...(target.presentationPreferences ?? {}),
    },
  };
}

export function attachReplicaTargetAsset(
  target: ReplicaTargetDraft,
  asset: ReplicaTargetAsset,
): ReplicaTargetDraft {
  return {
    ...target,
    assetId: asset.asset_id,
    filename: asset.filename,
    width: asset.width,
    height: asset.height,
    // 换图后旧分析失效：必须重新识别，避免拿上一张图的结论提交。
    ...emptyReplicaAnalysisFields(),
  };
}

export function removeReplicaTarget(
  targets: readonly ReplicaTargetDraft[],
  clientId: string,
): ReplicaTargetDraft[] {
  return targets.filter((target) => target.clientId !== clientId);
}

/**
 * 「复制其他产品信息」：深拷贝来源的上架信息 / SKU / 规格卡；保留当前产品的
 * 名称、类目（category_name）、图片与稳定 client ID。复制后两份数据互不关联。
 */
export function copyReplicaListingInfo(
  source: ReplicaTargetDraft,
  target: ReplicaTargetDraft,
): ReplicaTargetDraft {
  return {
    clientId: target.clientId,
    assetId: target.assetId,
    filename: target.filename,
    width: target.width,
    height: target.height,
    productName: target.productName,
    listingFields: {
      title_mode: source.listingFields.title_mode,
      suggested_price_usd: source.listingFields.suggested_price_usd,
      category_name: target.listingFields.category_name,
      skus: source.listingFields.skus.map((sku) => ({ ...sku })),
    },
    specCard: cloneSpecCardConfig(source.specCard),
    // 识图结论属于目标产品本身，不随「导出信息」一起复制。
    ...cloneReplicaAnalysisFields(target),
  };
}

// --- 识图主流程：状态流转与提交门禁 ---

/** 该款是否已完成识图确认（提交门禁之一）。 */
export function isReplicaAnalysisConfirmed(target: ReplicaTargetDraft): boolean {
  return target.analysisStatus === "confirmed";
}

/** 未确认时的阻塞原因；已确认返回空串。 */
export function replicaAnalysisBlockReason(target: ReplicaTargetDraft): string {
  switch (target.analysisStatus) {
    case "analyzing": return "识图分析进行中，请稍候。";
    case "needs_confirmation": return "请先确认识别结果。";
    case "failed": return "识图分析失败，请重新分析。";
    case "confirmed": return "";
    default: return "请等待识图分析完成。";
  }
}

/** 标记为「识别中」；不改变已有事实快照（失败后仍可看到上次结果）。 */
export function markReplicaAnalysisAnalyzing(target: ReplicaTargetDraft): ReplicaTargetDraft {
  return { ...target, analysisStatus: "analyzing", analysisError: "" };
}

/** 标记为分析失败，并记录可读原因。 */
export function markReplicaAnalysisFailed(target: ReplicaTargetDraft, error: string): ReplicaTargetDraft {
  return { ...target, analysisStatus: "failed", analysisError: error };
}

/**
 * 落库一次分析结果。
 *
 * 低置信度（服务端 needs_confirmation）→ 待确认；高置信度直接置为已确认。
 * 服务端返回 failed 时按失败处理，不覆盖既有事实快照。
 */
export function applyReplicaAnalysis(
  target: ReplicaTargetDraft,
  response: ReplicaAnalyzeResponse,
): ReplicaTargetDraft {
  if (response.status !== "succeeded") {
    return markReplicaAnalysisFailed(target, response.error_code || "识别失败");
  }
  const needsConfirmation = response.source_needs_confirmation || response.target_needs_confirmation;
  return {
    ...target,
    analysisId: response.analysis_id,
    analysisError: "",
    analysisStatus: needsConfirmation ? "needs_confirmation" : "confirmed",
    sourceArtworkSpec: response.source,
    targetProductSpec: response.target,
    candidateCapabilities: [...response.candidate_capabilities],
    unknownCapabilities: [...response.unknown_capability],
  };
}

/** 用户确认识别结果（或改完事实后确认）。 */
export function confirmReplicaAnalysis(target: ReplicaTargetDraft): ReplicaTargetDraft {
  if (target.analysisStatus === "failed" || target.analysisStatus === "analyzing") return target;
  return { ...target, analysisStatus: "confirmed" };
}

/** 用户修正事实：合并覆盖项并标记为已确认（多次修正会累积）。 */
export function setReplicaFactOverrides(
  target: ReplicaTargetDraft,
  patch: ReplicaFactOverrides,
): ReplicaTargetDraft {
  return confirmReplicaAnalysis({
    ...target,
    factOverrides: { ...target.factOverrides, ...patch },
  });
}

/** 切换一条展示偏好。能力标签 ≠ 必拍画面，只有这里选中才会进入本次构图。 */
export function setReplicaPresentationPreference(
  target: ReplicaTargetDraft,
  key: keyof ReplicaPresentationPreferences,
  value: boolean,
): ReplicaTargetDraft {
  return {
    ...target,
    presentationPreferences: { ...target.presentationPreferences, [key]: value },
  };
}

/** 读取一条事实的展示文本；缺失返回空串。 */
export function replicaFactText(spec: ReplicaSpec | null, key: string): string {
  const fact = spec?.[key];
  if (!fact) return "";
  const value = fact.value;
  if (Array.isArray(value)) return value.map((item) => String(item)).filter(Boolean).join("、");
  if (value === null || value === undefined) return "";
  return String(value);
}

/** 读取一条事实的置信度；缺失返回 0。 */
export function replicaFactConfidence(spec: ReplicaSpec | null, key: string): number {
  const confidence = spec?.[key]?.confidence;
  return typeof confidence === "number" && Number.isFinite(confidence) ? confidence : 0;
}

/** 低于该置信度的事实需要用户确认（与服务端阈值一致）。 */
export const REPLICA_FACT_CONFIDENCE_THRESHOLD = 0.6;

export function replicaFactNeedsConfirmation(spec: ReplicaSpec | null, key: string): boolean {
  return replicaFactConfidence(spec, key) < REPLICA_FACT_CONFIDENCE_THRESHOLD;
}

/** 事实的生效文本：优先取用户修正，其次取识别结果。 */
export function replicaEffectiveFactText(target: ReplicaTargetDraft, key: string): string {
  const override = (target.factOverrides as Record<string, unknown>)[key];
  if (override !== null && override !== undefined) {
    if (Array.isArray(override)) return override.map((item) => String(item)).filter(Boolean).join("、");
    return String(override);
  }
  return replicaFactText(target.targetProductSpec, key);
}

/** 把编辑框里的文本按顿号 / 逗号 / 换行切分为数组事实。 */
export function parseReplicaListFact(value: string): string[] {
  return value.split(/[、,，\n]/).map((item) => item.trim()).filter(Boolean);
}

/** 提交门禁：所有目标都必须完成识图确认。 */
export function replicaAnalysesConfirmed(targets: readonly ReplicaTargetDraft[]): boolean {
  return targets.length > 0 && targets.every(isReplicaAnalysisConfirmed);
}

export function validateReplicaTargetDraft(target: ReplicaTargetDraft): ReplicaTargetValidation {
  const productName = target.productName.trim();
  if (!productName) return { ok: false, error: "请填写产品名称。" };
  if (!target.assetId) return { ok: false, error: "请先上传该产品的白底图。" };
  // 提交门禁：该款识图必须已确认（未识别 / 识别中 / 待确认 / 失败都不允许提交）。
  const analysisBlock = replicaAnalysisBlockReason(target);
  if (analysisBlock) return { ok: false, error: analysisBlock };
  // 复用全定制的 SKU / 售价 / 申报价 / 重量 / 类目 / 规格卡尺寸校验。
  const listing = listingFieldsForApi(target.listingFields, target.specCard);
  if (!listing.value) return { ok: false, error: listing.error };
  if (!isSpecCardConfigured(target.specCard)) {
    return { ok: false, error: "请先点击「批量添加尺寸」完成表格配置。" };
  }
  return { ok: true, listingFields: listing.value };
}

export function isReplicaTargetComplete(target: ReplicaTargetDraft): boolean {
  return validateReplicaTargetDraft(target).ok;
}

export function firstInvalidReplicaTargetIndex(targets: readonly ReplicaTargetDraft[]): number {
  return targets.findIndex((target) => !validateReplicaTargetDraft(target).ok);
}

export function buildReplicaTargetRequest(
  target: ReplicaTargetDraft,
  listingFields: PodListingFields,
): CreateReplicaTargetRequest {
  const request: CreateReplicaTargetRequest = {
    target_asset_id: target.assetId,
    product_name: target.productName.trim(),
    listing_fields: listingFields,
  };
  // 未识别的款不携带分析字段，保持与旧客户端一致的上报形状。
  if (target.analysisId) {
    request.analysis_id = target.analysisId;
    request.fact_overrides = hasReplicaFactOverrides(target.factOverrides) ? target.factOverrides : null;
    request.presentation_preferences = { ...target.presentationPreferences };
  }
  return request;
}

/** 是否填写过事实修正（空对象不上报，避免把默认值当成用户意图）。 */
export function hasReplicaFactOverrides(overrides: ReplicaFactOverrides): boolean {
  return Object.values(overrides).some((value) => value !== null && value !== undefined);
}

export function buildReplicaBatchRequest(input: {
  client_request_id: string;
  source_asset_id: string;
  title?: string;
  composition_id?: string;
  generation_strategy?: ReplicaGenerationStrategy | null;
  targets: readonly ReplicaTargetDraft[];
}): CreateReplicaBatchRequest {
  if (!input.client_request_id.trim()) throw new Error("缺少 client_request_id。");
  if (!input.source_asset_id.trim()) throw new Error("请先上传 POD 样图。");
  if (!input.targets.length) throw new Error("请至少添加一个目标产品。");
  const requests: CreateReplicaTargetRequest[] = [];
  for (const [index, target] of input.targets.entries()) {
    const result = validateReplicaTargetDraft(target);
    if (!result.ok) throw new Error(`第 ${index + 1} 个产品有误：${result.error}`);
    requests.push(buildReplicaTargetRequest(target, result.listingFields));
  }
  return {
    client_request_id: input.client_request_id,
    source_asset_id: input.source_asset_id,
    title: input.title?.trim() ?? "",
    creative_prompt: "",
    composition_id: input.composition_id?.trim() || undefined,
    generation_strategy: input.generation_strategy ?? null,
    targets: requests,
  };
}

export function replicaFeeEstimate(count: number): { min: number; max: number } {
  const safe = Math.max(0, Math.floor(Number(count) || 0));
  return { min: REPLICA_FEE_PER_PRODUCT_MIN * safe, max: REPLICA_FEE_PER_PRODUCT_MAX * safe };
}

export function replicaFeeEstimateText(count: number): string {
  const { min, max } = replicaFeeEstimate(count);
  return `约 ${min}–${max} 积分`;
}