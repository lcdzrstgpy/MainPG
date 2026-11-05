export type OrderProfitSummary = {
  order_count: number;
  sales_payback: number;
  settled_total: number;
  sales_reversal: number;
  purchase_cost: number;
  head_freight: number;
  tail_freight: number;
  penalty: number;
  ad_cost: number;
  gmv_total: number;
  declared_total: number;
  profit: number;
  profit_rate: number | null;
  missing_purchase_sku: number;
  missing_weight_sku: number;
  missing_declared: number;
  missing_gmv: number;
};

export type OrderProfitItem = {
  id: string;
  parent_order_sn: string;
  sub_order_sn: string;
  sku_id: string;
  skc_id: string;
  skc_code?: string;
  ext_code: string;
  goods_name: string;
  image_url?: string;
  quantity: number;
  declared_price: number | null;
  declared_currency: string;
  gmv: number | null;
  gmv_cny: number | null;
  gmv_estimate_cny?: number | null;
  gmv_currency: string;
  sales_payback: number | null;
  sales_reversal: number | null;
  freight_payback: number | null;
  freight_reversal: number | null;
  estimated_income: number | null;
  order_payment: number | null;
  second_payment: number | null;
  tail_freight: number | null;
  tail_currency: string;
  tail_freight_cny: number | null;
  tracking_no: string;
  order_status: string;
  order_status_code: string;
  site: string;
  order_time: string;
  shop_name: string;
  mall_id: string;
  unit_cost_cny: number | null;
  purchase_cost_cny: number | null;
  weight_kg: number | null;
  cost_source: string;
  match_status: string;
  commission_cny: number | null;
  head_freight_cny: number | null;
  profit_estimate_cny: number | null;
  profit_rate: number | null;
  source: string;
  captured_at: string;
  missing?: {
    purchase_cost: boolean;
    weight: boolean;
    declared_price: boolean;
    gmv: boolean;
    tail_freight: boolean;
  };
};

export type OrderProfitRow = {
  parent_order_sn: string;
  shop_name: string;
  mall_id: string;
  site: string;
  order_status: string;
  order_time: string;
  sub_order_count: number;
  item_count: number;
  sku_count: number;
  gmv_total: number;
  declared_total: number;
  sales_payback: number;
  sales_reversal: number;
  freight_payback: number;
  freight_reversal: number;
  settled_total: number;
  purchase_cost: number;
  head_freight: number;
  tail_freight: number;
  penalty: number;
  ad_cost: number;
  profit: number;
  profit_rate: number | null;
  items: OrderProfitItem[];
};

export type OrderProfitOrdersResponse = {
  summary: OrderProfitSummary;
  orders: OrderProfitRow[];
  total: number;
  items_total?: number;
};

export type OrderTrendPoint = {
  month: string;
  order_count: number;
  pending: number;
  shipped: number;
  signed: number;
  cancelled: number;
  other: number;
  gmv_total: number;
  sales_payback: number;
  settled_total: number;
  purchase_cost: number;
  tail_freight: number;
  profit: number;
};

export type OrderTrendResponse = {
  months: string[];
  available_months: string[];
  series: OrderTrendPoint[];
};

export type OrderProfitDailySummary = {
  date: string;
  order_count: number;
  sub_order_count: number;
  item_count: number;
  gmv_total: number;
  declared_total: number;
  purchase_cost: number;
  head_freight: number;
  tail_freight: number;
  commission: number;
  profit: number;
  profit_rate: number | null;
  head_unit_price_cny: number;
  tail_currency: string;
};

export type OrderProfitWarning = {
  type: "purchase" | "weight" | string;
  count: number;
  sku_count: number;
  message: string;
};

export type OrderProfitDailyResponse = {
  summary: OrderProfitDailySummary;
  items: OrderProfitItem[];
  warnings: OrderProfitWarning[];
};

export type OrderProfitStore = {
  id: string;
  name: string;
  site: string;
};

export type PluginSession = {
  session_id: string;
  browser: string;
  plugin_version: string;
  capabilities: Record<string, unknown>;
  status: string;
  online: boolean;
  last_seen_at: string;
  device_id?: string;
  device_name?: string;
};

export type PluginDevice = {
  device_id: string;
  name: string;
  browser: string;
  extension_version: string;
  allowed_shops: string[];
  status: string;
  created_at: string;
  last_seen_at: string;
};

