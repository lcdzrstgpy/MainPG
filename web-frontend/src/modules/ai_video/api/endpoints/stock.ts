import { cfJson, type CfInit } from "../clipforgeClient";
import type {
  StockFillInput,
  StockFillResult,
  StockSearchInput,
  StockSearchResult,
  StockSourceMeta,
} from "../types";

/** GET /api/stock/search —— 可用素材源（含是否需要 Key）。 */
export function listStockSources(init?: CfInit) {
  return cfJson<{ sources: StockSourceMeta[] }>("/api/stock/search", init);
}

/** POST /api/stock/search —— 检索；download=true 时下载并落库到指定分镜。 */
export function searchStock(input: StockSearchInput, init?: CfInit) {
  return cfJson<StockSearchResult>("/api/stock/search", { ...init, method: "POST", json: input });
}

/** POST /api/project/:id/stock-fill —— 按已选脚本逐镜自动配免费素材。 */
export function fillStock(projectId: string, input: StockFillInput = {}, init?: CfInit) {
  return cfJson<StockFillResult>(`/api/project/${encodeURIComponent(projectId)}/stock-fill`, {
    ...init,
    method: "POST",
    json: input,
  });
}
