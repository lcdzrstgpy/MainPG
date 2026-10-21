import { cfJson, type CfInit } from "../clipforgeClient";

export interface AiImageInput {
  provider: string;
  model: string;
  prompt: string;
  apiKey: string;
  baseUrl?: string;
  projectId?: string;
  mode?: string;
  imageUrl?: string;
  imageUrls?: string[];
  options?: Record<string, unknown>;
}

export interface AiImageResult {
  taskId?: string;
  imageUrls: string[];
  modelId?: string;
  duration?: number;
  seed?: number;
}

/** POST /api/ai/image —— 文生图 / 图生图（带 projectId 时受出片策略门禁约束）。 */
export function generateAiImage(input: AiImageInput, init?: CfInit) {
  return cfJson<AiImageResult>("/api/ai/image", { ...init, method: "POST", json: input });
}
