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
};

/** 创建请求：款数由 targets 长度决定；creative_prompt 必须为空。 */
export type CreateReplicaBatchRequest = {
  client_request_id: string;
  source_asset_id: string;
  title: string;
  creative_prompt: string;
  targets: CreateReplicaTargetRequest[];
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