export type OrderProfitCommand = {
  command_id: number;
  command_type: string;
  status: string;
  payload?: Record<string, unknown>;
};

export type OrderProfitCaptureStatus = {
  command_id: number;
  command_type: string;
  status: string;
  capture_mode: string;
  ok: boolean;
  ingested: boolean;
  batch_id: string;
  order_count: number;
  sub_order_count: number;
  item_count: number;
  gmv_total: number;
  declared_total: number;
  tail_freight_total: number;
  tail_currency: string;
  status_text: string;
  error: string;
  help: string;
  captured_at?: string;
  results?: Array<{ key: string; label: string; state: string; detail: string }>;
  ok_count?: number;
  /** 插件正在等待用户输入（如 sms_code 短信验证码）。 */
  waiting_for?: string;
  /** 用户刚提交、等插件取走的输入内容。 */
  pending_input?: string;
  skipped_count?: number;
  failed_count?: number;
  /** 插件实时上报的采集进度（仅运行中有值）。 */
  progress?: {
    step: number;
    step_total: number;
    step_label: string;
    detail: string;
    percent: number;
    /** 已完成/进行中的步骤快照：用来「完成一个出一个」。 */
    steps?: Array<{
      key: string;
      label: string;
      state: string;
      detail: string;
      updated_at?: string;
    }>;
    updated_at?: string;
  } | null;
};

export type OrderProfitSettings = {
  usd_cny_rate: number;
  commission_rate: number;
  head_unit_price_cny: number;
};

export type OperationCard = {
  key: string;
  label: string;
  value: number;
  tone?: string;
};

export type OperationTodo = {
  key: string;
  label: string;
  count: number;
  view: string;
  hint: string;
};

export type OperationDataSource = {
  key: string;
  label: string;
  status: "ready" | "empty" | "pending" | string;
};

export type OperationOverview = {
  cards: OperationCard[];
  todos: OperationTodo[];
  data_sources: OperationDataSource[];
  latest_capture: Record<string, unknown> | null;
  daily: OrderProfitDailySummary | null;
  counts: Record<string, number>;
};

export type OperationUnreceived = {
  orders: OrderProfitRow[];
  total: number;
  summary: OrderProfitSummary;
};

export type MarkupRow = {
  sku_id: string;
  skc_id: string;
  ext_code: string;
  goods_name: string;
  site: string;
  quantity: number;
  order_count: number;
  declared_price: number | null;
  unit_cost_cny: number | null;
  markup_rate: number | null;
  gross_profit_cny: number | null;
  status: "matched" | "needs_cost" | "needs_declared" | string;
};

export type MarkupResponse = {
  items: MarkupRow[];
  summary: {
    sku_count: number;
    matched_count: number;
    missing_cost_count: number;
    missing_declared_count: number;
    avg_markup_rate: number | null;
  };
};

export type OperationListResponse<T> = {
  items: T[];
  total: number;
  data_source: "ready" | "pending" | string;
  message: string;
};

export type LinkChangeRow = {
  id: string;
  product_key: string;
  product_title: string;
  old_url: string;
  new_url: string;
  change_type: string;
  detected_at: string;
  status: string;
};

export type AfterSaleRow = {
  id: string;
  parent_order_sn: string;
  sub_order_sn: string;
  sku_id: string;
  type: string;
  reason: string;
  status: string;
  amount: number | null;
  currency: string;
  occurred_at: string;
};

export type AppealRow = {
  id: string;
  parent_order_sn: string;
  sub_order_sn: string;
  appeal_type: string;
  deadline: string;
  status: string;
  result: string;
};

export type TrafficRow = {
  id: string;
  week_start: string;
  site: string;
  visitors: number;
  page_views: number;
  orders: number;
  gmv: number;
  conversion_rate: number | null;
};

export type CaptureBatch = {
  id: string;
  command_id: number;
  capture_mode: string;
  status: string;
  order_count: number;
  sub_order_count: number;
  item_count: number;
  captured_at: string;
  created_at: string;
};

export type CaptureBatches = {
  batches: CaptureBatch[];
  latest: CaptureBatch | null;
};

export type FinanceEntry = {
  id: string;
  entry_time: string;
  entry_type: string;
  amount: number;
  currency: string;
  note: string;
  category: string;
  shop_name: string;
  captured_at: string;
};

