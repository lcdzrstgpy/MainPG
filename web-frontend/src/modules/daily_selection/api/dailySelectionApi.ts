import type {
  DailySelectionCriteria,
  DailySelectionHandoff,
  DailySelectionRun,
  DailySelectionRunSummary,
} from "../types";

const apiToken = import.meta.env.VITE_WH_API_TOKEN || "dev-admin-token";

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      Authorization: `Bearer ${apiToken}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let message = `请求失败（${response.status}）`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") message = payload.detail;
    } catch {
      // Keep the status-based fallback when the response is not JSON.
    }
    throw new Error(message);
  }

  return response.json() as Promise<T>;
}

export function collectByCriteria(criteria: DailySelectionCriteria): Promise<DailySelectionRun> {
  return apiRequest("/desktop/daily-selection/preview", {
    method: "POST",
    body: JSON.stringify(criteria),
  });
}

export function listSelectionRuns(): Promise<DailySelectionRunSummary[]> {
  return apiRequest("/desktop/daily-selection/runs");
}

export function getSelectionRun(runId: string): Promise<DailySelectionRun> {
  return apiRequest(`/desktop/daily-selection/runs/${encodeURIComponent(runId)}`);
}

export function confirmCandidates(runId: string, candidateIds: string[]): Promise<DailySelectionHandoff[]> {
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
