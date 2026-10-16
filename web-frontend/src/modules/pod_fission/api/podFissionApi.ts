import { getAuthToken, httpJson, toUserMessage } from "../../../transport/http/client";
import type { AnalyzeFissionAssetsRequest, CreateFissionBatchRequest, FissionAnalysisResponse, FissionBatch, FissionBatchListResponse, FissionImageRole, FissionImageUploadResponse, FissionStyleRetryResponse } from "../types";
const API_BASE = "/api/pod-customization/fission"; // analysis_id is returned by analyze and required to create a batch
function apiUrl(path: string) { return `${(import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "")}${path}`; }
const FISSION_IMAGE_ROLES: readonly FissionImageRole[] = ["source_artwork", "target_product"];

async function uploadImage(file: File, role: FissionImageRole): Promise<FissionImageUploadResponse> {
  if (!FISSION_IMAGE_ROLES.includes(role)) throw new Error("图片角色不受支持");
  const form = new FormData(); form.append("file", file); form.append("role", role);
  const headers: Record<string,string> = {}; const token = getAuthToken(); if (token) headers.authorization = `Bearer ${token}`;
  const response = await fetch(apiUrl(`${API_BASE}/images`), { method: "POST", headers, body: form });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(toUserMessage(typeof payload?.detail === "string" ? payload.detail : `图片上传失败 (HTTP ${response.status})`));
  return payload as FissionImageUploadResponse;
}
export const podFissionApi = {
  uploadImage,
  analyzeAssets: (body: AnalyzeFissionAssetsRequest) => httpJson<FissionAnalysisResponse>(`${API_BASE}/analyze`, { method: "POST", body, timeoutMs: 300_000 }),
  createBatch: (body: CreateFissionBatchRequest) => httpJson<FissionBatch>(`${API_BASE}/batches`, { method: "POST", body }),
  listBatches: (limit = 20, offset = 0) => httpJson<FissionBatchListResponse>(`${API_BASE}/batches?${new URLSearchParams({limit:String(limit),offset:String(offset)})}`),
  getBatch: (id: string) => httpJson<FissionBatch>(`${API_BASE}/batches/${encodeURIComponent(id)}`),
  retryStyle: (batchId: string, styleIndex: number) => httpJson<FissionStyleRetryResponse>(`${API_BASE}/batches/${encodeURIComponent(batchId)}/styles/${styleIndex}/retry`, { method: "POST" }),
  assetExists: async (assetId: string) => { try { const token = getAuthToken(); if (!token || !assetId) return false; const response = await fetch(apiUrl(`/api/pod-customization/assets/${encodeURIComponent(assetId)}`), { headers: { authorization: `Bearer ${token}` } }); return response.ok; } catch { return false; } },
  exportDianxiaomi: async (batchId: string) => {
    const token = getAuthToken();
    const response = await fetch(apiUrl(`/api/pod-customization/batches/${encodeURIComponent(batchId)}/exports/dianxiaomi`), { headers: token ? { authorization: `Bearer ${token}` } : {} });
    if (!response.ok) throw new Error(`导出失败 (HTTP ${response.status})`);
    return { blob: await response.blob(), filename: response.headers.get("content-disposition") ?? "pod-fission-export.xlsx" };
  },
};
