import type { ReplicaTargetDraft } from "./podReplicaModel";
import type { PodListingFieldsDraft, SpecCardConfig } from "../../pod_customization/types";

export const POD_REPLICA_DRAFT_VERSION = 1;

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