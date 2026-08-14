export type DraftStatus = "draft" | "processing" | "processed" | "deleted" | "attention_required";

export type Draft = {
  id: number;
  workspace_id: string;
  source_type: string;
  source_ref: string;
  candidate_id: string | null;
  selection_run_id: string | null;
  handoff_id: string | null;
  handoff_idempotency_key: string | null;
  skc: string | null;
  sku: string | null;
  product_name: string;
  title: string;
  description: string;
  image_url: string;
  image_path: string;
  cost: number | null;
  declared_price: number | null;
  status: DraftStatus;
  raw_payload: Record<string, any>;
  media_contract_version: number;
  created_at: string;
  updated_at: string;
};

export type DraftSummary = Draft & {
  raw_payload_summary?: boolean;
};

export type DraftVariant = {
  sku_id?: string;
  source_sku_id?: string;
  display_name?: string;
  attributes?: Record<string, string>;
  [key: string]: any;
};

export type DraftUpdateRequest = {
  source_ref?: string;
  skc?: string;
  sku?: string;
  product_name?: string;
  title?: string;
  description?: string;
  image_url?: string;
  main_image_url?: string;
  image_path?: string;
  cost?: number;
  declared_price?: number;
  status?: string;
  sku_name_edits?: Record<string, string>;
  sku_name_deletes?: string[];
};

export type TaskItemStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped"
  | "attention_required"
  | "preflight_passed";

export type TaskItem = {
  id: number;
  item_id: number;
  task_id: number;
  product_draft_id: number | null;
  skc: string;
  spu: string;
  title: string;
  image_url: string;
  status: TaskItemStatus;
  reason: string;
  result: Record<string, unknown>;
  created_at: string;
  updated_at: string;
};

export type TaskStatus =
  | "queued"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "partial_failure";

export type Task = {
  id: number;
  task_id: number;
  workspace_id: string;
  title: string;
  status: TaskStatus;
  preflight_only: boolean;
  total_count: number;
  success_count: number;
  failed_count: number;
  skipped_count: number;
  settings: Record<string, unknown>;
  idempotency_key: string | null;
  output_file: string;
  error_report_file: string;
  video_manifest_file: string;
  cleared_from_product_processing: boolean;
  created_at: string;
  updated_at: string;
  items: TaskItem[];
};

export type EngineStatus = {
  available: boolean;
  ready: boolean;
  unavailable_reasons: string[];
  app_dir: string;
  app_file: string;
  python: string;
  worker: string;
  message: string;
  diagnostics: {
    config: Record<string, unknown>;
    tenant_ai_capability: Record<string, unknown>;
    capabilities: Record<string, { enabled: boolean; ready: boolean; reason: string }>;
    dependencies: Record<string, boolean>;
    ocr_gate: Record<string, unknown>;
    storage_root: string;
  };
};

export type DraftListResponse = {
  drafts: Draft[];
  pagination: {
    limit: number;
    offset: number;
    returned: number;
    has_more: boolean;
    view: string;
  };
};

export interface ProcessingScopeOption {
  key: string;
  label: string;
}

export interface ProductProcessingOptions {
  targetSite: 'US' | 'CO' | 'EC';
  targetLanguage: 'en' | 'es';
  maxProducts: number;
  processingScope: string[];
  qualificationMode: 'standard' | 'strict';
  includeProductVideo: boolean;
  skipDuplicates: boolean;
  ipCheck: boolean;
  maxParallelDrafts: number;
  /** 生图提示词模板：A=标准商品海报，B=高端模特视觉（防比价） */
  imageTemplate?: 'A' | 'B';
  /** 历史/API 兼容字段；新前端任务固定发送 4，不再向用户暴露。 */
  imageGenerationCount?: 1 | 2 | 4;
}

/** 生图提示词模板注册表（对齐后端 IMAGE_TEMPLATES） */
export interface ImageTemplateOption {
  id: 'A' | 'B';
  name: string;
  description: string;
}

export type PreviewCoreFields = {
  sku?: string;
  declared_price?: number | string | null;
  suggested_price?: number | string | null;
  stock?: number | string | null;
  category_path?: string;
  category_id?: string;
  length_cm?: number | string | null;
  width_cm?: number | string | null;
  height_cm?: number | string | null;
  weight_g?: number | string | null;
};

export type PreviewImageOrigin = "source" | "generated" | "dimension" | "upload";

export type PreviewImageBucket = "source" | "processed";

export type PreviewSourceKind = "main" | "gallery" | "sku" | "detail" | "";

export type PreviewImageAsset = {
  id: string;
  origin: PreviewImageOrigin;
  preview_url: string;
  publication_status:
    | "local"
    | "materializing"
    | "ready"
    | "publishing"
    | "published"
    | "publish_failed";
  public_url: string | null;
  width: number;
  height: number;
  bucket: PreviewImageBucket;
  source_kind: PreviewSourceKind;
  media_asset_id: string;
  media_status: MediaAssetStatus | "";
};

export type PreviewImageManifest = {
  main_asset_id: string;
  carousel_asset_ids: string[];
  detail_asset_ids: string[];
  library_asset_ids: string[];
  semantic_asset_ids: Record<string, string>;
};

export type MediaAssetOrigin =
  | "remote_source"
  | "ai_generated"
  | "preview_upload"
  | "dimension_rendered";

export type MediaAssetStatus =
  | "pending"
  | "materializing"
  | "ready"
  | "retryable"
  | "failed";

export type MediaAssetView = {
  id: string;
  origin: MediaAssetOrigin;
  status: MediaAssetStatus;
  content_type: string;
  width: number;
  height: number;
  preview_url: string;
  error_code: string;
  error_message: string;
};

