import { emptyReplicaAnalysisFields, defaultReplicaPresentationPreferences, type ReplicaTargetDraft } from "./podReplicaModel.ts";
import type { PodListingFieldsDraft, SpecCardConfig } from "../../pod_customization/types";
import type {
  ReplicaAnalysisStatus,
  ReplicaPresentationPreferences,
  ReplicaSpec,
} from "../types";

export const POD_REPLICA_DRAFT_VERSION = 1;

export type PodReplicaSourceDraft = {
  assetId: string;
  filename: string;
  width?: number;
  height?: number;
  /** 原素材识别结果是否已被用户确认（有低置信度字段时才需要）。 */
  confirmed?: boolean;
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
      suggested_price_usd: target.listingFields.suggested_price_usd,
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
  "pending", "analyzing", "needs_confirmation", "confirmed", "failed",
];

function isReplicaSpec(value: unknown): value is ReplicaSpec {
  return isRecord(value);
}

function isPresentationPreferences(value: unknown): value is ReplicaPresentationPreferences {
  return isRecord(value)
    && typeof value.use_hand_model === "boolean"
    && typeof value.use_body_model === "boolean"
    && typeof value.use_wall_display === "boolean"
    && typeof value.use_white_background === "boolean"
    && typeof value.use_lifestyle_scene === "boolean";
}

/**
 * 归一化识图相关字段。
 *
 * 旧草稿（v1 及更早，没有这些字段）→ 全部回退默认值，显示「待识别」而不是崩溃。
 * 正在进行的识别状态（analyzing）不落库：恢复后应回到待识别，由用户重新触发分析。
 */
function analysisOrDefault(target: Partial<ReplicaTargetDraft>): Pick<
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
  const fallback = emptyReplicaAnalysisFields();
  const rawStatus = target.analysisStatus;
  const status: ReplicaAnalysisStatus = ANALYSIS_STATUSES.includes(rawStatus as ReplicaAnalysisStatus)
    && rawStatus !== "analyzing"
    ? (rawStatus as ReplicaAnalysisStatus)
    : "pending";
  const preferences = isPresentationPreferences(target.presentationPreferences)
    ? target.presentationPreferences
    : defaultReplicaPresentationPreferences();
  return {
    analysisId: typeof target.analysisId === "string" ? target.analysisId : fallback.analysisId,
    analysisStatus: target.analysisId ? status : "pending",
    analysisError: typeof target.analysisError === "string" ? target.analysisError : "",
    sourceArtworkSpec: isReplicaSpec(target.sourceArtworkSpec) ? target.sourceArtworkSpec : null,
    sourceNeedsConfirmation: Boolean(target.sourceNeedsConfirmation),
    targetProductSpec: isReplicaSpec(target.targetProductSpec) ? target.targetProductSpec : null,
    candidateCapabilities: Array.isArray(target.candidateCapabilities)
      ? target.candidateCapabilities.filter((item): item is string => typeof item === "string")
      : [],
    unknownCapabilities: Array.isArray(target.unknownCapabilities)
      ? target.unknownCapabilities.filter((item): item is string => typeof item === "string")
      : [],
    factOverrides: isRecord(target.factOverrides) ? (target.factOverrides as typeof fallback.factOverrides) : {},
    presentationPreferences: preferences,
  };
}

function isListingFields(value: unknown): value is PodListingFieldsDraft {
  return isRecord(value)
    && (value.title_mode === "long" || value.title_mode === "short")
    && typeof value.suggested_price_usd === "string"
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
    confirmed: value.confirmed === true,
  };
}

function browserStorage(): PodReplicaStorage | null {
  try {
    return typeof window !== "undefined" && window.localStorage ? window.localStorage : null;
  } catch {
    return null;
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
    if (!isRecord(parsed) || parsed.version !== POD_REPLICA_DRAFT_VERSION) throw new Error("invalid payload");
    const targets = Array.isArray(parsed.targets)
      ? parsed.targets.filter(isTarget).map(cloneTarget)
      : [];
    return { state: { version: POD_REPLICA_DRAFT_VERSION, source: sourceOrDefault(parsed.source), targets } };
  } catch {
    try {
      storage.removeItem(key);
    } catch {
      // 损坏存储实现不应阻碍页面以空草稿打开。
    }
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