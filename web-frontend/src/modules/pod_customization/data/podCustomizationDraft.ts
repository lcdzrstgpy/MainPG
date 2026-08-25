import type { PodBusinessFieldsDraft, PodListingFieldsDraft, PodTemplate } from "../types";

export const POD_CUSTOMIZATION_DRAFT_VERSION = 3;
const PREVIOUS_POD_CUSTOMIZATION_DRAFT_VERSION = 2;
const LEGACY_POD_CUSTOMIZATION_DRAFT_VERSION = 1;

export type PodCustomizationStorage = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export type PodSystemTemplate = {
  id: string;
  name: string;
  creativePrompt: string;
  templateId: string;
  template: PodTemplate;
  createdAt: string;
};

export type PodCustomizationDraft = {
  version: typeof POD_CUSTOMIZATION_DRAFT_VERSION;
  business_fields: PodBusinessFieldsDraft;
  listing_fields: PodListingFieldsDraft;
  batch_count: number;
  custom_count_mode: boolean;
  custom_count_input: string;
  selected_template_id: string;
  current_batch_edit: string | null;
  system_templates: PodSystemTemplate[];
};

export type PodDraftLoadResult = { state: PodCustomizationDraft; error?: string };
export type PodDraftSaveResult = { ok: true } | { ok: false; error: string };
export type CreatePodSystemTemplateResult =
  | { ok: true; template: PodSystemTemplate }
  | { ok: false; error: string };
export type ResolvePodSystemTemplateResult =
  | { valid: true; template: PodTemplate }
  | { valid: false; reason: string };

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
};

const EMPTY_LISTING_FIELDS: PodListingFieldsDraft = {
  title_mode: "long",
  declared_price: "",
  suggested_price_usd: "",
  category_name: "",
  skus: [{ name: "", length_cm: "", width_cm: "", height_cm: "", weight_g: "" }],
};

export function podCustomizationDraftStorageKey(accountId: string, workspaceId: string): string {
  return `mainpg:pod-customization:v${POD_CUSTOMIZATION_DRAFT_VERSION}:${encodeURIComponent(accountId)}:${encodeURIComponent(workspaceId)}`;
}

function previousPodCustomizationDraftStorageKey(accountId: string, workspaceId: string): string {
  return `mainpg:pod-customization:v${PREVIOUS_POD_CUSTOMIZATION_DRAFT_VERSION}:${encodeURIComponent(accountId)}:${encodeURIComponent(workspaceId)}`;
}

function legacyPodCustomizationDraftStorageKey(accountId: string, workspaceId: string): string {
  return `mainpg:pod-customization:v${LEGACY_POD_CUSTOMIZATION_DRAFT_VERSION}:${encodeURIComponent(accountId)}:${encodeURIComponent(workspaceId)}`;
}

export function createEmptyPodCustomizationDraft(): PodCustomizationDraft {
  return {
    version: POD_CUSTOMIZATION_DRAFT_VERSION,
    business_fields: { ...EMPTY_BUSINESS_FIELDS },
    listing_fields: { ...EMPTY_LISTING_FIELDS },
    batch_count: 20,
    custom_count_mode: false,
    custom_count_input: "20",
    selected_template_id: "",
    current_batch_edit: null,
    system_templates: [],
  };
}

export function loadPodCustomizationDraft(
  accountId: string,
  workspaceId: string,
  storage: PodCustomizationStorage | null | undefined = browserStorage(),
): PodDraftLoadResult {
  const empty = createEmptyPodCustomizationDraft();
  if (!storage) return { state: empty, error: "无法读取 POD 草稿：浏览器本地存储不可用。" };

  const key = podCustomizationDraftStorageKey(accountId, workspaceId);
  let sourceKey = key;
  let raw: string | null;
  try {
    raw = storage.getItem(key);
    if (!raw) {
      sourceKey = previousPodCustomizationDraftStorageKey(accountId, workspaceId);
      raw = storage.getItem(sourceKey);
    }
    if (!raw) {
      sourceKey = legacyPodCustomizationDraftStorageKey(accountId, workspaceId);
      raw = storage.getItem(sourceKey);
    }
  } catch {
    return { state: empty, error: "无法读取 POD 草稿：浏览器本地存储不可用。" };
  }
  if (!raw) return { state: empty };

  try {
    const parsed: unknown = JSON.parse(raw);
    if (isPodCustomizationDraft(parsed)) return { state: cloneDraft(parsed) };
    if (isPreviousPodCustomizationDraft(parsed) || isLegacyPodCustomizationDraft(parsed)) {
      const state = migrateDraft(parsed);
      try {
        storage.setItem(key, JSON.stringify(cloneDraft(state)));
      } catch {
        // Reading a compatible old draft remains useful even if its v3 replacement cannot be persisted.
      }
      return { state };
    }
    throw new Error("invalid payload");
  } catch {
    try {
      storage.removeItem(sourceKey);
    } catch {
      // A broken storage implementation must not prevent the page from opening with an empty draft.
    }
    return { state: empty, error: "POD 草稿数据已损坏，已清除当前账号的本地草稿。" };
  }
}

