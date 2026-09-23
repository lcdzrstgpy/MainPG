import {
  sanitizeCreationBrief,
  type AudioStrategy,
  type CreationBrief,
  type OutputStrategy,
  type StyleSource,
} from "@/lib/creation-brief";
import { resolveScriptStyle } from "@/lib/script-style";
import { snapTargetDuration } from "@/lib/creation-entry-prefill";

/**
 * 批量出片与单项目共用同一份 `CreationBrief` 与同一个请求体构造器（设计 §8 阶段 5）。
 *
 * 批量只额外提供「变量矩阵」（钩子/风格/音色/BGM/字幕轮换），不再维护第二套创建语义，
 * 也不再自己拼脚本请求体。策略与风格必须显式、合法：`auto` 只保留给用户显式选择的
 * 「智能推荐」，默认路径不允许落到 409 后静默失败。
 */

/** 出片策略与单项目完全一致（设计 §5.2）。 */
export const BATCH_OUTPUT_STRATEGIES: readonly OutputStrategy[] = ["draft", "controlled-motion", "native-film"];

/** 用户显式选择的「智能推荐」：由服务端按历史数据解析，无数据时以 409 给出可读原因。 */
export const AUTO_STYLE = "auto";

/**
 * 策略默认音源（设计 §5.2）：只有原生整片由模型自带音轨，其余走火山语音。
 * 与 `creation-brief-defaults.defaultAudioStrategyFor` 是同一张表；在 lib 层复刻是为了
 * 不让 `lib/` 反向依赖组件目录（见实施计划「分层决策」）。
 */
export function batchAudioStrategyFor(strategy: OutputStrategy): AudioStrategy {
  return strategy === "native-film" ? "native-audio" : "volcengine-tts";
}

export interface BatchItemStyle {
  /** 发给脚本接口的风格 */
  styleType: string;
  styleSource: StyleSource;
  /** true = 经 `resolveScriptStyle` 一定能解析，不会因为「没有历史数据」被 409 挡下 */
  resolvesWithoutData: boolean;
}

export interface BatchItemStyleInput {
  /** 变量矩阵给这一件指定的风格（引擎词表，如 pain_point / scene） */
  slotStyleType?: string;
  /** 用户在批量入口选的风格（UI 白名单或 auto） */
  chosenStyle: string;
}

/** 每件视频的风格：变量矩阵优先，其次用户手选；两者都拿不到时才回到显式「智能推荐」。 */
export function resolveBatchItemStyle(input: BatchItemStyleInput): BatchItemStyle {
  const resolved = (raw?: string): string | null => {
    const value = (raw ?? "").trim();
    if (!value || value === AUTO_STYLE) return null;
    const resolution = resolveScriptStyle({ requestedStyle: value });
    return resolution.kind === "resolved" ? resolution.styleType : null;
  };

  const slotStyle = resolved(input.slotStyleType);
  if (slotStyle) return { styleType: slotStyle, styleSource: "explicit", resolvesWithoutData: true };

  const chosen = resolved(input.chosenStyle);
  if (chosen) return { styleType: chosen, styleSource: "explicit", resolvesWithoutData: true };

  const raw = (input.chosenStyle ?? "").trim();
  // 认不出的风格原样上报：服务端以 409 needs_explicit_style(unknown-style) 给出候选与原因，
  // 不静默替换成别的风格。
  if (raw && raw !== AUTO_STYLE) return { styleType: raw, styleSource: "explicit", resolvesWithoutData: false };
  return { styleType: AUTO_STYLE, styleSource: "performance-recommendation", resolvesWithoutData: false };
}

export interface BatchItemBriefInput {
  style: BatchItemStyle;
  /** 批量入口选的目标时长（秒）；合同只接受 15/30/60，其它值按最近档位归位 */
  baseDuration: number;
  /** 变量矩阵给这一件的时长抖动（秒） */
  durationOffset?: number;
  strategy: OutputStrategy;
  /** 商品库里的目标人群文本（可选） */
  audience?: string;
}

export interface BatchItemBrief {
  /** 这一件的统一创作简报：创建项目与生成脚本共用同一份 */
  brief: CreationBrief;
  /** 实际生效的时长差（秒）——合同只有三档，用它回填变量标签，避免显示不会发生的抖动 */
  appliedDurationOffset: number;
}

export function buildBatchItemBrief(input: BatchItemBriefInput): BatchItemBrief {
  const duration = snapTargetDuration(input.baseDuration + (input.durationOffset ?? 0));
  const audience = (input.audience ?? "").trim();
  return {
    brief: sanitizeCreationBrief({
      inputMode: "product-library",
      targetDuration: duration,
      styleType: input.style.styleType,
      styleSource: input.style.styleSource,
      outputStrategy: input.strategy,
      audioStrategy: batchAudioStrategyFor(input.strategy),
      ...(audience ? { targetAudience: [audience] } : {}),
    }),
    appliedDurationOffset: duration - snapTargetDuration(input.baseDuration),
  };
}

/**
 * 只有显式 `draft` 能走「脚本后自动配画面 + 免费合成」这条免费链；
 * controlled-motion / native-film 必须回到项目页显式确认后再生视频（设计 §7.4）。
 */
export function batchAutoComposeEnabled(strategy: OutputStrategy, autoCompose: boolean): boolean {
  return strategy === "draft" && autoCompose;
}

/** 批量项完成后的去向：免费草稿直达成片，其余策略回脚本页继续显式流程。 */
export function batchItemTargetPath(projectId: string, strategy: OutputStrategy, autoCompose: boolean): string {
  return batchAutoComposeEnabled(strategy, autoCompose)
    ? `/project/${projectId}/export`
    : `/project/${projectId}/script`;
}
