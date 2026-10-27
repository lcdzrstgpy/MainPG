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
} from "../types";

/**
 * 复刻单款草稿。
 *
 * 主图方案（图案迁移）由服务端一次豆包调用产出，前端只保存 `analysis_id` 与状态，
 * 不再保存/展示任何事实、能力或展示偏好——方案正文由后端冻结进款级计划。
 */
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
  /** 图案迁移方案 id；为空表示尚未生成方案。 */
  analysisId: string;
  analysisStatus: ReplicaAnalysisStatus;
  analysisError: string;
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

/** 方案状态展示文案：待生成 / 生成中 / 就绪 / 失败。 */
export const REPLICA_ANALYSIS_STATUS_LABELS: Record<ReplicaAnalysisStatus, string> = {
  pending: "待生成方案",
  analyzing: "生成中",
  ready: "方案就绪",
  failed: "方案失败",
};

/** 方案相关字段的默认值（新建目标 / 旧草稿缺省时使用）。 */
export function emptyReplicaAnalysisFields(): Pick<
  ReplicaTargetDraft,
  "analysisId" | "analysisStatus" | "analysisError"
> {
  return {
    analysisId: "",
    analysisStatus: "pending",
    analysisError: "",
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
    // 换图后旧方案失效：必须重新生成，避免拿上一张图的结论提交。
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
    // 主图方案属于目标产品图片本身，不随「导出信息」一起复制。
    ...cloneReplicaAnalysisFields(target),
  };
}

// --- 主图方案：状态流转与提交门禁 ---

/** 该款的主图方案是否已就绪（提交门禁之一）。 */
export function isReplicaAnalysisReady(target: ReplicaTargetDraft): boolean {
  return target.analysisStatus === "ready" && Boolean(target.analysisId);
}

/** 未就绪时的阻塞原因；就绪返回空串。 */
export function replicaAnalysisBlockReason(target: ReplicaTargetDraft): string {
  switch (target.analysisStatus) {
    case "analyzing": return "正在生成主图方案，请稍候。";
    case "failed": return "主图方案生成失败，请重试。";
    case "ready": return target.analysisId ? "" : "主图方案缺失，请重新生成。";
    default: return "正在等待主图方案生成。";
  }
}

/** 标记为「生成中」；不覆盖已有方案 id（失败后仍可看到上一份可用的方案）。 */
export function markReplicaAnalysisAnalyzing(target: ReplicaTargetDraft): ReplicaTargetDraft {
  return { ...target, analysisStatus: "analyzing", analysisError: "" };
}

/** 标记为方案生成失败，并记录可读原因。 */
export function markReplicaAnalysisFailed(target: ReplicaTargetDraft, error: string): ReplicaTargetDraft {
  return { ...target, analysisStatus: "failed", analysisError: error };
}

/**
 * 落库一次方案结果。
 *
 * 成功即「就绪」——方案正文由后端保管，前端不展示也不需要用户确认；
 * 服务端返回 failed 时按失败处理，不覆盖既有的可用方案 id。
 */
export function applyReplicaAnalysis(
  target: ReplicaTargetDraft,
  response: ReplicaAnalyzeResponse,
): ReplicaTargetDraft {
  if (response.status !== "succeeded" || !response.analysis_id) {
    return markReplicaAnalysisFailed(target, "主图方案生成失败");
  }
  return {
    ...target,
    analysisId: response.analysis_id,
    analysisStatus: "ready",
    analysisError: "",
  };
}

/** 提交门禁：所有目标都必须拿到可用的主图方案。 */
export function replicaAnalysesReady(targets: readonly ReplicaTargetDraft[]): boolean {
  return targets.length > 0 && targets.every(isReplicaAnalysisReady);
}

export function validateReplicaTargetDraft(target: ReplicaTargetDraft): ReplicaTargetValidation {
  const productName = target.productName.trim();
  if (!productName) return { ok: false, error: "请填写产品名称。" };
  if (!target.assetId) return { ok: false, error: "请先上传该产品的白底图。" };
  // 提交门禁：该款主图方案必须已就绪（未生成 / 生成中 / 失败都不允许提交）。
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
  if (!target.analysisId) throw new Error("主图方案缺失，请重新生成。");
  return {
    target_asset_id: target.assetId,
    product_name: target.productName.trim(),
    listing_fields: listingFields,
    analysis_id: target.analysisId,
  };
}

export function buildReplicaBatchRequest(input: {
  client_request_id: string;
  source_asset_id: string;
  title?: string;
  composition_id?: string;
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
