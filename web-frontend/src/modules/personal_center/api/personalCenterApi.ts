import { httpJson } from "../../../transport/http/client";

export type BillingPackage = {
  package_id: string;
  label: string;
  amount_cents: number;
  points: number;
};

export type BillingOrder = {
  order_id: string;
  out_trade_no: string;
  provider: "wechat" | "alipay";
  package_id: string;
  amount_cents: number;
  currency: string;
  points: number;
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
  source_ref: string;
  reserved_points: number;
  charged_points: number;
  refunded_points: number;
  status: "reserved" | "succeeded" | "failed";
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  error_message: string;
  created_at: string;
  settled_at: string;
  rule_version: number | "legacy";
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

export function loadBillingSummary() {
  return httpJson<BillingSummary>("/api/customer/billing/summary");
}

export function loadBillingUsageHistory(cursor = "") {
  const query = new URLSearchParams({ limit: "30" });
  if (cursor) query.set("cursor", cursor);
  return httpJson<BillingUsageHistory>(`/api/customer/billing/usage?${query.toString()}`);
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
