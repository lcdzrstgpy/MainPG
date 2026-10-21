/**
 * AI 视频设置（T0.4）：纯数据层，可脱离 React/DOM 单测。
 *
 * 字段形状照搬 ClipForge `lib/stores/settings-store.ts`，但存储位置改到 MainPG origin：
 * sidecar 端口每次随机，旧 origin 的 localStorage 必然丢失。
 * 存储键沿用 `daihuo-jianshou-settings`；uiMode 与 `mainpg.aiVideo.uiMode` 双向同步。
 */

import type { ClipForgeDefaults } from "../api/types";

export const SETTINGS_STORAGE_KEY = "daihuo-jianshou-settings";

export const SETTINGS_SCHEMA_VERSION = 1;

export interface ProviderSetting {
  enabled: boolean;
  apiKey: string;
  baseUrl?: string;
}

export interface LLMSetting {
  provider: string;
  baseUrl: string;
  apiKey: string;
  model: string;
  visionModel?: string;
}

export type TTSProvider = "openai" | "atlas" | "minimax" | "falai" | "volcengine-speech";

export interface TTSSetting {
  enabled: boolean;
  provider?: TTSProvider;
  baseUrl: string;
  apiKey: string;
  model: string;
  voice: string;
  speed?: number;
  groupId?: string;
  resourceId?: string;
  speechRate?: number;
}

export type GenMediaType = "image" | "video";
export type GenAspectRatio = "9:16" | "16:9" | "1:1";
export type GenResolution = "720p" | "1080p";
export type MotionIntensity = "subtle" | "normal" | "strong";
export type MotionRealismTier = "auto" | "constraints" | "off";
export type ChainMode = "pin" | "tail" | "off";
export type ProductionProfileId = "rapid" | "balanced" | "cinematic";
export type SettingsUiMode = "simple" | "pro";

export interface CustomModel {
  id: string;
  provider: string;
  modelId: string;
  name: string;
  mediaType: GenMediaType;
  supportsAudio?: boolean;
}

export interface ImageGenParams {
  aspectRatio: GenAspectRatio;
  count: number;
  steps?: number;
  guidanceScale?: number;
  seed?: number;
  negativePrompt?: string;
}

export interface VideoGenParams {
  aspectRatio: GenAspectRatio;
  resolution: GenResolution;
  duration?: number;
  fps?: number;
  motionStrength?: number;
  seed?: number;
  negativePrompt?: string;
}

export interface SettingsState {
  providers: Record<string, ProviderSetting>;
  llm: LLMSetting;
  tts: TTSSetting;
  defaultImageModel: string;
  defaultVideoModel: string;
  defaultResolution: GenResolution;
  defaultAspectRatio: GenAspectRatio;
  customModels: CustomModel[];
  imageParams: ImageGenParams;
  videoParams: VideoGenParams;
  motionIntensity: MotionIntensity;
  motionRealism: MotionRealismTier;
  chainMode: ChainMode;
  visualLook: string;
  uiMode: SettingsUiMode;
  spendCapUsd: number;
  activeProductionProfile: ProductionProfileId;
}

export const DEFAULT_SETTINGS: SettingsState = {
  providers: {
    volcengine: { enabled: false, apiKey: "" },
    suchuang: { enabled: false, apiKey: "" },
  },
  llm: { provider: "", baseUrl: "", apiKey: "", model: "", visionModel: "" },
  tts: {
    enabled: false,
    provider: "volcengine-speech",
    baseUrl: "",
    apiKey: "",
    model: "",
    voice: "",
    speed: 1,
  },
  defaultImageModel: "",
  defaultVideoModel: "",
  defaultResolution: "720p",
  defaultAspectRatio: "9:16",
  customModels: [],
  imageParams: { aspectRatio: "9:16", count: 1 },
  videoParams: { aspectRatio: "9:16", resolution: "720p", duration: 5 },
  motionIntensity: "normal",
  motionRealism: "auto",
  chainMode: "pin",
  visualLook: "none",
  uiMode: "simple",
  // 单次付费生成的估价上限（0 = 不限），与 ClipForge 默认一致。
  spendCapUsd: 5,
  activeProductionProfile: "balanced",
};

const ASPECT_RATIOS: readonly GenAspectRatio[] = ["9:16", "16:9", "1:1"];
const RESOLUTIONS: readonly GenResolution[] = ["720p", "1080p"];
const MOTION_INTENSITIES: readonly MotionIntensity[] = ["subtle", "normal", "strong"];
const MOTION_REALISM: readonly MotionRealismTier[] = ["auto", "constraints", "off"];
const CHAIN_MODES: readonly ChainMode[] = ["pin", "tail", "off"];
const PRODUCTION_PROFILES: readonly ProductionProfileId[] = ["rapid", "balanced", "cinematic"];
const UI_MODES: readonly SettingsUiMode[] = ["simple", "pro"];
const TTS_PROVIDERS: readonly TTSProvider[] = ["openai", "atlas", "minimax", "falai", "volcengine-speech"];

