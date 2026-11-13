import { ppDownload, ppRequest, ppUpload } from "../../product_processing/api/client";
import { productProcessingApiContext } from "../../product_processing/api/context";
import type {
  AfterSaleRow,
  AppealRow,
  AutoCaptureRunResult,
  AutoCaptureSettings,
  CaptureBatches,
  CaptureRunList,
  FinanceResponse,
  LinkChangeRow,
  MarkupResponse,
  OperationListResponse,
  OperationOverview,
  OperationUnreceived,
  OrderProfitCaptureStatus,
  OrderProfitCommand,
  OrderProfitDailyResponse,
  OrderProfitOrdersResponse,
  OrderProfitRankingResponse,
  OrderProfitSettings,
  OrderProfitStore,
  OrderTrendResponse,
  PluginDevice,
  PluginSession,
  ProfitShop,
  SalesSummaryResponse,
  TeamLinksPayload,
  TeamRollupPreview,
  TeamRollupSubmitResult,
  SettlementResponse,
  ShopsResponse,
  StoreAccount,
  StoreMetrics,
  TrafficRow,
  TrafficSummaryResponse,
} from "../types";

const BASE = "/api/order-profit";
const context = () => productProcessingApiContext();

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") return;
    search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const orderProfitApi = {
  listSessions: () =>
    ppRequest<{ sessions: PluginSession[] }>(context(), `${BASE}/plugin-sessions`),
  pluginPairCode: () =>
    ppRequest<{ code: string; expires_at: string; ttl_seconds: number }>(
      context(),
      "/plugin/pair-code",
      { method: "POST" },
    ),
  pluginDevices: () => ppRequest<{ devices: PluginDevice[] }>(context(), "/plugin/devices"),
  updatePluginDevice: (deviceId: string, allowedShops: string[]) =>
    ppRequest<{ device: PluginDevice }>(
      context(),
      `/plugin/devices/${encodeURIComponent(deviceId)}`,
      { method: "PATCH", body: { allowed_shops: allowedShops } },
    ),
  deletePluginDevice: (deviceId: string) =>
    ppRequest<{ ok: boolean }>(context(), `/plugin/devices/${encodeURIComponent(deviceId)}`, {
      method: "DELETE",
    }),
  startCapture: (sessionId: string, mode: "list" | "detail", months: string[] = [], regions: string[] = []) =>
    ppRequest<OrderProfitCommand>(context(), `${BASE}/captures`, {
      method: "POST",
      body: {
        session_id: sessionId,
        mode,
        ...(months.length ? { months } : {}),
        ...(regions.length ? { regions } : {}),
      },
    }),
  startCaptureAll: (
    sessionId: string,
    incremental = false,
    shopName = "",
    months: string[] = [],
    regions: string[] = [],
  ) =>
    ppRequest<OrderProfitCommand>(context(), `${BASE}/captures-all`, {
      method: "POST",
      body: {
        session_id: sessionId,
        incremental,
        ...(shopName.trim() ? { shop_name: shopName.trim() } : {}),
        ...(months.length ? { months } : {}),
        ...(regions.length ? { regions } : {}),
      },
    }),
  startResourceCapture: (sessionId: string, resource: string) =>
    ppRequest<OrderProfitCommand>(context(), `${BASE}/captures/resource`, {
      method: "POST",
      body: { session_id: sessionId, resource },
    }),
  cancelCapture: (commandId: number) =>
    ppRequest<{ command_id: number; status: string; cancelled: boolean }>(
      context(),
      `${BASE}/captures/${commandId}/cancel`,
      { method: "POST" },
    ),
  captureStatus: (commandId: number) =>
    ppRequest<OrderProfitCaptureStatus>(context(), `${BASE}/captures/${commandId}`),
  latestCapture: () =>
    ppRequest<{ capture: Record<string, unknown> | null }>(context(), `${BASE}/captures/latest`),
  recentCaptures: (limit = 10) =>
    ppRequest<CaptureRunList>(context(), `${BASE}/captures/recent${query({ limit })}`),
  skuCosts: (params: { shop?: string; keyword?: string } = {}) =>
    ppRequest<{
      items: Array<{
        shop_name: string;
        sku_id: string;
        skc_id: string;
        skc_code: string;
        goods_name: string;
        sku_manual_cost_cny: number | null;
        quantity: number;
        order_lines: number;
        updated_at: string;
      }>;
      total: number;
      shop: string;
    }>(context(), `${BASE}/sku-costs${query({ shop: params.shop || "all", keyword: params.keyword })}`),
  skuCostTemplate: (shop = "all") =>
    ppDownload(context(), `${BASE}/sku-costs/template${query({ shop })}`, `sku-cost-template-${encodeURIComponent(shop)}.xlsx`),
  importSkuCosts: (shop: string, file: File) => {
    const form = new FormData();
    form.append("shop", shop);
    form.append("file", file);
    return ppUpload<{
      applied: Array<{ sku_id: string; unit_cost_cny: number; rows: number }>;
      skipped: Array<{ sku_id: string; reason: string }>;
      issues: Array<{ row: number; sku_id: string; reason: string }>;
    }>(context(), `${BASE}/sku-costs/import`, form);
  },
  saveSkuCosts: (shop: string, items: Array<{ sku_id: string; unit_cost_cny: number | null }>) =>
    ppRequest<{
      applied: Array<{ sku_id: string; unit_cost_cny: number; rows: number }>;
      skipped: Array<{ sku_id: string; reason: string }>;
    }>(context(), `${BASE}/sku-costs`, {
      method: "PUT",
      body: { shop, items },
    }),
  orders: (params: {
    month?: string;
    store?: string;
    keyword?: string;
    shop?: string;
    limit?: number;
    offset?: number;
  }) =>
    ppRequest<OrderProfitOrdersResponse>(
      context(),
      `${BASE}/orders${query({
        month: params.month,
        store: params.store,
        keyword: params.keyword,
        shop: params.shop,
        limit: params.limit ?? 500,
        offset: params.offset ?? 0,
      })}`,
    ),
  ordersRanking: (params: {
    months?: string[];
    shop?: string;
    groupBy?: "link" | "sku";
    metric?: "quantity" | "payback";
    limit?: number;
  }) =>
    ppRequest<OrderProfitRankingResponse>(
      context(),
      `${BASE}/orders/ranking${query({
        months: (params.months || []).join(","),
        shop: params.shop,
        group_by: params.groupBy || "link",
        metric: params.metric || "quantity",
        limit: params.limit ?? 50,
      })}`,
    ),
  updateOrderCost: (parentOrderSn: string, purchaseCostCny: number | null, shop = "") =>
    ppRequest<{ ok: boolean; updated: number; purchase_cost: number | null }>(
      context(),
      `${BASE}/orders/${encodeURIComponent(parentOrderSn)}/cost`,
      {
        method: "PATCH",
        body: { purchase_cost_cny: purchaseCostCny, shop },
      },
    ),
  ordersMonths: (shop?: string) =>
    ppRequest<{ months: string[] }>(context(), `${BASE}/orders/months${query({ shop })}`),
  ordersTrend: (params: { months?: string[]; store?: string; shop?: string }) =>
    ppRequest<OrderTrendResponse>(
      context(),
      `${BASE}/orders/trend${query({
        months: (params.months || []).join(","),
        store: params.store,
        shop: params.shop,
      })}`,
    ),
  daily: (params: {
    date?: string;
    store?: string;
    keyword?: string;
    headUnitPrice?: number;
  }) =>
    ppRequest<OrderProfitDailyResponse>(
      context(),
      `${BASE}/daily${query({
        date: params.date,
        store: params.store,
        keyword: params.keyword,
        head_unit_price: params.headUnitPrice,
      })}`,
    ),
  listStores: () =>
    ppRequest<{ stores: OrderProfitStore[] }>(context(), `${BASE}/stores`),
  getSettings: () =>
    ppRequest<{ settings: OrderProfitSettings }>(context(), `${BASE}/settings`),
  saveSettings: (settings: OrderProfitSettings) =>
    ppRequest<{ settings: OrderProfitSettings }>(context(), `${BASE}/settings`, {
      method: "PUT",
      body: settings,
    }),
  recalculate: () =>
    ppRequest<{ updated: number; summary: Record<string, unknown> }>(
      context(),
      `${BASE}/recalculate`,
      { method: "POST" },
    ),
  overview: () => ppRequest<OperationOverview>(context(), `${BASE}/operations/overview`),
  unreceived: (params: { store?: string; keyword?: string }) =>
    ppRequest<OperationUnreceived>(
      context(),
      `${BASE}/operations/unreceived${query({ store: params.store, keyword: params.keyword })}`,
    ),
  markup: (params: { month?: string; store?: string }) =>
    ppRequest<MarkupResponse>(
      context(),
      `${BASE}/operations/markup${query({ month: params.month, store: params.store })}`,
    ),
  linkChanges: () =>
    ppRequest<OperationListResponse<LinkChangeRow>>(context(), `${BASE}/operations/link-changes`),
  afterSales: () =>
    ppRequest<OperationListResponse<AfterSaleRow>>(context(), `${BASE}/operations/after-sales`),
  appeals: () =>
    ppRequest<OperationListResponse<AppealRow>>(context(), `${BASE}/operations/appeals`),
  trafficWeekly: () =>
    ppRequest<OperationListResponse<TrafficRow>>(context(), `${BASE}/operations/traffic-weekly`),
  captureBatches: () =>
    ppRequest<CaptureBatches>(context(), `${BASE}/operations/capture-status`),
  financeSummary: (month?: string, shop?: string) =>
    ppRequest<FinanceResponse>(
      context(),
      `${BASE}/finance/summary${query({ month, shop })}`,
    ),
  settlementSummary: (shop?: string) =>
    ppRequest<SettlementResponse>(context(), `${BASE}/settlement/summary${query({ shop })}`),
  importSettlementFile: (file: File, status: "pending" | "settled", shop = "") => {
    const form = new FormData();
    form.append("file", file);
    form.append("status", status);
    if (shop.trim()) form.append("shop", shop.trim());
    return ppUpload<{
      ok: boolean;
      parsed: number;
      written: number;
      backfilled_items: number;
      file: string;
      headers?: string[];
    }>(context(), `${BASE}/settlement/import-file`, form);
  },
  storeMetrics: (shop?: string) =>
    ppRequest<{ metrics: StoreMetrics | null }>(context(), `${BASE}/store-metrics${query({ shop })}`),
  trafficSummary: (shop?: string) =>
    ppRequest<TrafficSummaryResponse>(context(), `${BASE}/traffic/summary${query({ shop })}`),
  salesSummary: (shop?: string) =>
    ppRequest<SalesSummaryResponse>(context(), `${BASE}/sales/summary${query({ shop })}`),
  listShops: () => ppRequest<ShopsResponse>(context(), `${BASE}/shops`),
  cleanupShops: () =>
    ppRequest<{ deleted: number; names: string[]; kept: string[]; active: ProfitShop | null }>(
      context(),
      `${BASE}/shops/cleanup`,
      { method: "POST" },
    ),
  accounts: () => ppRequest<{ accounts: StoreAccount[] }>(context(), `${BASE}/accounts`),
  saveAccount: (payload: {
    id?: string;
    alias?: string;
    phone: string;
    password?: string;
    region?: string;
    shop_name?: string;
  }) =>
    ppRequest<{ account: StoreAccount }>(context(), `${BASE}/accounts`, {
      method: "POST",
      body: payload,
    }),
  deleteAccount: (accountId: string) =>
    ppRequest<{ deleted: boolean }>(context(), `${BASE}/accounts/${accountId}`, {
      method: "DELETE",
    }),
  startAccountLogin: (accountId: string, capture = true, manual = false) =>
    ppRequest<OrderProfitCommand>(context(), `${BASE}/accounts/${accountId}/login`, {
      method: "POST",
      body: { capture, manual },
    }),
  startAccountCapture: (accountId: string) =>
    ppRequest<OrderProfitCommand>(context(), `${BASE}/accounts/${accountId}/capture`, {
      method: "POST",
    }),
  clearAccountData: (accountId: string) =>
    ppRequest<{ shop_name: string; cleared: Record<string, number> }>(
      context(),
      `${BASE}/accounts/${accountId}/data/clear`,
      { method: "POST" },
    ),
  openAccountBrowser: (accountId: string) =>
    ppRequest<{ instance_tag: string; pid: number; browser: string; profile: string }>(
      context(),
      `${BASE}/accounts/${accountId}/browser`,
      { method: "POST" },
    ),
  closeAccountBrowser: (accountId: string) =>
    ppRequest<{ closed: boolean }>(context(), `${BASE}/accounts/${accountId}/browser`, {
      method: "DELETE",
    }),
  submitCaptureInput: (commandId: number, value: string) =>
    ppRequest<{ command_id: number; accepted: boolean }>(
      context(),
      `${BASE}/captures/${commandId}/input`,
      { method: "POST", body: { value } },
    ),
  activeShop: () => ppRequest<{ shop: ProfitShop | null }>(context(), `${BASE}/shops/active`),
  autoCapture: () =>
    ppRequest<{ auto_capture: AutoCaptureSettings }>(context(), `${BASE}/auto-capture`),
  saveAutoCapture: (payload: {
    enabled: boolean;
    interval_minutes: number;
    auto_open_browser: boolean;
  }) =>
    ppRequest<{ auto_capture: AutoCaptureSettings }>(context(), `${BASE}/auto-capture`, {
      method: "PUT",
      body: payload,
    }),
  runAutoCapture: () =>
    ppRequest<AutoCaptureRunResult>(context(), `${BASE}/auto-capture/run`, { method: "POST" }),
  bindShop: (payload: { name: string; note?: string; platform?: string; mall_id?: string }) =>
    ppRequest<ShopsResponse & { shop: ProfitShop; backfilled: number; sync_warning?: string }>(
      context(),
      `${BASE}/shops`,
      {
        method: "POST",
        body: payload,
      },
    ),
  syncShopCode: (shopId: string) =>
    ppRequest<ShopsResponse & { shop: ProfitShop; conflict?: string }>(
      context(),
      `${BASE}/shops/${encodeURIComponent(shopId)}/sync-code`,
      { method: "POST" },
    ),
  teamProfile: () =>
    ppRequest<{ available: boolean; profile: { staff_no?: string } | null }>(
      context(),
      `${BASE}/team/profile`,
    ),
  teamLinks: () => ppRequest<TeamLinksPayload>(context(), `${BASE}/team-links`),
  requestTeamLink: (targetAccountId: string) =>
    ppRequest<TeamLinksPayload>(context(), `${BASE}/team-links`, {
      method: "POST",
      body: { target_account_id: targetAccountId },
    }),
  acceptTeamLink: (accountId: string) =>
    ppRequest<TeamLinksPayload>(
      context(),
      `${BASE}/team-links/${encodeURIComponent(accountId)}/accept`,
      { method: "POST" },
    ),
  rejectTeamLink: (accountId: string) =>
    ppRequest<TeamLinksPayload>(
      context(),
      `${BASE}/team-links/${encodeURIComponent(accountId)}/reject`,
      { method: "POST" },
    ),
  removeTeamLink: (accountId: string) =>
    ppRequest<TeamLinksPayload>(
      context(),
      `${BASE}/team-links/${encodeURIComponent(accountId)}`,
      { method: "DELETE" },
    ),
  saveTeamProfile: (staffNo: string) =>
    ppRequest<{ profile: { staff_no?: string } | null }>(context(), `${BASE}/team/profile`, {
      method: "POST",
      body: { staff_no: staffNo },
    }),
  teamRollupPreview: () =>
    ppRequest<TeamRollupPreview>(context(), `${BASE}/team/rollup-preview`),
  submitTeamRollup: (weekStart = "") =>
    ppRequest<TeamRollupSubmitResult>(context(), `${BASE}/team/submit`, {
      method: "POST",
      body: { week_start: weekStart },
    }),
  activateShop: (shopId: string) =>
    ppRequest<ShopsResponse & { shop: ProfitShop; backfilled: number }>(context(), `${BASE}/shops/active`, {
      method: "POST",
      body: { shop_id: shopId },
    }),
};
