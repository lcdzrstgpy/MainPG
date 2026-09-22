import { isOutputStrategy, type AudioStrategy, type OutputStrategy } from "@/lib/creation-brief";
import type { VoiceSource } from "@/lib/voice-report";

/**
 * 项目详情页（素材 / 视频 / 导出）消费创作简报的纯归约逻辑。
 *
 * 为什么单独成模块：这三页都需要「按 outputStrategy 决定展示什么」的判定，而仓库没有组件
 * 渲染测试（无 @testing-library/react）。把判定抽成不依赖 React 的纯函数，既能让页面保持薄，
 * 又能直接在单测里断言——页面本身只保留源码契约测试。
 *
 * 约定：全部函数不抛错；输入缺失（旧项目没有 creationBrief、sidecar 读不到）时返回
 * 显式空值，让页面回退到迁移前行为，而不是报错。
 */

/** 旧项目（`creationBrief` 为 null）在三个页面统一显示的兼容提示。 */
export const LEGACY_BRIEF_NOTICE = "旧项目：未记录创作简报";

/** 导出页的成片分组标题（设计文档 §6.2 / §8 阶段 4）。 */
export const OUTPUT_STRATEGY_GROUP_LABELS: Record<OutputStrategy, string> = {
  draft: "免费草稿",
  "controlled-motion": "受控动态成片",
  "native-film": "原生整片",
};

// ==================== 成片按出片策略分组 ====================

/**
 * 判定一条成片属于哪种策略时可用的信号。
 * `strategy` 是合成记录落库的显式策略列（迁移 0021）；`label` 是历史成片唯一可用的线索
 * （整片生成会写入「九宫格整片 · 模型」，其它合成记录多数没有标签）。
 */
export interface CompositionStrategySource {
  strategy?: string | null;
  label?: string | null;
}

const NATIVE_FILM_LABEL_MARKERS = [/九宫格整片/, /原生整片/, /native\s*-?\s*film/i];
const DRAFT_LABEL_MARKERS = [/免费草稿/, /静态草稿/, /(^|[^a-z])draft([^a-z]|$)/i];
const CONTROLLED_MOTION_LABEL_MARKERS = [/受控动态/, /逐镜/, /controlled\s*-?\s*motion/i];

/**
 * 尽最大努力判定成片的出片策略：显式字段优先，其次读标签。
 * 判定不出来回 null——宁可归入「其他版本」，也不冒充某个策略的主版本。
 */
export function compositionStrategyOf(composition: CompositionStrategySource | null | undefined): OutputStrategy | null {
  if (!composition || typeof composition !== "object") return null;
  if (isOutputStrategy(composition.strategy)) return composition.strategy;
  const label = typeof composition.label === "string" ? composition.label : "";
  if (!label) return null;
  if (NATIVE_FILM_LABEL_MARKERS.some((marker) => marker.test(label))) return "native-film";
  if (DRAFT_LABEL_MARKERS.some((marker) => marker.test(label))) return "draft";
  if (CONTROLLED_MOTION_LABEL_MARKERS.some((marker) => marker.test(label))) return "controlled-motion";
  return null;
}

/** 成片卡片上的策略标签；判定不出来时如实说明。 */
export function compositionStrategyLabel(composition: CompositionStrategySource | null | undefined): string {
  const strategy = compositionStrategyOf(composition);
  return strategy ? OUTPUT_STRATEGY_GROUP_LABELS[strategy] : "策略未记录";
}

/**
 * 新建成片记录应落库的策略：读项目创作简报的 `outputStrategy`（写入侧的唯一来源）。
 * 旧项目没有简报、结构异常或枚举非法时返回 null —— 宁可写 null 让导出页回退到标签粗判，
 * 也不要按当前默认值瞎猜（那会把受控动态的成片冒充成免费草稿）。
 */
export function compositionStrategyFromBrief(creationBrief: unknown): OutputStrategy | null {
  if (!creationBrief || typeof creationBrief !== "object" || Array.isArray(creationBrief)) return null;
  const raw = (creationBrief as Record<string, unknown>).outputStrategy;
  return isOutputStrategy(raw) ? raw : null;
}

