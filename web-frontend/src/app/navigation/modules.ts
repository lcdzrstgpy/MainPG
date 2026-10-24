import { AI_VIDEO_NAV } from "../../modules/ai_video/nav.ts";

export type WorkspaceModuleId =
  | "dashboard"
  | "daily_selection"
  | "daily_selection_collection"
  | "product_processing"
  | "product_processing_history"
  | "product_processing_sourcing"
  | "product_processing_tasks"
  | "combo_generate"
  | "combo_prompt_preset"
  | "combo_history"
  | "dimension_canvas"
  | "pod_customization"
  | "pod_semi_customization"
  | "pod_replica"
  | "pod_fission"
  | "ai_video"
  | "profit_activity"
  | "profit_activity_products"
  | "price_verification"
  | "sourcing_ledger"
  | "personal_center";

export type WorkspaceNavigationGroupId =
  | "product_workflow"
  | "combo_workflow"
  | "sourcing_workflow"
  | "pod_workflow"
  | "personal_center"
  | "ai_video";

/** 个人中心页面内的二级面板；点击只切换面板，不新开页签。 */
export type PersonalCenterPanelId =
  | "wallet"
  | "usage"
  | "pricing"
  | "preferences"
  | "version"
  | "storage"
  | "migration"
  | "promo"
  | "feedback";

export type WorkspaceNavigationPanel = {
  /** 面板标识：个人中心用面板 key，AI 视频用 iframe 内路径。 */
  id: string;
  label: string;
  iconClass?: string;
  /** 字符图标：沿用内嵌页面原横条导航的符号。 */
  icon?: string;
};

export type WorkspaceModule = {
  id: WorkspaceModuleId;
  label: string;
  icon: string;
  iconClass?: string;
  description: string;
  hiddenFromSidebar?: boolean;
  /** 仅 admin/owner 角色可见并可使用（前端隐藏 + 服务端鉴权双向控制）。 */
  adminOnly?: boolean;
};

export type WorkspaceNavigationGroup = {
  id: WorkspaceNavigationGroupId;
  label: string;
  icon: string;
  iconClass?: string;
  description: string;
  defaultChildId: WorkspaceModuleId;
  children: WorkspaceModule[];
  /** 页面内二级面板（个人中心）：子导航只切面板，不新开页签。 */
  panels?: WorkspaceNavigationPanel[];
};

export type WorkspaceNavigationItem = WorkspaceModule | WorkspaceNavigationGroup;

export function isWorkspaceNavigationGroup(item: WorkspaceNavigationItem): item is WorkspaceNavigationGroup {
  return "defaultChildId" in item;
}

const dashboard: WorkspaceModule = {
  id: "dashboard",
  label: "工作台",
  icon: "",
  iconClass: "iconfont icon-dashboard",
  description: "任务概览与快捷入口",
};

const collection: WorkspaceModule = {
  id: "daily_selection",
  label: "采集",
  icon: "",
  iconClass: "iconfont icon-compass",
  description: "采集候选商品并确认入池",
};

const productProcessing: WorkspaceModule = {
  id: "product_processing",
  label: "AI处理",
  icon: "",
  iconClass: "iconfont icon-robot",
  description: "管理草稿池、处理设置与任务进度",
};

const dimensionCanvas: WorkspaceModule = {
  id: "dimension_canvas",
  label: "尺寸画布",
  icon: "",
  iconClass: "iconfont icon-column-width",
  description: "精确制作并审核商品尺寸图",
};

const podCustomization: WorkspaceModule = {
  id: "pod_customization",
  label: "全定制",
  icon: "",
  iconClass: "iconfont icon-appstore",
  description: "批量生成 POD 图片与标题并导出店小秘文件",
};

const podSemiCustomization: WorkspaceModule = {
  id: "pod_semi_customization",
  label: "半定制",
  icon: "",
  iconClass: "iconfont icon-formatpainter",
  description: "按提示词生成纯图案花色图，按批次打包下载",
};

