import { cfJson, type CfInit } from "../clipforgeClient";
import type {
  GenerationReview,
  MasteringAnalysis,
  MasteringResult,
  ProductionOverview,
  ProductionPatch,
  QualityOverview,
  VideoRepairPreview,
} from "../types";

function base(projectId: string) {
  return `/api/project/${encodeURIComponent(projectId)}`;
}

/** GET /api/project/:id/production —— 工作流、创作意图、Visual Bible、版本树与成本所需快照。 */
export function getProduction(projectId: string, init?: CfInit) {
  return cfJson<ProductionOverview>(`${base(projectId)}/production`, init);
}

/** PATCH /api/project/:id/production —— 工作流开关 / 创意意图 / Visual Bible / 母版快照。 */
export function patchProduction(projectId: string, input: ProductionPatch, init?: CfInit) {
  return cfJson<Record<string, unknown>>(`${base(projectId)}/production`, {
    ...init,
    method: "PATCH",
    json: input,
  });
}

/** GET /api/project/:id/quality —— 候选素材 + 最近一次质量评估 + 模型统计。 */
export function getQuality(projectId: string, init?: CfInit) {
  return cfJson<QualityOverview>(`${base(projectId)}/quality`, init);
}

/** POST /api/project/:id/quality —— 让视觉模型重新评估一个候选素材。 */
export function evaluateQuality(
  projectId: string,
  input: {
    assetId: string;
    llmConfig: { baseUrl: string; apiKey: string; model: string; visionModel?: string };
  },
  init?: CfInit,
) {
  return cfJson<GenerationReview>(`${base(projectId)}/quality`, {
    ...init,
    method: "POST",
    json: input,
  });
}

/** PATCH /api/project/:id/quality —— 人工裁决（accepted 会同时把该素材设为镜头选中项）。 */
export function decideQuality(
  projectId: string,
  input: { reviewId: string; decision: "accepted" | "rejected" },
  init?: CfInit,
) {
  return cfJson<GenerationReview>(`${base(projectId)}/quality`, {
    ...init,
    method: "PATCH",
    json: input,
  });
}

export interface RepairPreviewInput {
  assetId: string;
  reviewId: string;
  provider: string;
  model: string;
  supportsAudio?: boolean;
  pricePerCall?: number;
  scope?: "temporal" | "region";
  window?: { start: number; end: number };
}

/** POST /api/project/:id/repair（默认 action=preview）—— 不提交、不计费。 */
export function previewRepair(projectId: string, input: RepairPreviewInput, init?: CfInit) {
  return cfJson<VideoRepairPreview>(`${base(projectId)}/repair`, {
    ...init,
    method: "POST",
    json: { action: "preview", ...input },
  });
}

/** POST /api/project/:id/repair（action=execute）—— 显式确认后才产生付费任务。 */
export function executeRepair(
  projectId: string,
  input: RepairPreviewInput & {
    apiKey: string;
    baseUrl?: string;
    operationId: string;
    planHash: string;
    confirmed: true;
  },
  init?: CfInit,
) {
  return cfJson<{ taskId?: string; status?: string; modelId?: string }>(`${base(projectId)}/repair`, {
    ...init,
    method: "POST",
    json: { action: "execute", ...input },
  });
}

/** POST /api/project/:id/mastering（默认 action=analyze）。 */
export function analyzeMastering(
  projectId: string,
  input: { compositionId?: string } = {},
  init?: CfInit,
) {
  return cfJson<{ compositionId: string; analysis: MasteringAnalysis }>(`${base(projectId)}/mastering`, {
    ...init,
    method: "POST",
    json: input,
  });
}

/** POST /api/project/:id/mastering（action=render）—— 非破坏性生成新的本地母版。 */
export function renderMastering(
  projectId: string,
  input: {
    compositionId?: string;
    normalizeAudio: boolean;
    deflicker: boolean;
    label?: string;
  },
  init?: CfInit,
) {
  return cfJson<MasteringResult>(`${base(projectId)}/mastering`, {
    ...init,
    method: "POST",
    json: { action: "render", ...input },
  });
}
