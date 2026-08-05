export type WorkspaceModuleId = "dashboard" | "daily_selection" | "daily_selection_collection" | "product_processing" | "profit_activity" | "price_verification" | "basic_settings";

export type WorkspaceModule = {
  id: WorkspaceModuleId;
  label: string;
  icon: string;
  description: string;
  hiddenFromSidebar?: boolean;
};

export const workspaceModules: WorkspaceModule[] = [
  { id: "dashboard", label: "工作台", icon: "◈", description: "任务概览与快捷入口" },
  { id: "daily_selection", label: "每日选品", icon: "⌁", description: "采集候选商品并确认入池" },
  { id: "daily_selection_collection", label: "采集面板", icon: "⌕", description: "OneBound 采集、候选商品和历史批次", hiddenFromSidebar: true },
  { id: "product_processing", label: "产品处理", icon: "▣", description: "管理草稿池和商品处理任务" },
  { id: "profit_activity", label: "利润活动", icon: "◌", description: "核算利润、入档并过滤活动" },
  { id: "price_verification", label: "核价及货源", icon: "◇", description: "核验价格与维护货源信息" },
  { id: "basic_settings", label: "系统配置", icon: "⚙", description: "管理本地运行参数和服务配置" },
];
