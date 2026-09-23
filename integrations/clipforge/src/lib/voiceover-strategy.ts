import type { AudioStrategy } from "@/lib/creation-brief";

/**
 * 「本次合成要不要跑 TTS 人声」的判定输入。
 * - `audioStrategy`：创作简报里的音频策略；旧项目（无简报）为 null。
 * - `manualTtsEnabled`：用户在视频页手动拨动的配音开关；null = 尚未手动覆盖，跟随默认。
 * - `legacyWorkflowVoiceEnabled`：旧项目沿用 productionWorkflow.voice 阶段的启用状态；
 *   null/undefined 表示该项目没记录该阶段 → 回落到改造前的默认值 true。
 */
export interface VoiceoverRequestInput {
  audioStrategy: AudioStrategy | null;
  manualTtsEnabled: boolean | null;
  legacyWorkflowVoiceEnabled?: boolean | null;
}

/** 当前生效值的来源：手动覆盖 / 简报策略默认 / 旧项目既有默认 */
export type VoiceoverSource = "manual" | "strategy" | "legacy";

export interface VoiceoverRequest {
  /** true 时合成请求才携带 ttsConfig / freeTts（二者都不带即不生成人声） */
  ttsEnabled: boolean;
  source: VoiceoverSource;
}

export const AUDIO_STRATEGY_LABELS: Record<AudioStrategy, string> = {
  "volcengine-tts": "火山语音 TTS",
  "native-audio": "模型原生音频",
  mute: "静音",
};

/**
 * 音频策略 → 是否启用 TTS 的纯判定，手动开关优先级最高：
 * - `mute` / `native-audio`：不生成 TTS 人声（原生音频由模型整片产出）；
 * - `volcengine-tts`：保持既有行为（启用 TTS，付费就绪走付费、否则免费 Edge 回退）；
 * - 简报为 null（旧项目）：保持改造前的默认行为（无记录时为 true）。
 * `source` 让界面说清当前生效的是策略默认还是手动覆盖，两者互不污染。
 */
export function resolveVoiceoverRequest(input: VoiceoverRequestInput): VoiceoverRequest {
  if (input.manualTtsEnabled !== null) {
    return { ttsEnabled: input.manualTtsEnabled, source: "manual" };
  }
  if (input.audioStrategy === "mute" || input.audioStrategy === "native-audio") {
    return { ttsEnabled: false, source: "strategy" };
  }
  if (input.audioStrategy === "volcengine-tts") {
    return { ttsEnabled: true, source: "strategy" };
  }
  return { ttsEnabled: input.legacyWorkflowVoiceEnabled ?? true, source: "legacy" };
}
