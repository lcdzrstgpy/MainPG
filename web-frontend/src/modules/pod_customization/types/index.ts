export type PodTemplateSource = "system" | "personal";

/** 结构视图角色：dimension（尺寸图）只用于识图，不参与生图。 */
export type PodTemplateViewRole = "side" | "top" | "dimension";

export type PodTemplateView = {
  role: PodTemplateViewRole;
  url: string;
  width: number;
  height: number;
};

/** none=无视图；ready=可直接用；needs_confirmation=低置信度待人工确认；failed=识图失败。 */
export type PodStructureStatus = "none" | "ready" | "needs_confirmation" | "failed";

export type PodStructureFacts = {
  product_type?: string;
  part_count?: number;
  tiers?: number;
  units_per_tier?: number;
  height_mm?: number;
  thickness_mm?: number;
  material?: string;
  transparency?: string;
  surface_finish?: string;
  symmetry?: string;
  connections?: string;
  printable_regions?: string[];
  confidence?: Record<string, number>;
};

export type PodTemplate = {
  id: string;
  name: string;
  source: PodTemplateSource;
  /** 主图资产 id：智能填写用它附带模板图生成批级美术指导。 */
  asset_id?: string;
  preview_url: string;
  original_url: string;
  width: number;
  height: number;
  error_message?: string;
  structure_status?: PodStructureStatus;
  structure_facts?: PodStructureFacts;
  structure_analysis_version?: string;
  views?: PodTemplateView[];
  created_at: string;
  updated_at: string;
};

export type PodBusinessFieldsDraft = {
  product_name: string;
  product_category: string;
  target_market: string;
  target_audience: string;
  core_selling_points: string;
  design_theme: string;
  /** 批级全局字段：用户在「智能填写」区直接填写的产品工艺（如绗缝 / 缎面绣），留空即不约束。 */
  fabric_craft: string;
  /** 批级全局字段：整批统一的场景基调（款间在基调内变化），留空即回退默认场景轮换。 */
  scene_mood: string;
  style_keywords: string;
  /** 主题强关联色：必须「主题词 + 颜色」，生图时每格至少用 1 款、且主色必须来自这里。 */
  color_preferences: string;
  /** 建议点缀色：只作辅色/点缀，不得当主色；不要求主题前缀。 */
  accent_colors: string;
  excluded_elements: string;
  /** 选填：用户手写的上架文案限制（如「标题不要出现刺绣」），只作用于标题与描述，不影响图片。 */
  copy_restrictions: string;
};

export type PodBusinessFields = Omit<PodBusinessFieldsDraft,
  "core_selling_points" | "style_keywords" | "color_preferences" | "accent_colors" | "excluded_elements"
> & {
  core_selling_points: string[];
  style_keywords: string[];
  color_preferences: string[];
  accent_colors: string[];
  excluded_elements: string[];
};

/** 智能前置层「一句话 → 10 个业务字段」的接口响应；fields 与批次业务字段同形。 */
export type PodBriefFieldsResponse = {
  brief_id: string;
  prompt_version: string;
  model: string;
  fields: PodBusinessFields;
  /** 批级美术指导：只有请求带模板图且视觉调用成功时才有值。 */
  art_direction?: PodArtDirection | null;
};

/**
 * 批级美术指导：随智能填写一次性生成，整批（所有二级批次、所有轮次）共用。
 * 只注入第 1 轮的美术指导，派生轮由锚点继承场景与光线。
 */
export type PodSceneOption = {
  scene: string;
  surface: string;
  props: string[];
};

export type PodArtDirection = {
  scene: string;
  lighting: string;
  color_temperature: string;
  mood: string;
  palette: string[];
  props: string[];
  background: string;
  composition_hint: string;
  avoid: string[];
  /** 豆包返回的 2-3 个候选场景；生图按款轮换，旧数据可能没有。 */
  scene_options?: PodSceneOption[];
};

/** 智能前置层实际代填的字段：业务字段去掉「上架文案限制」（该类限制不由智能填写生成）。 */
export type PodBriefFieldsDraft = Omit<PodBusinessFieldsDraft, "copy_restrictions">;

/** 「最近生成」历史条目；fields 为回填前的字符串口径（数组已用「、」连接）。 */
export type PodBriefHistoryItem = {
  id: string;
  input: string;
  fields: PodBriefFieldsDraft;
  created_at: string;
};

