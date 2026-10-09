import { getAuthToken, httpBlob, httpJson } from "../../../transport/http/client";
import type { CreateSemiBatchRequest, SemiBatch, SemiBatchListResponse } from "../types";

const API_BASE = "/api/pod-customization/semi";

function apiUrl(path: string): string {
  return `${(import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")}${path}`;
}

/**
 * 从 Content-Disposition 取文件名。
 * 服务端按 RFC 5987 传了中文名（filename*=UTF-8''…），优先用它；
 * 取不到再退回 ASCII 的 filename=。
 */
export function parseZipFilename(disposition: string, fallback: string): string {
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition ?? "")?.[1];
  if (utf8) {
    try {
      return decodeURIComponent(utf8.trim());
    } catch {
      // 编码异常时退回下面的 ASCII 分支。
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition ?? "")?.[1];
  return plain?.trim() || fallback;
}

async function downloadZip(batchId: string): Promise<{ filename: string; blob: Blob }> {
  const headers: Record<string, string> = {};
  const token = getAuthToken();
  if (token) headers.authorization = `Bearer ${token}`;
  const response = await fetch(apiUrl(`${API_BASE}/batches/${encodeURIComponent(batchId)}/download`), { headers });
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    const detail = typeof payload?.detail === "string" ? payload.detail : `下载失败 (HTTP ${response.status})`;
    throw new Error(detail);
  }
  const filename = parseZipFilename(
    response.headers.get("content-disposition") ?? "",
    `pod-semi-${batchId.slice(0, 8)}.zip`,
  );
  return { filename, blob: await response.blob() };
}

export const podSemiCustomizationApi = {
  listBatches: (limit = 20, offset = 0) => httpJson<SemiBatchListResponse>(
    `${API_BASE}/batches?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`,
  ),
  createBatch: (body: CreateSemiBatchRequest) => httpJson<SemiBatch>(`${API_BASE}/batches`, {
    method: "POST",
    body,
  }),
  getBatch: (batchId: string) => httpJson<SemiBatch>(`${API_BASE}/batches/${encodeURIComponent(batchId)}`),
  pauseBatch: (batchId: string) => httpJson<SemiBatch>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/pause`,
    { method: "POST", body: {} },
  ),
  cancelBatch: (batchId: string) => httpJson<SemiBatch>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/cancel`,
    { method: "POST", body: {} },
  ),
  resumeBatch: (batchId: string) => httpJson<SemiBatch>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/resume`,
    { method: "POST", body: {} },
  ),
  deleteBatch: (batchId: string) => httpJson<{ deleted: string }>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}`,
    { method: "DELETE" },
  ),
  regenerateGroup: (batchId: string, groupIndex: number, creativePrompt?: string) => httpJson<{ style_index: number }>(
    `${API_BASE}/batches/${encodeURIComponent(batchId)}/styles/${groupIndex}/regenerate`,
    { method: "POST", body: creativePrompt?.trim() ? { creative_prompt: creativePrompt.trim() } : {} },
  ),
  downloadZip,
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

export function blobForPath(path: string): Promise<Blob> {
  return /^https?:\/\//i.test(path) ? fetch(path).then((r) => {
    if (!r.ok) throw new Error(`下载失败 (HTTP ${r.status})`);
    return r.blob();
  }) : httpBlob(path);
}
