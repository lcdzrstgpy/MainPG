import { httpJson } from "../../../transport/http/client";

export type BillingPackage = {
  package_id: string;
  label: string;
  amount_cents: number;
  /** 旧服务端字段；只用于兼容历史订单。 */
  points?: number;
  base_points?: number;
  promotion_bonus_points?: number;
  /** 该固定套餐的赠送比例（如 25/50/75/100）；custom 或非赠送档为 0。 */
  promotion_bonus_percent?: number;
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
    plan: {
      plan_type: "experience" | "flagship" | string;
      plan_label: string;
      plan_balance: number;
      plan_limit: number;
      plan_used: number;
      next_refresh_at: string;
      /** 基础版套餐到期时刻（ISO 8601）；空串=无到期限制（体验版/旗舰版）。 */
      plan_expire_at: string;
      /** 基础版每周领取：每次可领积分（非基础版为 0）。 */
      basic_claim_points: number;
      /** 已领取次数。 */
      basic_claim_count: number;
      /** 领取次数上限（4 次）。 */
      basic_claim_max: number;
      /** 当前是否可领取（服务端已算好：套餐有效 + 未领满 + 本周未领）。 */
      basic_claimable: boolean;
      /** 每日免费领取：每次可领积分（所有套餐统一 100）。 */
      daily_claim_points: number;
      /** 今天是否还没领（服务端按北京自然日判定，所有套餐通用）。 */
      daily_claimable: boolean;
      /** 上次领取的北京自然日（YYYY-MM-DD）；空串=从未领过。 */
      daily_claim_date: string;
      /** 今日已领时，下一次可领时刻（次日 00:00，ISO 8601）；未领时为空串。 */
      daily_next_claim_at: string;
      /** 额外积分独立子池实时余额（每日 + 基础版每周领取都进这里，消费时在体验之后、充值之前扣）。 */
      extra_balance: number;
    };
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
    name: string;
    /** 固定套餐可达到的最高赠送百分比。 */
    bonus_rate_percent: number;
    /** 每个固定套餐的赠送比例。 */
    tiers?: Array<{ package_id: string; bonus_rate_percent: number }>;
    applies_to: "fixed_packages";
    /** 常驻规则始终为 true；保留该字段供服务端摘要表达规则状态。 */
    active: true;
  };
  recent_ledger: BillingLedgerEntry[];
  recent_orders: BillingOrder[];
  /** 当前仍在支付中的单子（最多一条）；仅供支付完成后的轮询识别到账。 */
  pending_order?: BillingOrder | null;
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

/** 基础版每周领取 1000 积分（额外积分池，永久有效）。 */
export function claimBasicWeeklyPoints() {
  return httpJson<{
    ok: boolean;
    claimed_points: number;
    claim_count: number;
    claim_max: number;
    period: string;
  }>("/api/customer/billing/plan-basic/claim", { method: "POST" });
}

/** 每日免费领取 100 积分（额外积分池，永久有效，所有套餐可用；按北京自然日幂等）。 */
export function claimDailyExtraPoints() {
  return httpJson<{
    ok: boolean;
    claimed_points: number;
    /** 累计领取天数。 */
    claim_count: number;
    /** 本次账期（北京自然日 YYYY-MM-DD）。 */
    period: string;
    /** 下次可领时刻（次日 00:00，ISO 8601）。 */
    next_claim_at: string;
  }>("/api/customer/billing/daily-extra/claim", { method: "POST" });
}

/**
 * 自定义金额的到账积分必须由服务端报价，客户端不自行推算赠送规则。
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

/** 发送改用户名用的邮箱验证码（发到账号绑定邮箱）。 */
export function sendUsernameChangeCode(email: string) {
  return httpJson<{ ok: boolean; message: string }>("/api/customer/email-code", {
    method: "POST",
    body: { email, purpose: "change_username" },
    token: "",
  });
}

/** 修改登录用户名：服务端校验已登录 + 邮箱验证码，30 天限一次。 */
export function changeUsername(input: { new_username: string; code: string }) {
  return httpJson<{ ok: boolean; message: string }>("/api/customer/change-username", {
    method: "POST",
    body: input,
  });
}

export type ImageModelChoice = {
  value: string;
  label: string;
};

export type ImageModelSetting = {
  ok: boolean;
  model: string;
  choices: ImageModelChoice[];
};

/** 个人中心「模型选择」：读取当前生图模型与可选项（只含模型名，不含上游凭据）。 */
export function loadImageModel() {
  return httpJson<ImageModelSetting>("/desktop/basic-settings/image-model");
}

/** 切换生图模型；只改模型字段，不影响系统配置的其他内容。 */
export function saveImageModel(model: string) {
  return httpJson<{ ok: boolean; model: string; message: string }>(
    "/desktop/basic-settings/image-model",
    { method: "PUT", body: { model } },
  );
}

export type PodImageModelSetting = ImageModelSetting;

/** 读取 POD 独立生图模型，不跟随 AI处理 的模型配置。 */
export function loadPodImageModel() {
  return httpJson<PodImageModelSetting>("/desktop/basic-settings/pod-image-model");
}

/** 切换 POD 独立生图模型。 */
export function savePodImageModel(model: string) {
  return httpJson<{ ok: boolean; model: string; message: string }>(
    "/desktop/basic-settings/pod-image-model",
    { method: "PUT", body: { model } },
  );
}

// ---- 意见反馈 ----
export type FeedbackCategory = "bug" | "suggestion" | "other";
export type FeedbackStatus = "new" | "processing" | "resolved";

export type FeedbackImagePayload = {
  name: string;
  mime: string;
  data_b64: string;
};

export type FeedbackHistoryItem = {
  feedback_id: string;
  category: FeedbackCategory;
  content: string;
  contact: string;
  image_count: number;
  total_image_bytes: number;
  status: FeedbackStatus;
  admin_note: string;
  /** 管理员真正推送给用户的回复（来自 feedback_replies），按时间升序。 */
  replies: Array<{ content: string; created_at: string }>;
  status_updated_at: string;
  app_version: string;
  platform: string;
  created_at: string;
};

export function submitFeedback(input: {
  content: string;
  category: FeedbackCategory;
  contact?: string;
  images?: FeedbackImagePayload[];
  app_version?: string;
  platform?: string;
}) {
  return httpJson<{ ok: boolean; feedback_id: string; created_at: string }>("/api/customer/feedback", {
    method: "POST",
    body: input,
  });
}

export function loadMyFeedback(limit = 50, offset = 0) {
  return httpJson<{
    ok: boolean;
    feedback: FeedbackHistoryItem[];
    total: number;
    limit: number;
    offset: number;
  }>(`/api/customer/feedback/mine?limit=${limit}&offset=${offset}`);
}
