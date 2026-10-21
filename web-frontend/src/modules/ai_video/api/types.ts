/**
 * ClipForge 接口数据类型（T0.2）。
 *
 * 只保留原生前端会消费的字段；来源为 `integrations/clipforge/src/lib/db/schema.ts`
 * 与各 route 的响应形状（那边是权威定义，本文件只读同步，不反向修改 ClipForge）。
 * 时间字段经 JSON 序列化后一律是 ISO 字符串。
 */

export type IsoDate = string;

// ---------------------------------------------------------------------------
// 创作简报：单一事实源在 lib/creation-brief.ts（T0.5 已复制），这里只做转出
// ---------------------------------------------------------------------------

import type {
  AudioStrategy,
  CreationBrief,
  InputMode,
  OutputStrategy,
  StyleSource,
} from "../lib/creation-brief";
import type {
  CreativeIntent,
  ProjectMediaInsight,
  ProductionSnapshot,
  SemanticAsset,
  VersionTree,
  VisualBible,
  WorkflowStagePlan,
} from "../lib/production-system";
import type { TranscriptEditPlan } from "../lib/transcript-editor";
import type {
  TranscriptEditActor,
  TranscriptEditProposal,
  TranscriptEditSummary,
} from "../lib/transcript-edit-protocol";

export type { AudioStrategy, CreationBrief, InputMode, OutputStrategy, StyleSource };
export type { CreativeIntent, ProjectMediaInsight, ProductionSnapshot, SemanticAsset, VersionTree, VisualBible, WorkflowStagePlan };

// ---------------------------------------------------------------------------
// 项目
// ---------------------------------------------------------------------------

export type ProjectStatus = "draft" | "scripting" | "assets" | "video" | "composing" | "done";
export type VideoMode = "product_closeup" | "graphic_montage" | "scene_demo" | "live_presenter";
export type ProjectSourceType = "manual" | "clone";

export interface Project {
  id: string;
  name: string;
  status: ProjectStatus;
  contentType: "product" | "topic" | null;
  topic: string | null;
  productName: string | null;
  productCategory: string | null;
  productDescription: string | null;
  productPrice: string | null;
  shopUrl: string | null;
  affiliateCode: string | null;
  productImages: string[];
  productAnalysis: string | null;
  productId: string | null;
  brandId: string | null;
  templateId: string | null;
  videoMode: VideoMode | null;
  sourceType: ProjectSourceType | null;
  sourceVideoUrl: string | null;
  characterId: string | null;
  creationBrief: CreationBrief | null;
  createdAt: IsoDate;
  updatedAt: IsoDate;
}

/** POST /api/project 的可写字段（服务端对其余字段直接忽略）。 */
export interface ProjectCreateInput {
  name?: string;
  productName?: string;
  productCategory?: string;
  productDescription?: string;
  productImages?: string[];
  videoMode?: VideoMode;
  sourceType?: ProjectSourceType;
  contentType?: "product" | "topic";
  topic?: string;
  characterId?: string;
  sourceVideoUrl?: string;
  creationBrief?: CreationBrief;
}

/** PATCH /api/project/:id 的白名单字段。 */
export type ProjectPatch = Partial<
  Pick<
    Project,
    | "name"
    | "productName"
    | "productCategory"
    | "productDescription"
    | "productImages"
    | "productAnalysis"
    | "shopUrl"
    | "affiliateCode"
    | "productId"
    | "brandId"
    | "templateId"
    | "videoMode"
    | "sourceType"
    | "sourceVideoUrl"
    | "characterId"
    | "status"
    | "creationBrief"
  >
>;

/** GET /api/works —— 跨项目成片 feed。 */
export interface WorkItem {
  id: string;
  projectId: string;
  projectName: string;
  productName: string | null;
  label: string | null;
  duration: number | null;
  aspectRatio: "9:16" | "16:9" | "1:1" | null;
  createdAt: IsoDate;
  url: string;
  thumbnailUrl: string | null;
}

// ---------------------------------------------------------------------------
// 脚本 / 分镜
// ---------------------------------------------------------------------------

export type ShotType = "hook" | "pain_point" | "product_reveal" | "demo" | "social_proof" | "cta";
export type ShotVisualSource = "ai_generate" | "product_image" | "user_upload";
export type ShotTransition = "ai_start_end" | "ai_reference" | "direct_concat" | "ffmpeg_fade";
export type ShotMotion = "zoom_in_slow" | "pan_left" | "pan_right" | "ken_burns" | "static";

