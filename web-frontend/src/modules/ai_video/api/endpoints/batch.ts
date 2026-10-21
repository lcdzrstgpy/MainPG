import { cfJson, type CfInit } from "../clipforgeClient";
import type {
  BatchItemPatch,
  BatchJobCreated,
  BatchJobCreateInput,
  BatchJobSnapshot,
  BatchJobStatus,
} from "../types";

/** GET /api/batch?jobId=xxx | ?active=1 —— 任务与其条目。 */
export function getBatchJob(query: { jobId?: string; active?: boolean } = {}, init?: CfInit) {
  const params = new URLSearchParams();
  if (query.jobId) params.set("jobId", query.jobId);
  if (query.active) params.set("active", "1");
  const suffix = params.size ? `?${params.toString()}` : "";
  return cfJson<BatchJobSnapshot>(`/api/batch${suffix}`, init);
}

/** 建批量任务；同一时刻只允许一个 running 的任务（服务端会先结算旧的）。 */
export function createBatchJob(input: BatchJobCreateInput, init?: CfInit) {
  return cfJson<BatchJobCreated>("/api/batch", { ...init, method: "POST", json: input });
}

export function updateBatchJob(
  jobId: string,
  status: BatchJobStatus,
  init?: CfInit,
) {
  return cfJson<{ ok: boolean }>("/api/batch", {
    ...init,
    method: "PATCH",
    json: { jobId, status },
  });
}

/** 推进单条状态并回链 project/composition，断点续跑的依据。 */
export function updateBatchItem(itemId: string, patch: BatchItemPatch, init?: CfInit) {
  return cfJson<{ ok: boolean }>("/api/batch", {
    ...init,
    method: "PATCH",
    json: { itemId, patch },
  });
}
