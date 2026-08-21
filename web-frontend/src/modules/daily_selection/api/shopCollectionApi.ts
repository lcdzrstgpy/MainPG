import { apiRequest } from "../../../shared/api/apiClient";
import type { ShopCollectionBatch, ShopCollectionItemsPage } from "../data/shopCollectionModel";

const SHOP_BATCHES_PATH = "/desktop/data-collection/shop-batches";

export type ShopCollectionBatchPage = {
  items: ShopCollectionBatch[];
  total: number;
};

export function listShopCollectionBatches(limit = 30, offset = 0): Promise<ShopCollectionBatchPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiRequest(`${SHOP_BATCHES_PATH}?${query}`);
}

export function createShopCollectionBatch(sourceInput: string): Promise<ShopCollectionBatch> {
  return apiRequest(SHOP_BATCHES_PATH, {
    method: "POST",
    body: JSON.stringify({ source_input: sourceInput }),
  });
}

export function getShopCollectionBatch(batchId: string): Promise<ShopCollectionBatch> {
  return apiRequest(`${SHOP_BATCHES_PATH}/${encodeURIComponent(batchId)}`);
}

export function listShopCollectionItems(batchId: string, limit = 20, offset = 0): Promise<ShopCollectionItemsPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiRequest(`${SHOP_BATCHES_PATH}/${encodeURIComponent(batchId)}/items?${query}`);
}

function batchAction(batchId: string, action: "pause" | "resume" | "cancel" | "retry-failed"): Promise<ShopCollectionBatch> {
  return apiRequest(`${SHOP_BATCHES_PATH}/${encodeURIComponent(batchId)}/${action}`, { method: "POST" });
}

export const shopCollectionApi = {
  listBatches: listShopCollectionBatches,
  createBatch: createShopCollectionBatch,
  getBatch: getShopCollectionBatch,
  listItems: listShopCollectionItems,
  pause: (batchId: string) => batchAction(batchId, "pause"),
  resume: (batchId: string) => batchAction(batchId, "resume"),
  cancel: (batchId: string) => batchAction(batchId, "cancel"),
  retryFailed: (batchId: string) => batchAction(batchId, "retry-failed"),
};

