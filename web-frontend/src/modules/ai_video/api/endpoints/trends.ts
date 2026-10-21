import { cfJson, type CfInit } from "../clipforgeClient";
import type { TrendsResult } from "../types";

/**
 * GET /api/trends
 * - 缺省 / source=cn：抖音热搜（头条兜底）
 * - geo=US 等：Google Trends（海外/英文内容）
 */
export function getTrends(
  query: { source?: "cn"; geo?: string; limit?: number } = {},
  init?: CfInit,
) {
  const params = new URLSearchParams();
  if (query.source) params.set("source", query.source);
  if (query.geo) params.set("geo", query.geo);
  if (query.limit) params.set("limit", String(query.limit));
  const suffix = params.size ? `?${params.toString()}` : "";
  return cfJson<TrendsResult>(`/api/trends${suffix}`, init);
}
