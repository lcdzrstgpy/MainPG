import {
  DEFAULT_CREATION_BRIEF,
  type AudioStrategy,
  type InputMode,
  type OutputStrategy,
  type StyleSource,
} from "@/lib/creation-brief";

/**
 * Option vocabularies and defaults for the creation brief.
 *
 * Rules for this module:
 * - every option carries a stable `id` (what the API/storage sees) plus Chinese copy, so a later
 *   batch can swap the copy for i18n keys without touching call sites;
 * - no implicit downgrade: nothing here turns "auto" into a concrete style, and nothing here
 *   silently promotes a static draft into an AI-motion strategy.
 */

export interface BriefOption<T extends string | number> {
  id: T;
  label: string;
}

export interface DescribedBriefOption<T extends string | number> extends BriefOption<T> {
  description: string;
}

/** Maximum number of source images per project (same limit as /project/new). */
export const MAX_SOURCE_IMAGES = 5;

// ==================== Input source ====================

export const INPUT_MODE_OPTIONS: ReadonlyArray<DescribedBriefOption<InputMode>> = [
  { id: "upload", label: "上传商品图", description: "上传 1–5 张商品图作为主素材" },
  { id: "link", label: "商品链接", description: "粘贴商品链接，自动抓取标题、价格与图片" },
  { id: "topic", label: "一句话主题", description: "只给一句话主题，自动匹配免费素材" },
  { id: "product-library", label: "商品库预填", description: "从商品库挑一个商品，预填商品信息" },
  { id: "clone", label: "爆款复刻", description: "按爆款视频复刻结构与节奏" },
];

/** Same category values /project/new sends as `productCategory`. */
export const CATEGORY_OPTIONS: ReadonlyArray<BriefOption<string>> = [
  { id: "beauty", label: "美妆护肤" },
  { id: "food", label: "食品零食" },
  { id: "home", label: "家居日用" },
  { id: "fashion", label: "服饰鞋包" },
  { id: "digital", label: "数码3C" },
  { id: "other", label: "其他" },
];

// ==================== Duration & video mode ====================

export const DURATION_OPTIONS: ReadonlyArray<BriefOption<15 | 30 | 60>> = [
  { id: 15, label: "15s" },
  { id: 30, label: "30s" },
  { id: 60, label: "60s" },
];

export type VideoModeId = "product_closeup" | "graphic_montage" | "scene_demo" | "live_presenter";

/** Same default as /project/new (the only mode that works without a character or live footage). */
export const DEFAULT_VIDEO_MODE: VideoModeId = "product_closeup";

export const VIDEO_MODE_OPTIONS: ReadonlyArray<DescribedBriefOption<VideoModeId>> = [
  { id: "product_closeup", label: "产品特写", description: "商品原图为主，真实感最高" },
  { id: "graphic_montage", label: "图文混剪", description: "快节奏图文卡片，适合快消品" },
  { id: "scene_demo", label: "场景演示", description: "AI 生成使用场景，不含人脸" },
  { id: "live_presenter", label: "真人出镜", description: "人物出镜讲解（需要角色或素材）" },
];

// ==================== Narrative ====================

/** Exactly the 11 values /project/new offers; `pain-point` keeps its hyphen. */
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

/** The value is also the text handed to the script model, so ids stay human-readable. */
export const LANGUAGE_OPTIONS: ReadonlyArray<BriefOption<string>> = [
  { id: "中文", label: "中文" },
  { id: "英语", label: "英语" },
  { id: "日语", label: "日语" },
  { id: "韩语", label: "韩语" },
  { id: "中英双语", label: "中英双语" },
];

export const TONE_OPTIONS: ReadonlyArray<BriefOption<string>> = [
  { id: "轻快", label: "轻快" },
  { id: "共情", label: "共情" },
  { id: "专业", label: "专业" },
  { id: "紧迫", label: "紧迫" },
  { id: "幽默", label: "幽默" },
  { id: "克制", label: "克制" },
];

export const STYLE_SOURCE_LABELS: Record<StyleSource, string> = {
  explicit: "手动选择",
  template: "来自模板",
  "performance-recommendation": "数据推荐",
};

// ==================== Delivery (audience / platform / price) ====================

export const PRICE_RANGE_OPTIONS: ReadonlyArray<BriefOption<string>> = [
  { id: "0-50", label: "50元以下" },
  { id: "50-200", label: "50-200元" },
  { id: "200-500", label: "200-500元" },
  { id: "500+", label: "500元以上" },
];

/** The audience ids are the exact Chinese tags sent to the script API, so they are not translated. */
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

// ==================== Output strategy ====================

export interface OutputStrategyOption {
  id: OutputStrategy;
  label: string;
  description: string;
  /** Allowed source material, design doc §5.2. */
  allowedMedia: string;
  /** Default audio source, design doc §5.2. */
  defaultAudioSource: string;
  /** Primary finished output, design doc §5.2. */
  mainOutput: string;
}

