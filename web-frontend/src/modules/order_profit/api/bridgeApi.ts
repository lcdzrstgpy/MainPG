import { productProcessingApiContext } from "../../product_processing/api/context";
import { ppRequest } from "../../product_processing/api/client";

const BASE = "/api/bridge";

export type BridgeBinding = {
  account_id: string;
  shop_name: string;
  phone_tail: string;
  site: string;
  bound_at: string;
};

export type BridgeItem = {
  account_id: string;
  shop_name: string;
  skc_id: string;
  sku_id: string;
  sku_count: number;
  goods_name: string;
  image_url: string;
  order_count: number;
  quantity: number;
  declared_price: number;
  gmv: number;
  currency: string;
  order_status: string;
  last_order_time: string;
  unit_cost: number | null;
  cost_currency: string;
  cost_updated_at: string;
};

export type BridgeItemsResponse = {
  items: BridgeItem[];
  total: number;
  page: number;
  page_size: number;
  scanned: number;
  skipped_no_skc: number;
};

export type BridgeCost = {
  account_id: string;
  skc_id: string;
  unit_cost: number;
  currency: string;
  updated_by: string;
  updated_at: string;
};

type ItemsParams = {
  account_ids?: string;
  status?: string;
  date_from?: string;
  date_to?: string;
  keyword?: string;
  page?: number;
  page_size?: number;
};

function queryString(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "" || value === null) continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export const bridgeApi = {
  status: () =>
    ppRequest<{ configured: boolean; client_id: string }>(
      productProcessingApiContext(),
      `${BASE}/status`,
    ),
  bindings: () =>
    ppRequest<{ client_id: string; accounts: BridgeBinding[] }>(
      productProcessingApiContext(),
      `${BASE}/bindings`,
    ),
  bind: (phone: string, password: string) =>
    ppRequest<{
      account_id: string;
      shop_name: string;
      phone_tail: string;
      site: string;
      already_bound: boolean;
    }>(productProcessingApiContext(), `${BASE}/bindings`, {
      method: "POST",
      body: { phone, password },
    }),
  unbind: (accountId: string) =>
    ppRequest<{ removed: number }>(
      productProcessingApiContext(),
      `${BASE}/bindings/${encodeURIComponent(accountId)}`,
      { method: "DELETE" },
    ),
  items: (params: ItemsParams) =>
    ppRequest<BridgeItemsResponse>(
      productProcessingApiContext(),
      `${BASE}/items${queryString({ ...params })}`,
    ),
  costs: () =>
    ppRequest<{ items: BridgeCost[] }>(
      productProcessingApiContext(),
      `${BASE}/costs`,
    ),
  saveCosts: (
    accountId: string,
    items: { skc_id: string; unit_cost: number; currency: string }[],
  ) =>
    ppRequest<{ applied: number; skc_ids: string[]; skipped: { skc_id: string; reason: string }[] }>(
      productProcessingApiContext(),
      `${BASE}/costs`,
      { method: "POST", body: { account_id: accountId, items } },
    ),
};
