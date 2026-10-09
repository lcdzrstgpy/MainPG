import type { PodBriefHistoryItem, PodBusinessFieldsDraft } from "../../pod_customization/types";

export const POD_SEMI_DRAFT_VERSION = 1;

export type PodSemiDraft = {
  version: number;
  business_fields: PodBusinessFieldsDraft;
  creative_prompt: string;
  count: number;
  brief_history: PodBriefHistoryItem[];
};

export type PodSemiStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const EMPTY_BUSINESS_FIELDS: PodBusinessFieldsDraft = {
  product_name: "",
  product_category: "",
  target_market: "",
  target_audience: "",
  core_selling_points: "",
  design_theme: "",
  style_keywords: "",
  color_preferences: "",
  excluded_elements: "",
  copy_restrictions: "",
};

export function createEmptyPodSemiDraft(): PodSemiDraft {
  return {
    version: POD_SEMI_DRAFT_VERSION,
    business_fields: { ...EMPTY_BUSINESS_FIELDS },
    creative_prompt: "",
    count: 20,
    brief_history: [],
  };
}

export function podSemiDraftStorageKey(accountId: string, workspaceId: string): string {
  return `mainpg:pod-semi-customization:v${POD_SEMI_DRAFT_VERSION}:${encodeURIComponent(accountId)}:${encodeURIComponent(workspaceId)}`;
}

function isPodSemiDraft(value: unknown): value is PodSemiDraft {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return candidate.version === POD_SEMI_DRAFT_VERSION
    && typeof candidate.business_fields === "object" && candidate.business_fields !== null
    && Array.isArray(candidate.brief_history);
}

function cloneDraft(state: PodSemiDraft): PodSemiDraft {
  return {
    version: state.version,
    business_fields: { ...EMPTY_BUSINESS_FIELDS, ...state.business_fields },
    creative_prompt: state.creative_prompt ?? "",
    count: typeof state.count === "number" ? state.count : 20,
    brief_history: state.brief_history.map((item) => ({ ...item, fields: { ...item.fields } })),
  };
}

function browserStorage(): PodSemiStorage | null {
  try {
    return typeof window !== "undefined" && window.localStorage ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function loadPodSemiDraft(
  accountId: string,
  workspaceId: string,
  storage: PodSemiStorage | null | undefined = browserStorage(),
): PodSemiDraft {
  const empty = createEmptyPodSemiDraft();
  if (!storage) return empty;
  try {
    const raw = storage.getItem(podSemiDraftStorageKey(accountId, workspaceId));
    if (!raw) return empty;
    const parsed: unknown = JSON.parse(raw);
    return isPodSemiDraft(parsed) ? cloneDraft(parsed) : empty;
  } catch {
    return empty;
  }
}

export function savePodSemiDraft(
  accountId: string,
  workspaceId: string,
  state: PodSemiDraft,
  storage: PodSemiStorage | null | undefined = browserStorage(),
): void {
  if (!storage) return;
  try {
    storage.setItem(podSemiDraftStorageKey(accountId, workspaceId), JSON.stringify(state));
  } catch {
    // 本地存储写入失败不影响业务，静默忽略。
  }
}
