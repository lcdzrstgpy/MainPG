/**
 * Paid TTS platform presets (pure data, shared between client and server, no server-only dependencies).
 *
 * Unified "platform" dropdown. MainPG only exposes Volcengine Speech V3.
 * Atlas and fal reuse the API key already entered under the same provider in the "AI Platform" tab;
 * MiniMax has its own separate key (plus an optional GroupId).
 * Each platform provides default baseUrl/model/voice so the UI can conditionally render fields
 * and offer voice suggestions accordingly.
 */

export type TTSProvider = "openai" | "atlas" | "minimax" | "falai" | "volcengine-speech";

export interface TTSVoiceOption {
  value: string;
  label: string;
}

export interface TTSProviderMeta {
  value: TTSProvider;
  label: string;
  /** Default baseUrl for this platform's TTS endpoint */
  baseUrl: string;
  /** Default model id */
  defaultModel: string;
  /** Available models (empty means free-form input, e.g. OpenAI-compatible) */
  models: TTSVoiceOption[];
  /** Default voice id */
  defaultVoice: string;
  /** Suggested voice list */
  voices: TTSVoiceOption[];
  /**
   * Key source:
   * - "tts": use the apiKey stored in the TTS config itself (OpenAI-compatible / MiniMax)
   * - others: reuse the apiKey of the matching provider in the "AI Platform" store (atlas-cloud / fal-ai)
   */
  keySource: "tts" | "atlas-cloud" | "fal-ai";
  /** Whether a GroupId is required (needed for the MiniMax domestic endpoint api.minimax.chat) */
  needsGroupId?: boolean;
  /** Whether to expose a baseUrl input field (OpenAI-compatible and MiniMax support switching regional endpoints) */
  editableBaseUrl?: boolean;
  /** Volcengine Speech V3 resource ID; it selects the billed voice resource. */
  defaultResourceId?: string;
  /** Configuration hint shown in the UI */
  hint?: string;
}

/** Legacy OpenAI-compatible quick presets (kept for saved legacy settings only). */
export const OPENAI_TTS_PRESETS = [
  { label: "硅基流动 CosyVoice", baseUrl: "https://api.siliconflow.cn/v1", model: "FunAudioLLM/CosyVoice2-0.5B", voice: "FunAudioLLM/CosyVoice2-0.5B:alex" },
  { label: "OpenAI tts-1", baseUrl: "https://api.openai.com/v1", model: "tts-1", voice: "alloy" },
];

export const TTS_PROVIDERS: TTSProviderMeta[] = [
  {
    value: "volcengine-speech",
    label: "火山语音 V3（豆包 Seed TTS）",
    baseUrl: "https://openspeech.bytedance.com/api/v3/tts/unidirectional",
    defaultModel: "seed-tts-2.0-expressive",
    models: [
      { value: "seed-tts-2.0-expressive", label: "seed-tts-2.0-expressive（表现力增强）" },
      { value: "seed-tts-2.0-standard", label: "seed-tts-2.0-standard（更稳定、更快）" },
    ],
    defaultVoice: "",
    voices: [],
    keySource: "tts",
    defaultResourceId: "seed-tts-2.0",
    hint: "固定使用火山语音 V3；填新版语音控制台 API Key，并粘贴你已授权的 Seed TTS 2.0 音色 ID。",
  },
];

export const DEFAULT_TTS_PROVIDER: TTSProvider = "volcengine-speech";

/** Get platform metadata (with fallback: unknown/legacy config falls back to openai) */
export function getTTSProviderMeta(provider?: string | null): TTSProviderMeta {
  return TTS_PROVIDERS.find((p) => p.value === provider) ?? TTS_PROVIDERS[0];
}

/** Minimal input shape required when resolving TTS config (avoids circular dependency with store types) */
interface TTSSettingLike {
  enabled?: boolean;
  provider?: string;
  baseUrl?: string;
  apiKey?: string;
  model?: string;
  voice?: string;
  speed?: number;
  groupId?: string;
  resourceId?: string;
  speechRate?: number;
}
type ProvidersLike = Record<string, { apiKey?: string; baseUrl?: string } | undefined>;

/** Fully resolved TTS config used for actual requests / preview playback */
export interface ResolvedTTSConfig {
  provider: TTSProvider;
  baseUrl: string;
  apiKey: string;
  model: string;
  voice: string;
  speed?: number;
  groupId?: string;
  resourceId?: string;
  speechRate?: number;
}

/**
 * Resolves the "platform selection + reused AI platform key" into a complete TTS config
 * ready to send to the backend.
 * Atlas/fal keys are taken from the providers store; OpenAI-compatible/MiniMax use the TTS's own key.
 */
export function resolveTTSConfig(tts: TTSSettingLike | undefined, providers: ProvidersLike): ResolvedTTSConfig {
  const meta = getTTSProviderMeta(tts?.provider);
  // baseUrl: for editable platforms use the user-provided value (fall back to default if blank); otherwise force the platform default
  const baseUrl = meta.editableBaseUrl ? (tts?.baseUrl || meta.baseUrl) : meta.baseUrl;
  // apiKey: reuse the AI platform key or use the TTS-specific key
  const apiKey = meta.keySource === "tts" ? (tts?.apiKey || "") : (providers?.[meta.keySource]?.apiKey || "");
  return {
    provider: meta.value,
    baseUrl,
    apiKey,
    model: tts?.model || meta.defaultModel,
    voice: tts?.voice || meta.defaultVoice,
    ...(tts?.speed != null && { speed: tts.speed }),
    ...(meta.value === "minimax" && tts?.groupId ? { groupId: tts.groupId } : {}),
    ...(meta.value === "volcengine-speech" && { resourceId: tts?.resourceId || meta.defaultResourceId || "seed-tts-2.0" }),
    ...(meta.value === "volcengine-speech" && { speechRate: tts?.speechRate ?? 0 }),
  };
}

/** Whether paid TTS is ready (switch enabled + resolved key/model/voice all present) */
export function isPaidTTSReady(tts: TTSSettingLike | undefined, providers: ProvidersLike): boolean {
  if (!tts?.enabled) return false;
  const c = resolveTTSConfig(tts, providers);
  return Boolean(c.apiKey && c.baseUrl && c.model && c.voice);
}
