import { cfJson, type CfInit } from "../clipforgeClient";
import type { TaskCenterSnapshot } from "../types";

/** GET /api/tasks —— 全局任务中心快照（运行中 / 待处理 / 最近成片）。 */
export function getTaskCenter(init?: CfInit) {
  return cfJson<TaskCenterSnapshot>("/api/tasks", init);
}