export const OUTPUT_STRATEGY_OPTIONS: ReadonlyArray<OutputStrategyOption> = [
  {
    id: "draft",
    label: "免费草稿（静态素材 + FFmpeg，非 AI 动态视频）",
    description: "只用商品图或已授权素材在本地 FFmpeg 合成，不提交任何生视频任务，不计费",
    allowedMedia: "商品图 / 已授权静态素材",
    defaultAudioSource: "免费音色（Edge）或静音",
    mainOutput: "FFmpeg 常规合成（静态草稿）",
  },
  {
    id: "controlled-motion",
    label: "导演可控动态（逐镜生视频）",
    description: "逐镜生成关键帧并提交图生视频任务，镜头可控，按镜头数与模型计费",
    allowedMedia: "每镜图片 + 逐镜 I2V 视频片段",
    defaultAudioSource: "火山语音 V3",
    mainOutput: "逐镜动态合成",
  },
  {
    id: "native-film",
    label: "原生整片（一次模型生成，自带音频）",
    description: "一次模型调用生成整片画面动态与原生音频，不再单独跑语音阶段",
    allowedMedia: "分镜网格 / 参考图",
    defaultAudioSource: "模型原生音频",
    mainOutput: "整片原生生成",
  },
];

export const AUDIO_STRATEGY_OPTIONS: ReadonlyArray<DescribedBriefOption<AudioStrategy>> = [
  { id: "volcengine-tts", label: "火山语音配音（TTS）", description: "使用设置中的火山语音音色逐镜配音" },
  { id: "native-audio", label: "模型原生音频", description: "整片由模型生成音轨，不再单独 TTS" },
  { id: "mute", label: "静音（不加人声）", description: "只保留画面与背景音乐，不生成人声" },
];

// ==================== Label lookups ====================

function labelOf<T extends string | number>(options: ReadonlyArray<BriefOption<T>>, id: T, fallback: string): string {
  return options.find((option) => option.id === id)?.label ?? fallback;
}

export function inputModeLabel(id: InputMode): string {
  return labelOf(INPUT_MODE_OPTIONS, id, id);
}

export function durationLabel(id: 15 | 30 | 60): string {
  return labelOf(DURATION_OPTIONS, id, `${id}s`);
}

export function videoModeLabel(id: VideoModeId): string {
  return labelOf(VIDEO_MODE_OPTIONS, id, id);
}

/** Unknown values (legacy projects) fall back to the raw id instead of a made-up style name. */
export function scriptStyleLabel(id: string): string {
  return labelOf(SCRIPT_STYLE_OPTIONS, id, id);
}

export function outputStrategyLabel(id: OutputStrategy): string {
  return labelOf(OUTPUT_STRATEGY_OPTIONS, id, id);
}

export function audioStrategyLabel(id: AudioStrategy): string {
  return labelOf(AUDIO_STRATEGY_OPTIONS, id, id);
}

// ==================== Strategy mappings ====================

/**
 * Native film carries its own audio track, so the brief must record it. The other strategies keep
 * the shared default instead of being silently re-routed to another audio source.
 */
export function defaultAudioStrategyFor(strategy: OutputStrategy): AudioStrategy {
  return strategy === "native-film" ? "native-audio" : DEFAULT_CREATION_BRIEF.audioStrategy;
}

/**
 * Where the current script style came from. "auto" stays a recommendation request (never a silent
 * `pain_point`); a concrete style is attributed to the template only when one is applied.
 */
export function resolveStyleSource(input: { styleType: string; templateId?: string }): StyleSource {
  if (input.styleType === "auto") return "performance-recommendation";
  if (input.templateId) return "template";
  return "explicit";
}

// ==================== Validation ====================

export interface CreationBriefFormValidationInput {
  productName: string;
  images: ReadonlyArray<unknown>;
  /** One-sentence topic; replaces the product name + image requirement in topic mode. */
  topic?: string;
  inputMode?: InputMode;
}

export interface CreationBriefFormValidation {
  valid: boolean;
  errors: {
    productName?: string;
    images?: string;
    topic?: string;
  };
}

/** Pure validation for the create form: nothing is submitted until it returns `valid: true`. */
export function validateCreationBriefForm(input: CreationBriefFormValidationInput): CreationBriefFormValidation {
  const errors: CreationBriefFormValidation["errors"] = {};
  if (input.inputMode === "topic") {
    if (!(input.topic ?? "").trim()) errors.topic = "请填写一句话主题";
  } else {
    if (!input.productName.trim()) errors.productName = "请填写商品名称";
    if (input.images.length < 1) errors.images = "请至少上传 1 张商品图";
  }
  return { valid: Object.keys(errors).length === 0, errors };
}
