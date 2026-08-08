import { type ClipboardEvent, type DragEvent, type ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import "../styles/profitActivityTest.css";

type Site = "US" | "CO" | "EC";
type Scope = "default" | "company";

type ProductRow = {
  id?: number;
  skc: string;
  site?: Site;
  site_code?: Site;
  workspace_name?: string;
  created_by?: string;
  created_by_username?: string;
  selling_price?: number;
  cost_price?: number;
  weight_kg?: number;
  net_profit?: number;
  profit_rate?: number;
  source_url?: string;
  note?: string;
  image_path?: string;
  source_image_path?: string;
  is_owner?: boolean;
  can_edit?: boolean;
};

type CalculateResult = {
  calculation?: Record<string, unknown>;
  preview?: Record<string, unknown>;
  settings?: Record<string, unknown>;
};

type ImportPreview = {
  import_id?: string;
  original_filename?: string;
  site?: Site;
  created_at?: string;
  summary?: {
    total_rows?: number;
    importable_rows?: number;
    warning_rows?: number;
    blocked_rows?: number;
    duplicate_rows?: number;
    default_selected_rows?: number;
    sites?: Record<string, number>;
    [key: string]: unknown;
  };
  rows?: Array<Record<string, unknown>>;
};

type FilterDecision = {
  record_id?: number;
  skc?: string;
  site?: string;
  decision?: string;
  reason_code?: string;
};

type FilterTask = {
  task_id?: number;
  filter_task_id?: number;
  status?: string;
  kept_skc_count?: number;
  removed_skc_count?: number;
  kept_row_count?: number;
  removed_row_count?: number;
  kept_activity_count?: number;
  removed_activity_count?: number;
  qualification_counts?: Record<string, number>;
  filtered_path?: string;
  removed_path?: string;
  id?: number;
  site_code?: string;
  rule_version?: number;
  minimum_net_profit?: number;
  minimum_profit_rate?: number;
  retained_count?: number;
  excluded_count?: number;
  created_at?: string;
  decisions?: FilterDecision[];
  [key: string]: unknown;
};

type ProductForm = {
  skc: string;
  selling_price: string;
  cost_price: string;
  weight_kg: string;
  note: string;
  source_url: string;
  source_urls: string[];
};

type SiteSettingField = {
  key: string;
  label: string;
  transform?: "percent";
};

const defaultToken = localStorage.getItem("whLocalApiToken") || "dev-admin-token";
const emptyProduct: ProductForm = {
  skc: "",
  selling_price: "19.99",
  cost_price: "5.00",
  weight_kg: "0.35",
  note: "",
  source_url: "",
  source_urls: [],
};

const siteLabels: Record<Site, string> = { US: "美区", CO: "哥伦比亚", EC: "厄瓜多尔" };
const siteSettingFields: Record<Site, SiteSettingField[]> = {
  US: [
    { key: "us_first_mile_rate", label: "当前站点头程每kg" },
    { key: "us_first_mile_fixed", label: "当前站点头程固定费" },
    { key: "domestic_fee", label: "国内操作费" },
    { key: "shipping_subsidy", label: "运费补贴" },
    { key: "refund_rate", label: "退款率 %", transform: "percent" },
  ],
  CO: [
    { key: "co_first_mile_rate", label: "当前站点头程每kg" },
    { key: "co_first_mile_fixed", label: "当前站点头程固定费" },
    { key: "domestic_fee", label: "国内操作费" },
    { key: "shipping_subsidy", label: "运费补贴" },
    { key: "refund_rate", label: "退款率 %", transform: "percent" },
  ],
  EC: [
    { key: "ec_first_mile_rate", label: "当前站点头程每kg" },
    { key: "ec_first_mile_fixed", label: "当前站点头程固定费" },
    { key: "ec_domestic_fee", label: "国内操作费" },
    { key: "ec_shipping_subsidy", label: "运费补贴" },
    { key: "ec_shipping_subsidy_price_limit", label: "补贴售价上限（含）" },
    { key: "ec_end_fee", label: "尾程固定费" },
    { key: "ec_refund_rate", label: "退款率 %", transform: "percent" },
  ],
};

// 与后端 ProfitSettings 内置默认值保持一致，用于“恢复默认设置”
const DEFAULT_PROFIT_SETTINGS: Record<string, number> = {
  domestic_fee: 2.5,
  shipping_subsidy: 21,
  refund_rate: 0.05,
  us_first_mile_rate: 72,
  us_first_mile_fixed: 5,
  co_first_mile_rate: 80,
  co_first_mile_fixed: 0,
  ec_domestic_fee: 2.5,
  ec_shipping_subsidy: 15,
  ec_shipping_subsidy_price_limit: 120,
  ec_first_mile_rate: 108,
  ec_first_mile_fixed: 0,
  ec_end_fee: 27,
  ec_refund_rate: 0.05,
  activity_min_net_profit: 8,
  activity_profit_rate_threshold: 0.2,
};

export function ProfitActivityTestPage() {
  // API 地址固定为空：所有请求走同源相对路径，由 Vite 代理转发到后端 8010（团队约定端口）
  const [apiBase, setApiBase] = useState("");
  const [token, setToken] = useState(defaultToken);
  const [site, setSite] = useState<Site>("US");
  const [scope, setScope] = useState<Scope>("default");
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [siteSettings, setSiteSettings] = useState<Record<string, string>>({});
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [productForm, setProductForm] = useState<ProductForm>(emptyProduct);
  const [productImage, setProductImage] = useState<File | null>(null);
  // 每个货源链接一张货源图：sourceImages[0] 对应货源链接 1，sourceImages[i+1] 对应第 i 个追加链接
  const [sourceImages, setSourceImages] = useState<(File | null)[]>([null]);
  const [calculation, setCalculation] = useState<CalculateResult | null>(null);
  const [querySkcs, setQuerySkcs] = useState("");
  const [products, setProducts] = useState<ProductRow[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [importFiles, setImportFiles] = useState<File[]>([]);
  const [importPreviews, setImportPreviews] = useState<ImportPreview[]>([]);
  const [lastImportFiles, setLastImportFiles] = useState<string[]>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("profitActivityImportFileNames") || "[]");
      return Array.isArray(saved) ? saved.filter((item): item is string => typeof item === "string") : [];
    } catch {
      return [];
    }
  });
  const [activityFile, setActivityFile] = useState<File | null>(null);
  const [filterTask, setFilterTask] = useState<FilterTask | null>(null);
  // 过滤任务进行中（含上传/轮询），控制“产品过滤中…”按钮与暂停按钮
  const [filterBusy, setFilterBusy] = useState(false);
  // 历史过滤结果（按时间倒序）
  const [filterHistory, setFilterHistory] = useState<FilterTask[]>([]);
  const filterPollRef = useRef<number | undefined>(undefined);
  // 没有任何可申报产品时的提示弹窗
  const [noEligibleOpen, setNoEligibleOpen] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("输入 SKC、售价、成本、重量后会自动预览利润。");
  const [recentSaved, setRecentSaved] = useState<string[]>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("profitActivityRecentSaved") || "[]");
      return Array.isArray(saved) ? saved.slice(0, 3) : [];
    } catch {
      return [];
    }
  });
  const [log, setLog] = useState<string[]>([]);
  const debounceRef = useRef<number | undefined>();

  const selectedSkcs = useMemo(() => [...selected], [selected]);
  const formReadyForPreview = productForm.skc.trim() && positive(productForm.selling_price) && positive(productForm.cost_price) && positive(productForm.weight_kg);
  // 每个货源链接都必须有对应的货源图（第一张图对应货源链接 1，追加链接依次对应）
  const sourceLinks = [productForm.source_url, ...productForm.source_urls];
  const sourceImagesReady = sourceLinks.length > 0
    && sourceLinks.every((url, index) => !url.trim() || Boolean(sourceImages[index]))
    && sourceLinks.some((url) => url.trim());
  const formReadyForArchive = Boolean(formReadyForPreview && productImage && sourceImagesReady && productForm.note.trim());

  useEffect(() => {
    void loadSettings();
    void queryProducts("");
    void restoreImportSessions();
    void loadFilterHistory();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, token]);

  // 组件卸载时清理过滤轮询定时器
  useEffect(() => () => window.clearTimeout(filterPollRef.current), []);

  useEffect(() => {
    if (!settings) return;
    setSiteSettings(extractSiteSettings(settings, site));
  }, [settings, site]);

  useEffect(() => {
    window.clearTimeout(debounceRef.current);
    if (!formReadyForPreview) {
      setCalculation(null);
      return;
    }
    debounceRef.current = window.setTimeout(() => {
      void calculateProfit(false);
    }, 350);
    return () => window.clearTimeout(debounceRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productForm.skc, productForm.selling_price, productForm.cost_price, productForm.weight_kg, site]);

  const request = async <T,>(path: string, options: RequestInit = {}): Promise<T> => {
    const url = `${apiBase}${path}`;
    const headers = new Headers(options.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(url, { ...options, headers });
    const text = await response.text();
    let data: unknown = text;
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      // file download/text error
    }
    setLog((items) => [`${options.method || "GET"} ${url} -> ${response.status}`, ...items].slice(0, 10));
    if (!response.ok) throw new Error(typeof data === "string" ? data : JSON.stringify(data));
    return data as T;
  };

  const download = async (path: string, filename: string) => {
    const url = `${apiBase}${path}`;
    const headers = new Headers();
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const response = await fetch(url, { headers });
    setLog((items) => [`GET ${url} -> ${response.status}`, ...items].slice(0, 10));
    if (!response.ok) throw new Error(await response.text());
    const blob = await response.blob();
    const objectUrl = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = objectUrl;
    anchor.download = filename;
    anchor.click();
    // 延迟释放 objectURL，避免个别浏览器在下载尚未开始时就把数据源回收导致下载失败
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
  };

  const withBusy = async (label: string, action: () => Promise<void>, successMessage?: string) => {
    setBusy(label);
    setMessage(`${label} 中...`);
    try {
      await action();
      if (successMessage) setMessage(successMessage);
      else setMessage(`${label} 完成。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("");
    }
  };

  async function loadSettings() {
    const data = await request<Record<string, unknown>>("/api/profit-activity/settings");
    // 后端返回的是实际生效的本地保存目录；不在此处注入写死的兜底路径，避免展示与真实落盘位置不一致
    setSettings(data);
    setSiteSettings(extractSiteSettings(data, site));
    return data;
  }

  // 保存设置；遇到版本冲突（settings_revision_conflict）时自动重取最新设置并以最新 revision 重试一次，避免被乐观锁卡住
  const putSettings = async (payload: Record<string, unknown>) => {
    const save = () => request<Record<string, unknown>>("/api/profit-activity/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    try {
      return await save();
    } catch (error) {
      if (error instanceof Error && error.message.includes("settings_revision_conflict")) {
        const fresh = await loadSettings();
        payload.expected_revision = Number(fresh?.revision || 0);
        return await save();
      }
      throw error;
    }
  };

  const saveSiteSettings = () => withBusy("保存当前站点公式", async () => {
    const payload: Record<string, unknown> = {
      expected_revision: Number(settings?.revision || 0),
      save_root: String(settings?.save_root || ""),
    };
    for (const field of siteSettingFields[site]) {
      payload[field.key] = field.transform === "percent" ? Number(siteSettings[field.key] || 0) / 100 : Number(siteSettings[field.key] || 0);
    }
    const data = await putSettings(payload);
    setSettings(data);
    setSiteSettings(extractSiteSettings(data, site));
  });

  const restoreDefaultSettings = () => withBusy("恢复默认设置", async () => {
    const payload: Record<string, unknown> = {
      expected_revision: Number(settings?.revision || 0),
      save_root: String(settings?.save_root || ""),
    };
    for (const field of siteSettingFields[site]) {
      payload[field.key] = DEFAULT_PROFIT_SETTINGS[field.key] ?? 0;
    }
    const data = await putSettings(payload);
    setSettings(data);
    setSiteSettings(extractSiteSettings(data, site));
  }, "已将当前站点公式恢复为默认值并保存。");

  // 活动申报门槛：三区共用同一个全局值，保存/恢复只提交这两个字段
  const saveActivityThreshold = () => withBusy("保存活动门槛", async () => {
    const payload: Record<string, unknown> = {
      expected_revision: Number(settings?.revision || 0),
      save_root: String(settings?.save_root || ""),
      activity_min_net_profit: Number(siteSettings.activity_min_net_profit || 0),
      activity_profit_rate_threshold: Number(siteSettings.activity_profit_rate_threshold || 0) / 100,
    };
    const data = await putSettings(payload);
    setSettings(data);
    setSiteSettings(extractSiteSettings(data, site));
  });

  const restoreActivityThreshold = () => withBusy("恢复活动门槛默认", async () => {
    const payload: Record<string, unknown> = {
      expected_revision: Number(settings?.revision || 0),
      save_root: String(settings?.save_root || ""),
      activity_min_net_profit: DEFAULT_PROFIT_SETTINGS.activity_min_net_profit,
      activity_profit_rate_threshold: DEFAULT_PROFIT_SETTINGS.activity_profit_rate_threshold,
    };
    const data = await putSettings(payload);
    setSettings(data);
    setSiteSettings(extractSiteSettings(data, site));
  }, "已恢复活动门槛为默认值并保存。");

  const queryProducts = async (overrideSkcs = querySkcs) => {
    // 静默查询：仅用于刷新产品列表与按钮禁用，不写顶部状态条
    setBusy("查询数据库产品");
    try {
      const params = new URLSearchParams({ site, scope, skcs: overrideSkcs });
      const data = await request<{ products: ProductRow[] }>(`/api/profit-activity/products?${params}`);
      setProducts(data.products || []);
      setSelected(new Set());
    } finally {
      setBusy("");
    }
  };

  const persistImportFileNames = (files: File[]) => {
    const names = files.map((item) => item.name);
    setLastImportFiles(names);
    try {
      localStorage.setItem("profitActivityImportFileNames", JSON.stringify(names));
    } catch {
      // 本地存储不可用时静默跳过
    }
  };

  const restoreImportSessions = async () => {
    try {
      const sessions = await request<ImportPreview[]>("/api/profit-activity/products/import/sessions");
      if (Array.isArray(sessions) && sessions.some((item) => item?.import_id)) {
        setImportPreviews(sessions);
        setMessage(`已恢复最近 ${sessions.length} 次产品导入预览（${sessions.map((item) => item.original_filename).filter(Boolean).join("、")}），可继续确认导入。`);
      }
    } catch {
      // 无最近导入会话或接口不可用时静默跳过
    }
  };

  const calculateProfit = async (showBusy = true) => {
    const action = async () => {
      const data = await request<CalculateResult>("/api/profit-activity/calculate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ site, ...numericProductPayload(productForm) }),
      });
      setCalculation(data);
    };
    if (showBusy) await withBusy("单品利润预览", action);
    else {
      try {
        await action();
        setMessage("利润预览已刷新。");
      } catch (error) {
        setMessage(error instanceof Error ? error.message : String(error));
      }
    }
  };

  const saveProduct = () => withBusy("入产品库", async () => {
    if (!formReadyForArchive) {
      throw new Error("入档前必须填写 SKC/售价/成本/重量，并提供商品主图、每个货源链接对应的货源图和备注。");
    }
    if (!productImage) {
      throw new Error("请选择商品主图。");
    }
    const sourceGroups = [productForm.source_url, ...productForm.source_urls]
      .map((url) => url.trim())
      .filter(Boolean)
      .map((source_url) => ({ source_url, image_paths: [] }));
    const form = new FormData();
    form.append("site", site);
    for (const [key, value] of Object.entries({ ...numericProductPayload(productForm), note: productForm.note, source_url: productForm.source_url })) {
      form.append(key, String(value));
    }
    if (sourceGroups.length) {
      form.append("source_groups_json", JSON.stringify(sourceGroups));
    }
    form.append("image", productImage);
    // 每个货源链接一张货源图：source_group_images_{组号} 对应第 {组号} 个货源组
    sourceImages.forEach((file, index) => {
      if (file) form.append(`source_group_images_${index}`, file);
    });
    const data = await request<{ product: ProductRow }>("/api/profit-activity/products", { method: "POST", body: form });
    const savedSkc = data.product.skc;
    const nextRecent = [savedSkc, ...recentSaved].slice(0, 3);
    setRecentSaved(nextRecent);
    localStorage.setItem("profitActivityRecentSaved", JSON.stringify(nextRecent));
    setQuerySkcs(savedSkc);
    await queryProducts(savedSkc);
  }, `${productForm.skc} 入库成功`);

  const addSourceUrl = () => {
    setProductForm({ ...productForm, source_urls: [...productForm.source_urls, ""] });
    setSourceImages((current) => [...current, null]);
  };

  const changeSourceUrl = (index: number, value: string) => {
    const next = [...productForm.source_urls];
    next[index] = value;
    setProductForm({ ...productForm, source_urls: next });
  };

  const setSourceImageAt = (index: number, file: File | null) => {
    setSourceImages((current) => current.map((item, i) => (i === index ? file : item)));
  };

  const removeSourceUrl = (index: number) => {
    setProductForm({ ...productForm, source_urls: productForm.source_urls.filter((_, i) => i !== index) });
    setSourceImages((current) => current.filter((_, i) => i !== index + 1));
  };

  const deleteSelected = () => withBusy("删除已选产品", async () => {
    await request("/api/profit-activity/products", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site, skcs: selectedSkcs }),
    });
    await queryProducts();
  });

  const previewImport = () => withBusy("产品 Excel 导入预览", async () => {
    if (!importFiles.length) throw new Error("请选择产品 Excel 文件。");
    const results: ImportPreview[] = [];
    for (const file of importFiles) {
      const form = new FormData();
      form.append("site", site);
      form.append("file", file);
      results.push(await request<ImportPreview>("/api/profit-activity/products/import/preview", { method: "POST", body: form }));
    }
    setImportPreviews(results);
    persistImportFileNames(importFiles);
    setMessage(`已完成 ${results.length} 个文件的导入预览，可确认导入。`);
  });

  const confirmImport = () => withBusy("确认导入产品", async () => {
    if (!importPreviews.length) throw new Error("请先预览导入文件。");
    let imported = 0;
    let replaced = 0;
    let skipped = 0;
    for (const preview of importPreviews) {
      if (!preview.import_id) continue;
      const result = await request<{ imported?: number; replaced?: number; skipped?: number }>("/api/profit-activity/products/import/confirm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ import_id: preview.import_id, on_conflict: "replace" }),
      });
      imported += result.imported ?? 0;
      replaced += result.replaced ?? 0;
      skipped += result.skipped ?? 0;
    }
    setMessage(`确认导入完成：新增 ${imported}，替换 ${replaced}，跳过 ${skipped}。`);
    await queryProducts();
  });

  const filterTaskId = (task: FilterTask | null | undefined): number | null => {
    const id = task?.task_id ?? task?.filter_task_id ?? task?.operation_task_id ?? null;
    return typeof id === "number" ? id : null;
  };

  // 加载历史过滤结果（最新在前，最多保留 5 条）
  const loadFilterHistory = async () => {
    try {
      const tasks = await request<FilterTask[]>("/api/profit-activity/activity-filter/tasks?limit=5");
      setFilterHistory(Array.isArray(tasks) ? tasks.slice(0, 5) : []);
    } catch {
      // 历史接口不可用时静默忽略，不影响主流程
    }
  };

  // 过滤完成后保存并下载可申报产品
  const finishFilteredDownload = async (task: FilterTask, kept: number, removed: number) => {
    const taskId = filterTaskId(task);
    if (!taskId) return;
    const saved = await request<{ saved_path?: string }>(`/api/profit-activity/activity-filter/${taskId}/save?kind=filtered`, { method: "POST" });
    if (kept > 0) {
      await download(`/api/profit-activity/activity-filter/${taskId}/download?kind=filtered`, "可申报产品.xlsx");
    }
    setMessage(kept > 0
      ? `已生成 ${kept} 条可申报、${removed} 条剔除，可申报产品已自动下载到浏览器默认下载目录；文件也已保存到本地保存目录 ${saved.saved_path || "-"}。剔除产品可点下方“下载剔除产品”下载。`
      : `活动表 ${removed} 条均未通过判定（产品库无此 SKC 或利润不达标），可申报为空，未生成下载。`);
    if (kept <= 0) {
      setNoEligibleOpen(true);
    }
  };

  // 轮询过滤任务直到结束；downloadOnDone=true 时完成后自动保存并下载可申报产品
  const startFilterTask = (taskId: number, downloadOnDone: boolean) => {
    window.clearTimeout(filterPollRef.current);
    setFilterBusy(true);
    const tick = async () => {
      try {
        const data = await request<FilterTask>(`/api/profit-activity/activity-filter/tasks/${taskId}`);
        setFilterTask(data);
        const status = data.status ?? "running";
        if (status === "completed") {
          setFilterBusy(false);
          void loadFilterHistory();
          const kept = data.kept_row_count ?? data.kept_activity_count ?? data.kept_skc_count ?? 0;
          const removed = data.removed_row_count ?? data.removed_activity_count ?? data.removed_skc_count ?? 0;
          if (downloadOnDone) {
            try {
              await finishFilteredDownload(data, kept, removed);
            } catch (error) {
              setMessage(error instanceof Error ? error.message : String(error));
            }
          } else {
            setMessage(`产品过滤完成：逐条匹配 ${kept + removed} 条，可申报 ${kept} 条，剔除 ${removed} 条。确认后可点“生成并下载可申报产品”下载报告。`);
          }
        } else if (status === "paused") {
          setFilterBusy(false);
          void loadFilterHistory();
          setMessage("产品过滤已暂停。可重新点击“产品过滤”开始新的一次过滤。");
        } else if (status === "failed") {
          setFilterBusy(false);
          void loadFilterHistory();
          setMessage(`产品过滤失败：${typeof data.error === "string" ? data.error : "未知错误"}`);
        } else {
          // queued / running：继续轮询
          filterPollRef.current = window.setTimeout(() => void tick(), 1000);
        }
      } catch (error) {
        setFilterBusy(false);
        setMessage(error instanceof Error ? error.message : String(error));
      }
    };
    void tick();
  };

  // 产品过滤：上传活动 Excel 并异步启动过滤任务，轮询进度
  const runActivityFilter = async () => {
    if (!activityFile) {
      setMessage("先选择活动 Excel。");
      return;
    }
    if (filterBusy) return;
    // 防止站点选错导致整表“站点不匹配”剔除，先与用户确认过滤站点
    if (!window.confirm(`是否过滤${siteLabels[site]}的产品？`)) {
      setMessage("已取消产品过滤。");
      return;
    }
    setFilterBusy(true);
    setMessage("产品过滤中…");
    try {
      const form = new FormData();
      form.append("site", site);
      form.append("scope", scope);
      form.append("file", activityFile);
      const task = await request<FilterTask>("/api/profit-activity/activity-filter", { method: "POST", body: form });
      const taskId = filterTaskId(task);
      if (!taskId) throw new Error("过滤任务未返回任务编号。");
      setFilterTask(task);
      startFilterTask(taskId, false);
    } catch (error) {
      setFilterBusy(false);
      setMessage(error instanceof Error ? error.message : String(error));
    }
  };

  const generateFiltered = async () => {
    // 已有本次过滤的完成结果：直接基于该结果保存并下载可申报产品，避免重复过滤
    const currentTaskId = filterTaskId(filterTask);
    if (currentTaskId && filterTask?.status === "completed") {
      try {
        const kept = Number(filterTask.kept_row_count ?? filterTask.kept_activity_count ?? filterTask.kept_skc_count ?? 0);
        const removed = Number(filterTask.removed_row_count ?? filterTask.removed_activity_count ?? filterTask.removed_skc_count ?? 0);
        await finishFilteredDownload(filterTask, kept, removed);
      } catch (error) {
        setMessage(error instanceof Error ? error.message : String(error));
      }
      return;
    }
    if (!activityFile) {
      setMessage("先选择活动 Excel。");
      return;
    }
    if (filterBusy) return;
    setFilterBusy(true);
    setMessage("产品过滤中…");
    try {
      const form = new FormData();
      form.append("site", site);
      form.append("scope", scope);
      form.append("file", activityFile);
      const task = await request<FilterTask>("/api/profit-activity/activity-filter", { method: "POST", body: form });
      const taskId = filterTaskId(task);
      if (!taskId) throw new Error("生成任务未返回任务编号。");
      setFilterTask(task);
      startFilterTask(taskId, true);
    } catch (error) {
      setFilterBusy(false);
      setMessage(error instanceof Error ? error.message : String(error));
    }
  };

  // 暂停正在运行的过滤任务
  const pauseFilter = async () => {
    const taskId = filterTaskId(filterTask);
    if (!taskId) return;
    try {
      const data = await request<FilterTask>(`/api/profit-activity/activity-filter/${taskId}/pause`, { method: "POST" });
      // 后端只是置位了暂停标志，线程稍后才会落库为 paused；这里先按已暂停展示，
      // 避免用户点了暂停却仍看到“过滤中…”
      setFilterTask({ ...data, status: "paused" });
      setFilterBusy(false);
      setMessage("产品过滤已暂停。");
      // 后台再同步一次最终状态，供历史列表刷新
      window.clearTimeout(filterPollRef.current);
      filterPollRef.current = window.setTimeout(() => {
        void request<FilterTask>(`/api/profit-activity/activity-filter/tasks/${taskId}`)
          .then((latest) => {
            setFilterTask(latest);
            void loadFilterHistory();
          })
          .catch(() => {});
      }, 1500);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    }
  };

  const downloadCatalog = () => withBusy("下载产品档案", async () => {
    await download(`/api/profit-activity/catalog/rebuild?${new URLSearchParams({ site, scope })}`, `${site}_product_catalog.xlsx`);
  });

  const saveFilter = (kind: "filtered" | "removed") => withBusy(`下载${kind === "filtered" ? "可申报" : "剔除"}产品`, async () => {
    const task = filterTask;
    if (!task) throw new Error("暂无过滤结果。请先点击“生成并下载可申报产品”。");
    const filename = kind === "filtered" ? "可申报产品.xlsx" : "剔除产品.xlsx";
    // 产品过滤（filter-runs）返回批次 id，直接从批次导出报告；
    // 活动模板过滤（activity-filter）返回任务 id，从任务生成的文件下载。
    if (typeof task.id === "number" && !task.filtered_path) {
      const runKind = kind === "filtered" ? "eligible" : "excluded";
      await download(`/api/profit-activity/filter-runs/${task.id}/download?kind=${runKind}`, filename);
    } else {
      const taskId = task.task_id || task.filter_task_id;
      if (!taskId) throw new Error("暂无活动过滤任务可下载。");
      await download(`/api/profit-activity/activity-filter/${taskId}/download?kind=${kind}`, filename);
    }
    setMessage(`${kind === "filtered" ? "可申报" : "剔除"}产品报告已下载到浏览器默认下载目录。`);
  });

  return (
    <div className="profit-test-page">
      <section className="profit-activity-hero">
        <div className="profit-hero-main">
          <div>
            <p className="eyebrow">PROFIT ACTIVITY</p>
            <h1>利润活动</h1>
            <p>核算单品利润、保存产品资料，并生成活动申报与剔除结果。</p>
          </div>
          <div className="profit-hero-steps">
            <StepCard step="1" title="填单品" text="SKC、售价、成本、重量" />
            <StepCard step="2" title="入产品库" text="保存后可查询" />
            <StepCard step="3" title="导活动表" text="上传活动 Excel" />
          </div>
          <div className="profit-hero-info">管理员默认不加载员工资料库；需要查看时请从员工管理进入。当前验证页所有产品查询都来自数据库。</div>
        </div>
        <button className="profit-settings-toggle" type="button" aria-expanded={settingsOpen} onClick={() => setSettingsOpen((value) => !value)}>
          {settingsOpen ? "⌃ 收起设置" : "⌄ 展开设置"}
        </button>
      </section>

      <div className={`profit-settings-collapse ${settingsOpen ? "is-open" : ""}`} aria-hidden={!settingsOpen}>
        <div className="profit-settings-collapse-inner">
          <section className="profit-settings-panel">
            <div className="profit-settings-heading">
              <span className="profit-title-icon iconfont icon-setting" aria-hidden="true" />
              <div>
                <h2>保存目录与默认参数</h2>
                <p>按站点维护利润公式，保存后用于单品预览、产品入档和活动申报过滤。</p>
              </div>
            </div>
            <label className="profit-save-root">本地保存目录<input value={String(settings?.save_root || "")} placeholder="留空则使用后端默认输出目录" onChange={(event) => setSettings({ ...(settings || {}), save_root: event.target.value })} /></label>
            <p className="profit-formula-note">当前编辑{siteLabels[site]}公式：重量kg × 每kg费用 + 固定费；美国、哥伦比亚和厄瓜多尔互不影响，单品预览、入档和活动申报过滤都会使用保存后的当前站点公式。</p>
            <div className="profit-settings-grid">
              {siteSettingFields[site].map((field) => (
                <label key={field.key}>{field.label}<input type="number" value={siteSettings[field.key] ?? ""} onChange={(event) => setSiteSettings((current) => ({ ...current, [field.key]: event.target.value }))} /></label>
              ))}
              <button onClick={saveSiteSettings} disabled={!!busy}>保存设置</button>
              <button onClick={restoreDefaultSettings} disabled={!!busy}>恢复默认设置</button>
            </div>
          </section>
        </div>
      </div>

      {message && <p className="profit-status" role="status">{message}</p>}

      <section className="profit-business-grid">
        <article className="profit-test-card">
          <div className="profit-card-title">
            <span className="profit-title-icon iconfont icon-calculator-fill" aria-hidden="true" />
            <h2>单品利润</h2>
            <SiteTabs site={site} onSite={setSite} />
          </div>
          <div className="profit-form-grid">
            <label>SKC ID<input value={productForm.skc} onChange={(event) => setProductForm({ ...productForm, skc: event.target.value })} placeholder="必填" /></label>
            <label>售价<input value={productForm.selling_price} onChange={(event) => setProductForm({ ...productForm, selling_price: event.target.value })} placeholder="必填" /></label>
            <label>成本<input value={productForm.cost_price} onChange={(event) => setProductForm({ ...productForm, cost_price: event.target.value })} placeholder="必填" /></label>
            <label>重量 KG<input value={productForm.weight_kg} onChange={(event) => setProductForm({ ...productForm, weight_kg: event.target.value })} placeholder="必填" /></label>
          </div>
          <ImageDrop title="商品主图 Ctrl+V" hint="必填：粘贴、拖入或选择图片" file={productImage} onFile={setProductImage} />
          <div className="profit-source-head"><h3>货源</h3><button type="button" onClick={addSourceUrl}>新增货源</button></div>
          <div className="profit-source-link-block">
            <label className="profit-span-2">货源链接 1<input value={productForm.source_url} onChange={(event) => setProductForm({ ...productForm, source_url: event.target.value })} placeholder="必填" /></label>
            <ImageDrop title="货源1 图片 Ctrl+V" hint="必填：粘贴、拖入或选择该链接截图" file={sourceImages[0] ?? null} onFile={(file) => setSourceImageAt(0, file)} />
          </div>
          {productForm.source_urls.map((url, index) => (
            <div className="profit-source-link-block" key={index}>
              <div className="profit-source-url-row">
                <label className="profit-span-2">货源链接 {index + 2}<input value={url} onChange={(event) => changeSourceUrl(index, event.target.value)} placeholder="可选" /></label>
                <button type="button" className="profit-source-url-remove" onClick={() => removeSourceUrl(index)}>删除</button>
              </div>
              <ImageDrop title={`货源${index + 2} 图片 Ctrl+V`} hint="必填：该链接截图" file={sourceImages[index + 1] ?? null} onFile={(file) => setSourceImageAt(index + 1, file)} />
            </div>
          ))}
          <label className="profit-span-2">备注<input value={productForm.note} onChange={(event) => setProductForm({ ...productForm, note: event.target.value })} placeholder="必填" /></label>
          <h3>利润预览</h3>
          <PreviewStrip calculation={calculation?.calculation} />
          <div className="profit-actions">
            <button onClick={() => calculateProfit(true)} disabled={!!busy || !formReadyForPreview}>手动刷新预览</button>
            <button className="primary-button" onClick={saveProduct} disabled={!!busy || !formReadyForArchive}>入产品库</button>
          </div>
          {!formReadyForArchive && <p className="muted">入档必填：SKC、售价、成本、重量、商品主图、每个货源链接对应的货源图、备注。</p>}
          {recentSaved.length > 0 && (
            <p className="profit-recent-saved">最近入库：{recentSaved.map((skc) => <span key={skc}>{skc}</span>)}</p>
          )}
        </article>

        <article className="profit-test-card">
          <div className="profit-card-title">
            <span className="profit-title-icon iconfont icon-filter-fill" aria-hidden="true" />
            <h2>活动过滤</h2>
          </div>
          <div className="profit-upload-row">
            <label>活动 Excel<input type="file" accept=".xlsx,.xlsm" onChange={(event) => setActivityFile(event.target.files?.[0] || null)} /></label>
            <p className="profit-warn">{activityFile ? `已选择：${activityFile.name}` : "先选择活动 Excel。"}</p>
          </div>
          <p className="muted">上传活动表后，后端会用数据库产品和当前站点利润设置生成可申报模板，并把可申报/剔除文件保存到“本地保存目录”。</p>
          <div className="profit-threshold-box">
            <h3>活动申报门槛</h3>
            <p className="profit-formula-note">跟随当前站点 {siteLabels[site]}（美区/哥伦比亚/厄瓜多尔共用同一门槛值）。</p>
            <div className="profit-threshold-fields">
              <label>活动最低实际利润 元<input type="number" value={siteSettings.activity_min_net_profit ?? ""} onChange={(event) => setSiteSettings((current) => ({ ...current, activity_min_net_profit: event.target.value }))} /></label>
              <label>活动最低利润率 %<input type="number" value={siteSettings.activity_profit_rate_threshold ?? ""} onChange={(event) => setSiteSettings((current) => ({ ...current, activity_profit_rate_threshold: event.target.value }))} /></label>
            </div>
            <div className="profit-threshold-actions">
              <button onClick={saveActivityThreshold} disabled={!!busy}>保存设置</button>
              <button onClick={restoreActivityThreshold} disabled={!!busy}>恢复默认设置</button>
            </div>
          </div>
          <div className="profit-actions">
            <button className="primary-button" onClick={() => void runActivityFilter()} disabled={!!busy || filterBusy || !activityFile}>
              {filterBusy ? "产品过滤中…" : "产品过滤"}
            </button>
            <button className="primary-button" onClick={() => void generateFiltered()} disabled={!!busy || filterBusy || !activityFile}>
              生成并下载可申报产品
            </button>
            <button onClick={() => void pauseFilter()} disabled={!filterBusy}>暂停过滤</button>
            <button onClick={() => saveFilter("removed")} disabled={!!busy || !filterTask}>下载剔除产品</button>
          </div>
          {filterTask && <FilterRunSummary task={filterTask} />}
          {filterHistory.length > 0 && (
            <div className="profit-filter-history">
              <h3>历史过滤结果</h3>
              <div className="profit-filter-history-list">
                {filterHistory.map((item) => {
                  const kept = Number(item.kept_row_count ?? item.kept_activity_count ?? item.kept_skc_count ?? 0) || 0;
                  const removed = Number(item.removed_row_count ?? item.removed_activity_count ?? item.removed_skc_count ?? 0) || 0;
                  const active = filterTaskId(item) === filterTaskId(filterTask);
                  return (
                    <button
                      key={filterTaskId(item) ?? `${item.created_at ?? ""}-${item.original_filename ?? ""}`}
                      type="button"
                      className={`profit-filter-history-item ${active ? "is-active" : ""}`}
                      onClick={() => setFilterTask(item)}
                    >
                      <strong title={String(item.original_filename || "活动表")}>{String(item.original_filename || "活动表")}</strong>
                      <span>{formatImportTime(item.created_at) || "-"}</span>
                      <span>{filterStatusLabel(item.status)}</span>
                      {item.status === "completed" ? <em>可申报 {kept} · 剔除 {removed}</em> : null}
                      {item.status !== "completed" && typeof item.error === "string" ? <em className="is-error">{item.error}</em> : null}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </article>
      </section>

      <section className="profit-test-card">
        <div className="profit-card-title">
          <span className="profit-title-icon iconfont icon-upload" aria-hidden="true" />
          <h2>产品资料导入</h2>
        </div>
        <p className="muted">首次使用本工作台时，把本地维护的产品资料 Excel（SKC、售价、成本、重量等）批量导入产品库；导入后无需重复操作。</p>
        <div className="profit-upload-row">
          <label>产品资料 Excel（可多选）<input type="file" accept=".xlsx,.xlsm" multiple onChange={(event) => { const files = Array.from(event.target.files || []); setImportFiles(files); persistImportFileNames(files); }} /></label>
          <button onClick={previewImport} disabled={!!busy}>预览入档</button>
          <button onClick={confirmImport} disabled={!!busy || !importPreviews.length}>确认导入</button>
        </div>
        {importFiles.length ? <p className="profit-warn">已选择 {importFiles.length} 个文件：{importFiles.map((item) => item.name).join("、")}</p> : lastImportFiles.length ? <p className="muted">上次选择的文件：{lastImportFiles.join("、")}（浏览器出于安全原因不保留本地完整路径，切换页面后需重新选择文件）</p> : null}
        {importPreviews.length > 0 && <ImportPreviewSummary previews={importPreviews} />}
      </section>

      {noEligibleOpen && (
        <div className="profit-modal-mask" onClick={() => setNoEligibleOpen(false)}>
          <div className="profit-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <span className="profit-modal-icon">!</span>
            <h3>没有可申报的产品</h3>
            <p>活动表中没有满足条件的可申报产品。请确认产品库中是否已导入活动表对应的 SKC，以及申报价、成本、重量是否正确。</p>
            <div className="profit-modal-actions">
              <button className="primary-button" onClick={() => setNoEligibleOpen(false)}>知道了</button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

function StepCard({ step, title, text, active = false }: { step: string; title: string; text: string; active?: boolean }) {
  return <div className={`profit-step-card ${active ? "is-active" : ""}`}><b>{step}</b><div><strong>{title}</strong><span>{text}</span></div></div>;
}

function SiteTabs({ site, onSite }: { site: Site; onSite: (site: Site) => void }) {
  return <div className="profit-site-tabs">{(["US", "CO", "EC"] as Site[]).map((item) => <button key={item} className={site === item ? "is-active" : ""} onClick={() => onSite(item)}>{siteLabels[item]}</button>)}</div>;
}

function ImageDrop({ title, hint, file, onFile }: { title: string; hint: string; file: File | null; onFile: (file: File | null) => void }) {
  const [previewUrl, setPreviewUrl] = useState("");
  useEffect(() => {
    if (!file) {
      setPreviewUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const onPaste = (event: ClipboardEvent<HTMLDivElement>) => {
    const image = [...event.clipboardData.files].find((item) => item.type.startsWith("image/"));
    if (image) onFile(image);
  };
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    const image = [...event.dataTransfer.files].find((item) => item.type.startsWith("image/"));
    if (image) onFile(image);
  };
  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    onFile(event.target.files?.[0] || null);
  };
  return (
    <div className="profit-image-drop" tabIndex={0} onPaste={onPaste} onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
      {previewUrl ? <img className="profit-image-preview" src={previewUrl} alt={`${title}预览`} /> : <div className="profit-image-icon">▢</div>}
      <div><strong>{title}</strong><span>{file ? file.name : hint}</span></div>
      {file ? <button type="button" className="profit-image-remove" onClick={() => onFile(null)}>移除</button> : null}
      <label className="profit-file-button">选择图片<input type="file" accept="image/*" onChange={onChange} /></label>
    </div>
  );
}

function PreviewStrip({ calculation }: { calculation?: Record<string, unknown> }) {
  const items: Array<[string, unknown]> = [
    ["总成本", calculation?.total_cost],
    ["毛利润", calculation?.gross_profit],
    ["净利润", calculation?.net_profit],
    ["利润率", typeof calculation?.profit_rate === "number" ? `${(calculation.profit_rate * 100).toFixed(2)}%` : calculation?.profit_rate],
  ];
  return <div className="profit-preview-strip">{items.map(([label, value]) => <div key={label}><span>{label}</span><strong>{formatValue(value)}</strong></div>)}</div>;
}

function ImportPreviewSummary({ previews }: { previews: ImportPreview[] }) {
  const allRows = previews.flatMap((item) => item.rows || []);
  const totalRows = allRows.length;
  const importableRows = previews.reduce((sum, item) => sum + (item.summary?.importable_rows ?? 0), 0);
  const blockedRows = previews.reduce((sum, item) => sum + (item.summary?.blocked_rows ?? 0), 0);
  const reasons = importBlockerSummary(allRows);
  return (
    <section className="profit-import-summary" aria-label="产品资料导入预览结果">
      <div className="profit-import-summary-head">
        <strong>产品资料导入结果（{previews.length} 个文件）</strong>
        <span>{previews.some((item) => item.import_id) ? "已生成预览" : "等待预览"}</span>
      </div>
      {previews.map((preview) => (
        <div className="profit-import-file" key={preview.import_id}>
          <strong title={preview.original_filename || preview.import_id}>{preview.original_filename || preview.import_id}</strong>
          <span className="profit-import-file-meta">
            <span>共 {preview.summary?.total_rows ?? 0} 条，可入库 {preview.summary?.importable_rows ?? 0} 条，拦截 {preview.summary?.blocked_rows ?? 0} 条</span>
            {formatImportSites(preview.summary?.sites) ? <span>{formatImportSites(preview.summary?.sites)}</span> : null}
            {formatImportTime(preview.created_at) ? <time>{formatImportTime(preview.created_at)}</time> : null}
          </span>
        </div>
      ))}
      <div className="profit-import-stats">
        <div><span>共读取</span><strong>{totalRows}</strong><em>条</em></div>
        <div><span>可入库</span><strong>{importableRows}</strong><em>条</em></div>
        <div><span>被拦截</span><strong>{blockedRows}</strong><em>条</em></div>
      </div>
      <div className="profit-import-reasons">
        <span>主要原因</span>
        {reasons.length ? (
          <ul>
            {reasons.map((item) => <li key={item.reason}>{item.label}：{item.count} 条</li>)}
          </ul>
        ) : (
          <p>暂无拦截原因，可直接确认导入。</p>
        )}
      </div>
    </section>
  );
}

function FilterRunSummary({ task }: { task: FilterTask }) {
  const decisions = Array.isArray(task.decisions) ? task.decisions : [];
  // 逐条统计：优先用“行”数（每条 SKC×活动×申报价 一条），兼容旧任务/产品过滤的 SKC 口径
  const retained = task.retained_count
    ?? task.kept_row_count
    ?? task.kept_activity_count
    ?? (typeof task.kept_skc_count === "number" ? task.kept_skc_count : decisions.filter((item) => item.decision === "eligible").length);
  const excluded = task.excluded_count
    ?? task.removed_row_count
    ?? task.removed_activity_count
    ?? (typeof task.removed_skc_count === "number" ? task.removed_skc_count : decisions.filter((item) => item.decision === "excluded").length);
  const total = decisions.length || retained + excluded;
  // 兼容两种任务来源：产品过滤返回 rule_version/minimum_*，活动模板过滤返回 activity_filter_rule_version/min_net_profit_threshold/profit_rate_threshold
  const minNetProfit = (task.minimum_net_profit ?? task.min_net_profit_threshold ?? task.threshold) as number | undefined;
  const minProfitRate = (task.minimum_profit_rate ?? task.profit_rate_threshold ?? task.activity_profit_rate_threshold) as number | undefined;
  return (
    <section className="profit-import-summary" aria-label="活动过滤任务结果">
      <div className="profit-import-summary-head">
        <strong>活动过滤结果</strong>
        <span>{filterStatusLabel(task.status)}</span>
      </div>
      {task.status && task.status !== "completed" && (
        <p className="profit-warn">
          {task.status === "paused" ? "过滤已暂停，未生成可申报/剔除文件。" : task.status === "failed" ? `过滤失败：${typeof task.error === "string" ? task.error : "未知错误"}` : "过滤正在进行中，请稍候…"}
        </p>
      )}
      <p className="profit-formula-note">
        最低实际利润 {money(minNetProfit)} 元 · 最低利润率 {percent(minProfitRate)} · 任务时间 {formatImportTime(task.created_at) || "-"}
      </p>
      <div className="profit-import-stats">
        <div><span>过滤产品</span><strong>{total}</strong><em>条</em></div>
        <div><span>可申报</span><strong className="profit-good">{retained}</strong><em>条</em></div>
        <div><span>剔除</span><strong className="profit-bad">{excluded}</strong><em>条</em></div>
      </div>
    </section>
  );
}

function ProductTable({ products, selected, onSelected }: { products: ProductRow[]; selected: Set<string>; onSelected: (value: Set<string>) => void }) {
  return (
    <div className="profit-table-wrap">
      <table className="profit-table">
        <thead><tr><th>选择</th><th>SKC</th><th>公司</th><th>创建人</th><th>售价</th><th>成本</th><th>重量</th><th>利润</th><th>利润率</th><th>货源</th><th>图片</th><th>操作</th></tr></thead>
        <tbody>
          {products.length ? products.map((item) => (
            <tr key={`${item.site || item.site_code}-${item.skc}`}>
              <td><input type="checkbox" checked={selected.has(item.skc)} onChange={(event) => onSelected(toggleSet(selected, item.skc, event.target.checked))} /></td>
              <td>{item.skc}</td>
              <td>{item.workspace_name || "-"}</td>
              <td>{item.created_by_username || item.created_by || "-"}</td>
              <td>{money(item.selling_price)}</td>
              <td>{money(item.cost_price)}</td>
              <td>{item.weight_kg ?? "-"}</td>
              <td className={(item.net_profit ?? 0) >= 0 ? "profit-good" : "profit-bad"}>{money(item.net_profit)}</td>
              <td>{percent(item.profit_rate)}</td>
              <td>{item.source_url ? <a href={item.source_url} target="_blank" rel="noreferrer">打开</a> : "-"}</td>
              <td>{item.image_path ? "主图" : "-"} {item.source_image_path ? "货源图" : ""}</td>
              <td>{item.is_owner ? "本人" : item.can_edit ? "可编辑" : "只读"}</td>
            </tr>
          )) : <tr><td colSpan={12}>输入 SKC 后查询产品；留空查询会展示数据库中当前权限可见产品。</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function extractSiteSettings(settings: Record<string, unknown>, site: Site) {
  const result: Record<string, string> = {};
  for (const field of siteSettingFields[site]) {
    // 数据库缺失的字段回退到内置默认值；已保存的值（包括 0）原样展示
    const raw = settings[field.key] == null ? (DEFAULT_PROFIT_SETTINGS[field.key] ?? 0) : settings[field.key];
    const value = Number(raw);
    result[field.key] = String(field.transform === "percent" ? value * 100 : value);
  }
  // 活动申报门槛为三区共用的全局值，随站点 tab 一起展示
  for (const key of ["activity_min_net_profit", "activity_profit_rate_threshold"] as const) {
    const raw = settings[key] == null ? (DEFAULT_PROFIT_SETTINGS[key] ?? 0) : settings[key];
    const value = Number(raw);
    result[key] = String(key === "activity_profit_rate_threshold" ? value * 100 : value);
  }
  return result;
}

function numericProductPayload(form: ProductForm) {
  return {
    skc: form.skc.trim(),
    selling_price: Number(form.selling_price),
    cost_price: Number(form.cost_price),
    weight_kg: Number(form.weight_kg),
  };
}

const importBlockerLabels: Record<string, string> = {
  missing_skc: "SKC 缺失",
  invalid_selling_price: "售价缺失或无效",
  invalid_cost_price: "成本缺失或无效",
  invalid_weight_kg: "重量缺失或无效",
  duplicate_skc: "SKC 重复",
  missing_product_image: "商品主图缺失",
  missing_source_image: "货源图缺失",
  missing_source_url: "货源链接缺失",
  missing_note: "备注缺失",
};

function importBlockerSummary(rows: Array<Record<string, unknown>>) {
  const counts = new Map<string, number>();
  for (const row of rows) {
    const blockers = Array.isArray(row.blockers) ? row.blockers : [];
    for (const blocker of blockers) {
      if (typeof blocker === "string" && blocker) {
        counts.set(blocker, (counts.get(blocker) || 0) + 1);
      }
    }
  }
  return [...counts.entries()]
    .sort((left, right) => right[1] - left[1])
    .slice(0, 5)
    .map(([reason, count]) => ({ reason, count, label: importBlockerLabels[reason] || reason }));
}

function ResultPanel({ title, data }: { title: string; data: unknown }) {
  return <details className="profit-result" open><summary>{title}</summary><pre>{JSON.stringify(data, null, 2)}</pre></details>;
}

function toggleSet(source: Set<string>, value: string, checked: boolean) {
  const next = new Set(source);
  if (checked) next.add(value);
  else next.delete(value);
  return next;
}

function positive(value: string) {
  return Number(value) > 0;
}

function money(value: unknown) {
  const numberValue = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numberValue) ? numberValue.toFixed(2) : "-";
}

function percent(value: unknown) {
  const numberValue = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numberValue) ? `${(numberValue * 100).toFixed(2)}%` : "-";
}

function formatValue(value: unknown) {
  return typeof value === "number" ? value.toFixed(2) : value === undefined || value === null ? "-" : String(value);
}

function formatImportTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

const filterStatusLabels: Record<string, string> = {
  running: "过滤中…",
  queued: "排队中…",
  paused: "已暂停",
  failed: "失败",
  completed: "已完成",
};

function filterStatusLabel(status?: string) {
  return (status && filterStatusLabels[status]) || status || "-";
}

const importSiteNames: Record<string, string> = { US: "美区", CO: "哥伦比亚", EC: "厄瓜多尔" };

function formatImportSites(sites?: Record<string, number>): string {
  if (!sites) return "";
  const entries = Object.entries(sites);
  if (!entries.length) return "";
  return entries.map(([site, count]) => `${importSiteNames[site] ?? site} ${count} 条`).join(" · ");
}
