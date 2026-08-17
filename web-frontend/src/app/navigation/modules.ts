export type WorkspaceModuleId =
  | "dashboard"
  | "daily_selection"
  | "daily_selection_collection"
  | "product_processing"
  | "product_processing_tasks"
  | "dimension_canvas"
  | "profit_activity"
  | "profit_activity_products"
  | "price_verification"
  | "ai_service"
  | "basic_settings";

export type WorkspaceNavigationGroupId = "product_workflow" | "sourcing_workflow";

export type WorkspaceModule = {
  id: WorkspaceModuleId;
  label: string;
  icon: string;
  iconClass?: string;
  description: string;
  hiddenFromSidebar?: boolean;
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

const aiService: WorkspaceModule = {
  id: "ai_service",
  label: "AI 服务",
  icon: "",
  iconClass: "iconfont icon-robot",
  description: "本地多模态商品创作与图片辅助",
};

const basicSettings: WorkspaceModule = {
  id: "basic_settings",
  label: "系统配置",
  icon: "",
  iconClass: "iconfont icon-setting",
  description: "管理本地运行参数和服务配置",
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
    children: [collection, productProcessing, dimensionCanvas],
  },
  {
    id: "sourcing_workflow",
    label: "核价及货源",
    icon: "",
    iconClass: "iconfont icon-audit",
    description: "核价、利润与货源产品管理",
    defaultChildId: "price_verification",
    children: [priceVerification, profitActivity, productLibrary],
  },
  aiService,
  basicSettings,
];

export const workspacePageModules: WorkspaceModule[] = [
  dashboard,
  collection,
  collectionPanel,
  productProcessing,
  processingTasks,
  dimensionCanvas,
  priceVerification,
  profitActivity,
  productLibrary,
  aiService,
  basicSettings,
];
