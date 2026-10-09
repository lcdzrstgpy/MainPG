import { apiRequest } from "../../../shared/api/apiClient";
import { productProcessingApiContext } from "../../product_processing/api/context";

/** 工作台核心指标（后端按工作区聚合，口径与产品处理页一致）。 */
export type DashboardKpis = {
  product_total: number;
  today_inbound: number;
  drafts_pending: number;
  active_tasks: number;
  attention_required: number;
  task_total: number;
  today_task_count: number;
  today_processed_products: number;
  today_failed_products: number;
  today_product_count: number;
};

export type DashboardTrendPoint = {
  /** 北京时间日期，形如 2026-09-14 */
  date: string;
  /** 图表 X 轴用的短标签，形如 09-14 */
  label: string;
  inbound: number;
  processed: number;
};

export type DashboardTaskStatusSlice = {
  status: string;
  label: string;
  count: number;
};

export type DashboardSiteSlice = {
  site_code: string;
  label: string;
  count: number;
};

export type DashboardRecentTask = {
  task_id: number;
  title: string;
  status: string;
  status_label: string;
  total_count: number;
  success_count: number;
  failed_count: number;
  skipped_count: number;
  created_at: string;
  updated_at: string;
};

export type DashboardOverview = {
  workspace_id: string;
  generated_at: string;
  kpis: DashboardKpis;
  trend: {
    days: number;
    start: string;
    end: string;
    points: DashboardTrendPoint[];
  };
  task_status: DashboardTaskStatusSlice[];
  site_distribution: DashboardSiteSlice[];
  recent_tasks: DashboardRecentTask[];
};

/**
 * 读取工作台看板聚合数据。
 * 数据来自本地运行库（产品库 / 处理任务 / 趋势 / 分布），无需前端再逐模块拉全量。
 */
export async function getDashboardOverview(): Promise<DashboardOverview> {
  const { workspaceId } = productProcessingApiContext();
  return apiRequest<DashboardOverview>("/api/dashboard/overview", {
    headers: { "X-Workspace-ID": workspaceId },
  });
}
