import { businessFieldsForApi } from "../../pod_customization/data/podCustomizationModel";
import type { PodBatchStatus, PodBusinessFields, PodBusinessFieldsDraft } from "../../pod_customization/types";

export const SEMI_BATCH_COUNTS = [4, 8, 20, 40, 60, 100] as const;
export const SEMI_MIN_COUNT = 4;
export const SEMI_MAX_COUNT = 200;

/**
 * 半定制提交载荷：只有图案相关字段参与生成，产品名/品类/市场/人群/卖点一律留空。
 * 智能填写可能顺带返回那些产品字段，这里显式清空，避免把无关信息写进批次快照。
 */
export function semiBusinessFieldsForApi(fields: PodBusinessFieldsDraft): PodBusinessFields {
  return {
    ...businessFieldsForApi(fields),
    product_name: "",
    product_category: "",
    target_market: "",
    target_audience: "",
    core_selling_points: [],
    copy_restrictions: "",
  };
}

export function isSemiCountValid(count: number): boolean {
  return Number.isInteger(count) && count >= SEMI_MIN_COUNT && count <= SEMI_MAX_COUNT && count % 4 === 0;
}

export function semiGroupCount(count: number): number {
  return Math.floor(count / 4);
}

/** 款号标签（无扩展名），用于图上角标与列表：style_001。 */
export function semiItemTag(index: number): string {
  return `style_${String(index).padStart(3, "0")}`;
}

/** zip 内文件名：style_001.png（款号 = item.index，3 位补零）。 */
export function semiItemFilename(index: number, suffix = ".png"): string {
  return `${semiItemTag(index)}${suffix}`;
}

const SEMI_ITEM_STATUS_LABELS: Record<string, string> = {
  queued: "等待生成",
  generating_pattern: "生成中",
  compositing: "合成中",
  optimizing_scene: "处理中",
  completed: "已完成",
  failed: "失败",
};

export function semiItemStatusLabel(status: string): string {
  return SEMI_ITEM_STATUS_LABELS[status] ?? status;
}

const SEMI_STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  generating_patterns: "生成中",
  compositing: "生成中",
  generating_titles: "生成中",
  pausing: "暂停中",
  paused: "已暂停",
  cancelling: "取消中",
  cancelled: "已取消",
  completed: "已完成",
  partial_failure: "部分完成",
  failed: "失败",
  settlement_pending: "待结算",
};

export function semiBatchStatusLabel(status: PodBatchStatus | string): string {
  return SEMI_STATUS_LABELS[status] ?? status;
}

export function isSemiBatchRunning(status: PodBatchStatus | string): boolean {
  return ["queued", "generating_patterns", "compositing", "generating_titles", "pausing", "cancelling"].includes(status);
}

export function isSemiBatchPaused(status: PodBatchStatus | string): boolean {
  return status === "paused";
}

export function isSemiBatchTerminal(status: PodBatchStatus | string): boolean {
  return ["completed", "partial_failure", "failed", "cancelled"].includes(status);
}