export interface CompositionGroups<T> {
  /** 命中项目 outputStrategy 的主版本（传入顺序即展示顺序） */
  primary: T[];
  /** 不属于主策略的版本（含无法判定的） */
  others: T[];
}

/**
 * 按项目策略分组：主版本 = 判定为该策略的成片；其余全部进「其他版本」。
 * `outputStrategy` 为 null（旧项目）时不猜策略，主版本为空、全部归入其他版本，
 * 页面据此沿用迁移前的「默认选最新一条」行为。
 */
export function groupCompositionsByStrategy<T extends CompositionStrategySource>(
  compositions: readonly T[] | null | undefined,
  outputStrategy: OutputStrategy | null | undefined
): CompositionGroups<T> {
  const list = Array.isArray(compositions) ? compositions.filter(Boolean) : [];
  if (!isOutputStrategy(outputStrategy)) return { primary: [], others: list };
  const primary: T[] = [];
  const others: T[] = [];
  for (const composition of list) {
    if (compositionStrategyOf(composition) === outputStrategy) primary.push(composition);
    else others.push(composition);
  }
  return { primary, others };
}

// ==================== 逐镜音频来源报告 ====================

const VOICE_SOURCES: readonly VoiceSource[] = ["volcengine", "edge", "native", "none"];

export interface VoiceDegradation {
  shotId: number;
  source: VoiceSource;
  reason: string;
}

export interface VoiceReportSummary {
  counts: Record<VoiceSource, number>;
  failedShotIds: number[];
  hasFailures: boolean;
  /** 有降级/失败原因的镜头（来自 sidecar 的 voiceReport.shots） */
  degradations: VoiceDegradation[];
  /** 参与统计的镜头总数 */
  total: number;
}

function isVoiceSource(value: unknown): value is VoiceSource {
  return typeof value === "string" && (VOICE_SOURCES as readonly string[]).includes(value);
}

/**
 * 读取合成 sidecar（`<成片>.timeline.json`）里的 `voiceReport`。
 * 读不到、结构异常或老成片没有该字段时返回 null —— 页面显示「暂无音频报告」，不报错。
 */
export function summarizeVoiceReport(value: unknown): VoiceReportSummary | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const raw = value as Record<string, unknown>;
  const countsRaw = raw.counts;
  if (!countsRaw || typeof countsRaw !== "object" || Array.isArray(countsRaw)) return null;
  const countsObject = countsRaw as Record<string, unknown>;

  const counts: Record<VoiceSource, number> = { volcengine: 0, edge: 0, native: 0, none: 0 };
  for (const source of VOICE_SOURCES) {
    const count = countsObject[source];
    counts[source] = typeof count === "number" && Number.isFinite(count) && count > 0 ? Math.floor(count) : 0;
  }

  const failedShotIds = [...new Set(
    (Array.isArray(raw.failedShotIds) ? raw.failedShotIds : [])
      .filter((id): id is number => typeof id === "number" && Number.isFinite(id))
  )];

  const degradations: VoiceDegradation[] = (Array.isArray(raw.shots) ? raw.shots : []).flatMap((shot) => {
    if (!shot || typeof shot !== "object") return [];
    const row = shot as Record<string, unknown>;
    const reason = typeof row.reason === "string" ? row.reason.trim() : "";
    if (typeof row.shotId !== "number" || !reason) return [];
    return [{ shotId: row.shotId, source: isVoiceSource(row.source) ? row.source : "none", reason }];
  });

  return {
    counts,
    failedShotIds,
    hasFailures: raw.hasFailures === true || failedShotIds.length > 0,
    degradations,
    total: VOICE_SOURCES.reduce((sum, source) => sum + counts[source], 0),
  };
}

// ==================== 本次音频来源的含义 ====================

export interface VoiceSourceExplanation {
  kind: "volcengine" | "edge" | "native" | "mute" | "off";
  label: string;
  detail: string;
}

