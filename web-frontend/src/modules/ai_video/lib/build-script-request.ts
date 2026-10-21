/**
 * 复制自 integrations/clipforge/src/components/project-creation/build-script-request.ts。
 * 只读同步：请求键集以上游为准；修改请先改上游再同步，禁止在本文件单向漂移。
 */

import type { CreationBrief } from "./creation-brief.ts";
import type { VideoModeId } from "./creation-brief-defaults.ts";

export interface ScriptRequestLlmConfig {
  baseUrl: string;
  apiKey: string;
  model: string;
  visionModel?: string;
}

export interface ScriptRequestCharacter {
  id: string;
  name: string;
  appearance?: string;
  voiceStyle?: string;
}

export interface ScriptRequestInput {
  brief: CreationBrief;
  projectId: string;
  productName: string;
  category: string;
  productDescription?: string;
  productImages?: string[];
  videoMode: VideoModeId;
  llmConfig: ScriptRequestLlmConfig;
  referenceStructure?: unknown;
  customRequirements?: string;
  character?: ScriptRequestCharacter;
}

function buildNarrativePayload(
  narrative: CreationBrief["narrative"],
): { situation?: string; language?: string; tone?: string } | undefined {
  if (!narrative) return undefined;
  const fields: { situation?: string; language?: string; tone?: string } = {};
  if (narrative.situation?.trim()) fields.situation = narrative.situation;
  if (narrative.language?.trim()) fields.language = narrative.language;
  if (narrative.tone?.trim()) fields.tone = narrative.tone;
  return Object.keys(fields).length ? fields : undefined;
}

/** `/api/llm/script` 的唯一请求构造器：可选键缺省时不发送，而不是发 undefined。 */
export function buildScriptRequest(input: ScriptRequestInput): Record<string, unknown> {
  const narrative = buildNarrativePayload(input.brief.narrative);
  return {
    projectId: input.projectId,
    productName: input.productName,
    category: input.category,
    productDescription: input.productDescription ?? "",
    targetDuration: input.brief.targetDuration,
    styleType: input.brief.styleType,
    videoMode: input.videoMode,
    productImages: input.productImages ?? [],
    llmConfig: {
      baseUrl: input.llmConfig.baseUrl,
      apiKey: input.llmConfig.apiKey,
      model: input.llmConfig.model,
      visionModel: input.llmConfig.visionModel ?? "",
    },
    priceRange: input.brief.priceRange ?? "",
    targetAudience: input.brief.targetAudience.join(","),
    platforms: input.brief.platforms.join(","),
    usageAdvantage: input.brief.usageAdvantage ?? "",
    ...(narrative && { narrative }),
    ...(input.brief.templateId && { templateId: input.brief.templateId }),
    ...(input.referenceStructure !== undefined && { referenceStructure: input.referenceStructure }),
    ...(input.customRequirements && { customRequirements: input.customRequirements }),
    ...(input.character && {
      character: {
        id: input.character.id,
        name: input.character.name,
        appearance: input.character.appearance ?? "",
        ...(input.character.voiceStyle && { voiceStyle: input.character.voiceStyle }),
      },
    }),
  };
}
