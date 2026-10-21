import { cfJson, type CfInit } from "../clipforgeClient";
import type { FilmDryRun, FilmResult, StoryboardGridResult } from "../types";

export interface FilmRequestBase {
  scriptId: string;
  provider?: string;
  model?: string;
  apiKey?: string;
  baseUrl?: string;
  options?: Record<string, unknown>;
  characterSheetUrl?: string;
  spendCapUsd?: number;
  acknowledgeOverCap?: boolean;
}

export interface StoryboardGridRequest {
  scriptId: string;
  provider: string;
  model: string;
  apiKey: string;
  baseUrl?: string;
  options?: Record<string, unknown>;
  characterSheetUrl?: string;
  productImageUrl?: string;
  regenerate?: boolean;
}

/** 整片 dryRun：只返回提示词、镜头数/时长、参考图配额与花费估算，不提交付费任务。 */
export function previewFilm(projectId: string, input: FilmRequestBase, init?: CfInit) {
  return cfJson<FilmDryRun>(`/api/project/${encodeURIComponent(projectId)}/storyboard-film`, {
    ...init,
    method: "POST",
    json: { ...input, dryRun: true },
  });
}

/** 付费整片生成（同步阻塞，可能几分钟）。 */
export function generateFilm(projectId: string, input: FilmRequestBase, init?: CfInit) {
  return cfJson<FilmResult>(`/api/project/${encodeURIComponent(projectId)}/storyboard-film`, {
    ...init,
    method: "POST",
    json: input,
  });
}

/** 九宫格分镜关键帧（付费生图，供原生整片引用）。 */
export function generateStoryboardGrid(projectId: string, input: StoryboardGridRequest, init?: CfInit) {
  return cfJson<StoryboardGridResult>(`/api/project/${encodeURIComponent(projectId)}/storyboard-grid`, {
    ...init,
    method: "POST",
    json: input,
  });
}
