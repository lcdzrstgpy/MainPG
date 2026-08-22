import { getAuthToken, httpBlob, httpJson, toUserMessage } from "../../../transport/http/client";
import { parseDianxiaomiExportFilename, parseDianxiaomiExportHeaderCount } from "../data/dianxiaomiExport";
import { podStyleTitleRegenerateRequest } from "../data/styleTitleRequest";
import { podBillingPendingRequest, podBillingResumeRequest } from "../data/billingRuns";
import type {
  CreatePodBatchRequest,
  PodBatch,
  PodBatchItem,
  PodBatchListResponse,
  PodBillingRun,
  PodBillingRunListResponse,
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
    throw new Error(toUserMessage(typeof payload?.detail === "string" ? payload.detail : `模板上传失败（状态码 ${response.status}）`));
  }
  return payload as PodTemplate;
}

async function downloadAsset(path: string, filename: string): Promise<void> {
  const blob = /^https?:\/\//i.test(path)
    ? await fetch(path).then((response) => {
      if (!response.ok) throw new Error(`下载失败（状态码 ${response.status}），请稍后重试`);
      return response.blob();
    })
    : await httpBlob(path);
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}

export type PodDianxiaomiExportDownload = {
  exportedStyles: number;
  skippedStyles: number;
  filename: string;
};

function saveBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
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
  regenerateStyle: (batchId: string, styleIndex: number, creativePrompt?: string, ackPaidRetry = false) => httpJson<{ style_index: number; results: PodBatchItem[] }>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/styles/${styleIndex}/regenerate`,
    { method: "POST", body: { ...(creativePrompt?.trim() ? { creative_prompt: creativePrompt.trim() } : {}), ack_paid_retry: ackPaidRetry } },
  ),
  regenerateStyleTitle: (batchId: string, styleIndex: number, ackPaidRetry = false) => {
    const request = podStyleTitleRegenerateRequest(batchId, styleIndex, ackPaidRetry);
    return httpJson<PodStyleTitle>(request.path, request.options);
  },
  listPendingBillingRuns: () => {
    const request = podBillingPendingRequest();
    return httpJson<PodBillingRunListResponse>(request.path);
  },
  resumeBillingRun: (runId: string) => {
    const request = podBillingResumeRequest(runId);
    return httpJson<PodBillingRun>(request.path, request.options);
  },
  exportDianxiaomi,
  downloadAsset,
};
