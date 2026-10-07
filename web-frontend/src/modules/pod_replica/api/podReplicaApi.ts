import { getAuthToken, httpJson, toUserMessage } from "../../../transport/http/client";
import type {
  CreateReplicaBatchRequest,
  ReplicaBatch,
  ReplicaBatchListResponse,
  ReplicaImageRole,
  ReplicaImageUploadResponse,
} from "../types";

const API_BASE = "/api/pod-customization";

function apiUrl(path: string): string {
  return `${(import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")}${path}`;
}

/** 复刻图片上传：一次一图，multipart `file` + `role=source|target`。 */
async function uploadImage(file: File, role: ReplicaImageRole): Promise<ReplicaImageUploadResponse> {
  const headers: Record<string, string> = {};
  const token = getAuthToken();
  if (token) headers.authorization = `Bearer ${token}`;
  const form = new FormData();
  form.append("file", file);
  form.append("role", role);
  const response = await fetch(apiUrl(`${API_BASE}/replica/images`), { method: "POST", headers, body: form });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof payload?.detail === "string" ? payload.detail : `图片上传失败 (HTTP ${response.status})`;
    throw new Error(toUserMessage(detail));
  }
  return payload as ReplicaImageUploadResponse;
}

/**
 * 草稿恢复时校验资产是否仍存在：资产是服务端内容寻址存储，本地只存 asset_id。
 * 缺失时前端要求重新上传；这里仅探测响应状态，不消费响应体。
 */
async function assetExists(assetId: string): Promise<boolean> {
  const headers: Record<string, string> = {};
  const token = getAuthToken();
  if (token) headers.authorization = `Bearer ${token}`;
  try {
    const response = await fetch(apiUrl(`${API_BASE}/assets/${encodeURIComponent(assetId)}`), { headers });
    if (!response.ok) return false;
    await response.body?.cancel();
    return true;
  } catch {
    return false;
  }
}

export const podReplicaApi = {
  uploadImage,
  createBatch: (body: CreateReplicaBatchRequest) => httpJson<ReplicaBatch>(`${API_BASE}/replica/batches`, {
    method: "POST",
    body,
  }),
  listBatches: (limit = 20, offset = 0) => httpJson<ReplicaBatchListResponse>(
    `${API_BASE}/replica/batches?${new URLSearchParams({ limit: String(limit), offset: String(offset) })}`,
  ),
  getBatch: (batchId: string) => httpJson<ReplicaBatch>(`${API_BASE}/replica/batches/${encodeURIComponent(batchId)}`),
  assetExists,
};