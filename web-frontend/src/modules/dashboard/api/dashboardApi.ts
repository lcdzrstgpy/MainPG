import { apiRequest } from "../../../shared/api/apiClient";
import { listProfitActivityProducts } from "../../profit_activity/api/profitActivityApi";
import type { ProfitActivityProduct, ProfitActivitySite } from "../../profit_activity/types/products";

export type DashboardStats = {
  productCount: number | null;       // 产品库产品总数（US+CO+EC）
  todayInboundCount: number | null;  // 今日入库数量（北京时间今日新增入库的产品数）
  todayProcessedCount: number | null; // 今日产品处理数量（北京时间今日创建的处理的批次数量）
};

type ProcessingTaskItem = {
  task_id: number;
  title: string;
  status: string;
  created_at: string;
  total_count: number;
  success_count: number;
  failed_count: number;
  skipped_count: number;
};

const PRODUCT_SITES: ProfitActivitySite[] = ["US", "CO", "EC"];

const CHINA_TIME_ZONE = "Asia/Shanghai";

/**
 * 判断给定的 ISO 时间字符串是否为北京时间（Asia/Shanghai）的今天。
 * 兼容带时区（如 +00:00 / Z）与不带时区（视为 UTC）的输入。
 */
function isTodayInChina(value: string | null | undefined): boolean {
  if (!value) return false;
  let iso = value;
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(iso)) iso += "Z"; // 无时区后缀按 UTC 处理
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return false;
  const today = new Date();
  const dateKey = new Intl.DateTimeFormat("en-CA", { timeZone: CHINA_TIME_ZONE }).format(date);
  const todayKey = new Intl.DateTimeFormat("en-CA", { timeZone: CHINA_TIME_ZONE }).format(today);
  return dateKey === todayKey;
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const stats: DashboardStats = {
    productCount: null,
    todayInboundCount: null,
    todayProcessedCount: null,
  };

  const results = await Promise.allSettled([loadProducts(), loadTodayProcessed()]);

  const [products, processed] = results;
  if (products.status === "fulfilled") {
    stats.productCount = products.value.length;
    stats.todayInboundCount = products.value.filter((item) => isTodayInChina(item.created_at)).length;
  }
  if (processed.status === "fulfilled") stats.todayProcessedCount = processed.value;

  return stats;
}

async function loadProducts(): Promise<ProfitActivityProduct[]> {
  const lists = await Promise.all(
    PRODUCT_SITES.map((site) =>
      listProfitActivityProducts({ site, scope: "default", skcs: "" }).catch(() => [] as ProfitActivityProduct[])
    )
  );
  return lists.flat();
}

async function loadTodayProcessed(): Promise<number> {
  // 任务按工作区隔离，必须带与产品处理页面一致的 X-Workspace-ID（default），
  // 否则后端默认查 local 工作区会漏掉应用里实际创建的处理任务。
  const data = await apiRequest<{ tasks: ProcessingTaskItem[] }>("/product-processing/tasks/history?limit=200", {
    headers: { "X-Workspace-ID": "default" },
  });
  const tasks = Array.isArray(data.tasks) ? data.tasks : [];
  // 今日产品处理数量 = 今日历史处理任务的批次数量（每提交一批草稿即一条任务）
  return tasks.filter((task) => isTodayInChina(task.created_at)).length;
}
