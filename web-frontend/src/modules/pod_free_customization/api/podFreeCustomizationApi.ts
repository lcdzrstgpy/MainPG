import { getAuthToken, httpBlob, httpJson } from "../../../transport/http/client";
import type {
  CreateFreeBatchRequest,
  FreeBatch,
  FreeBatchListResponse,
  FreePanelPromptRequest,
} from "../types";
import type { PodListingFields, SpecCardConfig } from "../../pod_customization/types/index";

const API_BASE = "/api/pod-customization/free";

function apiBaseUrl() {
  return (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
}

async function uploadReference(file: File): Promise<{ asset_id: string; preview_url: string }> {
  const body = new FormData();
  body.append("file", file);
  const headers: Record<string, string> = {};
  const token = getAuthToken();
  if (token) headers.authorization = `Bearer ${token}`;
  const response = await fetch(`${apiBaseUrl()}${API_BASE}/images`, {
    method: "POST",
    headers,
    body,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(
      typeof payload?.detail === "string" ? payload.detail : `上传失败 (HTTP ${response.status})`,
    );
  }
  return response.json();
}

export const podFreeCustomizationApi = {
  uploadReference,
  listBatches: (limit = 20, offset = 0) =>
    httpJson<FreeBatchListResponse>(
      `${API_BASE}/batches?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`,
    ),
  createBatch: (body: CreateFreeBatchRequest) =>
    httpJson<FreeBatch>(`${API_BASE}/batches`, { method: "POST", body }),
  getBatch: (batchId: string) =>
    httpJson<FreeBatch>(`${API_BASE}/batches/${encodeURIComponent(batchId)}`),
  generatePanel: (batchId: string, variantIndex: number, prompt: string) =>
    httpJson<FreeBatch>(
      `${API_BASE}/batches/${encodeURIComponent(batchId)}/panels/${variantIndex}/generate`,
      { method: "POST", body: { prompt } satisfies FreePanelPromptRequest },
    ),
  retryPanel: (batchId: string, variantIndex: number) =>
    httpJson<FreeBatch>(
      `${API_BASE}/batches/${encodeURIComponent(batchId)}/panels/${variantIndex}/retry`,
      { method: "POST", body: {} },
    ),
  reorderPanels: (batchId: string, fromVariant: number, toVariant: number) =>
    httpJson<FreeBatch>(`${API_BASE}/batches/${encodeURIComponent(batchId)}/panels/reorder`, {
      method: "POST",
      body: { from_variant: fromVariant, to_variant: toVariant },
    }),
  updateListingFields: (batchId: string, listingFields: PodListingFields) =>
    httpJson<FreeBatch>(`${API_BASE}/batches/${encodeURIComponent(batchId)}/listing-fields`, {
      method: "PUT",
      body: { listing_fields: listingFields },
    }),
  updateSpecCard: (batchId: string, specCard: SpecCardConfig | null) =>
    httpJson<FreeBatch>(`${API_BASE}/batches/${encodeURIComponent(batchId)}/spec-card`, {
      method: "PUT",
      body: { spec_card: specCard },
    }),
  reprintSpecCard: (batchId: string) =>
    httpJson<FreeBatch>(`${API_BASE}/batches/${encodeURIComponent(batchId)}/spec-card/reprint`, {
      method: "POST",
      body: {},
    }),
  cancelBatch: (batchId: string) =>
    httpJson<FreeBatch>(`${API_BASE}/batches/${encodeURIComponent(batchId)}/cancel`, {
      method: "POST",
      body: {},
    }),
  deleteBatch: (batchId: string) =>
    httpJson<{ deleted: string }>(`${API_BASE}/batches/${encodeURIComponent(batchId)}`, {
      method: "DELETE",
    }),
  updateTitle: (batchId: string, title: string) =>
    httpJson<unknown>(
      `/api/pod-customization/batches/${encodeURIComponent(batchId)}/styles/1/title`,
      { method: "PATCH", body: { title } },
    ),
  regenerateTitle: (batchId: string) =>
    httpJson<unknown>(
      `/api/pod-customization/batches/${encodeURIComponent(batchId)}/styles/1/title/regenerate`,
      { method: "POST", body: {} },
    ),
  exportDianxiaomi: (batchId: string) =>
    httpBlob(`/api/pod-customization/batches/${encodeURIComponent(batchId)}/exports/dianxiaomi`),
  exportMiaoshou: (batchId: string, kind: "apparel" | "general") =>
    httpBlob(
      `/api/pod-customization/batches/${encodeURIComponent(batchId)}/exports/miaoshou?kind=${kind}`,
    ),
};

export function downloadBlobAs(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);
}
