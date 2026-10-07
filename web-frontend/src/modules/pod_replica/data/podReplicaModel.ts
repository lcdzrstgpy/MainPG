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
import type { CreateReplicaBatchRequest, CreateReplicaTargetRequest } from "../types";

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
  };
}

export function validateReplicaTargetDraft(target: ReplicaTargetDraft): ReplicaTargetValidation {
  const productName = target.productName.trim();
  if (!productName) return { ok: false, error: "请填写产品名称。" };
  if (!target.assetId) return { ok: false, error: "请先上传该产品的白底图。" };
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
  return {
    target_asset_id: target.assetId,
    product_name: target.productName.trim(),
    listing_fields: listingFields,
  };
}

export function buildReplicaBatchRequest(input: {
  client_request_id: string;
  source_asset_id: string;
  title?: string;
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