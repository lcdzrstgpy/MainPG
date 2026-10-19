export type ShopBatchStatus =
  | "queued"
  | "resolving"
  | "listing"
  | "enriching"
  | "pausing"
  | "paused"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "partial"
  | "failed";

export type ShopItemDetailStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";
export type ShopItemIntakeAction = "none" | "created" | "refreshed" | "skipped";

export type ShopCollectionBatch = {
  batch_id: string;
  workspace_id: string;
  actor_id: string;
  platform?: "1688" | "taobao";
  status: ShopBatchStatus;
  shop_sid: string;
  seller_id?: string;
  shop_name: string;
  display_name?: string;
  seed_offer_id?: string;
  shop_url?: string;
  next_page?: number;
  pages_fetched?: number;
  max_pages?: number;
  listing_complete?: boolean;
  discovered_count: number;
  duplicate_count?: number;
  missing_id_count?: number;
  succeeded_count: number;
  failed_count: number;
  created_count: number;
  refreshed_count: number;
  skipped_count: number;
  error_code?: string;
  error_message?: string;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  completed_at?: string | null;
};

export type ShopCollectionItemCandidate = {
  price_cny?: number | null;
  shop_name?: string;
  source_platform?: string;
  /** 详情补全后的真实商品名（历史批次只有 candidate_json 里有，item.source_title 可能为空）。 */
  source_title?: string;
  title?: string;
  product_name?: string;
};

export type ShopCollectionItem = {
  item_id: string;
  batch_id: string;
  workspace_id: string;
  offer_id: string;
  source_url: string;
  source_title: string;
  detail_status: ShopItemDetailStatus;
  intake_action: ShopItemIntakeAction;
  candidate?: ShopCollectionItemCandidate;
  attempts?: number;
  error_code?: string;
  error_message?: string;
  created_at: string;
  updated_at: string;
  completed_at?: string | null;
};

export type ShopCollectionItemsPage = {
  items: ShopCollectionItem[];
  limit: number;
  offset: number;
  total: number;
};

/**
 * 列表展示用的商品名：
 * 店铺列表阶段通常只拿得到 offer_id，真实标题要等 item_get 详情补全；
 * 历史批次的 item.source_title 可能还是空，但 candidate_json 里已有标题。
 */
export function shopItemDisplayTitle(item: ShopCollectionItem): string {
  const candidates = [
    item.source_title,
    item.candidate?.source_title,
    item.candidate?.title,
    item.candidate?.product_name,
  ];
  for (const value of candidates) {
    const title = typeof value === "string" ? value.trim() : "";
    // 上游偶尔把 offer_id 当标题返回，不能把它当成商品名展示。
    if (title && title !== item.offer_id) return title;
  }
  if (item.detail_status === "pending" || item.detail_status === "running") {
    return "商品信息补全中…";
  }
  return "未获取到商品名";
}

export type ShopBatchActions = {
  pause: boolean;
  resume: boolean;
  cancel: boolean;
  retryFailed: boolean;
};

const STATUS_LABELS: Record<ShopBatchStatus, string> = {
  queued: "等待开始",
  resolving: "识别店铺",
  listing: "发现商品",
  enriching: "补全详情",
  pausing: "正在暂停",
  paused: "已暂停",
  cancelling: "正在取消",
  cancelled: "已取消",
  completed: "采集完成",
  partial: "部分完成",
  failed: "采集失败",
};

const ACTIVE_STATUSES = new Set<ShopBatchStatus>(["queued", "resolving", "listing", "enriching", "pausing", "cancelling"]);
const TERMINAL_STATUSES = new Set<ShopBatchStatus>(["cancelled", "completed", "partial", "failed"]);

export function shopBatchStatusLabel(status: ShopBatchStatus): string {
  return STATUS_LABELS[status];
}
export function isActiveShopBatchStatus(status: ShopBatchStatus): boolean {
  return ACTIVE_STATUSES.has(status);
}

export function isTerminalShopBatchStatus(status: ShopBatchStatus): boolean {
  return TERMINAL_STATUSES.has(status);
}

export function shopBatchProgress(batch: ShopCollectionBatch): number {
  const discovered = finiteCount(batch.discovered_count);
  // 未发现任何商品时无法用「成功+失败」占比衡量进度：采集已正常结束的批次应按
  // 100% 收尾，否则空店铺批次完成后进度条永远停在 0%；其余状态仍显示 0%。
  if (discovered === 0) return batch.status === "completed" ? 100 : 0;
  const settled = finiteCount(batch.succeeded_count) + finiteCount(batch.failed_count);
  return Math.max(0, Math.min(100, Math.round((settled / discovered) * 100)));
}

export function getShopBatchActions(batch: ShopCollectionBatch): ShopBatchActions {
  const pause = ["queued", "resolving", "listing", "enriching"].includes(batch.status);
  return {
    pause,
    resume: batch.status === "paused",
    cancel: pause || batch.status === "paused" || batch.status === "pausing",
    retryFailed: batch.status === "failed"
      || (batch.status === "partial" && finiteCount(batch.failed_count) > 0),
  };
}

export function formatShopCollectionError(error: unknown): string {
  const fallback = "整店采集请求失败，请稍后重试";
  const message = error instanceof Error ? error.message.trim() : "";
  if (!message) return fallback;
  return message
    .replace(/(\b(?:set-)?cookie\s*(?::|=)\s*)[^\r\n]+/gi, "$1[已隐藏]")
    .replace(/(\b(?:proxy-)?authorization\s*(?::|=)\s*)(?:(?:basic|bearer)\s+)?[^\s,;}\]]+/gi, "$1[已隐藏]")
    .replace(/((?:["'])?(?:x[-_]?api[-_]?key|api[-_]?key|api[_-]?secret|secret|password|token|access[_-]?token|session(?:[_-]?id)?)(?:["'])?\s*(?::|=)\s*)(?:"(?:\\.|[^"])*"|'(?:\\.|[^'])*'|[^\s,;}\]]+)/gi, "$1[已隐藏]");
}

function finiteCount(value: number): number {
  return Number.isFinite(value) ? Math.max(0, value) : 0;
}
