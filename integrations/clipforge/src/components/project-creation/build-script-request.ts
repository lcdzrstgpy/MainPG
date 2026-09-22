import type { CreationBrief } from "@/components/project-creation/creation-brief-types";
import type { VideoModeId } from "@/components/project-creation/creation-brief-defaults";

/**
 * The single builder for the script request body.
 *
 * Key set is frozen against `/project/new` (src/app/project/new/page.tsx:587-623): the extraction is
 * only behaviour preserving while the same keys, in the same shapes, reach `/api/llm/script`.
 * Optional keys are omitted instead of being sent as `undefined`.
 */

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
  /**
   * `CreationBrief` intentionally does not carry the video mode (it stays on the project record and
   * on the ad-template recipe), so the caller passes it explicitly. It is a required script key.
   */
  videoMode: VideoModeId;
  llmConfig: ScriptRequestLlmConfig;
  /** Serialized shot structure of a picked script template (opaque to this module). */
  referenceStructure?: unknown;
  /** Ad-template creative directive. */
  customRequirements?: string;
  character?: ScriptRequestCharacter;
}

export function buildScriptRequest(input: ScriptRequestInput): Record<string, unknown> {
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
