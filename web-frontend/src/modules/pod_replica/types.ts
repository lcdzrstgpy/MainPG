import type {
  PodBatch,
  PodBatchSummary,
  PodBusinessFields,
  PodListingFields,
} from "../pod_customization/types/index";

export type ReplicaImageRole = "source" | "target";

/** 复刻独立图片上传结果：角色、尺寸与授权本地预览路径。 */
export type ReplicaImageUploadResponse = {
  asset_id: string;
  role: ReplicaImageRole;
  width: number;
  height: number;
  preview_url: string;
};

/** 公共响应里的来源样图。 */
export type ReplicaSource = {
  asset_id: string;
  preview_url: string | null;
  download_url: string | null;
  filename: string;
  content_type: string;
  width?: number;
  height?: number;
};

/** 公共响应里的单个目标产品快照（按 style_index 冻结的有序数组元素）。 */
export type ReplicaTargetSnapshot = {
  style_index: number;
  asset_id: string;
  preview_url: string | null;
  download_url: string | null;
  filename: string;
  width?: number;
  height?: number;
  product_name: string;
  business_fields: PodBusinessFields;
  listing_fields: PodListingFields | null;
};

/** 创建请求里的单项目标。 */
export type CreateReplicaTargetRequest = {
  target_asset_id: string;
  product_name: string;
  listing_fields: PodListingFields;
  /** 识图主流程：该款的分析 ID、用户修正与展示偏好（旧客户端不传保持旧行为）。 */
  analysis_id?: string;
  fact_overrides?: ReplicaFactOverrides | null;
  presentation_preferences?: ReplicaPresentationPreferences;
};

/** 创建请求：款数由 targets 长度决定；creative_prompt 必须为空。 */
export type CreateReplicaBatchRequest = {
  client_request_id: string;
  source_asset_id: string;
  title: string;
  creative_prompt: string;
  /** 构图模板与生成策略：留空时由服务端按能力推荐并冻结。 */
  composition_id?: string;
  generation_strategy?: ReplicaGenerationStrategy | null;
  targets: CreateReplicaTargetRequest[];
};

// --- 识图分析（主流程）：事实、能力候选与展示偏好 ---

export type ReplicaFactSource = "model" | "user" | "system";

/** 单个事实字段：值 + 置信度 + 来源。 */
export type ReplicaFactValue = {
  value: unknown;
  confidence: number;
  source: ReplicaFactSource;
};

export type ReplicaSpec = Record<string, ReplicaFactValue>;

/** 用户对目标产品事实的修正（只允许覆盖事实，不能改硬约束）。 */
export type ReplicaFactOverrides = {
  product_name?: string | null;
  product_type?: string | null;
  component_count?: number | null;
  component_structure?: string | null;
  material?: string | null;
  surface_texture?: string | null;
  printable_regions?: string[] | null;
  non_printable_regions?: string[] | null;
  supported_usage_modes?: string[] | null;
};

/** 展示偏好：能力标签 ≠ 必拍画面，展示类能力必须由这里显式选中。 */
export type ReplicaPresentationPreferences = {
  use_hand_model: boolean;
  use_body_model: boolean;
  use_wall_display: boolean;
  use_white_background: boolean;
  use_lifestyle_scene: boolean;
};

export type ReplicaGenerationStrategy = "grid" | "independent_panels";

/** 分析状态：待识别 / 识别中 / 待确认 / 已确认 / 分析失败。 */
export type ReplicaAnalysisStatus =
  | "pending"
  | "analyzing"
  | "needs_confirmation"
  | "confirmed"
  | "failed";

export type ReplicaAnalyzeRequest = {
  source_asset_id: string;
  target_asset_id: string;
  fact_overrides?: ReplicaFactOverrides | null;
  presentation_preferences?: ReplicaPresentationPreferences;
};

export type ReplicaAnalyzeResponse = {
  analysis_id: string;
  status: "succeeded" | "failed";
  source_asset_id: string;
  target_asset_id: string;
  source: ReplicaSpec;
  target: ReplicaSpec;
  source_needs_confirmation: boolean;
  target_needs_confirmation: boolean;
  candidate_capabilities: string[];
  unknown_capability: string[];
  model_version: string;
  schema_version: string;
  error_code: string;
};

export type ReplicaBatchSummary = PodBatchSummary & {
  mode: "replica";
  item_count: number;
};

export type ReplicaBatch = PodBatch & {
  mode: "replica";
  batch_id: string;
  style_count: number;
  item_count: number;
  source: ReplicaSource;
  targets: ReplicaTargetSnapshot[];
};

export type ReplicaBatchListResponse = {
  batches: ReplicaBatchSummary[];
  total: number;
};