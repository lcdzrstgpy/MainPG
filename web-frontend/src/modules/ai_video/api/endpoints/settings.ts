import { apiRequest } from "../../../../shared/api/apiClient";
import { cfJson, type CfInit } from "../clipforgeClient";
import type { ClipForgeDefaults, LlmModelsResult, LlmTestInput, LlmTestResult } from "../types";

/**
 * GET /api/clipforge/defaults —— 由 MainPG 后端读取 `clipforge.local.json`，
 * 不是 sidecar 路由，因此不能走 cfJson（避免被代理前缀二次拼接）。
 */
export function getClipForgeDefaults(signal?: AbortSignal) {
  return apiRequest<ClipForgeDefaults>("/api/clipforge/defaults", { signal });
}

/** POST /api/llm/test —— 服务端探测 LLM 连接（浏览器直连会被 CORS 拦）。 */
export function testLlm(input: LlmTestInput, init?: CfInit) {
  return cfJson<LlmTestResult>("/api/llm/test", { ...init, method: "POST", json: input });
}

/** POST /api/llm/models —— 列出 OpenAI 兼容端点的模型（本地 Ollama 的 tag 问题靠它兜底）。 */
export function listLlmModels(input: { baseUrl: string; apiKey?: string }, init?: CfInit) {
  return cfJson<LlmModelsResult>("/api/llm/models", { ...init, method: "POST", json: input });
}