export type MediaBindingView = {
  binding_id: string;
  asset_id: string;
  role: string;
  slot_id: string;
  sku_id: string;
  variant_label: string;
  sort_order: number;
  status: MediaAssetStatus;
  preview_url: string;
  width: number;
  height: number;
  content_type: string;
  error_code: string;
  error_message: string;
};

export type ImageManifestV2 = {
  main_asset_id: string;
  carousel_asset_ids: string[];
  detail_asset_ids: string[];
  semantic_asset_ids: Record<string, string>;
};

export type DraftMediaGroups = {
  main: MediaBindingView[];
  gallery: MediaBindingView[];
  detail: MediaBindingView[];
  sku: MediaBindingView[];
  carousel: MediaBindingView[];
  dimension: MediaBindingView[];
};

export type DraftMediaResponse = {
  contract_version: number;
  draft_id: number;
  groups: DraftMediaGroups & Record<string, MediaBindingView[]>;
};

export type PreviewFinalizeRun = {
  id: string;
  task_id: number;
  status: "queued" | "publishing" | "publish_failed" | "stale" | "completed";
  total_count: number;
  published_count: number;
  failed_count: number;
  errors: Array<{
    asset_id: string;
    product_draft_id: number;
    code: string;
    message: string;
  }>;
  workbook_ready: boolean;
  file: string;
  row_count: number;
  product_count: number;
  download: string;
};

export type PreviewOverrides = {
  title?: string;
  description?: string;
  main_image?: string;
  carousel_images?: string[];
  detail_images?: string[];
  core_fields?: PreviewCoreFields;
};

export type PreviewItem = {
  item_id: number;
  product_draft_id: number | null;
  media_contract_version: number;
  preview_revision: number;
  result_version: string;
  exportable: boolean;
  skc: string;
  status: string;
  reason: string;
  title: string;
  description: string;
  source_image_urls: string[];
  carousel_images: string[];
  main_image: string;
  detail_images: string[];
  core_fields: PreviewCoreFields;
  overrides: PreviewOverrides;
  assets: PreviewImageAsset[];
  image_manifest: PreviewImageManifest;
};

export type PreviewResponse = {
  task_id: number;
  task: {
    id: number;
    title: string;
    status: string;
    total_count: number;
    success_count: number;
    failed_count: number;
    skipped_count: number;
  };
  item_count: number;
  items: PreviewItem[];
};

export type PreviewExportResponse = {
  task_id: number;
  file: string;
  row_count: number;
  product_count: number;
  download: string;
};

export interface TaskArtifact {
  artifact_id: string;
  kind: 'dxm_import_workbook' | 'failure_manifest' | 'product_video_manifest';
  name: string;
  content_type: string;
  path: string;
}

export interface TaskManifestItemCounts {
  total: number;
  succeeded: number;
  failed: number;
  skipped: number;
  not_processed: number;
  attention_required: number;
  auto_recovery_pending: number;
  identity_review_required: number;
  logistics_review_required: number;
  technical_retryable: number;
  configuration_blocked: number;
}

export interface TaskManifest {
  manifest_id: string;
  task_id: number;
  contract_version: string;
  item_counts: TaskManifestItemCounts;
  created_at: string;
}

export interface TaskProjection {
  id: number;
  task_id: number;
  title: string;
  status: TaskStatus;
  total_count: number;
  success_count: number;
  failed_count: number;
  skipped_count: number;
  created_at: string;
  updated_at: string;
  elapsed_seconds?: number;
  metadata: Record<string, any>;
}

export interface TaskOutputsResponse {
  task_id: number;
  total_count: number;
  success_count: number;
  failed_count: number;
  processed_count: number;
  elapsed_seconds?: number;
  not_processed_count: number;
  attention_required_count: number;
  auto_recovery_pending_count: number;
  identity_review_required_count: number;
  logistics_review_required_count: number;
  technical_retryable_count: number;
  configuration_blocked_count: number;
  skipped_count: number;
  output_file: string;
  error_report_file: string;
  video_manifest_file: string;
  target_site: string;
  target_language: string;
  processing_scope: string[];
  qualification_mode: 'standard' | 'strict';
  include_product_video: boolean;
  items: TaskItem[];
  task: TaskProjection;
  outputs: {
    dxm_import: string;
    error_report: string;
    log_file: string;
    product_video_manifest: string;
  };
  manifest: TaskManifest;
  artifacts: TaskArtifact[];
  message: string;
}

export type TaskHistoryItem = {
  task_id: number;
  title: string;
  status: TaskStatus;
  created_at: string;
  updated_at: string;
  elapsed_seconds?: number;
  date: string;
  total_count: number;
  success_count: number;
  failed_count: number;
  skipped_count: number;
  downloadable: {
    dxm: boolean;
    errors: boolean;
    video_manifest: boolean;
  };
  downloadable_count: number;
  has_downloadable_output: boolean;
  cleared_from_product_processing: boolean;
  target_site: string;
  target_language: string;
  target_language_label: string;
  language_contract_version: string;
};

export type TaskHistoryResponse = {
  tasks: TaskHistoryItem[];
  limit: number;
};

export type {
  CanvasItemState,
  DimensionAnnotation,
  DimensionAsset,
  DimensionCanvasBatch,
  DimensionCanvasItem,
  DimensionChangeItem,
  DimensionChangeSet,
  DimensionEligibilityItem,
  DimensionKey,
  DimensionNotification,
  DimensionProvenance,
  DimensionTaskEligibility,
  DimensionUnit,
  DimensionValue,
  EditorState,
  ImportableDimensionTask,
  NormalizedPoint,
  PhysicalDimensions,
  SaveDimensionItemRequest,
  UploadedDimensionAsset,
} from "./dimensionCanvas";
