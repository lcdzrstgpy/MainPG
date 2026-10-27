import { emptyReplicaAnalysisFields, type ReplicaTargetDraft } from "./podReplicaModel.ts";
import type { PodListingFieldsDraft, SpecCardConfig } from "../../pod_customization/types";
import type { ReplicaAnalysisStatus } from "../types";

/**
 * 草稿版本 2：复刻改为「一次豆包出主图方案」，前端不再保存事实/能力/展示偏好，
 * 旧版草稿里的 analysis_id 指向已下线的分析表，必须整体作废重新上传。
 */
export const POD_REPLICA_DRAFT_VERSION = 2;

export type PodReplicaSourceDraft = {
  assetId: string;
  filename: string;
  width?: number;
  height?: number;
};

export type PodReplicaDraft = {
  version: typeof POD_REPLICA_DRAFT_VERSION;
  source: PodReplicaSourceDraft | null;
  targets: ReplicaTargetDraft[];
};

export type PodReplicaStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;
export type PodReplicaLoadResult = { state: PodReplicaDraft; error?: string };
export type PodReplicaSaveResult = { ok: true } | { ok: false; error: string };

function cloneSpecCardDraft(config: SpecCardConfig): SpecCardConfig {
  return { ...config, display_unit: config.display_unit ?? "cm", cells: config.cells.map((row) => [...row]) };
}

function isSpecCardConfigDraft(value: unknown): value is SpecCardConfig {
  return isRecord(value)
    && typeof value.enabled === "boolean"
    && (value.style === "light" || value.style === "dark")
    && (value.corner === "bottom-right" || value.corner === "bottom-left" || value.corner === "top-right" || value.corner === "top-left")
    && (value.display_unit === undefined || value.display_unit === "cm" || value.display_unit === "in")
    && Array.isArray(value.cells)
    && value.cells.every((row) => Array.isArray(row) && row.every((cell) => typeof cell === "string"));
}

export function createEmptyPodReplicaDraft(): PodReplicaDraft {
  return { version: POD_REPLICA_DRAFT_VERSION, source: null, targets: [] };
}

export function podReplicaDraftStorageKey(accountId: string, workspaceId: string): string {
  return `mainpg:pod-replica:v${POD_REPLICA_DRAFT_VERSION}:${encodeURIComponent(accountId)}:${encodeURIComponent(workspaceId)}`;
}

function cloneTarget(target: ReplicaTargetDraft): ReplicaTargetDraft {
  const analysis = analysisOrDefault(target);
  return {
    clientId: target.clientId,
    assetId: target.assetId,
    filename: target.filename ?? "",
    width: target.width,
    height: target.height,
    productName: target.productName,
    listingFields: {
      title_mode: target.listingFields.title_mode,
      category_name: target.listingFields.category_name,
      skus: target.listingFields.skus.map((sku) => ({ ...sku })),
    },
    specCard: cloneSpecCardDraft(target.specCard),
    ...analysis,
  };
}

function isTarget(value: unknown): value is ReplicaTargetDraft {
  return isRecord(value)
    && typeof value.clientId === "string"
    && typeof value.assetId === "string"
    && typeof value.productName === "string"
    && isListingFields(value.listingFields)
    && isSpecCardConfigDraft(value.specCard);
}

const ANALYSIS_STATUSES: readonly ReplicaAnalysisStatus[] = [
  "pending", "analyzing", "ready", "failed",
];

/**
 * 归一化方案字段。
 *
 * 旧草稿（缺字段）→ 回退「待生成方案」。正在生成的状态不落库：
 * 恢复后回到待生成，由页面自动重新发起，避免卡在「生成中」。
 */
function analysisOrDefault(target: Partial<ReplicaTargetDraft>): Pick<
  ReplicaTargetDraft,
  "analysisId" | "analysisStatus" | "analysisError"