export function savePodCustomizationDraft(
  accountId: string,
  workspaceId: string,
  state: PodCustomizationDraft,
  storage: PodCustomizationStorage | null | undefined = browserStorage(),
): PodDraftSaveResult {
  if (!storage) return { ok: false, error: "无法保存 POD 草稿：浏览器本地存储不可用。" };
  try {
    storage.setItem(podCustomizationDraftStorageKey(accountId, workspaceId), JSON.stringify(cloneDraft(state)));
    return { ok: true };
  } catch {
    return { ok: false, error: "无法保存 POD 草稿：浏览器本地存储不可用。" };
  }
}

export function createPodSystemTemplate(input: {
  name: string;
  creativePrompt: string;
  template: PodTemplate;
  id?: string;
  createdAt?: string;
}): CreatePodSystemTemplateResult {
  const name = input.name.trim();
  if (!name) return { ok: false, error: "请填写系统模板名称。" };
  return {
    ok: true,
    template: {
      id: input.id ?? nextSystemTemplateId(),
      name,
      creativePrompt: input.creativePrompt,
      templateId: input.template.id,
      template: clonePodTemplate(input.template),
      createdAt: input.createdAt ?? new Date().toISOString(),
    },
  };
}

export function removePodSystemTemplate(templates: readonly PodSystemTemplate[], templateId: string): PodSystemTemplate[] {
  return templates.filter((template) => template.id !== templateId).map(cloneSystemTemplate);
}

export function resolvePodSystemTemplate(
  systemTemplate: PodSystemTemplate,
  availableTemplates: readonly PodTemplate[],
): ResolvePodSystemTemplateResult {
  const available = availableTemplates.some((candidate) => candidate.id === systemTemplate.templateId);
  return available
    ? { valid: true, template: clonePodTemplate(systemTemplate.template) }
    : { valid: false, reason: "关联的图片模板已不可用，无法用于本批次。" };
}

function browserStorage(): PodCustomizationStorage | null {
  try {
    return globalThis.localStorage;
  } catch {
    return null;
  }
}

function cloneDraft(state: PodCustomizationDraft): PodCustomizationDraft {
  return {
    version: POD_CUSTOMIZATION_DRAFT_VERSION,
    business_fields: { ...state.business_fields },
    listing_fields: {
      ...state.listing_fields,
      skus: state.listing_fields.skus.map((sku) => ({ ...sku })),
    },
    batch_count: state.batch_count,
    custom_count_mode: state.custom_count_mode,
    custom_count_input: state.custom_count_input,
    selected_template_id: state.selected_template_id,
    current_batch_edit: state.current_batch_edit,
    system_templates: state.system_templates.map(cloneSystemTemplate),
  };
}

function cloneSystemTemplate(template: PodSystemTemplate): PodSystemTemplate {
  return { ...template, template: clonePodTemplate(template.template) };
}

function clonePodTemplate(template: PodTemplate): PodTemplate {
  return {
    ...template,
    calibration: template.calibration
      ? {
        mask: { ...template.calibration.mask },
        anchor: { ...template.calibration.anchor },
      }
      : null,
  };
}

