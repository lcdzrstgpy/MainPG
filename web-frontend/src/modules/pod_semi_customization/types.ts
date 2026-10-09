import type {
  PodBatchItem,
  PodBatchStatus,
  PodBusinessFields,
} from "../pod_customization/types/index";

export type SemiPatternRole = "pattern_1" | "pattern_2" | "pattern_3" | "pattern_4";

export type SemiBatchItem = PodBatchItem & {
  role?: SemiPatternRole | string;
};

export type CreateSemiBatchRequest = {
  count: number;
  prompt_version: "v1";
  business_fields: PodBusinessFields;
  creative_prompt: string;
  title?: string;
};

export type SemiBatchSummary = {
  id: string;
  mode: "semi";
  title: string;
  status: PodBatchStatus;
  count: number;
  item_count: number;
  /** 按「款」的完成/失败数（后端 completed_count 是按「组」统计的）。 */
  completed_item_count: number;
  failed_item_count: number;
  created_at: string;
  updated_at: string;
};

export type SemiBatchListResponse = {
  batches: SemiBatchSummary[];
  total: number;
};

export type SemiBatch = {
  id: string;
  batch_id: string;
  mode: "semi";
  title: string;
  status: PodBatchStatus;
  /** 组数（一次速创调用 = 一组 = 4 款）。 */
  count: number;
  style_count: number;
  /** 交付款数（= 组数 × 4）。 */
  item_count: number;
  processed_count: number;
  /** 按「组」统计。 */
  completed_count: number;
  failed_count: number;
  /** 按「款」统计，界面展示用。 */
  completed_item_count: number;
  failed_item_count: number;
  business_fields: PodBusinessFields;
  creative_prompt: string;
  items: SemiBatchItem[];
  created_at: string;
  updated_at: string;
};