export interface Shot {
  shotId: number;
  type: ShotType;
  /** 秒 */
  duration: number;
  description: string;
  camera: string;
  visualSource: ShotVisualSource;
  transition: ShotTransition;
  voiceover: string;
  prompt?: string;
  /** 免费素材库检索关键词（英文，1–3 个） */
  stockKeywords?: string[];
  characterId?: string;
  motion?: ShotMotion;
  textOverlay?: {
    text: string;
    style: "title" | "subtitle" | "highlight" | "price";
  };
}

export interface ScriptCharacter {
  id: string;
  name: string;
  gender: "female" | "male";
  persona?: string;
  appearance?: string;
}

export interface Script {
  id: string;
  projectId: string;
  version: number;
  styleType: string;
  title: string | null;
  totalDuration: number | null;
  shots: Shot[];
  characters: ScriptCharacter[] | null;
  selected: boolean;
  createdAt: IsoDate;
}

export interface ShotTextPatch {
  shotId: number;
  voiceover?: string;
  description?: string;
  camera?: string;
}

/** PATCH /api/project/:id/scripts 的两种操作二选一。 */
export type ScriptPatchInput =
  | { selectedScriptId: string }
  | { scriptId: string; shotTexts: ShotTextPatch[] };

export interface ScriptPatchResult {
  success: boolean;
  script?: Script;
}

/** POST /api/llm/script 的请求体（服务端对未列出的字段忽略）。 */
export interface ScriptGenerationInput {
  projectId?: string;
  productName: string;
  productImages?: string[];
  productDescription?: string;
  productAnalysis?: string;
  category?: string;
  productCategory?: string;
  targetDuration?: 15 | 30 | 60;
  duration?: 15 | 30 | 60;
  insightMode?: boolean;
  styleType?: string;
  targetAudience?: string;
  platforms?: string;
  narrative?: CreationBrief["narrative"];
  character?: { name?: string; gender?: "female" | "male"; persona?: string; appearance?: string };
  templateId?: string;
  videoMode?: VideoMode;
  priceRange?: string;
  usageAdvantage?: string;
  referenceStructure?: unknown;
  customRequirements?: string;
  preferredHookId?: string;
  llmConfig: { baseUrl: string; apiKey: string; model: string };
}

export interface ScriptGenerationResult {
  scripts: Array<Pick<Script, "id" | "title" | "styleType" | "totalDuration" | "shots" | "selected">>;
  analysis?: string;
  styleType?: string;
}

/** POST /api/llm/script 返回 409 + code="needs_explicit_style" 时的错误体。 */
export interface ScriptStyleConflict {
  error: string;
  code: "needs_explicit_style";
  reason: "no-data" | "unknown-style";
  candidates: string[];
}

/** POST /api/topic/script（一句话主题 → 旁白脚本）。 */
export interface TopicScriptInput {
  topic: string;
  projectId?: string;
  targetDuration?: number;
  count?: number;
  platforms?: string;
  /** 旁白风格（knowledge/story/lifestyle/inspiration/travel） */
  narrationStyle?: string;
  llmConfig?: { baseUrl?: string; apiKey?: string; model?: string };
}

export interface TopicScriptResult {
  projectId: string;
  scripts: Script[];
}

export type JudgeId = "pace" | "voice" | "idea" | "structure" | "visual";
export type JudgeTier = "invariant" | "default" | "taste";

export interface JudgeIssue {
  shotId?: number;
  issue: string;
  tier: JudgeTier;
}

export interface JudgeVerdict {
  judge: JudgeId;
  issues: JudgeIssue[];
}

export interface JudgeRewrite {
  shotId: number;
  voiceover: string;
  tier: JudgeTier;
}

export interface JudgeDescriptionRewrite {
  shotId: number;
  description: string;
  tier: JudgeTier;
}

export interface ScriptJudgeReport {
  verdicts: JudgeVerdict[];
  rewrites: JudgeRewrite[];
  descriptionRewrites: JudgeDescriptionRewrite[];
  summary?: string;
}

/** 台词未变化时判官团直接跳过（不重复花 LLM 调用）。 */
export type ScriptJudgeResult = ScriptJudgeReport | { skipped: true };

// ---------------------------------------------------------------------------
// 素材
// ---------------------------------------------------------------------------

export type AssetType = "ai_generated" | "product_image" | "user_upload" | "stock_footage";
export type AssetStatus = "pending" | "generating" | "done" | "failed";

export interface Asset {
  id: string;
  projectId: string;
  shotId: number;
  type: AssetType;
  /** 相对访问路径（/api/files/...）或本地落盘路径；UI 只能展示 cfUrl 后的相对路径。 */
  filePath: string | null;
  thumbnailPath: string | null;
  provider: string | null;
  model: string | null;
  prompt: string | null;
  sourceUrl: string | null;
  author: string | null;
  license: string | null;
  selected: boolean;
  status: AssetStatus;
  createdAt: IsoDate;
  /** 视频素材的尾帧（用于镜头衔接），仅 GET /assets 返回。 */
  lastFrameUrl?: string;
}

