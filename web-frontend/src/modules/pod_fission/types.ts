import type { PodBatch, PodBatchSummary, PodBatchItem, PodListingFields, PodListingFieldsDraft, PodSkuDraft } from "../pod_customization/types";

export type FissionImageRole = "source_artwork" | "target_product";
export type FissionImageUploadResponse = { asset_id: string; role: FissionImageRole; preview_url: string; width?: number; height?: number; filename?: string };
export type FissionSkuDraft = PodSkuDraft;
export type FissionListingFieldsDraft = PodListingFieldsDraft;
export type FissionListingFields = PodListingFields;
/** 一键开始裂变：一次请求内完成出方案、冻结与生图。 */
export type CreateFissionBatchRequest = { client_request_id: string; source_asset_id: string; target_asset_id: string; requested_count: number; listing_fields: FissionListingFields };
export type FissionVariant = { style_index: number; image_status: string; title_status: string; spec_card_status: string; error_message?: string | null };
export type FissionAsset = { asset_id?: string; preview_url?: string; relative_path?: string } | null;
/** Fission hides creative internals; the rest follows the common POD delivery contract. */
export type FissionBatch = Omit<PodBatch, "template" | "template_id" | "template_name" | "creative_prompt" | "prompt_version"> & {
  batch_id: string; mode: "fission"; source?: FissionAsset; target?: FissionAsset; variants?: FissionVariant[];
  /** grid = 旧版四宫格批次（只读）；independent_panels = AI 方案 + 主图与伴生三格。 */
  generation_strategy?: "grid" | "independent_panels";
};
export type FissionBatchListResponse = { batches: PodBatchSummary[]; total: number };
export type FissionStyleRetryResponse = { style_index: number; results: PodBatchItem[] };
