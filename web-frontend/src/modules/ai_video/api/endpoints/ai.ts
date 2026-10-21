import { cfJson, cfUrl, type CfInit } from "../clipforgeClient";
import type { AiModel, AiProviderCatalogStatus, ProviderTestResult, SidecarHealthSnapshot } from "../types";

/** POST /api/ai/models —— 模型目录（可 refresh 强制刷新上游）。 */
export function listAiModels(
  input: { providers: Array<{ name: string; apiKey?: string; baseUrl?: string }>; mediaType?: "image" | "video"; refresh?: boolean },
  init?: CfInit,
) {
  return cfJson<{ models: AiModel[]; providers: AiProviderCatalogStatus[] }>("/api/ai/models", {
    ...init,
    method: "POST",
    json: input,
  });
}

/** POST /api/ai/test-provider —— 只读鉴权探针，不产生计费生成。 */
export function testAiProvider(
  input: { name: string; apiKey: string; baseUrl?: string },
  init?: CfInit,
) {
  return cfJson<ProviderTestResult>("/api/ai/test-provider", {
    ...init,
    method: "POST",
    json: input,
  });
}

/**
 * GET /api/health —— sidecar 健康快照。
 * 503 也带完整诊断体，因此这里不用 cfJson（它会把非 2xx 当错误抛出）。
 */
export async function getSidecarHealth(signal?: AbortSignal): Promise<SidecarHealthSnapshot> {
  const response = await fetch(cfUrl("/api/health"), { method: "GET", signal });
  const payload = await response.json().catch(() => null);
  return {
    httpStatus: response.status,
    payload,
  };
}