export interface SaveAssetInput {
  shotId: number;
  sourceUrl: string;
  type?: "ai_generate" | "ai_generated" | "product_image" | "user_upload";
  provider?: string;
  model?: string;
  prompt?: string;
  thumbnailPath?: string;
  generationPlan?: Record<string, unknown>;
}

/** 本地素材库条目（GET/PATCH /api/project/:id/materials）。 */
export interface LocalMaterial {
  name: string;
  originalName: string;
  mediaType: "video" | "image";
  tags: string[];
  sizeBytes: number;
  updatedAt: number;
  width?: number;
  height?: number;
  durationSec?: number;
}

export interface PublicLocalMaterial extends LocalMaterial {
  url: string;
}

export interface MaterialUploadResult {
  materials: PublicLocalMaterial[];
  /** multipart 批量上传时按文件返回的失败项 */
  errors?: Array<Record<string, unknown>>;
  duplicate?: boolean;
}

// ---------------------------------------------------------------------------
// 免费素材库
// ---------------------------------------------------------------------------

export type StockSourceId =
  | "pexels"
  | "pixabay"
  | "openverse"
  | "wikimedia"
  | "local"
  | "nasa"
  | "archive"
  | "coverr"
  | "jamendo"
  | "freesound";

export type StockMediaType = "video" | "image" | "audio";
export type StockOrientation = "portrait" | "landscape" | "square";

export interface StockSourceMeta {
  id: StockSourceId;
  label: string;
  keyless: boolean;
  mediaTypes: StockMediaType[];
  signupUrl?: string;
  note?: string;
  available: boolean;
}

export interface StockCandidate {
  source: StockSourceId;
  mediaType: StockMediaType;
  id: string | number;
  downloadUrl: string;
  pageUrl: string;
  author: string;
  authorUrl: string;
  license: string;
  licenseUrl?: string;
  attributionText?: string;
  requiresAttribution?: boolean;
  width?: number;
  height?: number;
  durationSec?: number;
  previewImage?: string;
  title?: string;
  tags?: string[];
}

export interface StockSearchInput {
  query: string;
  source?: StockSourceId | "all";
  mediaType?: StockMediaType;
  orientation?: StockOrientation;
  perPage?: number;
  minSec?: number;
  maxSec?: number;
  download?: boolean;
  projectId?: string;
  shotId?: number;
  count?: number;
  apiKeys?: Partial<Record<StockSourceId, string>>;
  apiKey?: string;
}

/** download=true 时落库的素材：asset 行 + 下载源信息。 */
export interface StockAsset extends Asset {
  bytes?: number;
  mediaType?: StockMediaType;
  downloadUrl?: string;
  attributionText?: string;
}

export interface StockSearchResult {
  candidates?: StockCandidate[];
  /** download=true：已下载并落库的素材 */
  assets?: StockAsset[];
  candidatesCount?: number;
  skippedSources?: StockSourceId[];
}

export interface StockFillInput {
  source?: StockSourceId | "all";
  /** auto = 逐镜先找视频、没有再退图片 */
  mediaType?: StockMediaType | "auto";
  orientation?: StockOrientation;
  apiKeys?: Partial<Record<StockSourceId, string>>;
  force?: boolean;
  llmConfig?: { baseUrl?: string; apiKey?: string; model?: string };
}

export type StockFallbackLevel = "original" | "narrowed" | "universal";

export interface ShotFillResult {
  shotId: number;
  ok: boolean;
  query: string;
  provider?: string;
  mediaType?: StockMediaType;
  matchedBy?: "semantic" | "heuristic";
  sameSource?: boolean;
  fallbackLevel?: StockFallbackLevel;
  /** 通用兜底素材，需要用户确认 */
  warning?: "stock_universal_fallback";
  reason?: string;
}

export interface StockFillResult {
  projectId: string;
  scriptId: string;
  total: number;
  filled: number;
  semantic?: boolean;
  sameSourceHits: number;
  universalFallbacks: number[];
  results: ShotFillResult[];
}

// ---------------------------------------------------------------------------
// 合成 / 成片
// ---------------------------------------------------------------------------

export type CompositionStatus = "pending" | "composing" | "done" | "failed";
export type CompositionResolution = "720p" | "1080p";
export type AspectRatio = "9:16" | "16:9" | "1:1";

