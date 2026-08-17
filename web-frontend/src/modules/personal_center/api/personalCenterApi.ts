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
    available_points: number;
    version: number;
    ledger_head_hash: string;
    updated_at: string;
  };
  pricing: {
    currency: "CNY";
    point_ratio: number;
    ratio_label: string;
    status: string;
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

export function createTopupOrder(input: {
  provider: "wechat" | "alipay";
  package_id: string;
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
