import { cfJson, type CfInit } from "../clipforgeClient";
import type { HomogeneityResult } from "../types";

/** GET /api/insights/homogeneity —— 最近项目脚本结构的同质化自检。 */
export function getHomogeneity(limit = 8, init?: CfInit) {
  const safe = Math.max(2, Math.min(20, Math.floor(limit)));
  return cfJson<HomogeneityResult>(`/api/insights/homogeneity?limit=${safe}`, init);
}
