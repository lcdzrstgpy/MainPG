import type { FreeImageModel } from "../types";

export type FreeDraft = {
  image_model: FreeImageModel;
  prompt: string;
  reference_asset_ids: string[];
  reference_previews: Record<string, string>;
  title_enabled: boolean;
  product_name: string;
  product_category: string;
  target_market: string;
  target_audience: string;
  selling_points: string;
  copy_restrictions: string;
  batch_title: string;
};

const VERSION = 1;

export function createEmptyFreeDraft(): FreeDraft {
  return {
    image_model: "doubao-seedream-5-0-flash-260915",
    prompt: "",
    reference_asset_ids: [],
    reference_previews: {},
    title_enabled: true,
    product_name: "",
    product_category: "",
    target_market: "",
    target_audience: "",
    selling_points: "",
    copy_restrictions: "",
    batch_title: "",
  };
}

function storageKey(accountId: string, workspaceId: string): string {
  return `mainpg:pod-free-customization:v${VERSION}:${accountId}:${workspaceId}`;
}

export function loadFreeDraft(accountId: string, workspaceId: string): FreeDraft {
  try {
    const raw = window.localStorage.getItem(storageKey(accountId, workspaceId));
    if (!raw) return createEmptyFreeDraft();
    const parsed = JSON.parse(raw) as Partial<FreeDraft> | null;
    if (!parsed || typeof parsed !== "object") return createEmptyFreeDraft();
    return { ...createEmptyFreeDraft(), ...parsed };
  } catch {
    return createEmptyFreeDraft();
  }
}

export function saveFreeDraft(accountId: string, workspaceId: string, draft: FreeDraft): void {
  try {
    window.localStorage.setItem(storageKey(accountId, workspaceId), JSON.stringify(draft));
  } catch {
    // 草稿落盘失败不阻塞主流程。
  }
}
