/**
 * 抽取自 integrations/clipforge/src/components/project-creation/creation-brief-defaults.ts。
 * 只读同步：选项词表以 ClipForge 上游为准；修改请先改上游再同步，禁止在本文件单向漂移。
 * 本地化差异：去掉 i18n（MainPG 只有中文），文案直接使用中文。
 */

import {
  defaultAudioStrategyForStrategy,
  type AudioStrategy,
  type InputMode,
  type OutputStrategy,
  type StyleSource,
} from "./creation-brief.ts";
import { isTopicNarrationStyle, type TopicNarrationStyle } from "./creation-submit.ts";

export interface BriefOption<T extends string | number> {
  id: T;
  label: string;
}

export interface DescribedBriefOption<T extends string | number> extends BriefOption<T> {
  description: string;
}

export const MAX_SOURCE_IMAGES = 5;

export const INPUT_MODE_OPTIONS: ReadonlyArray<DescribedBriefOption<InputMode>> = [
  { id: "upload", label: "上传商品图", description: "上传 1–5 张商品图作为主素材" },
  { id: "link", label: "商品链接", description: "粘贴商品链接，自动抓取标题、价格与图片" },
  { id: "topic", label: "一句话主题", description: "只给一句话主题，自动匹配免费素材" },
  { id: "product-library", label: "商品库预填", description: "从商品库挑一个商品，预填商品信息" },
  { id: "clone", label: "爆款复刻", description: "按爆款视频复刻结构与节奏" },
];

export const CATEGORY_OPTIONS: ReadonlyArray<BriefOption<string>> = [
  { id: "beauty", label: "美妆护肤" },
  { id: "food", label: "食品零食" },
  { id: "home", label: "家居日用" },
  { id: "fashion", label: "服饰鞋包" },
  { id: "digital", label: "数码3C" },
  { id: "other", label: "其他" },
];

export const DURATION_OPTIONS: ReadonlyArray<BriefOption<15 | 30 | 60>> = [
  { id: 15, label: "15s" },
  { id: 30, label: "30s" },
  { id: 60, label: "60s" },
];

export type VideoModeId = "product_closeup" | "graphic_montage" | "scene_demo" | "live_presenter";
export const DEFAULT_VIDEO_MODE: VideoModeId = "product_closeup";

export const VIDEO_MODE_OPTIONS: ReadonlyArray<DescribedBriefOption<VideoModeId>> = [
  { id: "product_closeup", label: "产品特写", description: "商品原图为主，真实感最高" },
  { id: "graphic_montage", label: "图文混剪", description: "快节奏图文卡片，适合快消品" },
  { id: "scene_demo", label: "场景演示", description: "AI 生成使用场景，不含人脸" },
  { id: "live_presenter", label: "真人出镜", description: "人物出镜讲解（需要角色或素材）" },
];

export const SCRIPT_STYLE_OPTIONS: ReadonlyArray<DescribedBriefOption<string>> = [
  { id: "drama", label: "情景短剧", description: "双角色对话冲突剧，免费多音色配音" },
  { id: "reversal", label: "反转剧场", description: "立 flag 再打脸，反转即卖点" },
  { id: "interview", label: "街头采访", description: "主持人+路人实测，真实感种草" },
  { id: "story", label: "剧情故事", description: "故事化包装，增强代入感" },
  { id: "unboxing", label: "开箱测评", description: "第一人称沉浸开箱，挑刺换信任" },
  { id: "product_pov", label: "物品拟人", description: "商品开口自述，反差萌带货" },
  { id: "comparison", label: "对比测评", description: "横向对比突出优势" },
  { id: "talking_head", label: "达人口播", description: "人设化怼脸直给，证据快节奏堆叠" },
  { id: "pain-point", label: "痛点种草", description: "直击用户痛点，激发购买欲" },
  { id: "scenario", label: "场景安利", description: "真实场景展示，沉浸式种草" },
  { id: "auto", label: "智能推荐", description: "按历史投放数据推荐；数据不足时会要求你手动选风格" },
];

export const TOPIC_NARRATION_STYLE_OPTIONS: ReadonlyArray<DescribedBriefOption<TopicNarrationStyle>> = [
  { id: "knowledge", label: "知识科普", description: "抛问题再讲清 3-5 个知识点，结尾给一句金句" },
  { id: "story", label: "情感故事", description: "第一人称叙事，情绪有起伏，结尾落到共鸣点" },
  { id: "lifestyle", label: "生活方式", description: "日常场景里的做法与细节，轻松可复制" },
  { id: "inspiration", label: "励志金句", description: "短句递进给力量，适合转发与收藏" },
  { id: "travel", label: "旅行风光", description: "目的地风光与人文体验，画面感优先" },
];

/** 主题模式只认旁白词表；切来源时把残留的带货风格换成主题默认值。 */
export function coerceStyleTypeForInputMode(styleType: string, inputMode: InputMode): string {
  if (inputMode !== "topic") return styleType;
  return isTopicNarrationStyle(styleType) ? styleType : TOPIC_NARRATION_STYLE_OPTIONS[0].id;
}

