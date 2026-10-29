import type {
  PodBatchItem,
  PodBatchStatus,
  PodBusinessFields,
  PodDianxiaomiExportStatus,
  PodListingFields,
  PodStyleTitle,
} from "../pod_customization/types/index";

export type FreeImageModel =
  | "doubao-seedream-5-0-flash-260915"
  | "doubao-seedream-5-0-pro-260628";

export type FreeBatchItem = PodBatchItem & {
  variant_index: number;
  prompt: string;
};

export type CreateFreeBatchRequest = {
  client_request_id: string;
  image_model: FreeImageModel;
  target_image_count: number;
  prompt: string;
  reference_asset_ids: string[];
  title_enabled: boolean;
  business_fields: PodBusinessFields;
  title?: string;
};

export type FreePanelPromptRequest = {
  prompt: string;
};

export type FreeBatchSummary = {
  id: string;
  mode: "free";
  title: string;
  status: PodBatchStatus;
  item_count: number;
  completed_item_count: number;
  failed_item_count: number;
  target_image_count: number;
  created_at: string;
  updated_at: string;
};

export type FreeBatchListResponse = {
  batches: FreeBatchSummary[];
  total: number;
};

export type FreeBatch = {
  id: string;
  batch_id: string;
  mode: "free";
  title: string;
  status: PodBatchStatus;
  image_model: string;
  title_enabled: boolean;
  target_image_count: number;
  completed_image_count: number;
  free_stage: "initial" | "panels";
  business_fields: PodBusinessFields;
  listing_fields: PodListingFields | null;
  items: FreeBatchItem[];
  style_titles: PodStyleTitle[];
  dianxiaomi_export: PodDianxiaomiExportStatus;
  created_at: string;
  updated_at: string;
  finished_at?: string;
  error_message?: string;
};
