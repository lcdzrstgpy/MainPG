import { cfJson, type CfInit } from "../clipforgeClient";
import type { PipelineRun } from "../types";

/** GET /api/project/:id/pipeline —— 最新一次服务端流水线（executor 消失会被标记 interrupted）。 */
export function getPipelineRun(projectId: string, init?: CfInit) {
  return cfJson<{ run: PipelineRun | null }>(`/api/project/${encodeURIComponent(projectId)}/pipeline`, init);
}

/** POST /api/project/:id/pipeline —— 启动或从断点续跑免费草稿流水线。 */
export function startPipelineRun(
  projectId: string,
  input: { scriptId?: string; llmConfig?: { baseUrl?: string; apiKey?: string; model?: string }; resume?: boolean } = {},
  init?: CfInit,
) {
  return cfJson<{ runId: string; resumed?: boolean; reused?: boolean }>(
    `/api/project/${encodeURIComponent(projectId)}/pipeline`,
    { ...init, method: "POST", json: input },
  );
}