export type PodTitleMode = "long" | "short";

export type PodSkuDraft = {
  name: string;
  declared_price: string;
  weight_g: string;
};

export type PodSku = {
  name: string;
  declared_price: number;
  weight_g: number;
};

export type PodListingFieldsDraft = {
  title_mode: PodTitleMode;
  category_name: string;
  skus: PodSkuDraft[];
};

export type SpecCardStyle = "light" | "dark";

export type SpecCardCorner = "bottom-right" | "bottom-left" | "top-right" | "top-left";

export type SpecCardDisplayUnit = "cm" | "in";

/** 第 4 张图（素材图）的尺寸卡；单元格按厘米冻结，消费端按 display_unit 派生展示值。 */
export type SpecCardConfig = {
  enabled: boolean;
  style: SpecCardStyle;
  corner: SpecCardCorner;
  display_unit: SpecCardDisplayUnit;
  cells: string[][];
};

export type SpecCardPreviewRequest = {
  cells: string[][];
  style: SpecCardStyle;
  corner: SpecCardCorner;
  display_unit: SpecCardDisplayUnit;
  enabled: boolean;
  base_template_id?: string;
};

export type SpecCardPreviewResponse = {
  image: string;
};

export type SpecCardReprintRequest = {
  cells: string[][];
  style: SpecCardStyle;
  corner: SpecCardCorner;
  display_unit: SpecCardDisplayUnit;
  enabled: boolean;
  style_index?: number | null;
};

export type SpecCardReprintError = {
  style_index: number;
  message: string;
};

export type SpecCardReprintResponse = {
  saved: boolean;
  reprinted: number;
  failed: number;
  errors: SpecCardReprintError[];
  needs_re_export: boolean;
};

export type PodListingFields = {
  title_mode: PodTitleMode;
  category_name: string;
  skus: PodSku[];
  /** 批次冻结快照里的规格卡配置；旧批次快照可能缺失该键。 */
  spec_card?: SpecCardConfig;
};

export type PodDianxiaomiExportStatus = {
  ready: boolean;
  exportable_style_count: number;
  skipped_style_count: number;
  selected_exportable_style_count?: number;
  user_excluded_style_count?: number;
  block_reason: string | null;
};

/** 妙手 Temu 导入模板类型：服饰类 / 非服饰类。 */
export type PodMiaoshouTemplateKind = "apparel" | "general";

export type PodBatchCount = number;
export type PodBatchStatus =
  | "queued"
  | "generating_patterns"
  | "compositing"
  | "generating_titles"
  | "pausing"
  | "paused"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "partial_failure"
  | "failed"
  | "settlement_pending";

export type PodBatchItemStatus =
  | "queued"
  | "generating_pattern"
  | "compositing"
  | "completed"
  | "failed"
  | "optimizing_scene";

export type PodStyleTitleStatus = "queued" | "generating" | "completed" | "failed";
export type PodStyleTitleSource = "ai" | "manual";

export type PodStyleTitle = {
  style_index: number;
  style_task_id: string;
  status: PodStyleTitleStatus;
  title: string | null;
  source?: PodStyleTitleSource;
  listing_ready: boolean;
  export_selected: boolean;
  error_message?: string;
  updated_at: string;
};

export type PodBatchSummary = {
  id: string;
  title: string;
  status: PodBatchStatus;
  template_id: string;
  template_name: string;
  count: PodBatchCount;
  processed_count: number;
  completed_count: number;
  failed_count: number;
  title_completed_count?: number;
  title_failed_count?: number;
  listing_ready_count?: number;
  style_grid?: boolean;
  created_at: string;
  updated_at: string;
  /** 终态批次的收尾时间；运行中为空。前端「已等待」在批次结束后按它定格，不再跳秒。 */
  finished_at?: string;
};

export type PodBatchItem = {
  id: string;
  index: number;
  style_index?: number;
  variant_index?: number;
  status: PodBatchItemStatus;
  pattern_preview_url?: string;
  pattern_download_url?: string;
  composite_preview_url?: string;
  composite_download_url?: string;
  role?: "hero" | "detail_a" | "detail_b" | "lifestyle" | "detail" | "lifestyle_a" | "lifestyle_b";
  public_url?: string;
  scene_optimized: boolean;
  error_message?: string;
  updated_at: string;
};