> {
  const fallback = emptyReplicaAnalysisFields();
  const rawStatus = target.analysisStatus;
  const status: ReplicaAnalysisStatus = ANALYSIS_STATUSES.includes(rawStatus as ReplicaAnalysisStatus)
    && rawStatus !== "analyzing"
    ? (rawStatus as ReplicaAnalysisStatus)
    : "pending";
  const analysisId = typeof target.analysisId === "string" ? target.analysisId : fallback.analysisId;
  return {
    analysisId,
    // 没有方案 id 就不可能是「就绪」。
    analysisStatus: analysisId ? status : "pending",
    analysisError: typeof target.analysisError === "string" ? target.analysisError : "",
  };
}

function isListingFields(value: unknown): value is PodListingFieldsDraft {
  return isRecord(value)
    && (value.title_mode === "long" || value.title_mode === "short")
    && typeof value.category_name === "string"
    && Array.isArray(value.skus)
    && value.skus.every((sku) => isRecord(sku)
      && typeof sku.name === "string"
      && typeof sku.declared_price === "string"
      && typeof sku.weight_g === "string");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function sourceOrDefault(value: unknown): PodReplicaSourceDraft | null {
  if (!isRecord(value)) return null;
  return {
    assetId: typeof value.assetId === "string" ? value.assetId : "",
    filename: typeof value.filename === "string" ? value.filename : "",
    width: typeof value.width === "number" ? value.width : undefined,
    height: typeof value.height === "number" ? value.height : undefined,
  };
}

function browserStorage(): PodReplicaStorage | null {
  try {
    return typeof window !== "undefined" && window.localStorage ? window.localStorage : null;
  } catch {
    return null;
  }
}

function removeQuietly(storage: PodReplicaStorage, key: string): void {
  try {
    storage.removeItem(key);
  } catch {
    // 损坏存储实现不应阻碍页面以空草稿打开。
  }
}

export function loadPodReplicaDraft(
  accountId: string,
  workspaceId: string,
  storage: PodReplicaStorage | null | undefined = browserStorage(),
): PodReplicaLoadResult {
  const empty = createEmptyPodReplicaDraft();
  if (!storage) return { state: empty, error: "无法读取爆款复刻草稿：浏览器本地存储不可用。" };
  const key = podReplicaDraftStorageKey(accountId, workspaceId);
  let raw: string | null;
  try {
    raw = storage.getItem(key);
  } catch {
    return { state: empty, error: "无法读取爆款复刻草稿：浏览器本地存储不可用。" };
  }
  if (!raw) return { state: empty };
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) throw new Error("invalid payload");
    if (parsed.version !== POD_REPLICA_DRAFT_VERSION) {
      // 旧版草稿的方案 id 已失效：整体作废，提示重新上传，而不是带着坏 id 提交。
      removeQuietly(storage, key);
      return { state: empty, error: "爆款复刻已升级：请重新上传样图与白底图。" };
    }
    const targets = Array.isArray(parsed.targets)
      ? parsed.targets.filter(isTarget).map(cloneTarget)
      : [];
    return { state: { version: POD_REPLICA_DRAFT_VERSION, source: sourceOrDefault(parsed.source), targets } };
  } catch {
    removeQuietly(storage, key);
    return { state: empty, error: "爆款复刻草稿数据已损坏，已清除当前账号的本地草稿。" };
  }
}

export function savePodReplicaDraft(
  accountId: string,
  workspaceId: string,
  state: PodReplicaDraft,
  storage: PodReplicaStorage | null | undefined = browserStorage(),
): PodReplicaSaveResult {
  if (!storage) return { ok: false, error: "无法保存爆款复刻草稿：浏览器本地存储不可用。" };
  try {
    storage.setItem(podReplicaDraftStorageKey(accountId, workspaceId), JSON.stringify({
      version: POD_REPLICA_DRAFT_VERSION,
      source: state.source ? { ...state.source } : null,
      targets: state.targets.map(cloneTarget),
    }));
    return { ok: true };
  } catch {
    return { ok: false, error: "无法保存爆款复刻草稿：浏览器本地存储不可用。" };
  }
}