export interface Composition {
  id: string;
  projectId: string;
  thumbnailPath?: string | null;
  resolution: CompositionResolution | null;
  aspectRatio: AspectRatio | null;
  /** 毫秒 */
  duration: number | null;
  ttsEnabled: boolean | null;
  aigcBadge: boolean | null;
  label: string | null;
  strategy: OutputStrategy | null;
  status: CompositionStatus;
  createdAt: IsoDate;
  /** 以下由 GET /api/project/:id/compose 派生，只暴露相对 URL。 */
  fileName?: string | null;
  url?: string | null;
  timelineUrl?: string | null;
}

export interface ComposeInput {
  exportAudioStems?: boolean;
  voiceoverOverrides?: Array<{ shotId: number; voiceover: string }>;
  ttsConfig?: Record<string, unknown>;
  freeTts?: { enabled?: boolean; voice?: string; rate?: string };
  renderPreset?: unknown;
  captionPreset?: unknown;
  resolution?: CompositionResolution;
  aspectRatio?: AspectRatio;
  aigcBadge?: boolean;
  label?: string;
  ctaText?: string;
  aigcBadgeText?: string;
  bgmPath?: string;
  freeBgm?: boolean;
  bgmMood?: string;
  bgmVolume?: number;
  bgmDuck?: boolean;
  voiceGround?: boolean;
  karaoke?: boolean;
  productCard?: boolean;
}

/** POST /api/project/:id/compose 返回 202 + {compositionId,status}。 */
export interface ComposeAccepted {
  compositionId: string;
  status: "composing";
}

/** GET /api/project/:id/compositions/:cid/timeline */
export interface CompositionTimelineResult {
  compositionId: string;
  status: "ready" | "missing";
  fileName: string | null;
  timelineUrl: string | null;
  /** 逐镜音频/时长报告（结构由 sidecar 的 timeline sidecar 决定，页面按需读取） */
  timeline: unknown;
}

// ---------------------------------------------------------------------------
// 导出 / 发布（T2.4）
// ---------------------------------------------------------------------------

export interface PublishCopy {
  titles: string[];
  hashtags: string[];
  caption: string;
  commentKit?: {
    pinned?: string;
    objections?: Array<{ q?: string; a?: string }>;
    notice?: string;
  };
}

export type PlatformExportPlatform =
  | "douyin"
  | "kuaishou"
  | "xiaohongshu"
  | "shipinhao"
  | "tiktok"
  | "reels"
  | "shorts";

export interface PlatformExportResult {
  success: boolean;
  compositionId: string | null;
  platform: string;
  platformName: string;
  url: string;
  size: string;
  framing?: Record<string, unknown>;
  report?: Record<string, unknown>;
}

export interface QcCheck {
  id: string;
  level: "ok" | "warn" | "fail";
  message: { zh: string; en: string };
  data?: Record<string, unknown>;
}

export interface QcReport {
  compositionId: string;
  status: "ok" | "warn" | "fail";
  checks: QcCheck[];
  probe?: Record<string, unknown>;
  signals?: Record<string, unknown>;
}

export interface GateItem {
  id: string;
  status: "pass" | "warn" | "fail";
  message: { zh: string; en: string };
  problems: Array<{ zh: string; en: string }>;
}

export interface GateReport {
  status: "pass" | "warn" | "fail";
  items: GateItem[];
  summary: { pass: number; warn: number; fail: number };
  verdict: { zh: string; en: string };
}

export interface GateResult {
  projectId: string;
  compositionId: string | null;
  report: GateReport;
}

export interface CreditItem {
  shotId: number;
  kind: "image" | "video" | "audio" | "unknown";
  origin: "stock" | "own" | "ai";
  provider?: string;
  author?: string;
  license?: string;
  sourceUrl?: string;
  requiresAttribution: boolean;
  risk: "ok" | "attribution" | "review";
  note: { zh: string; en: string };
  attributionLine?: string;
}

export interface CreditsManifest {
  project: string;
  items: CreditItem[];
  bgm?: CreditItem;
  summary: {
    total: number;
    needsAttribution: number;
    needsReview: number;
    commercialSafe: boolean;
  };
}

export interface ContactSheetResult {
  sheet: string;
  cuts: unknown[];
  proxy?: string;
}

export interface CoverResult {
  cover: string;
  title: string;
}

export interface CarouselResult {
  cards: string[];
}

export interface ShopQrResult {
  qr: string;
  shopLink: string;
  warning?: { zh: string; en: string } | null;
}

export interface EndCardResult {
  video: string;
  shopLink: string;
  warning?: { zh: string; en: string } | null;
}

export interface NativeFeelResult {
  video: string;
}

export interface DubResult {
  scriptId: string;
  version: number;
  targetLang: string;
  shots: number;
  recommendedVoice: string;
}

