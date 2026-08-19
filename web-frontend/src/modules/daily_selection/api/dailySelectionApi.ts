import type {
  DailySelectionCriteria,
  DailySelectionConfirmResult,
  DailySelectionRun,
  DailySelectionRunSummary,
  DailySelectionTaskStatus,
  SkuRepullState,
} from "../types";
import { apiRequest } from "../../../shared/api/apiClient";

export function collectByCriteria(criteria: DailySelectionCriteria): Promise<DailySelectionRun> {
  return apiRequest("/desktop/daily-selection/preview", {
    method: "POST",
    body: JSON.stringify(criteria),
  });
}

export function startCollectionTask(criteria: DailySelectionCriteria): Promise<DailySelectionTaskStatus> {
  return apiRequest("/desktop/daily-selection/preview-tasks", {
    method: "POST",
    body: JSON.stringify(criteria),
  });
}

export function getCollectionTask(taskId: string): Promise<DailySelectionTaskStatus> {
  return apiRequest(`/desktop/daily-selection/preview-tasks/${encodeURIComponent(taskId)}`);
}

export function listSelectionRuns(): Promise<DailySelectionRunSummary[]> {
  return apiRequest("/desktop/daily-selection/runs");
}

export function getSelectionRun(runId: string): Promise<DailySelectionRun> {
  return apiRequest(`/desktop/daily-selection/runs/${encodeURIComponent(runId)}`);
}

export function confirmCandidates(runId: string, candidateIds: string[]): Promise<DailySelectionConfirmResult> {
  return apiRequest(`/desktop/daily-selection/runs/${encodeURIComponent(runId)}/confirm`, {
    method: "POST",
    body: JSON.stringify({ candidate_ids: candidateIds }),
  });
}

export function rejectCandidate(runId: string, candidateId: string, reason: string): Promise<unknown> {
  return apiRequest(`/desktop/daily-selection/runs/${encodeURIComponent(runId)}/feedback`, {
    method: "POST",
    body: JSON.stringify({ candidate_id: candidateId, reason, details: {} }),
  });
}

export function startSkuRepull(runId: string): Promise<SkuRepullState> {
  return apiRequest(`/desktop/daily-selection/runs/${encodeURIComponent(runId)}/sku-repull/start`, {
    method: "POST",
  });
}

export function getSkuRepullState(runId: string): Promise<SkuRepullState> {
  return apiRequest(`/desktop/daily-selection/runs/${encodeURIComponent(runId)}/sku-repull/state`);
}

export function cancelSkuRepull(runId: string): Promise<SkuRepullState> {
  return apiRequest(`/desktop/daily-selection/runs/${encodeURIComponent(runId)}/sku-repull/cancel`, {
    method: "POST",
  });
}