/**
 * 把「配音开关 + 是否已配置付费 TTS + 简报里的音频策略」翻译成一句人话，
 * 让用户明确本次合成的人声到底由谁生成（付费火山语音 / 免费 Edge 回退 / 模型原生音频 / 静音）。
 */
export function voiceSourceExplanation(input: {
  audioStrategy?: AudioStrategy | null;
  ttsEnabled: boolean;
  paidTtsReady: boolean;
}): VoiceSourceExplanation {
  if (input.audioStrategy === "native-audio") {
    return {
      kind: "native",
      label: "模型原生音频",
      detail: "本项目策略为原生整片，人声由模型在整片生成时一并产出，合成阶段不会另外跑 TTS。",
    };
  }
  if (input.audioStrategy === "mute") {
    return {
      kind: "mute",
      label: "静音（不加人声）",
      detail: "本项目策略要求静音，合成只保留画面与背景音乐，不生成人声。",
    };
  }
  if (!input.ttsEnabled) {
    return {
      kind: "off",
      label: "本次已关闭配音",
      detail: "合成不会生成人声；需要人声时请打开上面的配音开关。",
    };
  }
  if (input.paidTtsReady) {
    return {
      kind: "volcengine",
      label: "付费火山语音（当前生效）",
      detail: "逐镜使用设置中的火山语音音色合成旁白，按用量单独计费。",
    };
  }
  return {
    kind: "edge",
    label: "免费 Edge 回退（当前生效）",
    detail: "未配置火山语音，使用微软 Edge 免费音色；付费语音失败时也会回退到免费 Edge。",
  };
}

// ==================== 素材页的阶段引导 ====================

export type AssetsPrimaryAction = "stock-fill" | "per-shot-motion" | "storyboard-film";

export interface AssetsStageGuide {
  strategy: OutputStrategy | null;
  /** 旧项目：没有简报，保持迁移前的素材流程 */
  legacy: boolean;
  /** 本策略下素材阶段的主操作；旧项目为 null（不改变原有主次） */
  primaryAction: AssetsPrimaryAction | null;
  title: string;
  detail: string;
}

/**
 * 素材阶段的分策略引导：只做标注与主次说明，不改变任何既有按钮的行为。
 * - draft：免费草稿是静态素材，绝不能标成 AI 动态视频；
 * - controlled-motion：逐镜 I2V 才是主操作；
 * - native-film：引导到整片流程，不要逐镜烧钱。
 */
export function assetsStageGuide(strategy: OutputStrategy | null | undefined): AssetsStageGuide {
  const resolved = isOutputStrategy(strategy) ? strategy : null;
  switch (resolved) {
    case "draft":
      return {
        strategy: resolved,
        legacy: false,
        primaryAction: "stock-fill",
        title: "免费草稿：静态素材，非 AI 动态视频",
        detail: "本策略只用商品图或已授权静态素材在本地 FFmpeg 合成，不会提交生视频任务；逐镜「转为动态」属于付费升级，请确认后再用。",
      };
    case "controlled-motion":
      return {
        strategy: resolved,
        legacy: false,
        primaryAction: "per-shot-motion",
        title: "导演可控动态：逐镜 I2V 是本策略的主操作",
        detail: "先为每镜生成关键帧，再用「转为动态」提交图生视频任务；未生成动态的镜头在合成时会退回静态素材。",
      };
    case "native-film":
      return {
        strategy: resolved,
        legacy: false,
        primaryAction: "storyboard-film",
        title: "原生整片：走整片生成流程",
        detail: "用「整片」按钮把所有关键帧一次提交给模型，生成带原生音频的整片，无需逐镜做 I2V。",
      };
    default:
      return {
        strategy: null,
        legacy: true,
        primaryAction: null,
        title: LEGACY_BRIEF_NOTICE,
        detail: "该项目创建时未记录出片策略，素材流程保持原有行为；可先在脚本页确认策略，再决定是否生成动态镜头。",
      };
  }
}
