import { cfJson, type CfInit } from "../clipforgeClient";
import type {
  ComposeAccepted,
  ComposeInput,
  Composition,
  CompositionTimelineResult,
} from "../types";

function base(projectId: string) {
  return `/api/project/${encodeURIComponent(projectId)}`;
}

/** 只返回成功成片（失败重试不会遮住历史可用版本）。 */
export function listCompositions(projectId: string, init?: CfInit) {
  return cfJson<{ compositions: Composition[] }>(`${base(projectId)}/compositions`, init);
}

export function getCompositionTimeline(projectId: string, compositionId: string, init?: CfInit) {
  return cfJson<CompositionTimelineResult>(
    `${base(projectId)}/compositions/${encodeURIComponent(compositionId)}/timeline`,
    init,
  );
}

/** 启动合成：202 + {compositionId}，随后按 compositionId 精确轮询。 */
export function composeVideo(projectId: string, input: ComposeInput = {}, init?: CfInit) {
  return cfJson<ComposeAccepted>(`${base(projectId)}/compose`, {
    ...init,
    method: "POST",
    json: input,
  });
}

/** 轮询合成结果；不传 compositionId 时取最新一条。 */
export function getCompose(projectId: string, compositionId?: string, init?: CfInit) {
  const query = compositionId ? `?compositionId=${encodeURIComponent(compositionId)}` : "";
  return cfJson<{ composition: Composition | null }>(`${base(projectId)}/compose${query}`, init);
}

/** POST /api/project/:id/bgm —— 上传自定义背景音乐。 */
export function uploadBgm(projectId: string, file: File, init?: CfInit) {
  const form = new FormData();
  form.append("file", file);
  return cfJson<{ path: string; name: string }>(`${base(projectId)}/bgm`, {
    ...init,
    method: "POST",
    body: form,
  });
}
