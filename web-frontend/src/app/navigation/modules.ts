export type WorkspaceModuleId =
  | "dashboard"
  | "daily_selection"
  | "daily_selection_collection"
  | "product_processing"
  | "product_processing_history"
  | "product_processing_tasks"
  | "combo_generate"
  | "combo_prompt_preset"
  | "combo_history"
  | "dimension_canvas"
  | "pod_customization"
  | "profit_activity"
  | "profit_activity_products"
  | "price_verification"
  | "personal_center";

export type WorkspaceNavigationGroupId = "product_workflow" | "combo_workflow" | "sourcing_workflow";

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
  iconClass: "iconfont icon-build",
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
  label: "POD定制",
  icon: "",
  iconClass: "iconfont icon-skin",
  description: "批量生成 POD 图片与标题并导出店小秘文件",
};

const productProcessingHistory: WorkspaceModule = {
  id: "product_processing_history",
  label: "历史记录",
  icon: "",
  iconClass: "iconfont icon-time-circle",
  description: "查看并找回 AI 处理批次",
};

const comboGenerate: WorkspaceModule = {
  id: "combo_generate",
  label: "组合生图",
  icon: "",
  iconClass: "iconfont icon-skin",
  description: "独立组合套装：套装信息→上传原图→融合主图→文本→6张成品图→独立预检",
};

const comboPromptPreset: WorkspaceModule = {
  id: "combo_prompt_preset",
  label: "提示词模板预设",
  icon: "",
  iconClass: "iconfont icon-skin",
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
  iconClass: "iconfont icon-audit",
  description: "核验价格与维护货源信息",
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
  iconClass: "iconfont icon-database",
  description: "查询、批量管理和下载利润活动产品档案",
};

const personalCenter: WorkspaceModule = {
  id: "personal_center",
  label: "个人中心",
  icon: "",
  iconClass: "iconfont icon-user",
  description: "账户资料、积分钱包、充值订单与服务器账本安全状态",
};

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
    children: [collection, productProcessing, productProcessingHistory, dimensionCanvas],
  },
  {
    id: "combo_workflow",
    label: "商品组合套装",
    icon: "",
    iconClass: "iconfont icon-skin",
    description: "组合生图、提示词模板预设与历史组合套装",
    defaultChildId: "combo_generate",
    children: [comboGenerate, comboPromptPreset, comboHistory],
  },
  podCustomization,
  {
    id: "sourcing_workflow",
    label: "核价及货源",
    icon: "",
    iconClass: "iconfont icon-audit",
    description: "核价、利润与货源产品管理",
    defaultChildId: "price_verification",
    children: [priceVerification, profitActivity, productLibrary],
  },
  personalCenter,
];

export const workspacePageModules: WorkspaceModule[] = [
  dashboard,
  collection,
  collectionPanel,
  productProcessing,
  productProcessingHistory,
  comboGenerate,
  comboPromptPreset,
  comboHistory,
  processingTasks,
  dimensionCanvas,
  podCustomization,
  priceVerification,
  profitActivity,
  productLibrary,
  personalCenter,
];
