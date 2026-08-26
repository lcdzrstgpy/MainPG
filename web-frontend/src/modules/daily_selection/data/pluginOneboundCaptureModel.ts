export type PluginOneboundCaptureStatus =
  | "prepared"
  | "queued"
  | "running"
  | "completed"
  | "partial"
  | "cancelled"
  | "failed"
  | "expired"
  | "started"
  | "finished";

export type PluginOneboundCaptureBatch = {
  batch_id: string;
  parent_batch_id: string;
  page_url: string;
  status: PluginOneboundCaptureStatus;
  cancelled: boolean;
  created_count: number;
  refreshed_count: number;
  skipped_count: number;
  failed_count: number;
  unprocessed_count: number;
  total_count: number;
  error_code: string;
  error_message: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type PluginOneboundCaptureItemStatus = "pending" | "running" | "succeeded" | "failed" | "skipped" | "unprocessed";

export type PluginOneboundCaptureItem = {
  batch_id: string;
  offer_id: string;
  source_url: string;
  source_title: string;
  status: PluginOneboundCaptureItemStatus;
  outcome: "" | "created" | "refreshed" | "skipped" | "failed" | "unprocessed";
  draft_id: number | null;
  attempts: number;
  error_code: string;
  error_message: string;
  created_at: string;
  updated_at: string;
};

export type PluginOneboundCaptureBatchPage = {
  items: PluginOneboundCaptureBatch[];
  total: number;
  limit: number;
  offset: number;
};

export type PluginOneboundCaptureItemsPage = {
  items: PluginOneboundCaptureItem[];
  total: number;
  limit: number;
  offset: number;
};

const STATUS_LABELS: Record<PluginOneboundCaptureStatus, string> = {
  prepared: "等待启动",
  queued: "排队中",
  running: "采集中",
  completed: "采集完成",
  partial: "部分完成",
  cancelled: "已取消",
  failed: "采集失败",
  started: "采集中",
  finished: "采集完成",
  expired: "批次已过期",
};

export function pluginCaptureStatusLabel(status: PluginOneboundCaptureStatus): string {
  return STATUS_LABELS[status];
}

export function isActivePluginCaptureStatus(status: PluginOneboundCaptureStatus): boolean {
  return status === "prepared" || status === "queued" || status === "running" || status === "started";
}

export function isTerminalPluginCaptureStatus(status: PluginOneboundCaptureStatus): boolean {
  return status === "completed"
    || status === "partial"
    || status === "cancelled"
    || status === "failed"
    || status === "expired"
    || status === "finished";
}

export function canRetryPluginCaptureFailures(batch: PluginOneboundCaptureBatch): boolean {
  return isTerminalPluginCaptureStatus(batch.status) && finiteCount(batch.failed_count) > 0;
}

export function pluginCaptureProgress(batch: PluginOneboundCaptureBatch): { completed: number; total: number; percent: number } {
  const completed = finiteCount(batch.created_count)
    + finiteCount(batch.refreshed_count)
    + finiteCount(batch.skipped_count)
    + finiteCount(batch.failed_count);
  const total = Number.isFinite(batch.total_count) && batch.total_count >= 0
    ? batch.total_count
    : completed + finiteCount(batch.unprocessed_count);
  const percent = total
    ? Math.max(0, Math.min(100, Math.round((completed / total) * 100)))
    : 0;
  return { completed, total, percent };
}

function finiteCount(value: number): number {
  return Number.isFinite(value) ? Math.max(0, value) : 0;
}
