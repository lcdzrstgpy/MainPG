import { httpJson } from "../../../transport/http/client";

export type BillingPackage = {
  package_id: string;
  label: string;
  amount_cents: number;
  /** 旧服务端字段；只用于兼容历史订单。 */
  points?: number;
  base_points?: number;
  promotion_bonus_points?: number;
  total_points?: number;
};

export type BillingOrder = {
  order_id: string;
  out_trade_no: string;
  provider: "wechat" | "alipay";
  package_id: string;
  amount_cents: number;
  currency: string;
  /** 旧服务端字段；只用于兼容历史订单。 */
  points?: number;
  base_points?: number;
  promotion_bonus_points?: number;
  total_points?: number;
  status: "pending" | "paid" | "closed" | "failed" | "refunded";
  created_at: string;
  paid_at: string;
  expires_at: string;
};

export type BillingLedgerEntry = {
  entry_id: string;
  direction: "credit" | "debit" | "lock" | "unlock";
  points_delta: number;
  balance_after: number;
  source_type: string;
  source_id: string;
  created_at: string;
};

export type BillingSummary = {
  ok: boolean;
  account: {
    account_id: string;
    username: string;
    workspace_id: string;
    workspace_code: string;
  };
  wallet: {
    points_balance: number;
    locked_points: number;
    manual_frozen_points: number;
    frozen_points: number;
    available_points: number;
    version: number;
    ledger_head_hash: string;
    updated_at: string;
  };
  pricing: {
    currency: "CNY";
    rule_version: number;
    point_unit_scale: number;
    points_per_cny: number;
    ratio_label: string;
    product_link: {
      actual_charge_min_points: number;
      actual_charge_max_points: number;
      reserve_max_points: number;
    };
    features: Record<string, { reserve_points: number; charge_points: number }>;
    min_client_version: string;
    effective_at: string;
  };
  topup_products: BillingPackage[];
  topup_promotion?: {
    active: boolean;
    name: string;
    multiplier: number;
  };
  recent_ledger: BillingLedgerEntry[];
  recent_orders: BillingOrder[];
  security: {
    server_authoritative: boolean;
    local_balance_trusted: boolean;
    ledger_hash_chain: boolean;
    settlement_requires_signed_provider_callback: boolean;
  };
};

export type BillingUsageEntry = {
  usage_id: string;
  feature_key: string;
  billing_profile: "ai_usage" | "product_processing" | "pod_random_v1";
  source_ref: string;
  reserved_points: number;
  charged_points: number;
  refunded_points: number;
  status: "reserved" | "succeeded" | "failed" | "frozen";
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  error_message: string;
  created_at: string;
  settled_at: string;
  rule_version: number | string | "legacy";
  task: string | number;
};

export type BillingUsageHistory = {
  ok: boolean;
  items: BillingUsageEntry[];
  next_cursor: string;
  has_more: boolean;
};

export type TopupOrderResponse = {
  ok: boolean;
  reused: boolean;
  order: BillingOrder;
  payment: {
    provider: "wechat" | "alipay";
    mode: "gateway_not_configured" | "native_qr" | "page_pay";
    qr_code_url: string;
    pay_url: string;
    message: string;
  };
};

export type TopupQuoteResponse = {
  ok: boolean;
  product: BillingPackage;
};

export function loadBillingSummary() {
  return httpJson<BillingSummary>("/api/customer/billing/summary");
}

export type BillingUsageQuery = {
  cursor?: string;
  featureKey?: string;
  /** 逗号分隔的多状态值，例如 "reserved,frozen" 表示处理中 */
  usageStatus?: string;
  /** YYYY-MM-DD，按记录创建日期过滤（含当日） */
  dateFrom?: string;
  dateTo?: string;
  /** 单次拉取条数上限（服务端上限 100）。分页与统计在客户端完成。 */
  limit?: number;
};

export function loadBillingUsageHistory(query: BillingUsageQuery = {}) {
  const params = new URLSearchParams({ limit: String(query.limit ?? 30) });
  if (query.cursor) params.set("cursor", query.cursor);
  if (query.featureKey) params.set("feature_key", query.featureKey);
  if (query.usageStatus) params.set("usage_status", query.usageStatus);
  if (query.dateFrom) params.set("date_from", query.dateFrom);
  if (query.dateTo) params.set("date_to", query.dateTo);
  return httpJson<BillingUsageHistory>(`/api/customer/billing/usage?${params.toString()}`);
}

export function createTopupOrder(input: {
  // The desktop client currently exposes only Alipay. Keep other provider
  // support on the server isolated until its payment flow is implemented.
  provider: "alipay";
  package_id: string;
  amount_cents?: number;
}) {
  return httpJson<TopupOrderResponse>("/api/customer/billing/topup-orders", {
    method: "POST",
    body: {
      ...input,
      idempotency_key: `idem_${crypto.randomUUID().replace(/-/g, "")}`,
    },
  });
}

/**
 * 自定义金额的到账积分必须由服务端报价，避免活动切换期间由客户端自行推算。
 */
export function quoteCustomTopup(amountCents: number) {
  return httpJson<TopupQuoteResponse>("/api/customer/billing/topup-quote", {
    method: "POST",
    body: { amount_cents: amountCents },
  });
}

export function changeAccountPassword(input: {
  account_id?: string;
  username?: string;
  email?: string;
  current_password: string;
  new_password: string;
}) {
  return httpJson<{ ok: boolean; message: string }>("/api/customer/change-password", {
    method: "POST",
    body: input,
  });
}
