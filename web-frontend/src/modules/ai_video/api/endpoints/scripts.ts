import { cfJson, type CfInit } from "../clipforgeClient";
import type {
  Script,
  ScriptGenerationInput,
  ScriptGenerationResult,
  ScriptJudgeResult,
  ScriptPatchInput,
  ScriptPatchResult,
  TopicScriptInput,
  TopicScriptResult,
} from "../types";

function base(projectId: string) {
  return `/api/project/${encodeURIComponent(projectId)}`;
}

export function listScripts(projectId: string, init?: CfInit) {
  return cfJson<Script[]>(`${base(projectId)}/scripts`, init);
}

/** 切换选中版本，或按分镜批量改台词/画面描述/运镜。 */
export function patchScript(projectId: string, input: ScriptPatchInput, init?: CfInit) {
  return cfJson<ScriptPatchResult>(`${base(projectId)}/scripts`, {
    ...init,
    method: "PATCH",
    json: input,
  });
}

/** 带货脚本（可带 projectId 落库并同步项目状态）。 */
export function generateScript(input: ScriptGenerationInput, init?: CfInit) {
  return cfJson<ScriptGenerationResult>("/api/llm/script", {
    ...init,
    method: "POST",
    json: input,
  });
}

/** 一句话主题 → 旁白脚本。 */
export function generateTopicScript(input: TopicScriptInput, init?: CfInit) {
  return cfJson<TopicScriptResult>("/api/topic/script", {
    ...init,
    method: "POST",
    json: input,
  });
}

/** 判官团评审（台词未变化时返回 {skipped:true}，不重复花 LLM 调用）。 */
export function judgeScript(
  projectId: string,
  input: { scriptId: string; llmConfig: { baseUrl?: string; apiKey?: string; model: string } },
  init?: CfInit,
) {
  return cfJson<ScriptJudgeResult>(`${base(projectId)}/script-judge`, {
    ...init,
    method: "POST",
    json: input,
  });
}
