import { apiRequest } from "../../../shared/api/apiClient";
import { listProfitActivityProducts, listProfitActivitySites } from "../../profit_activity/api/profitActivityApi";
import type { ProfitActivityProduct, ProfitActivitySite } from "../../profit_activity/types/products";

export type DashboardStats = {
  productCount: number | null;
  productSites: string[];
  todayInboundCount: number | null;  // 今日入库数量（北京时间今日新增入库的产品数）
  todayProcessedCount: number | null; // 今日产品处理数量（北京时间今日创建的处理的批次数量）
  trend: DashboardTrendPoint[];
};

export type DashboardTrendPoint = {
  date: string;
  inboundCount: number;
  processedCount: number;
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

const FALLBACK_PRODUCT_SITES: Array<{ site_code: ProfitActivitySite; display_name: string }> = [
  { site_code: "US", display_name: "美区" },
  { site_code: "CO", display_name: "哥伦比亚" },
  { site_code: "EC", display_name: "厄瓜多尔" },
  { site_code: "PE", display_name: "秘鲁" },
];

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

function toChinaDateKey(value: string | null | undefined): string | null {
  if (!value) return null;
  let iso = value;
  if (!/(Z|[+-]\d{2}:?\d{2})$/.test(iso)) iso += "Z";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-CA", { timeZone: CHINA_TIME_ZONE }).format(date);
}

function buildTrend(products: ProfitActivityProduct[], tasks: ProcessingTaskItem[], days = 30): DashboardTrendPoint[] {
  const inboundByDate = new Map<string, number>();
  const processedByDate = new Map<string, number>();

  products.forEach((product) => {
    const key = toChinaDateKey(product.library_created_at ?? product.created_at);
    if (key) inboundByDate.set(key, (inboundByDate.get(key) ?? 0) + 1);
  });
  tasks.forEach((task) => {
    const key = toChinaDateKey(task.created_at);
    if (key) processedByDate.set(key, (processedByDate.get(key) ?? 0) + 1);
  });

  const today = new Date();
  return Array.from({ length: days }, (_, index) => {
    const date = new Date(today);
    date.setDate(today.getDate() - (days - 1 - index));
    const key = new Intl.DateTimeFormat("en-CA", { timeZone: CHINA_TIME_ZONE }).format(date);
    return {
      date: key,
      inboundCount: inboundByDate.get(key) ?? 0,
      processedCount: processedByDate.get(key) ?? 0,
    };
  });
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const stats: DashboardStats = {
    productCount: null,
    productSites: [],
    todayInboundCount: null,
    todayProcessedCount: null,
    trend: buildTrend([], []),
  };

  const results = await Promise.allSettled([loadProducts(), loadProcessingTasks()]);

  const [productsResult, tasksResult] = results;
  const productLibrary = productsResult.status === "fulfilled" ? productsResult.value : null;
  const products = productLibrary?.products ?? [];
  const tasks = tasksResult.status === "fulfilled" ? tasksResult.value : [];

  if (productsResult.status === "fulfilled") {
    stats.productCount = products.length;
    stats.productSites = productLibrary?.siteLabels ?? [];
    stats.todayInboundCount = products.filter((item) => isTodayInChina(item.library_created_at ?? item.created_at)).length;
  }
  if (tasksResult.status === "fulfilled") {
    stats.todayProcessedCount = tasks.filter((task) => isTodayInChina(task.created_at)).length;
  }
  stats.trend = buildTrend(products, tasks);

  return stats;
}

async function loadProducts(): Promise<{ products: ProfitActivityProduct[]; siteLabels: string[] }> {
  const siteOptions = await listProfitActivitySites().catch(() => FALLBACK_PRODUCT_SITES);
  const lists = await Promise.all(
    siteOptions.map((site) =>
      listProfitActivityProducts({ site: site.site_code, scope: "default", skcs: "" }).catch(() => [] as ProfitActivityProduct[])
    )
  );
  const products = lists.flat();
  const siteNames = new Map(siteOptions.map((site) => [site.site_code, site.display_name]));
  const siteLabels = [...new Set(products.map((product) => product.site ?? product.site_code).filter(Boolean))]
    .map((site) => siteNames.get(site!) ?? site!);
  return { products, siteLabels };
}

async function loadProcessingTasks(): Promise<ProcessingTaskItem[]> {
  // 任务按工作区隔离，必须带与产品处理页面一致的 X-Workspace-ID（default），
  // 否则后端默认查 local 工作区会漏掉应用里实际创建的处理任务。
  const data = await apiRequest<{ tasks: ProcessingTaskItem[] }>("/product-processing/tasks/history?limit=200", {
    headers: { "X-Workspace-ID": "default" },
  });
  return Array.isArray(data.tasks) ? data.tasks : [];
}