function isPodCustomizationDraft(value: unknown): value is PodCustomizationDraft {
  if (!isRecord(value) || value.version !== POD_CUSTOMIZATION_DRAFT_VERSION) return false;
  return isBusinessFields(value.business_fields)
    && isListingFields(value.listing_fields)
    && typeof value.batch_count === "number"
    && typeof value.custom_count_mode === "boolean"
    && typeof value.custom_count_input === "string"
    && typeof value.selected_template_id === "string"
    && (typeof value.current_batch_edit === "string" || value.current_batch_edit === null)
    && Array.isArray(value.system_templates)
    && value.system_templates.every(isPodSystemTemplate);
}

function isBusinessFields(value: unknown): value is PodBusinessFieldsDraft {
  return isRecord(value)
    && typeof value.product_name === "string"
    && typeof value.product_category === "string"
    && typeof value.target_market === "string"
    && typeof value.target_audience === "string"
    && typeof value.core_selling_points === "string"
    && typeof value.design_theme === "string"
    && typeof value.style_keywords === "string"
    && typeof value.color_preferences === "string"
    && typeof value.excluded_elements === "string";
}

function isListingFields(value: unknown): value is PodListingFieldsDraft {
  return isRecord(value)
    && (value.title_mode === "long" || value.title_mode === "short")
    && typeof value.declared_price === "string"
    && typeof value.suggested_price_usd === "string"
    && typeof value.category_name === "string"
    && Array.isArray(value.skus)
    && value.skus.every(isSkuDraft);
}

type PreviousPodSkuDraft = Omit<PodListingFieldsDraft["skus"][number], "weight_g">;

type PreviousPodListingFieldsDraft = Omit<PodListingFieldsDraft, "skus"> & {
  weight_g: string;
  skus: PreviousPodSkuDraft[];
};

type PreviousPodCustomizationDraft = Omit<PodCustomizationDraft, "version" | "listing_fields"> & {
  version: typeof PREVIOUS_POD_CUSTOMIZATION_DRAFT_VERSION;
  listing_fields: PreviousPodListingFieldsDraft;
};

type LegacyPodListingFieldsDraft = {
  title_mode: PodListingFieldsDraft["title_mode"];
  declared_price: string;
  suggested_price_usd: string;
  length_cm: string;
  width_cm: string;
  height_cm: string;
  weight_g: string;
  category_name: string;
  sku_names?: string[];
};

type LegacyPodCustomizationDraft = Omit<PodCustomizationDraft, "version" | "listing_fields"> & {
  version: typeof LEGACY_POD_CUSTOMIZATION_DRAFT_VERSION;
  listing_fields: LegacyPodListingFieldsDraft;
};

function isLegacyPodCustomizationDraft(value: unknown): value is LegacyPodCustomizationDraft {
  if (!isRecord(value) || value.version !== LEGACY_POD_CUSTOMIZATION_DRAFT_VERSION) return false;
  return isBusinessFields(value.business_fields)
    && isLegacyListingFields(value.listing_fields)
    && typeof value.batch_count === "number"
    && typeof value.custom_count_mode === "boolean"
    && typeof value.custom_count_input === "string"
    && typeof value.selected_template_id === "string"
    && (typeof value.current_batch_edit === "string" || value.current_batch_edit === null)
    && Array.isArray(value.system_templates)
    && value.system_templates.every(isPodSystemTemplate);
}

function isPreviousPodCustomizationDraft(value: unknown): value is PreviousPodCustomizationDraft {
  if (!isRecord(value) || value.version !== PREVIOUS_POD_CUSTOMIZATION_DRAFT_VERSION) return false;
  return isBusinessFields(value.business_fields)
    && isPreviousListingFields(value.listing_fields)
    && typeof value.batch_count === "number"
    && typeof value.custom_count_mode === "boolean"
    && typeof value.custom_count_input === "string"
    && typeof value.selected_template_id === "string"
    && (typeof value.current_batch_edit === "string" || value.current_batch_edit === null)
    && Array.isArray(value.system_templates)
    && value.system_templates.every(isPodSystemTemplate);
}

function isPreviousListingFields(value: unknown): value is PreviousPodListingFieldsDraft {
  return isRecord(value)
    && (value.title_mode === "long" || value.title_mode === "short")
    && typeof value.declared_price === "string"
    && typeof value.suggested_price_usd === "string"
    && typeof value.weight_g === "string"
    && typeof value.category_name === "string"
    && Array.isArray(value.skus)
    && value.skus.every(isPreviousSkuDraft);
}

