import type { CreationBrief } from "@/lib/creation-brief";
import type { ImageGenParams, VideoGenParams } from "@/lib/gen-params";
import type { MotionIntensity, MotionRealismTier } from "@/lib/motion-prompt";

export type OutputSchemeId =
  | "draft"
  | "controlled-rapid"
  | "controlled-balanced"
  | "controlled-cinematic"
  | "native-film";

export interface OutputSchemeSnapshot {
  id: OutputSchemeId;
  outputStrategy: "draft" | "controlled-motion" | "native-film";
  audioStrategy: "volcengine-tts" | "native-audio" | "mute";
  resolution: "720p" | "1080p";
  shotDuration: number;
  motionStrength: number;
  motionIntensity: MotionIntensity;
  motionRealism: MotionRealismTier;
  chainMode: "pin" | "tail" | "off";
  visualLook: string;
}

/** Preset values are copied into each project's creation brief at selection time. */
export const OUTPUT_SCHEMES: Readonly<Record<OutputSchemeId, Readonly<OutputSchemeSnapshot>>> = Object.freeze({
  draft: Object.freeze({
    id: "draft", outputStrategy: "draft", audioStrategy: "volcengine-tts",
    resolution: "720p", shotDuration: 5, motionStrength: 0,
    motionIntensity: "normal", motionRealism: "auto", chainMode: "off", visualLook: "none",
  }),
  "controlled-rapid": Object.freeze({
    id: "controlled-rapid", outputStrategy: "controlled-motion", audioStrategy: "volcengine-tts",
    resolution: "720p", shotDuration: 4, motionStrength: 0.35,
    motionIntensity: "subtle", motionRealism: "constraints", chainMode: "off", visualLook: "none",
  }),
  "controlled-balanced": Object.freeze({
    id: "controlled-balanced", outputStrategy: "controlled-motion", audioStrategy: "volcengine-tts",
    resolution: "720p", shotDuration: 5, motionStrength: 0.55,
    motionIntensity: "normal", motionRealism: "auto", chainMode: "pin", visualLook: "daylight_clean",
  }),
  "controlled-cinematic": Object.freeze({
    id: "controlled-cinematic", outputStrategy: "controlled-motion", audioStrategy: "volcengine-tts",
    resolution: "1080p", shotDuration: 8, motionStrength: 0.72,
    motionIntensity: "strong", motionRealism: "auto", chainMode: "tail", visualLook: "studio_product",
  }),
  "native-film": Object.freeze({
    id: "native-film", outputStrategy: "native-film", audioStrategy: "native-audio",
    resolution: "720p", shotDuration: 5, motionStrength: 0.55,
    motionIntensity: "normal", motionRealism: "auto", chainMode: "off", visualLook: "daylight_clean",
  }),
});

function isOutputSchemeId(value: unknown): value is OutputSchemeId {
  return typeof value === "string" && Object.hasOwn(OUTPUT_SCHEMES, value);
}

/** Keep only project-owned generation controls; provider and model fields never enter a snapshot. */
export function sanitizeOutputSchemeSnapshot(value: unknown): OutputSchemeSnapshot {
  const raw = value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
  const preset = OUTPUT_SCHEMES[isOutputSchemeId(raw.id) ? raw.id : "native-film"];
  return {
    ...preset,
    ...(preset.id !== "native-film" && raw.audioStrategy === "mute" && { audioStrategy: "mute" as const }),
  };
}

/** Translate records written before `outputScheme` existed. */
export function inferLegacyOutputSchemeSnapshot(outputStrategy: unknown, audioStrategy: unknown): OutputSchemeSnapshot {
  const id: OutputSchemeId = outputStrategy === "draft" ? "draft"
    : outputStrategy === "controlled-motion" ? "controlled-balanced"
    : "native-film";
  return sanitizeOutputSchemeSnapshot({ id, audioStrategy });
}

export interface ProjectGenerationGlobals {
  imageParams: ImageGenParams;
  videoParams: VideoGenParams;
  motionIntensity: MotionIntensity;
  motionRealism: MotionRealismTier;
  chainMode: "pin" | "tail" | "off";
  visualLook: string;
}

export interface ProjectGenerationSettings extends ProjectGenerationGlobals {
  outputStrategy?: OutputSchemeSnapshot["outputStrategy"];
  audioStrategy?: OutputSchemeSnapshot["audioStrategy"];
}

/** The script page has one primary action; the saved scheme decides its only legal workflow. */
export type OutputSchemeExecution = "draft-pipeline" | "controlled-assets" | "native-film" | "legacy-pipeline";

export function outputSchemeExecution(brief: CreationBrief | null): OutputSchemeExecution {
  if (!brief) return "legacy-pipeline";
  const scheme = brief.outputScheme
    ? sanitizeOutputSchemeSnapshot(brief.outputScheme)
    : inferLegacyOutputSchemeSnapshot(brief.outputStrategy, brief.audioStrategy);
  if (scheme.outputStrategy === "draft") return "draft-pipeline";
  if (scheme.outputStrategy === "controlled-motion") return "controlled-assets";
  return "native-film";
}

/** Apply a project's saved choice while retaining unrelated global generation parameters. */
export function projectGenerationSettings(
  brief: CreationBrief | null,
  global: ProjectGenerationGlobals,
): ProjectGenerationSettings {
  if (!brief) return global;
  const scheme = brief.outputScheme
    ? sanitizeOutputSchemeSnapshot(brief.outputScheme)
    : inferLegacyOutputSchemeSnapshot(brief.outputStrategy, brief.audioStrategy);
  return {
    outputStrategy: scheme.outputStrategy,
    audioStrategy: scheme.audioStrategy,
    imageParams: { ...global.imageParams },
    videoParams: {
      ...global.videoParams,
      resolution: scheme.resolution,
      duration: scheme.shotDuration,
      motionStrength: scheme.motionStrength,
    },
    motionIntensity: scheme.motionIntensity,
    motionRealism: scheme.motionRealism,
    chainMode: scheme.chainMode,
    visualLook: scheme.visualLook,
  };
}
