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
  /** 图案迁移方案 id（`/replica/analyze` 产出）；每个目标都必须携带。 */
  analysis_id: string;
};

/** 创建请求：款数由 targets 长度决定；creative_prompt 必须为空。 */
export type CreateReplicaBatchRequest = {
  client_request_id: string;
  source_asset_id: string;
  title: string;
  creative_prompt: string;
  /** 复刻构图模板（只作用于派生三格）；留空时服务端回退默认三拍。 */
  composition_id?: string;
  targets: CreateReplicaTargetRequest[];
};

/** 主图方案请求：A 原素材图 + B 目标白底图；参考图顺序由服务端固定为 [B, A]。 */
export type ReplicaAnalyzeRequest = {
  source_asset_id: string;
  target_asset_id: string;
  /** 「重新生成方案」置 true：跳过服务端缓存，重新调豆包并覆盖手动编辑。 */
  force?: boolean;
};

/** 主图方案响应：方案 id、版本与提示词正文；edited_at 非空表示正文被手动改过。 */
export type ReplicaAnalyzeResponse = {
  analysis_id: string;
  status: "succeeded" | "failed";
  source_asset_id: string;
  target_asset_id: string;
  prompt_version: string;
  model: string;
  main_prompt: string;
  edited_at: string;
  cached: boolean;
};

/** 手动编辑主图方案：只改提示词正文。 */
export type ReplicaPlanUpdateRequest = {
  main_prompt: string;
};

/** 前端方案状态：待生成 / 生成中 / 就绪 / 失败。 */
export type ReplicaAnalysisStatus = "pending" | "analyzing" | "ready" | "failed";

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