const podFission: WorkspaceModule = {
  id: "pod_fission",
  label: "爆款裂变",
  icon: "",
  iconClass: "iconfont icon-branches",
  description: "上传爆款样图与白底产品图，自动裂变生成同系列多款商品",
};

const podReplica: WorkspaceModule = {
  id: "pod_replica",
  label: "爆款复刻",
  icon: "",
  iconClass: "iconfont icon-fire",
  description: "把一张 POD 样图图案复刻到多个白底产品，逐款填写上架信息并导出",
};

const aiVideo: WorkspaceModule = {
  id: "ai_video",
  label: "AI 视频（实验）",
  icon: "",
  iconClass: "iconfont icon-video",
  description: "通过 ClipForge 生成电商短视频",
};

const productProcessingHistory: WorkspaceModule = {
  id: "product_processing_history",
  label: "历史记录",
  icon: "",
  iconClass: "iconfont icon-reloadtime",
  description: "查看并找回 AI 处理批次",
};

const productProcessingSourcing: WorkspaceModule = {
  id: "product_processing_sourcing",
  label: "货源成本",
  icon: "",
  iconClass: "iconfont icon-database",
  description: "汇总各渠道采集商品的货源链接、店铺与成本价格",
  // 已合并进「核价及货源 → 货源与利润」，保留页面与路由避免旧页签失效。
  hiddenFromSidebar: true,
};

const comboGenerate: WorkspaceModule = {
  id: "combo_generate",
  label: "组合生图",
  icon: "",
  iconClass: "iconfont icon-image",
  description: "独立组合套装：套装信息→上传原图→融合主图→文本→6张成品图→独立预检",
};

const comboPromptPreset: WorkspaceModule = {
  id: "combo_prompt_preset",
  label: "提示词模板预设",
  icon: "",
  iconClass: "iconfont icon-snippets",
  description: "预设/自定义组合生图提示词模板，供组合生图板块自动填充使用",
};

const comboHistory: WorkspaceModule = {
  id: "combo_history",
  label: "历史组合套装",
  icon: "",
  iconClass: "iconfont icon-time-circle",
  description: "查看历史组合套装，可按标题模糊查询并回到组合生图继续制作",
};

const priceVerification: WorkspaceModule = {
  id: "price_verification",
  label: "核价/货源匹配",
  icon: "",
  iconClass: "iconfont icon-reconciliation",
  description: "核验价格与维护货源信息",
  // 图搜链路已下线（代码保留），入口合并进「货源与利润」。
  hiddenFromSidebar: true,
};

const sourcingLedger: WorkspaceModule = {
  id: "sourcing_ledger",
  label: "货源与利润",
  icon: "",
  iconClass: "iconfont icon-calculator",
  description: "采集货源、Temu 申报价、利润核算与入产品库",
};

const profitActivity: WorkspaceModule = {
  id: "profit_activity",
  label: "利润活动",
  icon: "",
  iconClass: "iconfont icon-calculator",
  description: "核算利润、入档并过滤活动",
};

const productLibrary: WorkspaceModule = {
  id: "profit_activity_products",
  label: "货源关联产品库",
  icon: "",
  iconClass: "iconfont icon-book",
  description: "查询、批量管理和下载利润活动产品档案",
};

const personalCenter: WorkspaceModule = {
  id: "personal_center",
  label: "个人中心",
  icon: "",
  iconClass: "iconfont icon-user",
  description: "账户资料、积分钱包、充值订单与服务器账本安全状态",
};