export interface PublishMetricInput {
  platform?: string;
  views?: number;
  likes?: number;
  comments?: number;
  shares?: number;
  orders?: number;
  note?: string;
  hookId?: string;
}

export interface PublishMetric extends PublishMetricInput {
  id: string;
  projectId: string;
  createdAt: IsoDate;
}

// ---------------------------------------------------------------------------
// 制作控制台 / 质量门禁（T2.5）
// ---------------------------------------------------------------------------

export interface ProductionProject extends Project {
  productionWorkflow?: WorkflowStagePlan[] | null;
  creativeIntent?: CreativeIntent | null;
  visualBible?: VisualBible | null;
  mediaInsights?: ProjectMediaInsight[] | null;
  versionSnapshots?: ProductionSnapshot[] | null;
}

export interface ProductionFailure {
  source: "task" | "pipeline";
  id: string;
  status: string;
  stage: string;
  error: string;
  createdAt: IsoDate | number | null;
}

export interface ProductionOverview {
  project: ProductionProject;
  workflow: WorkflowStagePlan[] | null;
  creativeIntent: CreativeIntent | null;
  visualBible: VisualBible | null;
  mediaInsights: ProjectMediaInsight[];
  snapshots: ProductionSnapshot[];
  semanticAssets: SemanticAsset[];
  versionTree: VersionTree;
  latestRun: Record<string, unknown> | null;
  latestComposition: Composition | null;
  latestFailure: ProductionFailure | null;
  selectedScript: { id: string; shotCount: number; totalDuration: number } | null;
  counts: { scripts: number; assets: number; clips: number; tasks: number; compositions: number };
}

export interface ProductionPatch {
  creativeIntent?: CreativeIntent;
  visualBible?: VisualBible;
  productionWorkflow?: WorkflowStagePlan[];
  action?: "snapshot";
  label?: string;
}

export type QualityVerdict = "accept" | "review" | "reject";

export interface GenerationQualityReport {
  version: number;
  summary: string;
  overall: number;
  confidence: number;
  verdict: QualityVerdict;
  dimensions: Array<{ id: string; score: number; note?: string }>;
  issues: Array<{ severity: string; observation: string; time?: number }>;
  evaluatedAt: string;
}

export interface GenerationReview {
  id: string;
  assetId: string;
  shotId: number;
  contract: Record<string, unknown>;
  report: GenerationQualityReport;
  disposition: { action: string; paid: boolean; automatic: boolean; reason: string } | null;
  evaluatorModel: string;
  verdict: QualityVerdict;
  humanDecision: "accepted" | "rejected" | null;
  createdAt: IsoDate;
  updatedAt: IsoDate;
}

export interface QualityCandidate {
  id: string;
  shotId: number;
  type: string;
  filePath: string;
  thumbnailPath?: string | null;
  provider?: string | null;
  model?: string | null;
  prompt?: string | null;
  generationPlan?: Record<string, unknown> | null;
  selected: boolean;
  createdAt: IsoDate;
  latestReview: GenerationReview | null;
}

export interface QualityOverview {
  candidates: QualityCandidate[];
  reviewCount: number;
  modelStats: Array<{ model: string; reviews: number; averageOverall: number; rejectionRate: number }>;
}

export interface VideoRepairSummary {
  version: number;
  kind: "repair";
  operationId: string;
  sourceAssetId: string;
  reviewId: string;
  shotId: number;
  provider: string;
  model: string;
  window: { start: number; end: number };
  requestedScope: string;
  effectiveScope: "full-frame" | "region";
  region?: { x: number; y: number; width: number; height: number };
  keyframes: Array<{ assetId: string; time: number; role: string }>;
  strategy: string;
  generatedDuration: number;
  estimatedCostUsd?: number;
  referenceCount: number;
  audioMode: string;
  warnings: string[];
  planHash: string;
}

export interface VideoRepairPreview {
  summary: VideoRepairSummary;
  prompt: string;
  requestedRegion?: Record<string, unknown>;
  keyframes: Array<{ assetId: string; time: number; role: string }>;
  capabilities: Record<string, unknown>;
  executable: boolean;
  estimatedCost: { currency: "USD"; min?: number; max?: number };
  estimatedSeconds: { min: number; max: number };
}

export interface MasteringAnalysis {
  duration: number;
  hasAudio: boolean;
  loudness?: { integratedLufs?: number; truePeakDb?: number } | null;
  splicePoints?: number[];
  [key: string]: unknown;
}

export interface MasteringResult {
  compositionId: string;
  status?: "composing";
  analysis: MasteringAnalysis;
  options?: { normalizeAudio: boolean; deflicker: boolean };
}

// ---------------------------------------------------------------------------
// 爆款复刻
// ---------------------------------------------------------------------------

