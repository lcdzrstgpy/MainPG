import { ppRequest } from "../../product_processing/api/client";
import { productProcessingApiContext } from "../../product_processing/api/context";

const base = "/api/sourcing-ledger";

export type LedgerStatus =
  | "ready"
  | "low_profit"
  | "needs_price"
  | "needs_source"
  | "needs_site"
  | "unmatched_source"
  | "in_library";

export type LedgerProfit = {
  net_profit?: string;
  profit_rate?: string;
  gross_profit?: string;
  total_cost?: string;
  [key: string]: unknown;
};

export type LedgerSourceImage = {
  id: string;
  image_url: string;
  position?: number;
};

export type LedgerSource = {
  id: string;
  source_url: string;
  source_title: string;
  shop_name?: string;
  main_image_url?: string;
  offer_id?: string;
  price_cny: string;
  moq: number;
  domestic_freight_cny: string;
  weight_kg: string;
  weight_estimated?: boolean;
  landed_cost_cny: string;
  is_primary: boolean | number;
  source_kind?: string;
  note?: string;
  images?: LedgerSourceImage[];
  profit?: LedgerProfit;
};

export type LedgerItem = {
  skc: string;
  title: string;
  image_url: string;
  site: string;
  site_code: string;
  market_skc_id: string;
  market_skc_code: string;
  sku_count: number;
  original_min: string;
  adjusted_min: string;
  captured_at: string;
  source_count: number;
  best_source: Partial<LedgerSource>;
  profit: LedgerProfit;
  status: LedgerStatus;
  in_library: boolean;
  weight_estimated?: boolean;
};

export type LedgerDeclaredPrice = {
  sku_id: string;
  sku_merchant_code: string;
  sku_attribute_text: string;
  original_price_cny: string;
  adjusted_price_cny: string;
  site: string;
  captured_at: string;
};

export type LedgerItemDetail = {
  skc: string;
  market_skc_id?: string;
  title: string;
  image_url: string;
  site: string;
  site_code: string;
  site_labels: string[];
  sites: Array<{ site_code: string; display_name: string }>;
  sources: LedgerSource[];
  declared_prices: LedgerDeclaredPrice[];
  original_min: string;
  adjusted_min: string;
  in_library: boolean;
};

export type LedgerSourcePayload = {
  source_url?: string;
  source_title?: string;
  shop_name?: string;
  main_image_url?: string;
  price_cny?: string;
  moq?: number;
  domestic_freight_cny?: string;
  weight_kg?: string;
  note?: string;
  offer_id?: string;
};

const context = () => productProcessingApiContext();

export const sourcingLedgerApi = {
  listItems: (params: { status?: string; site?: string; keyword?: string; limit?: number; offset?: number }) => {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    if (params.site) query.set("site", params.site);
    if (params.keyword) query.set("keyword", params.keyword);
    query.set("limit", String(params.limit ?? 200));
    query.set("offset", String(params.offset ?? 0));
    return ppRequest<{ items: LedgerItem[]; total: number; counts: Record<string, number> }>(
      context(),
      `${base}/items?${query.toString()}`,
    );
  },
  getItem: (skc: string, site = "") =>
    ppRequest<LedgerItemDetail>(
      context(),
      `${base}/items/${encodeURIComponent(skc)}${site ? `?site=${encodeURIComponent(site)}` : ""}`,
    ),
  setItemSite: (skc: string, siteCode: string) =>
    ppRequest<LedgerItemDetail>(context(), `${base}/items/${encodeURIComponent(skc)}/site`, {
      method: "POST",
      body: { site_code: siteCode },
    }),
  createSource: (skc: string, payload: LedgerSourcePayload) =>
    ppRequest<LedgerItemDetail>(context(), `${base}/items/${encodeURIComponent(skc)}/sources`, {
      method: "POST",
      body: payload,
    }),
  updateSource: (skc: string, sourceId: string, payload: LedgerSourcePayload) =>
    ppRequest<LedgerItemDetail>(
      context(),
      `${base}/items/${encodeURIComponent(skc)}/sources/${encodeURIComponent(sourceId)}`,
      { method: "PATCH", body: payload },
    ),
  deleteSource: (skc: string, sourceId: string) =>
    ppRequest<LedgerItemDetail>(
      context(),
      `${base}/items/${encodeURIComponent(skc)}/sources/${encodeURIComponent(sourceId)}`,
      { method: "DELETE" },
    ),
  setPrimarySource: (skc: string, sourceId: string) =>
    ppRequest<LedgerItemDetail>(
      context(),
      `${base}/items/${encodeURIComponent(skc)}/sources/${encodeURIComponent(sourceId)}/primary`,
      { method: "POST" },
    ),
  addSourceImage: (skc: string, sourceId: string, imageUrl: string) =>
    ppRequest<LedgerItemDetail>(
      context(),
      `${base}/items/${encodeURIComponent(skc)}/sources/${encodeURIComponent(sourceId)}/images`,
      { method: "POST", body: { image_url: imageUrl } },
    ),
  removeSourceImage: (skc: string, sourceId: string, imageId: string) =>
    ppRequest<LedgerItemDetail>(
      context(),
      `${base}/items/${encodeURIComponent(skc)}/sources/${encodeURIComponent(sourceId)}/images/${encodeURIComponent(imageId)}`,
      { method: "DELETE" },
    ),
  removeSourceMainImage: (skc: string, sourceId: string) =>
    ppRequest<LedgerItemDetail>(
      context(),
      `${base}/items/${encodeURIComponent(skc)}/sources/${encodeURIComponent(sourceId)}/main-image`,
      { method: "DELETE" },
    ),
  setWeights: (skcs: string[], weightKg: string) =>
    ppRequest<{ updated: number; skcs: string[]; weight_kg: string }>(context(), `${base}/weights`, {
      method: "POST",
      body: { skcs, weight_kg: weightKg },
    }),
  deleteItems: (skcs: string[]) =>
    ppRequest<{ deleted: string[]; sources: number; declared: number; images: number }>(
      context(),
      `${base}/items/delete`,
      { method: "POST", body: { skcs } },
    ),
  listSites: async () =>
    (await ppRequest<{ sites: Array<{ site_code: string; display_name: string }> }>(context(), `${base}/sites`)).sites,
  enterLibrary: (skc: string, site: string, sourceId = "") =>
    ppRequest<{ library: unknown; profit: LedgerProfit; site_code: string }>(
      context(),
      `${base}/items/${encodeURIComponent(skc)}/library`,
      { method: "POST", body: { site, source_id: sourceId } },
    ),
  leaveLibrary: (skc: string, site: string) =>
    ppRequest<{ deleted: boolean }>(
      context(),
      `${base}/items/${encodeURIComponent(skc)}/library?site=${encodeURIComponent(site)}`,
      { method: "DELETE" },
    ),
};
