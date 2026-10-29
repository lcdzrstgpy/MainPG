import type { SpecCardConfig } from "../../pod_customization/types";
import type { FissionListingFieldsDraft } from "../types";

export const POD_FISSION_DRAFT_VERSION = 1;
export type PodFissionStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;
export type PodFissionDraft = { version: typeof POD_FISSION_DRAFT_VERSION; sourceAssetId: string; targetAssetId: string; sourceFilename?: string; targetFilename?: string; count: number; listingFields: FissionListingFieldsDraft; specCard?: SpecCardConfig };
export type PodFissionLoadResult = { state: PodFissionDraft; error?: string };
export function createEmptyPodFissionDraft(): PodFissionDraft {
  return { version: POD_FISSION_DRAFT_VERSION, sourceAssetId: "", targetAssetId: "", count: 1, listingFields: { title_mode: "long", category_name: "", skus: [{ name: "", declared_price: "", weight_g: "" }] } };
}
export function podFissionDraftStorageKey(accountId: string, workspaceId: string) { return `mainpg:pod-fission:v${POD_FISSION_DRAFT_VERSION}:${encodeURIComponent(accountId)}:${encodeURIComponent(workspaceId)}`; }
function restoredSpecCard(value: unknown): SpecCardConfig | undefined {
  if (!value || typeof value !== "object") return undefined;
  const card = value as SpecCardConfig;
  if (typeof card.enabled !== "boolean" || !["light", "dark"].includes(card.style) || !["bottom-right", "bottom-left", "top-right", "top-left"].includes(card.corner) || !["cm", "in"].includes(card.display_unit) || !Array.isArray(card.cells) || !card.cells.every((row) => Array.isArray(row) && row.every((cell) => typeof cell === "string"))) return undefined;
  return card;
}
/** 旧草稿里可能还带 analysis 字段；一键开始裂变后不再恢复它。 */
export function loadPodFissionDraft(accountId: string, workspaceId: string, storage?: PodFissionStorage | null): PodFissionLoadResult {
  const empty = createEmptyPodFissionDraft();
  if (!storage) return { state: empty, error: "无法读取爆款裂变草稿：浏览器本地存储不可用。" };
  const key = podFissionDraftStorageKey(accountId, workspaceId);
  let raw: string | null;
  try { raw = storage.getItem(key); } catch { return { state: empty, error: "无法读取爆款裂变草稿：浏览器本地存储不可用。" }; }
  if (!raw) return { state: empty };
  try {
    const payload = JSON.parse(raw);
    if (!payload || payload.version !== POD_FISSION_DRAFT_VERSION || typeof payload.sourceAssetId !== "string" || typeof payload.targetAssetId !== "string" || !payload.listingFields || !Array.isArray(payload.listingFields.skus) || payload.listingFields.skus.length < 1 || payload.listingFields.skus.length > 100) throw Error();
    const listingFields: FissionListingFieldsDraft = {
      title_mode: payload.listingFields.title_mode === "short" ? "short" : "long",
      category_name: typeof payload.listingFields.category_name === "string" ? payload.listingFields.category_name : "",
      skus: payload.listingFields.skus.map((sku: unknown) => {
        if (!sku || typeof sku !== "object") throw Error();
        const row = sku as Record<string, unknown>;
        return { name: typeof row.name === "string" ? row.name : "", declared_price: typeof row.declared_price === "string" ? row.declared_price : "", weight_g: typeof row.weight_g === "string" ? row.weight_g : "" };
      }),
    };
    return { state: { ...empty, sourceAssetId: payload.sourceAssetId, targetAssetId: payload.targetAssetId, sourceFilename: typeof payload.sourceFilename === "string" ? payload.sourceFilename : undefined, targetFilename: typeof payload.targetFilename === "string" ? payload.targetFilename : undefined, count: Number.isInteger(payload.count) && payload.count >= 1 && payload.count <= 5 ? payload.count : 1, listingFields, specCard: restoredSpecCard(payload.specCard) } };
  } catch {
    try { storage.removeItem(key); } catch { /* Leave other accounts' drafts untouched. */ }
    return { state: empty, error: "爆款裂变草稿数据已损坏，已清除当前账号的本地草稿。" };
  }
}
export function savePodFissionDraft(accountId: string, workspaceId: string, state: PodFissionDraft, storage?: PodFissionStorage | null): { ok: true } | { ok: false; error: string } {
  if (!storage) return { ok: false, error: "无法保存爆款裂变草稿：浏览器本地存储不可用。" };
  try { storage.setItem(podFissionDraftStorageKey(accountId, workspaceId), JSON.stringify({ ...state, version: POD_FISSION_DRAFT_VERSION })); return { ok: true }; }
  catch { return { ok: false, error: "无法保存爆款裂变草稿：浏览器本地存储不可用。" }; }
}
