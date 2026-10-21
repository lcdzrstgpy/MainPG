import { cfJson, type CfInit } from "../clipforgeClient";
import type {
  AiVideoRecoverable,
  AiVideoResult,
  AiVideoSubmitInput,
  AiVideoTaskQueryInput,
  AiVideoTaskQueryResult,
  AiTask,
} from "../types";

/**
 * GET /api/ai/tasks
 * - `{ projectId }`：该项目的历史任务
 * - `{ projectId, active: true }`：该项目仍需恢复的任务
 * - `{ active: true }`：跨项目仍需恢复的付费任务
 */
export function listAiTasks(
  query: { projectId?: string; active?: boolean } = {},
  init?: CfInit,
) {
  const params = new URLSearchParams();
  if (query.projectId) params.set("projectId", query.projectId);
  if (query.active) params.set("active", "1");
  const suffix = params.size ? `?${params.toString()}` : "";
  return cfJson<AiTask[]>(`/api/ai/tasks${suffix}`, init);
}

/** 提交付费生视频；超时返回带 taskId + recoverable 的结果，只能恢复不能重提。 */
export function submitVideoTask(input: AiVideoSubmitInput, init?: CfInit) {
  return cfJson<AiVideoResult | AiVideoRecoverable>("/api/ai/video", {
    ...init,
    method: "POST",
    json: input,
  });
}

/** 按供应商 taskId 查询/恢复；wait=true 时等待终态。 */
export function queryVideoTask(input: AiVideoTaskQueryInput, init?: CfInit) {
  return cfJson<AiVideoTaskQueryResult | AiVideoRecoverable>("/api/ai/video/task", {
    ...init,
    method: "POST",
    json: input,
  });
}