export interface SettingsStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function flag(value: unknown, fallback = false): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

function objectOf(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

/** 兼容 zustand persist 的 `{state, version}` 包装与裸状态。 */
function unwrapState(raw: unknown): Record<string, unknown> | null {
  const source = objectOf(raw);
  if (!source) return null;
  const wrapped = objectOf(source.state);
  return wrapped ?? source;
}

function cloneDefaults(): SettingsState {
  return {
    ...DEFAULT_SETTINGS,
    providers: Object.fromEntries(
      Object.entries(DEFAULT_SETTINGS.providers).map(([name, setting]) => [name, { ...setting }]),
    ),
    llm: { ...DEFAULT_SETTINGS.llm },
    tts: { ...DEFAULT_SETTINGS.tts },
    customModels: DEFAULT_SETTINGS.customModels.map((model) => ({ ...model })),
    imageParams: { ...DEFAULT_SETTINGS.imageParams },
    videoParams: { ...DEFAULT_SETTINGS.videoParams },
  };
}

/** localStorage 是不可信输入：逐字段校验，坏值一律回落默认，绝不让页面拿到脏数据。 */
export function sanitizeSettings(raw: unknown): SettingsState {
  const state = cloneDefaults();
  const source = unwrapState(raw);
  if (!source) return state;

  const providers = objectOf(source.providers);
  if (providers) {
    for (const [name, value] of Object.entries(providers)) {
      if (!/^[a-z0-9-]{1,40}$/i.test(name)) continue;
      const setting = objectOf(value);
      if (!setting) continue;
      state.providers[name] = {
        enabled: flag(setting.enabled),
        apiKey: text(setting.apiKey),
        ...(text(setting.baseUrl) ? { baseUrl: text(setting.baseUrl) } : {}),
      };
    }
  }

  const llm = objectOf(source.llm);
  if (llm) {
    state.llm = {
      provider: text(llm.provider),
      baseUrl: text(llm.baseUrl),
      apiKey: text(llm.apiKey),
      model: text(llm.model),
      ...(text(llm.visionModel) ? { visionModel: text(llm.visionModel) } : {}),
    };
  }

  const tts = objectOf(source.tts);
  if (tts) {
    state.tts = {
      enabled: flag(tts.enabled),
      provider: oneOf(tts.provider, TTS_PROVIDERS, "volcengine-speech"),
      baseUrl: text(tts.baseUrl),
      apiKey: text(tts.apiKey),
      model: text(tts.model),
      voice: text(tts.voice),
      speed: finiteNumber(tts.speed) ?? 1,
      ...(text(tts.groupId) ? { groupId: text(tts.groupId) } : {}),
      ...(text(tts.resourceId) ? { resourceId: text(tts.resourceId) } : {}),
      ...(finiteNumber(tts.speechRate) !== undefined ? { speechRate: finiteNumber(tts.speechRate) } : {}),
    };
  }

  state.defaultImageModel = text(source.defaultImageModel);
  state.defaultVideoModel = text(source.defaultVideoModel);
  state.defaultResolution = oneOf(source.defaultResolution, RESOLUTIONS, DEFAULT_SETTINGS.defaultResolution);
  state.defaultAspectRatio = oneOf(source.defaultAspectRatio, ASPECT_RATIOS, DEFAULT_SETTINGS.defaultAspectRatio);
  state.spendCapUsd = Math.max(0, finiteNumber(source.spendCapUsd) ?? DEFAULT_SETTINGS.spendCapUsd);
  state.motionIntensity = oneOf(source.motionIntensity, MOTION_INTENSITIES, DEFAULT_SETTINGS.motionIntensity);
  state.motionRealism = oneOf(source.motionRealism, MOTION_REALISM, DEFAULT_SETTINGS.motionRealism);
  state.chainMode = oneOf(source.chainMode, CHAIN_MODES, DEFAULT_SETTINGS.chainMode);
  state.visualLook = text(source.visualLook, DEFAULT_SETTINGS.visualLook);
  state.uiMode = oneOf(source.uiMode, UI_MODES, DEFAULT_SETTINGS.uiMode);
  state.activeProductionProfile = oneOf(
    source.activeProductionProfile,
    PRODUCTION_PROFILES,
    DEFAULT_SETTINGS.activeProductionProfile,
  );

  if (Array.isArray(source.customModels)) {
    state.customModels = source.customModels.flatMap((entry) => {
      const model = objectOf(entry);
      if (!model) return [];
      const id = text(model.id);
      const provider = text(model.provider);
      const modelId = text(model.modelId);
      const name = text(model.name) || modelId;
      if (!id || !provider || !modelId) return [];
      return [{
        id,
        provider,
        modelId,
        name,
        mediaType: oneOf(model.mediaType, ["image", "video"] as const, "image"),
        ...(flag(model.supportsAudio) ? { supportsAudio: true } : {}),
      }];
    });
  }

  const imageParams = objectOf(source.imageParams);
  if (imageParams) {
    state.imageParams = {
      aspectRatio: oneOf(imageParams.aspectRatio, ASPECT_RATIOS, DEFAULT_SETTINGS.imageParams.aspectRatio),
      count: finiteNumber(imageParams.count) ?? DEFAULT_SETTINGS.imageParams.count,
      ...(finiteNumber(imageParams.steps) !== undefined ? { steps: finiteNumber(imageParams.steps) } : {}),
      ...(finiteNumber(imageParams.guidanceScale) !== undefined
        ? { guidanceScale: finiteNumber(imageParams.guidanceScale) }
        : {}),
      ...(finiteNumber(imageParams.seed) !== undefined ? { seed: finiteNumber(imageParams.seed) } : {}),
      ...(text(imageParams.negativePrompt) ? { negativePrompt: text(imageParams.negativePrompt) } : {}),
    };
  }

  const videoParams = objectOf(source.videoParams);
  if (videoParams) {
    state.videoParams = {
      aspectRatio: oneOf(videoParams.aspectRatio, ASPECT_RATIOS, DEFAULT_SETTINGS.videoParams.aspectRatio),
      resolution: oneOf(videoParams.resolution, RESOLUTIONS, DEFAULT_SETTINGS.videoParams.resolution),
      ...(finiteNumber(videoParams.duration) !== undefined ? { duration: finiteNumber(videoParams.duration) } : {}),
      ...(finiteNumber(videoParams.fps) !== undefined ? { fps: finiteNumber(videoParams.fps) } : {}),
      ...(finiteNumber(videoParams.motionStrength) !== undefined
        ? { motionStrength: finiteNumber(videoParams.motionStrength) }
        : {}),
      ...(finiteNumber(videoParams.seed) !== undefined ? { seed: finiteNumber(videoParams.seed) } : {}),
      ...(text(videoParams.negativePrompt) ? { negativePrompt: text(videoParams.negativePrompt) } : {}),
    };
  }

  return state;
}

/**
 * 运维默认值的落地规则：**只补空**。
 * 用户自己填过/改过的值优先，配置只做兜底——与 ClipForge `configured-defaults.ts` 语义一致。
 */
export function configuredDefaultsPatch(
  state: SettingsState,
  defaults: ClipForgeDefaults | null | undefined,
): Partial<SettingsState> {
  if (!defaults) return {};
  const patch: Partial<SettingsState> = {};

  const providers = { ...state.providers };
  let providersChanged = false;
  for (const [name, configured] of Object.entries(defaults.providers ?? {})) {
    const apiKey = typeof configured?.apiKey === "string" ? configured.apiKey.trim() : "";
    if (!apiKey) continue;
    const current = state.providers[name];
    if (current?.apiKey?.trim()) continue; // 已有 Key 就不动
    providers[name] = {
      ...current,
      ...configured,
      enabled: configured?.enabled ?? current?.enabled ?? true,
      apiKey,
    };
    providersChanged = true;
  }
  if (providersChanged) patch.providers = providers;

  const llmApiKey = defaults.llm?.apiKey?.trim();
  if (defaults.llm && llmApiKey && !state.llm.apiKey.trim()) {
    patch.llm = { ...state.llm, ...defaults.llm, apiKey: llmApiKey };
  }

  const imageModel = defaults.defaultImageModel?.trim();
  if (imageModel && !state.defaultImageModel.trim()) patch.defaultImageModel = imageModel;

  const videoModel = defaults.defaultVideoModel?.trim();
  if (videoModel && !state.defaultVideoModel.trim()) patch.defaultVideoModel = videoModel;

  return patch;
}

export function readSettingsFrom(storage: SettingsStorage | null): SettingsState {
  let raw: unknown = null;
  if (storage) {
    try {
      const stored = storage.getItem(SETTINGS_STORAGE_KEY);
      if (stored) raw = JSON.parse(stored);
    } catch {
      raw = null;
    }
  }
  return sanitizeSettings(raw);
}

export function writeSettingsTo(storage: SettingsStorage | null, state: SettingsState): void {
  if (!storage) return;
  try {
    storage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify({ version: SETTINGS_SCHEMA_VERSION, ...state }));
  } catch {
    // 隐私模式 / 配额写满：存不下就算了，本次会话内仍然可用。
  }
}
