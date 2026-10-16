import type { PodBatch, PodBatchSummary, PodBatchItem, PodListingFields, PodListingFieldsDraft, PodSkuDraft } from "../pod_customization/types";

export type FissionImageRole = "source_artwork" | "target_product";
export type FissionImageUploadResponse = { asset_id: string; role: FissionImageRole; preview_url: string; width?: number; height?: number; filename?: string };
export type FissionAnalysisResponse = { analysis_id: string; status: "succeeded" | "failed"; product_summary: string; artwork_summary: string; error?: string | null };
export type AnalyzeFissionAssetsRequest = { source_asset_id: string; target_asset_id: string };
export type FissionSkuDraft = PodSkuDraft;
export type FissionListingFieldsDraft = PodListingFieldsDraft;
export type FissionListingFields = PodListingFields;
export type CreateFissionBatchRequest = { analysis_id: string; requested_count: number; client_request_id: string; listing_fields: FissionListingFields };
export type FissionVariant = { style_index: number; image_status: string; title_status: string; spec_card_status: string; error_message?: string | null };
export type FissionAsset = { asset_id?: string; preview_url?: string; relative_path?: string } | null;
/** Fission hides creative internals; the rest follows the common POD delivery contract. */
export type FissionBatch = Omit<PodBatch, "template" | "template_id" | "template_name" | "creative_prompt" | "prompt_version"> & {
  batch_id: string; mode: "fission"; source?: FissionAsset; target?: FissionAsset; variants?: FissionVariant[];
};
export type FissionBatchListResponse = { batches: PodBatchSummary[]; total: number };
export type FissionStyleRetryResponse = { style_index: number; results: PodBatchItem[] };
