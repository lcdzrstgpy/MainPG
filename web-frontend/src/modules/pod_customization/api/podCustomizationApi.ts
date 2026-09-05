import { getAuthToken, httpBlob, httpJson } from "../../../transport/http/client";
import { parseDianxiaomiExportFilename, parseDianxiaomiExportHeaderCount } from "../data/dianxiaomiExport";
import { podStyleTitleRegenerateRequest } from "../data/styleTitleRequest";
import type { PodBatchRetryRequest } from "../data/podBatchRetry";
import type {
  CreatePodBatchRequest,
  PodBatch,
  PodBatchItem,
  PodBatchListResponse,
  PodStyleTitle,
  PodTemplate,
  PodTemplateCalibration,
} from "../types";

const API_BASE = "/api/pod-customization";

function apiUrl(path: string): string {
  return `${(import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")}${path}`;
}

async function uploadTemplate(file: File, name: string): Promise<PodTemplate> {
  const headers: Record<string, string> = {};
  const token = getAuthToken();
  if (token) headers.authorization = `Bearer ${token}`;
  const form = new FormData();
  form.append("file", file);
  form.append("name", name.trim() || file.name.replace(/\.[^.]+$/, ""));
  const response = await fetch(apiUrl(`${API_BASE}/templates`), { method: "POST", headers, body: form });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload?.detail === "string" ? payload.detail : `模板上传失败 (HTTP ${response.status})`);
  }
  return payload as PodTemplate;
}

function triggerBlobDownload(blob: Blob, filename: string): void {
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

async function downloadAsset(path: string, filename: string): Promise<void> {
  const blob = /^https?:\/\//i.test(path)
    ? await fetch(path).then((response) => {
      if (!response.ok) throw new Error(`下载失败 (HTTP ${response.status})`);
      return response.blob();
    })
    : await httpBlob(path);
  triggerBlobDownload(blob, filename);
}

export type PodDianxiaomiExportDownload = {
  exportedStyles: number;
  skippedStyles: number;
  filename: string;
};

export type PodBatchRetryResult = {
  image_style_indices: number[];
  title_style_indices: number[];
};

function saveBlob(blob: Blob, filename: string): void {
  triggerBlobDownload(blob, filename);
}

async function exportDianxiaomi(batchId: string): Promise<PodDianxiaomiExportDownload> {
  const headers: Record<string, string> = {};
  const token = getAuthToken();
  if (token) headers.authorization = `Bearer ${token}`;
  const response = await fetch(apiUrl(`${API_BASE}/batches/${encodeURIComponent(batchId)}/exports/dianxiaomi`), { headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = typeof payload?.detail === "string" ? payload.detail : `导出失败 (HTTP ${response.status})`;
    throw new Error(detail);
  }
  const filename = parseDianxiaomiExportFilename(response.headers.get("content-disposition"), `pod-${batchId}-dianxiaomi.xlsx`);
  saveBlob(await response.blob(), filename);
  return {
    exportedStyles: parseDianxiaomiExportHeaderCount(response.headers.get("x-pod-exported-styles")),
    skippedStyles: parseDianxiaomiExportHeaderCount(response.headers.get("x-pod-skipped-styles")),
    filename,
  };
}

export const podCustomizationApi = {
  listTemplates: () => httpJson<{ templates: PodTemplate[] }>(`${API_BASE}/templates`),
  uploadTemplate,
  calibrateTemplate: (templateId: string) => httpJson<PodTemplate>(
    `${API_BASE}/templates/${encodeURIComponent(templateId)}/calibrate`,
    { method: "POST", body: {} },
  ),
  saveTemplateCalibration: (templateId: string, calibration: PodTemplateCalibration) => httpJson<PodTemplate>(
    `${API_BASE}/templates/${encodeURIComponent(templateId)}/calibration`,
    { method: "PATCH", body: { calibration } },
  ),
  listBatches: (limit = 20, offset = 0) => httpJson<PodBatchListResponse>(
    `${API_BASE}/batches?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`,
  ),
  createBatch: (body: CreatePodBatchRequest) => httpJson<PodBatch>(`${API_BASE}/batches`, {
    method: "POST",
    body,
  }),
  getBatch: (batchId: string) => httpJson<PodBatch>(`${API_BASE}/batches/${encodeURIComponent(batchId)}`),
  pauseBatch: (batchId: string) => httpJson<PodBatch>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/pause`,
    { method: "POST", body: {} },
  ),
  cancelBatch: (batchId: string) => httpJson<PodBatch>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/cancel`,
    { method: "POST", body: {} },
  ),
  deleteBatch: (batchId: string) => httpJson<{ deleted: string }>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}`,
    { method: "DELETE" },
  ),
  resumeBatch: (batchId: string) => httpJson<PodBatch>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/resume`,
    { method: "POST", body: {} },
  ),
  regenerateStyle: (batchId: string, styleIndex: number, creativePrompt?: string) => httpJson<{ style_index: number; results: PodBatchItem[] }>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/styles/${styleIndex}/regenerate`,
    { method: "POST", body: creativePrompt?.trim() ? { creative_prompt: creativePrompt.trim() } : {} },
  ),
  regenerateStyleTitle: (batchId: string, styleIndex: number) => {
    const request = podStyleTitleRegenerateRequest(batchId, styleIndex);
    return httpJson<PodStyleTitle>(request.path, request.options);
  },
  updateManualTitle: (batchId: string, styleIndex: number, title: string) => httpJson<PodStyleTitle>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/styles/${styleIndex}/title`,
    { method: "PATCH", body: { title } },
  ),
  updateExportSelection: (batchId: string, styleIndex: number, selected: boolean) => httpJson<{ style_index: number; export_selected: boolean }>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/styles/${styleIndex}/export-selection`,
    { method: "PATCH", body: { selected } },
  ),
  retryFailed: (batchId: string, body: PodBatchRetryRequest) => httpJson<PodBatchRetryResult>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/retry-failed`,
    { method: "POST", body },
  ),
  exportDianxiaomi,
  downloadAsset,
};