export interface ReplicateShot {
  index: number;
  /** 秒 */
  duration: number;
}

export interface ReplicateAnalysis {
  duration: number;
  shots: ReplicateShot[];
  /** 交给脚本接口的镜头节奏骨架 */
  referenceStructure: string;
}

// ---------------------------------------------------------------------------
// 付费 AI 任务
// ---------------------------------------------------------------------------

export type AiTaskStatus = "submitted" | "processing" | "completed" | "failed" | "unknown";

export interface AiTask {
  id: string;
  projectId: string | null;
  shotId: number | null;
  provider: string;
  model: string;
  mediaType: "image" | "video";
  mode: string | null;
  prompt: string | null;
  /** 供应商侧任务 ID —— 恢复付费任务的唯一凭据。 */
  taskId: string;
  status: AiTaskStatus;
  resultUrls: string[] | null;
  error: string | null;
  createdAt: IsoDate;
  updatedAt: IsoDate;
}

export interface AiVideoSubmitInput {
  provider: string;
  model: string;
  prompt?: string;
  imageUrl?: string;
  lastImageUrl?: string;
  mode?: string;
  apiKey: string;
  baseUrl?: string;
  options?: Record<string, unknown>;
  projectId?: string;
  shotId?: number;
  referenceVideoUrls?: string[];
  referenceImageUrls?: string[];
  referenceAudioUrls?: string[];
  controlPlan?: Record<string, unknown>;
}

export interface AiVideoResult {
  taskId: string;
  videoUrls: string[];
  modelId: string;
  duration?: number;
  processingTime?: number;
  hasAudio?: boolean;
}

/** 超时/断连时后端仍返回 taskId + recoverable：只能恢复，禁止重复提交。 */
export interface AiVideoRecoverable {
  error: string;
  taskId: string;
  modelId: string;
  recoverable: boolean;
}

export interface AiVideoTaskQueryInput {
  provider: string;
  apiKey: string;
  baseUrl?: string;
  taskId: string;
  wait?: boolean;
}

export interface AiVideoTaskQueryResult {
  taskId: string;
  status: "pending" | "processing" | "completed" | "failed" | "cancelled";
  videoUrls?: string[];
  error?: string;
}

export interface AiTestProviderResult {
  ok: boolean;
  error?: string;
  [key: string]: unknown;
}

// ---------------------------------------------------------------------------
// 批量任务
// ---------------------------------------------------------------------------

export type BatchJobStatus = "running" | "done" | "cancelled";
export type BatchItemStatus = "pending" | "generating" | "composing" | "done" | "failed";

export interface BatchJob {
  id: string;
  status: BatchJobStatus;
  total: number;
  config: Record<string, unknown> | null;
  createdAt: IsoDate;
  updatedAt: IsoDate;
}

export interface BatchJobItem {
  id: string;
  jobId: string;
  productId: string;
  productName: string;
  variation: string | null;
  projectId: string | null;
  compositionId: string | null;
  status: BatchItemStatus;
  error: string | null;
  createdAt: IsoDate;
  updatedAt: IsoDate;
}

export interface BatchJobSnapshot {
  job: BatchJob | null;
  items: BatchJobItem[];
}

export interface BatchJobCreateInput {
  config: Record<string, unknown>;
  items: Array<{ productId: string; productName: string; variation?: string }>;
}

export interface BatchJobCreated {
  jobId: string;
  items: Array<{ id: string; productId: string }>;
}

