import { buildWorkflowPlan, type WorkflowStagePlan } from "@/lib/production-system";
import { OUTPUT_SCHEMES, sanitizeOutputSchemeSnapshot, type OutputSchemeSnapshot } from "@/lib/output-schemes";

export type InputMode = "upload" | "link" | "topic" | "product-library" | "clone";
export type OutputStrategy = "draft" | "controlled-motion" | "native-film";
export type AudioStrategy = "volcengine-tts" | "native-audio" | "mute";
export type StyleSource = "explicit" | "template" | "performance-recommendation";

/**
 * The single project-level creation contract shared by every entry point.
 * It only stores decisions that are not already covered by the product fields on
 * `projects` (product info) or by `creativeIntent` / `visualBible` (production detail).
 */
export interface CreationBrief {
  version: 1;
  inputMode: InputMode;
  targetDuration: 15 | 30 | 60;
  styleType: string;
  styleSource: StyleSource;
  targetAudience: string[];
  platforms: string[];
  priceRange?: string;
  usageAdvantage?: string;
  narrative?: {
    situation?: string;
    language?: string;
    tone?: string;
  };
  outputScheme: OutputSchemeSnapshot;
  outputStrategy: OutputStrategy;
  audioStrategy: AudioStrategy;
  templateId?: string;
  characterId?: string;
}

const clean = (value: unknown, max = 120): string => typeof value === "string" ? value.trim().slice(0, max) : "";
const cleanList = (value: unknown, max = 12): string[] => Array.isArray(value)
  ? [...new Set(value.filter((item): item is string => typeof item === "string").map((item) => clean(item)).filter(Boolean))].slice(0, max)
  : [];

const INPUT_MODES: readonly InputMode[] = ["upload", "link", "topic", "product-library", "clone"];
const OUTPUT_STRATEGIES: readonly OutputStrategy[] = ["draft", "controlled-motion", "native-film"];
const STYLE_SOURCES: readonly StyleSource[] = ["explicit", "template", "performance-recommendation"];
const TARGET_DURATIONS: readonly (15 | 30 | 60)[] = [15, 30, 60];

function pickEnum<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? value as T : fallback;
}

export const DEFAULT_CREATION_BRIEF: CreationBrief = {
  version: 1,
  inputMode: "upload",
  targetDuration: 30,
  styleType: "",
  styleSource: "explicit",
  targetAudience: [],
  platforms: ["douyin"],
  outputScheme: { ...OUTPUT_SCHEMES["native-film"] },
  outputStrategy: OUTPUT_SCHEMES["native-film"].outputStrategy,
  audioStrategy: OUTPUT_SCHEMES["native-film"].audioStrategy,
};

export function isOutputStrategy(value: unknown): value is OutputStrategy {
  return typeof value === "string" && (OUTPUT_STRATEGIES as readonly string[]).includes(value);
}

/** Never throws: partial, legacy, or hostile input is normalized into a usable brief. */
export function sanitizeCreationBrief(value: unknown): CreationBrief {
  const raw = value && typeof value === "object" ? value as Record<string, unknown> : {};
  const narrativeRaw = raw.narrative && typeof raw.narrative === "object" ? raw.narrative as Record<string, unknown> : {};
  const narrative = {
    ...(clean(narrativeRaw.situation) && { situation: clean(narrativeRaw.situation) }),
    ...(clean(narrativeRaw.language) && { language: clean(narrativeRaw.language) }),
    ...(clean(narrativeRaw.tone) && { tone: clean(narrativeRaw.tone) }),
  };
  const platforms = cleanList(raw.platforms);
  const priceRange = clean(raw.priceRange, 60);
  const usageAdvantage = clean(raw.usageAdvantage, 300);
  const templateId = clean(raw.templateId, 80);
  const characterId = clean(raw.characterId, 80);
  const legacyId = raw.outputStrategy === "draft" ? "draft"
    : raw.outputStrategy === "controlled-motion" ? "controlled-balanced"
    : "native-film";
  const outputScheme = sanitizeOutputSchemeSnapshot(raw.outputScheme ?? {
    id: legacyId,
    audioStrategy: raw.audioStrategy,
  });
  return {
    version: 1,
    inputMode: pickEnum(raw.inputMode, INPUT_MODES, DEFAULT_CREATION_BRIEF.inputMode),
    targetDuration: TARGET_DURATIONS.includes(raw.targetDuration as 15 | 30 | 60)
      ? raw.targetDuration as 15 | 30 | 60
      : DEFAULT_CREATION_BRIEF.targetDuration,
    styleType: clean(raw.styleType, 60),
    styleSource: pickEnum(raw.styleSource, STYLE_SOURCES, DEFAULT_CREATION_BRIEF.styleSource),
    targetAudience: cleanList(raw.targetAudience),
    platforms: platforms.length ? platforms : [...DEFAULT_CREATION_BRIEF.platforms],
    ...(priceRange && { priceRange }),
    ...(usageAdvantage && { usageAdvantage }),
    ...(Object.keys(narrative).length && { narrative }),
    outputScheme,
    outputStrategy: outputScheme.outputStrategy,
    audioStrategy: outputScheme.audioStrategy,
    ...(templateId && { templateId }),
    ...(characterId && { characterId }),
  };
}

/** Strategy → `buildWorkflowPlan` inputs, so the nine-stage plan stays defined in one place. */
const WORKFLOW_INPUT_BY_STRATEGY: Record<OutputStrategy, Parameters<typeof buildWorkflowPlan>[0]> = {
  // Free draft: source media + free voice, never AI motion.
  draft: { hasSourceMedia: true, aiKeyframes: false, aiMotion: false, nativeAudio: false },
  // Director-controlled motion: paid keyframes + paid I2V motion + TTS voice.
  "controlled-motion": { hasSourceMedia: true, aiKeyframes: true, aiMotion: true, nativeAudio: false },
  // Native film: one model call carries motion and audio, so the voice stage is dropped.
  "native-film": { hasSourceMedia: true, aiKeyframes: false, aiMotion: true, nativeAudio: true },
};

export function buildWorkflowPlanForStrategy(strategy: OutputStrategy): WorkflowStagePlan[] {
  return buildWorkflowPlan(WORKFLOW_INPUT_BY_STRATEGY[strategy]);
}

/**
 * Design §7.2: the server must reject a brief whose strategy and workflow disagree, instead of
 * silently producing a cheaper film than the user asked for. Only an *explicitly* disabled key
 * stage is a conflict — an absent stage is not questioned here, and `draft` is defined as the
 * still-output plan, so its disabled `motion` stage is expected.
 * Returns a human-readable reason, or null when the pair is consistent.
 */
export function assertStrategyWorkflowConsistency(brief: CreationBrief, workflow: WorkflowStagePlan[]): string | null {
  const stage = (id: WorkflowStagePlan["id"]) => workflow.find((row) => row.id === id);
  if (brief.outputStrategy === "controlled-motion" && stage("motion")?.enabled === false) {
    return "出片策略 controlled-motion 要求启用 motion 阶段，但提交的工作流把 motion 设为 disabled";
  }
  if (brief.outputStrategy === "native-film" && stage("compose")?.enabled === false) {
    return "出片策略 native-film 要求启用 compose 阶段，但提交的工作流把 compose 设为 disabled";
  }
  return null;
}