/** 二级批次（4 款一组）的执行状态；列式批次才有。 */
export type PodBatchGroup = {
  group_index: number;
  style_indices: number[];
  status: string;
  current_round: string;
  sheets: Record<string, string>;
};

export type PodBatch = PodBatchSummary & {
  template: PodTemplate;
  /** 该批次生成时所用「大提示词」的版本，由服务端 prompts.PATTERN_PROMPT_VERSION 决定（历史批次为 v1）。 */
  prompt_version: "v1" | "v2" | "v3";
  business_fields: PodBusinessFields;
  listing_fields: PodListingFields | null;
  dianxiaomi_export: PodDianxiaomiExportStatus;
  creative_prompt: string;
  /** 生成布局：legacy = 一款一次调用拆四格；column_v1 = 两级批次按角色列式生成。 */
  generation_layout?: "legacy" | "column_v1";
  /** 二级批次数（4 款一组）；legacy 批次为 0。 */
  group_count?: number;
  /** 批级美术指导（建批时冻结，整批共用）。 */
  art_direction?: PodArtDirection | null;
  /** 二级批次摘要；legacy 批次为空数组。 */
  groups?: PodBatchGroup[];
  /** 该批次冻结的最新构图（无则为 null）；用于展示「本批次套用的构图」。 */
  composition?: PodComposition | null;
  items: PodBatchItem[];
  style_titles?: PodStyleTitle[];
};

/** 单格画面指令：中文供展示/编辑，英文由后台转写后注入生图提示词。 */
export type PodCompositionPanel = {
  zh: string;
  en: string;
};

/** 构图/视角定制：四格画面指令，键固定对应固定角色。 */
export type PodCompositionPanels = {
  /** 主图 */
  panel_1: PodCompositionPanel;
  /** 细节图 A */
  panel_2: PodCompositionPanel;
  /** 细节图 B */
  panel_3: PodCompositionPanel;
  /** 素材图 */
  panel_4: PodCompositionPanel;
};

/** 手动编辑提交形状：只提交四格中文指令，后台据此重新转写英文。 */
export type PodCompositionPanelsZh = {
  panel_1: string;
  panel_2: string;
  panel_3: string;
  panel_4: string;
};

/** 构图模板所属模块：全定制 / 爆款复刻，两池各自独立「生效」。 */
export type PodCompositionScope = "full" | "replica" | "fission";

/** 一份构图模板：中文供展示/编辑，英文由后台转写后注入生图提示词。 */
export type PodComposition = {
  composition_id: string;
  name: string;
  raw_input: string;
  panels: PodCompositionPanels;
  model: string;
  prompt_version: string;
  /** 是否为当前生效的那份（新建批次用它）。 */
  is_active: boolean;
  /** 系统内置「默认模板」：不可编辑/重命名/删除，只能设为生效。 */
  is_builtin: boolean;
  updated_at: string;
  /** 归属模块（full / replica）。 */
  scope?: PodCompositionScope;
  /** 构图语义来源：panel_v1 = 旧「一张图的四格」；role_v1 = 「四次调用的四轮」。 */
  layout?: "panel_v1" | "role_v1";
};

export type PodCompositionListResponse = {
  templates: PodComposition[];
  total: number;
};

export type PodBatchListResponse = {
  batches: PodBatchSummary[];
  total: number;
};

export type PodBillingRunStatus = "authorized" | "settling" | "settlement_pending" | "settled";

export type PodBillingRun = {
  id: string;
  action_type: string;
  target_id: string;
  batch_id: string;
  freeze_id: string;
  rule_version: number;
  expires_at: string;
  status: PodBillingRunStatus;
  error_message?: string;
  created_at: string;
  updated_at: string;
};

export type PodBillingRunListResponse = {
  runs: PodBillingRun[];
  total: number;
};

export type CreatePodBatchRequest = {
  template_id: string;
  count: PodBatchCount;
  /** 兼容字段：提示词由服务端构造，实际入库版本以服务端 prompts.PATTERN_PROMPT_VERSION 为准。 */
  prompt_version: "v1";
  business_fields: PodBusinessFields;
  listing_fields: PodListingFields;
  creative_prompt: string;
  /** 列式生成（两级批次）：款数必须是 4 的倍数，后端按 4 款一组执行 4 轮调用。 */
  generation_layout?: "column_v1";
  /** 批级美术指导：随建批冻结，整批共用，只注入第 1 轮。 */
  art_direction?: PodArtDirection | null;
};
