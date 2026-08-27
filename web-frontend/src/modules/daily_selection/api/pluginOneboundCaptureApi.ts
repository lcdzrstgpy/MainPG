import { apiRequest } from "../../../shared/api/apiClient";
import type {
  PluginOneboundCaptureBatch,
  PluginOneboundCaptureBatchPage,
  PluginOneboundCaptureItemsPage,
  PluginOneboundCandidatesPage,
  PluginOneboundConfirmResult,
  PluginOneboundSkuRepullState,
} from "../data/pluginOneboundCaptureModel";

const PLUGIN_BATCHES_PATH = "/desktop/data-collection/plugin-onebound-batches";

export function listPluginOneboundCaptureBatches(limit = 30, offset = 0): Promise<PluginOneboundCaptureBatchPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiRequest(`${PLUGIN_BATCHES_PATH}?${query}`);
}

export async function getPluginOneboundCaptureBatch(batchId: string): Promise<PluginOneboundCaptureBatch> {
  const response = await apiRequest<{ batch: PluginOneboundCaptureBatch }>(
    `${PLUGIN_BATCHES_PATH}/${encodeURIComponent(batchId)}`,
  );
  return response.batch;
}

export function listPluginOneboundCaptureItems(batchId: string, limit = 80, offset = 0): Promise<PluginOneboundCaptureItemsPage> {
  const query = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  return apiRequest(`${PLUGIN_BATCHES_PATH}/${encodeURIComponent(batchId)}/items?${query}`);
}

export async function startPluginOneboundCaptureBatch(batchId: string): Promise<PluginOneboundCaptureBatch> {
  const response = await apiRequest<{ batch: PluginOneboundCaptureBatch }>(
    `${PLUGIN_BATCHES_PATH}/${encodeURIComponent(batchId)}/start`,
    { method: "POST" },
  );
  return response.batch;
}

export async function retryPluginOneboundCaptureFailures(batchId: string): Promise<PluginOneboundCaptureBatch> {
  const response = await apiRequest<{ batch: PluginOneboundCaptureBatch }>(
    `${PLUGIN_BATCHES_PATH}/${encodeURIComponent(batchId)}/retry-failed`,
    { method: "POST" },
  );
  return response.batch;
}

export function listPluginOneboundCandidates(batchId: string): Promise<PluginOneboundCandidatesPage> {
  return apiRequest(`${PLUGIN_BATCHES_PATH}/${encodeURIComponent(batchId)}/candidates`);
}

export function getPluginSkuRepullState(batchId: string): Promise<PluginOneboundSkuRepullState> {
  return apiRequest(`${PLUGIN_BATCHES_PATH}/${encodeURIComponent(batchId)}/sku-repull/state`);
}

export function startPluginSkuRepull(batchId: string): Promise<PluginOneboundSkuRepullState> {
  return apiRequest(`${PLUGIN_BATCHES_PATH}/${encodeURIComponent(batchId)}/sku-repull/start`, { method: "POST" });
}

export function cancelPluginSkuRepull(batchId: string): Promise<PluginOneboundSkuRepullState> {
  return apiRequest(`${PLUGIN_BATCHES_PATH}/${encodeURIComponent(batchId)}/sku-repull/cancel`, { method: "POST" });
}

export function confirmPluginCandidates(batchId: string, offerIds: string[]): Promise<PluginOneboundConfirmResult> {
  return apiRequest(`${PLUGIN_BATCHES_PATH}/${encodeURIComponent(batchId)}/confirm`, {
    method: "POST",
    body: JSON.stringify({ offer_ids: offerIds }),
  });
}

export const pluginOneboundCaptureApi = {
  listBatches: listPluginOneboundCaptureBatches,
  getBatch: getPluginOneboundCaptureBatch,
  listItems: listPluginOneboundCaptureItems,
  startBatch: startPluginOneboundCaptureBatch,
  retryFailed: retryPluginOneboundCaptureFailures,
  listCandidates: listPluginOneboundCandidates,
  getSkuRepullState: getPluginSkuRepullState,
  startSkuRepull: startPluginSkuRepull,
  cancelSkuRepull: cancelPluginSkuRepull,
  confirmCandidates: confirmPluginCandidates,
};