export type FinanceSummary = {
  entries: number;
  inflow: number;
  outflow: number;
  ad_cost: number;
  penalty: number;
  settlement: number;
  other_cost: number;
};

export type FinanceResponse = {
  summary: FinanceSummary;
  items: FinanceEntry[];
};

export type SettlementRow = {
  id: string;
  parent_order_sn: string;
  status: "pending" | "settled";
  currency: string;
  sales_payback: number | null;
  sales_reversal: number | null;
  freight_payback: number | null;
  freight_reversal: number | null;
  declared_price: number | null;
  quantity: number;
  goods_name: string;
  site: string;
  shop_name: string;
  captured_at: string;
};

export type SettlementTotals = {
  po_count: number;
  sales_payback: number;
  sales_reversal: number;
  freight_payback: number;
  freight_reversal: number;
  total_payback: number;
};

export type SettlementResponse = {
  summary: SettlementTotals;
  pending: SettlementTotals;
  settled: SettlementTotals;
  rows: SettlementRow[];
};

export type StoreMetrics = {
  workspace_id?: string;
  shop_name: string;
  pending_shipment: number;
  active_products: number;
  sales_today: number;
  sales_7d: number;
  sales_30d: number;
  refund_pending: number;
  cancel_pending: number;
  ship_timeliness: number | null;
  delivery_timeliness: number | null;
  captured_at: string;
  updated_at?: string;
};

export type TrafficSnapshot = {
  id: string;
  scope: "product" | "shop";
  period: string;
  stat_date: string;
  exposure: number;
  clicks: number;
  visitors: number;
  paid_orders: number;
  paid_items: number;
  click_rate: number | null;
  click_order_rate: number | null;
  exposure_order_rate: number | null;
  metrics: Record<string, string>;
  shop_name: string;
  captured_at: string;
};

/** 商品流量页按小时的图表序列（来自 /api/flow/analysis/trend）。 */
export type FluxTrendPoint = { time: string; value: number };
export type FluxTrendSeries = { key: string; points: FluxTrendPoint[] };
export type FluxTrend = {
  series?: FluxTrendSeries[];
  channels?: FluxTrendSeries[];
  overview?: Record<string, number | string | null>;
};

export type TrafficSummaryResponse = {
  product: TrafficSnapshot | null;
  shop: TrafficSnapshot | null;
  history: TrafficSnapshot[];
};

export type SalesSnapshot = {
  id: string;
  region: string;
  stat_date: string;
  metrics: Record<string, string>;
  health: Record<string, string>;
  shop_name: string;
  captured_at: string;
};

export type SalesSummaryResponse = {
  latest: SalesSnapshot | null;
  history: SalesSnapshot[];
};

export type ProfitShop = {
  id: string;
  name: string;
  platform: string;
  note: string;
  is_active: number;
  last_capture_at: string;
  /** 团队服务发放的店铺码（20261026-01）；断网建店时是 LOCAL- 开头的临时码。 */
  store_code?: string;
  code_status?: "official" | "local" | string;
  staff_no?: string;
  created_at?: string;
};

export type StoreAccount = {
  id: string;
  alias: string;
  phone: string;
  phone_tail: string;
  region: string;
  shop_name: string;
  status: string;
  last_login_at: string;
  last_error: string;
  has_password: boolean;
  instance_tag: string;
  instance_pid: number;
  last_command_id: number;
  created_at?: string;
  updated_at?: string;
};

export type ShopsResponse = {
  shops: ProfitShop[];
  active: ProfitShop | null;
};

export type TeamLinkItem = {
  account_id: string;
  username: string;
  staff_no: string;
  store_count: number;
  status: string;
  requested_at: string;
};

export type TeamLinksPayload = {
  available: boolean;
  reason?: string;
  account_id?: string;
  /** 我的登录账号（别人可以直接用它发起关联）。 */
  username?: string;
  /** 我的员工标识，同样可以用于发起关联。 */
  staff_no?: string;
  max_links?: number;
  linked?: TeamLinkItem[];
  pending_out?: TeamLinkItem[];
  pending_in?: TeamLinkItem[];
  linked_by?: TeamLinkItem[];
  linked_count?: number;
  accepted_count?: number;
  pending_out_count?: number;
  pending_in_count?: number;
  linked_by_count?: number;
};