export const AUDIENCE_OPTIONS: ReadonlyArray<BriefOption<string>> = [
  { id: "学生党", label: "学生党" },
  { id: "上班族", label: "上班族" },
  { id: "宝妈", label: "宝妈" },
  { id: "精致白领", label: "精致白领" },
  { id: "中年群体", label: "中年群体" },
  { id: "男性用户", label: "男性用户" },
  { id: "健身人群", label: "健身人群" },
  { id: "数码爱好者", label: "数码爱好者" },
];

export const PLATFORM_OPTIONS: ReadonlyArray<BriefOption<string>> = [
  { id: "douyin", label: "抖音" },
  { id: "kuaishou", label: "快手" },
  { id: "xiaohongshu", label: "小红书" },
  { id: "tiktok", label: "TikTok Shop" },
];

export interface OutputStrategyOption {
  id: OutputStrategy;
  label: string;
  description: string;
}

export const OUTPUT_STRATEGY_OPTIONS: ReadonlyArray<OutputStrategyOption> = [
  {
    id: "draft",
    label: "免费草稿",
    description: "只用商品图或已授权素材在本地 FFmpeg 合成，不提交生视频任务，不计费",
  },
  {
    id: "controlled-motion",
    label: "导演可控动态",
    description: "逐镜生成关键帧并提交图生视频任务，镜头可控，按镜头数与模型计费",
  },
  {
    id: "native-film",
    label: "原生整片",
    description: "一次模型调用生成整片画面动态与原生音频，不再单独跑语音阶段",
  },
];

export const AUDIO_STRATEGY_OPTIONS: ReadonlyArray<DescribedBriefOption<AudioStrategy>> = [
  { id: "volcengine-tts", label: "火山语音配音（TTS）", description: "使用设置中的火山语音音色逐镜配音" },
  { id: "native-audio", label: "模型原生音频", description: "整片由模型生成音轨，不再单独 TTS" },
  { id: "mute", label: "静音（不加人声）", description: "只保留画面与背景音乐，不生成人声" },
];

export const STYLE_SOURCE_LABELS: Record<StyleSource, string> = {
  explicit: "手动选择",
  template: "来自模板",
  "performance-recommendation": "数据推荐",
};

export function durationLabel(id: 15 | 30 | 60): string {
  return DURATION_OPTIONS.find((option) => option.id === id)?.label ?? `${id}s`;
}

export function videoModeLabel(id: VideoModeId): string {
  return VIDEO_MODE_OPTIONS.find((option) => option.id === id)?.label ?? id;
}

export function scriptStyleLabel(id: string): string {
  return SCRIPT_STYLE_OPTIONS.find((option) => option.id === id)?.label ?? id;
}

export function topicNarrationStyleLabel(id: string): string {
  return TOPIC_NARRATION_STYLE_OPTIONS.find((option) => option.id === id)?.label ?? id;
}

export function outputStrategyLabel(id: OutputStrategy): string {
  return OUTPUT_STRATEGY_OPTIONS.find((option) => option.id === id)?.label ?? id;
}

export function audioStrategyLabel(id: AudioStrategy): string {
  return AUDIO_STRATEGY_OPTIONS.find((option) => option.id === id)?.label ?? id;
}

export function defaultAudioStrategyFor(strategy: OutputStrategy): AudioStrategy {
  return defaultAudioStrategyForStrategy(strategy);
}

/** 风格来源：auto 是「请系统推荐」，具体风格才算显式选择。 */
export function resolveStyleSource(input: { styleType: string; templateId?: string }): StyleSource {
  if (input.styleType === "auto") return "performance-recommendation";
  if (input.templateId) return "template";
  return "explicit";
}

export interface CreationBriefFormValidationInput {
  productName: string;
  images: ReadonlyArray<unknown>;
  topic?: string;
  inputMode?: InputMode;
  linkImported?: boolean;
  strategyChosen?: boolean;
}

export interface CreationBriefFormValidation {
  valid: boolean;
  errors: {
    productName?: string;
    images?: string;
    topic?: string;
    outputStrategy?: string;
  };
}

export function validateCreationBriefForm(input: CreationBriefFormValidationInput): CreationBriefFormValidation {
  const errors: CreationBriefFormValidation["errors"] = {};
  if (input.inputMode === "topic") {
    if (!(input.topic ?? "").trim()) errors.topic = "请填写一句话主题";
  } else {
    if (!input.productName.trim()) errors.productName = "请填写商品名称";
    const linkCarriesImages = input.inputMode === "link" && input.linkImported === true;
    if (!linkCarriesImages && input.images.length < 1) errors.images = "请至少上传 1 张商品图";
  }
  // 默认策略不再视为「已选择」：必须点过一次策略卡才放行（与上游 T1.1 语义一致）。
  if (input.strategyChosen !== true) errors.outputStrategy = "请先选择一个出片策略";
  return { valid: Object.keys(errors).length === 0, errors };
}
