export type CollectionMode = "keyword" | "image";
export type SelectionScope = "exact" | "divergent";
export type TargetSite = "US" | "CO" | "EC";
export type CollectionPlatform = "1688" | "taobao" | "1688+taobao";

export type DailySelectionCriteria = {
  keywords: string[];
  collection_mode?: "keyword" | "image";
  collection_platform?: "1688";
  selection_scope: SelectionScope;
  reference_image_url?: string;
  category: string;
  min_price?: number;
  max_price?: number;
  min_moq?: number;
  min_sku_count?: number;
  max_sku_count?: number;
  min_sku_price?: number;
  max_sku_price?: number;
  min_sku_stock?: number;
  max_sku_stock?: number;
  target_count: number;
  max_api_calls: number;
  detail_count: number;
  exclude_risks: boolean;
  site: TargetSite;
  max_parallel_collect?: number;
};

export type SourceVariantRecord = {
  sku_id: string;
  attributes: Record<string, unknown>;
  spec_text: string | null;
  image_url: string | null;
  price_cny: number | string | null;
  min_order_quantity: number | null;
  quantity: number | null;
  sales: number | null;
};

export type DailySelectionCandidate = {
  candidate_id: string;
  offer_id: string;
  source_platform: "1688";
  source_url: string;
  source_title: string;
  query_keyword: string | null;
  selection_result_label: string | null;
  listed_at: string | null;
  main_image_url: string | null;
  source_image_urls: string[];
  source_detail_image_urls: string[];
  source_variant_records: SourceVariantRecord[];
  source_attributes: Record<string, unknown>;
  price_cny: number | string | null;
  min_order_quantity: number | null;
  selection_score: number | string;
  selection_reasons: string[];
  risk_tags: string[];
  status: "candidate" | "filtered" | "confirmed" | "rejected";
  shop_name: string | null;
  location: string | null;
  sales_text: string | null;
  weight_text: string | null;
  package_info_text: string | null;
  freight_cny: number | string | null;
  captured_fields: string[];
  missing_capture_fields: string[];
  score_components: Record<string, unknown>;
};

export type DailySelectionRunSummary = {
  run_id: string;
  workspace_id: string;
  status: string;
  candidate_count: number;
  created_at: string;
  updated_at: string;
};

export type DailySelectionRunMetadata = Record<string, unknown>;

export type DailySelectionRun = DailySelectionRunSummary & {
  criteria: Record<string, unknown>;
  metadata: DailySelectionRunMetadata;
  candidates: DailySelectionCandidate[];
};

export type DailySelectionTaskStatus = {
  task_id: string;
  status: "queued" | "running" | "completed" | "failed";
  stage: string;
  progress: number;
  completed: number;
  total: number;
  message: string;
  run_id: string | null;
  error: string | null;
  updated_at: string;
};

export type DailySelectionHandoff = {
  handoff_id: string;
  run_id: string;
  candidate_id: string;
  workspace_id: string;
  payload_json: string;
  status: "pending" | "consumed" | "failed";
  idempotency_key: string;
  created_at: string;
};

export type DailySelectionConfirmResult = {
  handoffs: DailySelectionHandoff[];
  selected_count: number;
  created_count: number;
  replayed_count: number;
  pending_count: number;
};

export type SkuRepullState = {
  status: "idle" | "running" | "completed" | "cancelled" | "failed";
  round: number;
  total: number;
  done: number;
  succeeded: number;
  failed: number;
  message: string;
  updated_at: string;
};