export interface BatchItemPatch {
  status?: BatchItemStatus;
  projectId?: string;
  compositionId?: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// 逐字稿 / 长视频
// ---------------------------------------------------------------------------

export interface TimeRange {
  start: number;
  end: number;
}

export interface TranscriptWord extends TimeRange {
  id: string;
  text: string;
  confidence?: number;
}

export interface TranscriptSegment extends TimeRange {
  id: string;
  text: string;
}

export interface TranscriptDocument {
  version: 1;
  text: string;
  language: string;
  duration: number;
  model: string;
  device: "webgpu" | "wasm";
  words: TranscriptWord[];
  segments: TranscriptSegment[];
  silenceRanges: TimeRange[];
  createdAt: string;
}

export interface TranscriptCheckpointSummary {
  processedSeconds: number;
  duration: number;
  resumable: boolean;
  model: string;
  language: string;
}

export type MediaSourceStatus = "uploaded" | "transcribing" | "ready" | "failed";

export interface MediaSource {
  id: string;
  projectId: string;
  originalName: string;
  mimeType: string;
  sizeBytes: number;
  /** 毫秒 */
  duration: number;
  width: number;
  height: number;
  hasAudio: boolean;
  status: MediaSourceStatus;
  progress: number;
  model: string | null;
  device: "webgpu" | "wasm" | null;
  language: string | null;
  transcript: TranscriptDocument | null;
  checkpoint: TranscriptCheckpointSummary | null;
  error: string | null;
  createdAt: IsoDate;
  updatedAt: IsoDate;
  /** GET /api/project/:id/media 补充的访问路径。 */
  url: string;
  edits: MediaEdit[];
}

export type MediaEditStatus = "queued" | "rendering" | "done" | "failed" | "cancelled";

export interface MediaEdit {
  id: string;
  projectId: string;
  sourceId: string;
  revision: number;
  operationId?: string | null;
  baseRevision?: number;
  actor?: TranscriptEditActor;
  plan: TranscriptEditPlan;
  keepRanges?: TimeRange[];
  summary?: TranscriptEditSummary | null;
  status: MediaEditStatus;
  progress: number;
  compositionId: string | null;
  error: string | null;
  createdAt: IsoDate;
  updatedAt: IsoDate;
  composition?: MediaCompositionResult | null;
}

// ---------------------------------------------------------------------------
// 任务中心
// ---------------------------------------------------------------------------

export type TaskCenterKind =
  | "pipeline"
  | "pipeline_interrupted"
  | "pipeline_failed"
  | "transcript"
  | "transcript_failed"
  | "compose"
  | "paid"
  | "paid_unknown"
  | "batch"
  | "done";

export interface TaskCenterEntry {
  kind: TaskCenterKind;
  id: string;
  projectId?: string;
  projectName?: string;
  stage?: string;
  label?: string | null;
  provider?: string;
  model?: string;
  mediaType?: "image" | "video";
  status?: string;
  progress?: number;
  revision?: number;
  sourceId?: string;
  total?: number;
  done?: number;
  failed?: number;
  createdAt: IsoDate;
}

export interface TaskCenterSnapshot {
  active: TaskCenterEntry[];
  attention: TaskCenterEntry[];
  recent: TaskCenterEntry[];
}

// ---------------------------------------------------------------------------
// 设置 / 默认值 / LLM
// ---------------------------------------------------------------------------

/** GET /api/clipforge/defaults —— 运维下发的默认值，只补空字段。 */
export interface ClipForgeDefaults {
  providers?: Record<string, { enabled?: boolean; apiKey?: string; baseUrl?: string }>;
  llm?: {
    provider?: string;
    baseUrl?: string;
    apiKey?: string;
    model?: string;
    visionModel?: string;
  };
  defaultImageModel?: string;
  defaultVideoModel?: string;
}

export interface LlmTestInput {
  baseUrl: string;
  apiKey: string;
  model?: string;
}

export interface LlmTestResult {
  ok: boolean;
  status?: number;
  error?: string;
  warning?: string;
}

export interface LlmModelsResult {
  ok: boolean;
  models: string[];
  error?: string;
}

// ---------------------------------------------------------------------------
// AI 平台 / 模型目录 / 诊断
// ---------------------------------------------------------------------------

export interface AiModel {
  id: string;
  name: string;
  description?: string;
  modes: string[];
  mediaType: "image" | "video";
  provider: string;
  supportsAudio?: boolean;
  extra?: Record<string, unknown>;
}

export interface AiProviderCatalogStatus {
  provider: string;
  mediaType: "image" | "video";
  status: "ready" | "fallback" | "empty" | "error";
  checkedAt: string;
  durationMs: number;
  count: number;
  source?: string;
  catalogUpdatedAt?: string;
  errorCode?: string;
}

export interface ProviderTestResult {
  status: "ok" | "invalid" | "unknown";
  message: string;
}

export interface SidecarHealthSnapshot {
  httpStatus: number;
  /** /api/health 的原始 JSON（503 时同样有诊断体） */
  payload: unknown;
}

// ---------------------------------------------------------------------------
// 服务端流水线 / 原生整片
// ---------------------------------------------------------------------------

export type PipelineStage = "judge" | "stock_fill" | "compose";
export type PipelineStatus = "running" | "done" | "failed";

export interface PipelineRun {
  id: string;
  projectId: string;
  scriptId: string | null;
  stage: PipelineStage;
  status: PipelineStatus;
  compositionId: string | null;
  error: string | null;
  createdAt: IsoDate;
  updatedAt: IsoDate;
  /** executor 已消失（页面/服务重启）时由 GET 标记，供 UI 提供断点续跑 */
  interrupted?: boolean;
}

export interface FilmSpendEstimate {
  unitUsd: number;
  seconds: number;
  minUsd: number;
  maxUsd: number;
  tierMultiplier: number;
}

export interface FilmDryRun {
  dryRun: true;
  prompt: string;
  shotCount: number;
  seconds: number;
  model: string;
  scriptSeconds: number;
  modelMaxSeconds: number;
  durationOverflow: boolean;
  referenceImages: number;
  referenceQuota: { ok: boolean; count: number; limit?: number };
  dialogueWarnings: Array<{ index: number; seconds: number; count: number; limit: number }>;
  estimate?: FilmSpendEstimate;
}

export interface FilmResult {
  url: string;
  compositionId: string;
  fileName: string;
  taskId?: string;
  modelId?: string;
  seconds?: number;
  dialogueWarnings?: FilmDryRun["dialogueWarnings"];
}

export interface StoryboardGridResult {
  reused?: boolean;
  gridPath?: string;
  cells?: Array<Record<string, unknown>>;
  count: number;
}

// ---------------------------------------------------------------------------
// 洞察 / 自检
// ---------------------------------------------------------------------------

export interface HomogeneityResult {
  total: number;
  similarPairs: Array<{ a?: string; b?: string; similarity?: number }>;
  similarShare: number;
  verdict: "ok" | "warn" | "risk" | string;
  message: { zh: string; en: string } | null;
}

// ---------------------------------------------------------------------------
// 趋势 / 商品导入
// ---------------------------------------------------------------------------

export type TrendSource = "douyin" | "toutiao" | "google";

export interface TrendTopic {
  title: string;
  traffic?: string;
  context?: string;
  rank?: number;
  hotValue?: number;
  source?: TrendSource;
}

export interface TrendsResult {
  source: TrendSource | "google";
  geo?: string;
  count: number;
  topics: TrendTopic[];
}

/** POST /api/ingest/product 解析出的商品信息（原 lib/product-ingest.ts 的 ProductIngest）。 */
export interface IngestedProduct {
  title: string;
  priceText?: string;
  description?: string;
  images: string[];
  sourceUrl: string;
}

export interface IngestProductInput {
  url: string;
  /** false = 只解析不入库；默认 true（建项目并下载商品图） */
  createProject?: boolean;
  /** 商品库导入模式：下载图片到该商品目录，返回持久 /api/files 路径 */
  libraryProductId?: string;
}

export interface IngestProductResult {
  product: IngestedProduct;
  /** libraryProductId 模式：下载后仍可长期访问的图片路径 */
  images?: string[];
  /** createProject=true（默认）：已创建的项目与其商品图 */
  projectId?: string;
  productImages?: string[];
}

export interface UploadResult {
  paths: string[];
}

// ---------------------------------------------------------------------------
// 逐字稿 / 本地转写（T2.6）
// ---------------------------------------------------------------------------

export interface MediaCompositionResult {
  id: string;
  status: "pending" | "composing" | "done" | "failed";
  label?: string | null;
  outputUrl?: string | null;
  downloadUrl?: string | null;
}

export interface TranscriptClipCandidate {
  id: string;
  sourceRange: TimeRange;
  duration: number;
  text: string;
  firstWordId: string;
  lastWordId: string;
  wordCount: number;
  speechRatio: number;
  reasons: string[];
  plan: TranscriptEditPlan;
}

export interface TranscriptClipsResult {
  projectId: string;
  mediaId: string;
  latestRevision: number;
  method: "local-transcript";
  query: string;
  targetSeconds: number;
  candidates: TranscriptClipCandidate[];
}

export interface TranscriptEditResponse {
  idempotent?: boolean;
  proposal?: TranscriptEditProposal;
  edit?: MediaEdit | null;
  compositionId?: string | null;
  status?: string;
}

export type { TranscriptEditProposal, TranscriptEditSummary, TranscriptEditPlan, TranscriptEditActor };

// ---------------------------------------------------------------------------
// 商品库 / 角色 / 品牌（本地 store 与设置页共用）
// ---------------------------------------------------------------------------

export type ProductCategory = "beauty" | "food" | "home" | "fashion" | "tech" | "other";

/** 商品库条目（原 lib/stores/product-library-store.ts 的 ProductItem，键 `daihuo-jianshou-products`）。 */
export interface ProductItem {
  id: string;
  name: string;
  category: ProductCategory;
  description?: string;
  images: string[];
  price?: string;
  targetAudience?: string;
  videoCount: number;
  createdAt: Date;
}

export interface CharacterVoiceProfile {
  style: string;
  speed?: number;
  emotion?: "neutral" | "happy" | "serious" | "energetic";
}

/** 主播角色（原 lib/stores/project-store.ts 的 Character，键 `daihuo-jianshou-characters`）。 */
export interface Character {
  id: string;
  name: string;
  description?: string;
  appearance?: string;
  referenceImages: string[];
  voiceProfile?: CharacterVoiceProfile;
  isDefault?: boolean;
}