/** 个人中心侧栏子导航：顺序与原页内横条一致。 */
export const personalCenterPanels: WorkspaceNavigationPanel[] = [
  { id: "wallet", label: "积分钱包", iconClass: "iconfont icon-wallet-fill" },
  { id: "usage", label: "消费流水", iconClass: "iconfont icon-accountbook-fill" },
  { id: "pricing", label: "计费规则", iconClass: "iconfont icon-calculator" },
  { id: "preferences", label: "偏好设置", iconClass: "iconfont icon-skin" },
  { id: "version", label: "系统版本", iconClass: "iconfont icon-setting" },
  { id: "storage", label: "存储空间", iconClass: "iconfont icon-database" },
  { id: "promo", label: "推广计划", iconClass: "iconfont icon-gift" },
  { id: "feedback", label: "意见反馈", iconClass: "iconfont icon-message" },
];

/** AI 视频侧栏子导航：直接复用内嵌 ClipForge 的 7 个入口定义。 */
export const aiVideoPanels: WorkspaceNavigationPanel[] = AI_VIDEO_NAV.map((item) => ({
  id: item.href,
  label: item.label,
  icon: item.icon,
}));

const collectionPanel: WorkspaceModule = {
  id: "daily_selection_collection",
  label: "采集面板",
  icon: "",
  iconClass: "iconfont icon-scan",
  description: "OneBound 采集、候选商品和历史批次",
  hiddenFromSidebar: true,
};

const processingTasks: WorkspaceModule = {
  id: "product_processing_tasks",
  label: "处理任务",
  icon: "",
  iconClass: "iconfont icon-build",
  description: "处理设置、任务进度与历史记录",
  hiddenFromSidebar: true,
};

export const workspaceModules: WorkspaceNavigationItem[] = [
  dashboard,
  {
    id: "product_workflow",
    label: "产品处理",
    icon: "",
    iconClass: "iconfont icon-build",
    description: "采集、处理与尺寸图制作",
    defaultChildId: "daily_selection",
    children: [collection, productProcessing, productProcessingHistory, productProcessingSourcing, dimensionCanvas],
  },
  {
    id: "combo_workflow",
    label: "商品组合套装",
    icon: "",
    iconClass: "iconfont icon-layout",
    description: "组合生图、提示词模板预设与历史组合套装",
    defaultChildId: "combo_generate",
    children: [comboGenerate, comboPromptPreset, comboHistory],
  },
  {
    id: "pod_workflow",
    label: "POD定制",
    icon: "",
    iconClass: "iconfont icon-skin",
    description: "POD 全定制、半定制、爆款复刻与爆款裂变",
    defaultChildId: "pod_customization",
    children: [podCustomization, podSemiCustomization, podReplica, podFission],
  },
  {
    id: "sourcing_workflow",
    label: "核价及货源",
    icon: "",
    iconClass: "iconfont icon-audit",
    description: "核价、利润与货源产品管理",
    defaultChildId: "sourcing_ledger",
    children: [sourcingLedger, priceVerification, profitActivity, productLibrary],
  },
  {
    // AI 视频仍是单个页签模块，侧栏按分组展开 iframe 内的 7 个入口。
    id: "ai_video",
    label: "AI 视频（实验）",
    icon: "",
    iconClass: "iconfont icon-video",
    description: "通过 ClipForge 生成电商短视频",
    defaultChildId: "ai_video",
    children: [aiVideo],
    panels: aiVideoPanels,
  },
  {
    // 个人中心仍是单个页签模块，但侧栏里按分组展开二级面板。
    id: "personal_center",
    label: "个人中心",
    icon: "",
    iconClass: "iconfont icon-user",
    description: "账户资料、积分钱包、充值订单与服务器账本安全状态",
    defaultChildId: "personal_center",
    children: [personalCenter],
    panels: personalCenterPanels,
  },
];

export const workspacePageModules: WorkspaceModule[] = [
  dashboard,
  aiVideo,
  collection,
  collectionPanel,
  productProcessing,
  productProcessingHistory,
  productProcessingSourcing,
  comboGenerate,
  comboPromptPreset,
  comboHistory,
  processingTasks,
  dimensionCanvas,
  podCustomization,
  podSemiCustomization,
  podReplica,
  podFission,
  priceVerification,
  sourcingLedger,
  profitActivity,
  productLibrary,
  personalCenter,
];