function isLegacyListingFields(value: unknown): value is LegacyPodListingFieldsDraft {
  return isRecord(value)
    && (value.title_mode === "long" || value.title_mode === "short")
    && typeof value.declared_price === "string"
    && typeof value.suggested_price_usd === "string"
    && typeof value.length_cm === "string"
    && typeof value.width_cm === "string"
    && typeof value.height_cm === "string"
    && typeof value.weight_g === "string"
    && typeof value.category_name === "string"
    && (value.sku_names === undefined || (Array.isArray(value.sku_names) && value.sku_names.every((name) => typeof name === "string")));
}

function isSkuDraft(value: unknown): boolean {
  return isRecord(value)
    && typeof value.name === "string"
    && typeof value.length_cm === "string"
    && typeof value.width_cm === "string"
    && typeof value.height_cm === "string"
    && typeof value.weight_g === "string";
}

function isPreviousSkuDraft(value: unknown): value is PreviousPodSkuDraft {
  return isRecord(value)
    && typeof value.name === "string"
    && typeof value.length_cm === "string"
    && typeof value.width_cm === "string"
    && typeof value.height_cm === "string";
}

function migrateDraft(legacy: PreviousPodCustomizationDraft | LegacyPodCustomizationDraft): PodCustomizationDraft {
  if (legacy.version === PREVIOUS_POD_CUSTOMIZATION_DRAFT_VERSION) {
    const { listing_fields: previousListing, ...rest } = legacy;
    return {
      ...rest,
      version: POD_CUSTOMIZATION_DRAFT_VERSION,
      listing_fields: {
        title_mode: previousListing.title_mode,
        declared_price: previousListing.declared_price,
        suggested_price_usd: previousListing.suggested_price_usd,
        category_name: previousListing.category_name,
        skus: previousListing.skus.map((sku) => ({ ...sku, weight_g: previousListing.weight_g })),
      },
    };
  }
  const { listing_fields: legacyListing, ...rest } = legacy;
  const names = legacyListing.sku_names?.length ? legacyListing.sku_names : ["默认款"];
  return {
    ...rest,
    version: POD_CUSTOMIZATION_DRAFT_VERSION,
    listing_fields: {
      title_mode: legacyListing.title_mode,
      declared_price: legacyListing.declared_price,
      suggested_price_usd: legacyListing.suggested_price_usd,
      category_name: legacyListing.category_name,
      skus: names.map((name) => ({
        name,
        length_cm: legacyListing.length_cm,
        width_cm: legacyListing.width_cm,
        height_cm: legacyListing.height_cm,
        weight_g: legacyListing.weight_g,
      })),
    },
  };
}

function isPodSystemTemplate(value: unknown): value is PodSystemTemplate {
  return isRecord(value)
    && typeof value.id === "string"
    && typeof value.name === "string"
    && typeof value.creativePrompt === "string"
    && typeof value.templateId === "string"
    && typeof value.createdAt === "string"
    && isPodTemplate(value.template);
}

function isPodTemplate(value: unknown): value is PodTemplate {
  if (!isRecord(value)) return false;
  const calibration = value.calibration;
  return typeof value.id === "string"
    && typeof value.name === "string"
    && (value.source === "system" || value.source === "personal")
    && typeof value.preview_url === "string"
    && typeof value.original_url === "string"
    && typeof value.width === "number"
    && typeof value.height === "number"
    && (value.calibration_status === "pending" || value.calibration_status === "calibrating" || value.calibration_status === "ready" || value.calibration_status === "failed")
    && typeof value.created_at === "string"
    && typeof value.updated_at === "string"
    && (calibration === null || isCalibration(calibration));
}

function isCalibration(value: unknown): boolean {
  return isRecord(value)
    && isPoint(value.anchor)
    && isRecord(value.mask)
    && isPoint(value.mask)
    && typeof value.mask.width === "number"
    && typeof value.mask.height === "number";
}

function isPoint(value: unknown): boolean {
  return isRecord(value) && typeof value.x === "number" && typeof value.y === "number";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function nextSystemTemplateId(): string {
  return typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
    ? `pod-system-${crypto.randomUUID()}`
    : `pod-system-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
