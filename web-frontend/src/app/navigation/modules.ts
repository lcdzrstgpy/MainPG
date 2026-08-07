export type WorkspaceModuleId =
  | "dashboard"
  | "daily_selection"
  | "daily_selection_collection"
  | "product_processing"
  | "product_processing_tasks"
  | "profit_activity"
  | "profit_activity_products"
  | "price_verification"
  | "basic_settings";

export type WorkspaceModule = {
  id: WorkspaceModuleId;
  label: string;
  icon: string;
  iconClass?: string;
  description: string;
  hiddenFromSidebar?: boolean;
  children?: WorkspaceModule[];
};

export const workspaceModules: WorkspaceModule[] = [
  { id: "dashboard", label: "工作台", icon: "", iconClass: "iconfont icon-dashboard", description: "任务概览与快捷入口" },
  { id: "daily_selection", label: "每日选品", icon: "", iconClass: "iconfont icon-compass", description: "采集候选商品并确认入池" },
  { id: "daily_selection_collection", label: "采集面板", icon: "", iconClass: "iconfont icon-scan", description: "OneBound 采集、候选商品和历史批次", hiddenFromSidebar: true },
  { id: "product_processing", label: "产品处理", icon: "", iconClass: "iconfont icon-build", description: "管理草稿池、处理设置与任务进度" },
  { id: "product_processing_tasks", label: "处理任务", icon: "", iconClass: "iconfont icon-build", description: "处理设置、任务进度与历史记录", hiddenFromSidebar: true },
  {
    id: "profit_activity",
    label: "利润活动",
    icon: "",
    iconClass: "iconfont icon-calculator",
    description: "核算利润、入档并过滤活动",
  },
  { id: "price_verification", label: "核价及货源", icon: "", iconClass: "iconfont icon-audit", description: "核验价格与维护货源信息" },
  { id: "profit_activity_products", label: "产品库", icon: "", iconClass: "iconfont icon-database", description: "查询、批量管理和下载利润活动产品档案" },
  { id: "basic_settings", label: "系统配置", icon: "", iconClass: "iconfont icon-setting", description: "管理本地运行参数和服务配置" },
];
