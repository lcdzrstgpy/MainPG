export type PodTemplateSource = "system" | "personal";
export type PodTemplateCalibrationStatus = "pending" | "calibrating" | "ready" | "failed";

export type PodNormalizedPoint = {
  x: number;
  y: number;
};

export type PodTemplateMask = PodNormalizedPoint & {
  width: number;
  height: number;
};

export type PodTemplateCalibration = {
  mask: PodTemplateMask;
  anchor: PodNormalizedPoint;
};

export type PodTemplate = {
  id: string;
  name: string;
  source: PodTemplateSource;
  preview_url: string;
  original_url: string;
  width: number;
  height: number;
  calibration_status: PodTemplateCalibrationStatus;
  calibration: PodTemplateCalibration | null;
  mask_preview_url?: string;
  error_message?: string;
  created_at: string;
  updated_at: string;
};

export type PodBusinessFieldsDraft = {
  product_name: string;
  product_category: string;
  target_market: string;
  target_audience: string;
  core_selling_points: string;
  design_theme: string;
  style_keywords: string;
  color_preferences: string;
  excluded_elements: string;
};

export type PodBusinessFields = Omit<PodBusinessFieldsDraft,
  "core_selling_points" | "style_keywords" | "color_preferences" | "excluded_elements"
> & {
  core_selling_points: string[];
  style_keywords: string[];
  color_preferences: string[];
  excluded_elements: string[];
};

export type PodTitleMode = "long" | "short";

export type PodListingFieldsDraft = {
  title_mode: PodTitleMode;
  declared_price: string;
  suggested_price_usd: string;
  length_cm: string;
  width_cm: string;
  height_cm: string;
  weight_g: string;
  category_name: string;
  sku_names: string[];
};

export type PodListingFields = {
  title_mode: PodTitleMode;
  declared_price: number;
  suggested_price_usd: number;
  length_cm: number;
  width_cm: number;
  height_cm: number;
  weight_g: number;
  category_name: string;
  sku_names: string[];
};

export type PodDianxiaomiExportStatus = {
  ready: boolean;
  exportable_style_count: number;
  skipped_style_count: number;
  block_reason: string | null;
};

export type PodBatchCount = number;
export type PodBatchStatus =
  | "queued"
  | "generating_patterns"
  | "compositing"
  | "generating_titles"
  | "completed"
  | "partial_failure"
  | "failed"
  | "settlement_pending"
  | "billing_auth_required";

export type PodBatchItemStatus =
  | "queued"
  | "generating_pattern"
  | "compositing"
  | "completed"
  | "failed"
  | "optimizing_scene";

export type PodStyleTitleStatus = "queued" | "generating" | "completed" | "failed";

export type PodStyleTitle = {
  style_index: number;
  style_task_id: string;
  status: PodStyleTitleStatus;
  title: string | null;
  listing_ready: boolean;
  error_message?: string;
  updated_at: string;
};

export type PodStyleRetry = {
  style_index: number;
  retry_count: number;
};

export type PodBatchSummary = {
  id: string;
  title: string;
  status: PodBatchStatus;
  template_id: string;
  template_name: string;
  count: PodBatchCount;
  processed_count: number;
  completed_count: number;
  failed_count: number;
  title_completed_count?: number;
  title_failed_count?: number;
  listing_ready_count?: number;
  style_grid?: boolean;
  created_at: string;
  updated_at: string;
};

export type PodBatchItem = {
  id: string;
  index: number;
  style_index?: number;
  variant_index?: number;
  status: PodBatchItemStatus;
  pattern_preview_url?: string;
  pattern_download_url?: string;
  composite_preview_url?: string;
  composite_download_url?: string;
  role?: "hero" | "detail_a" | "detail_b" | "lifestyle" | "detail" | "lifestyle_a" | "lifestyle_b";
  public_url?: string;
  scene_optimized: boolean;
  error_message?: string;
  updated_at: string;
};

export type PodBatch = PodBatchSummary & {
  template: PodTemplate;
  prompt_version: "v1";
  business_fields: PodBusinessFields;
  listing_fields: PodListingFields | null;
  dianxiaomi_export: PodDianxiaomiExportStatus;
  creative_prompt: string;
  items: PodBatchItem[];
  style_titles?: PodStyleTitle[];
  free_retry_limit?: number;
  style_retries?: PodStyleRetry[];
};

export type PodBatchListResponse = {
  batches: PodBatchSummary[];
  total: number;
};

export type PodBillingRunStatus = "auth_required" | "authorized" | "settling" | "settlement_pending" | "settled";

export type PodBillingRun = {
  id: string;
  action_type: string;
  target_id: string;
  batch_id: string;
  freeze_id: string;
  rule_version: number;
  expires_at: string;
  status: PodBillingRunStatus;
  error_message?: string;
  created_at: string;
  updated_at: string;
};

export type PodBillingRunListResponse = {
  runs: PodBillingRun[];
  total: number;
};

export type CreatePodBatchRequest = {
  template_id: string;
  count: PodBatchCount;
  prompt_version: "v1";
  business_fields: PodBusinessFields;
  listing_fields: PodListingFields;
  creative_prompt: string;
};
