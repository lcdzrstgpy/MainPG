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
  /** A 原素材识别是否存在低置信度字段（需用户确认整批 A 结果）。 */
  sourceNeedsConfirmation: boolean;
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

/**
 * 事实值的英文稳定标识 → 中文展示名。
 *
 * 豆包按契约把产品类型 / 材质 / 使用方式返回成英文标识（后端据此做能力匹配），
 * 所以只能在展示层翻译：绝不回写、不参与匹配。词表里没有的值原样显示。
 */
const REPLICA_FACT_VALUE_LABELS: Record<string, Record<string, string>> = {
  product_type: {
    tote_bag: "托特包",
    duffel_bag: "圆筒旅行包",
    backpack: "双肩包",
    shoulder_bag: "单肩包",
    crossbody_bag: "斜挎包",
    drawstring_bag: "抽绳袋",
    cosmetic_bag: "化妆包",
    pouch: "收纳袋",
    t_shirt: "T 恤",
    tshirt: "T 恤",
    hoodie: "连帽卫衣",
    sweatshirt: "圆领卫衣",
    cap: "帽子",
    hat: "帽子",
    pillow: "抱枕",
    pillowcase: "抱枕套",
    cushion: "靠垫",
    blanket: "毛毯",
    towel: "毛巾",
    apron: "围裙",
    socks: "袜子",
    mug: "马克杯",
    tumbler: "保温杯",
    bottle: "水杯",
    coaster: "杯垫",
    placemat: "餐垫",
    poster: "海报",
    canvas: "装饰画",
    phone_case: "手机壳",
    mouse_pad: "鼠标垫",
    sticker: "贴纸",
    notebook: "笔记本",
    journal: "手账本",
    puzzle: "拼图",
    keychain: "钥匙扣",
    magnet: "冰箱贴",
    nail_tip_set: "美甲片套装",
    nail_tips: "美甲片",
    pet_bed: "宠物窝",
    pet_collar: "宠物项圈",
    clock: "挂钟",
    flag: "旗帜",
  },
  material: {
    gel: "凝胶",
    fabric: "织物",
    cloth: "布料",
    cotton: "棉",
    canvas: "帆布",
    linen: "亚麻",
    denim: "牛仔",
    polyester: "涤纶",
    nylon: "尼龙",
    velvet: "天鹅绒",
    satin: "缎面",
    leather: "皮革",
    paper: "纸质",
    cardboard: "硬纸板",
    plastic: "塑料",
    acrylic: "亚克力",
    silicone: "硅胶",
    rubber: "橡胶",
    metal: "金属",
    stainless_steel: "不锈钢",
    aluminum: "铝合金",
    wood: "木质",
    bamboo: "竹",
    glass: "玻璃",
    ceramic: "陶瓷",
    resin: "树脂",
  },
  supported_usage_modes: {
    hand_worn: "手持佩戴",
    hand_carried: "手提",
    hand_carry: "手提",
    carried: "手提",
    shoulder_carried: "肩背",
    back_carried: "背负",
    body_worn: "人体穿着",
    worn: "穿戴",
    neck_worn: "颈部佩戴",
    wall_mounted: "墙面悬挂",
    wall_hung: "墙面悬挂",
    hanging: "悬挂",
    standalone: "独立摆放",
    freestanding: "独立摆放",
    tabletop: "桌面摆放",
    desktop: "桌面摆放",
    floor_standing: "落地摆放",
  },
};

/** 单条事实值的展示文本：命中词表按中文展示，否则原样返回。 */
function replicaFactValueText(key: string, value: unknown): string {
  const table = REPLICA_FACT_VALUE_LABELS[key];
  const label = (item: unknown): string => {
    const text = String(item ?? "").trim();
    if (!text) return "";
    return table?.[text.toLowerCase().replace(/[\s-]+/g, "_")] ?? text;
  };
  if (Array.isArray(value)) return value.map(label).filter(Boolean).join("、");
  if (value === null || value === undefined) return "";
  return label(value);
}

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
  | "sourceNeedsConfirmation"
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
    sourceNeedsConfirmation: false,
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
    sourceNeedsConfirmation: Boolean(target.sourceNeedsConfirmation),
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
    sourceNeedsConfirmation: response.source_needs_confirmation,
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
  return replicaFactValueText(key, fact.value);
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

/** 事实的生效文本：优先取用户修正，其次取识别结果；英文标识按词表翻成中文展示。 */
export function replicaEffectiveFactText(target: ReplicaTargetDraft, key: string): string {
  const override = (target.factOverrides as Record<string, unknown>)[key];
  if (override !== null && override !== undefined) {
    return replicaFactValueText(key, override);
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

/** 原素材（A）识别是否有低置信度字段：需要用户整批确认 A 之后才能提交。 */
export function replicaSourceNeedsConfirmation(targets: readonly ReplicaTargetDraft[]): boolean {
  return targets.some((target) => target.sourceNeedsConfirmation);
}

export function validateReplicaTargetDraft(target: ReplicaTargetDraft): ReplicaTargetValidation {
  const productName = target.productName.trim();
  if (!productName) return { ok: false, error: "请填写产品名称。" };
  if (!target.assetId) return { ok: false, error: "请先上传该产品的白底图。" };
  // 提交门禁：该款识图必须已确认（未识别 / 识别中 / 待确认 / 失败都不允许提交）。
  const analysisBlock = replicaAnalysisBlockReason(target);
  if (analysisBlock) return { ok: false, error: analysisBlock };
  // 复用全定制的 SKU / 申报价 / 重量 / 类目 / 规格卡尺寸校验。
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
  source_confirmed?: boolean;
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
    source_confirmed: Boolean(input.source_confirmed),
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