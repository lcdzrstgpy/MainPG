import { type ClipboardEvent, type DragEvent, type ChangeEvent, useEffect, useMemo, useRef, useState } from "react";

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
  summary?: Record<string, number>;
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
};

type SiteSettingField = {
  key: string;
  label: string;
  transform?: "percent";
};

const defaultToken = localStorage.getItem("whLocalApiToken") || "dev-admin-token";
const defaultSaveRoot = "C:\\Users\\HUAWEI\\Desktop\\利润核算与活动申报";
const emptyProduct: ProductForm = {
  skc: "",
  selling_price: "19.99",
  cost_price: "5.00",
  weight_kg: "0.35",
  note: "",
  source_url: "",
};

const siteLabels: Record<Site, string> = { US: "美区", CO: "哥伦比亚", EC: "厄瓜多尔" };
const siteSettingFields: Record<Site, SiteSettingField[]> = {
  US: [
    { key: "us_first_mile_rate", label: "当前站点头程每kg" },
    { key: "us_first_mile_fixed", label: "当前站点头程固定费" },
    { key: "domestic_fee", label: "国内操作费" },
    { key: "shipping_subsidy", label: "运费补贴" },
    { key: "refund_rate", label: "退款率 %", transform: "percent" },
    { key: "activity_min_net_profit", label: "活动最低实际利润 元" },
    { key: "activity_profit_rate_threshold", label: "活动最低利润率 %", transform: "percent" },
  ],
  CO: [
    { key: "co_first_mile_rate", label: "当前站点头程每kg" },
    { key: "co_first_mile_fixed", label: "当前站点头程固定费" },
    { key: "domestic_fee", label: "国内操作费" },
    { key: "shipping_subsidy", label: "运费补贴" },
    { key: "refund_rate", label: "退款率 %", transform: "percent" },
    { key: "activity_min_net_profit", label: "活动最低实际利润 元" },
    { key: "activity_profit_rate_threshold", label: "活动最低利润率 %", transform: "percent" },
  ],
  EC: [
    { key: "ec_first_mile_rate", label: "当前站点头程每kg" },
    { key: "ec_first_mile_fixed", label: "当前站点头程固定费" },
    { key: "ec_domestic_fee", label: "国内操作费" },
    { key: "ec_shipping_subsidy", label: "运费补贴" },
    { key: "ec_shipping_subsidy_price_limit", label: "补贴售价上限（含）" },
    { key: "ec_end_fee", label: "尾程固定费" },
    { key: "ec_refund_rate", label: "退款率 %", transform: "percent" },
    { key: "activity_min_net_profit", label: "活动最低实际利润 元" },
    { key: "activity_profit_rate_threshold", label: "活动最低利润率 %", transform: "percent" },
  ],
};

export function ProfitActivityTestPage() {
  const [apiBase, setApiBase] = useState(localStorage.getItem("profitActivityApiBase") || "http://127.0.0.1:8010");
  const [token, setToken] = useState(defaultToken);
  const [site, setSite] = useState<Site>("US");
  const [scope, setScope] = useState<Scope>("default");
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [siteSettings, setSiteSettings] = useState<Record<string, string>>({});
  const [settingsOpen, setSettingsOpen] = useState(true);
  const [productForm, setProductForm] = useState<ProductForm>(emptyProduct);
  const [productImage, setProductImage] = useState<File | null>(null);
  const [sourceImage, setSourceImage] = useState<File | null>(null);
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
  const formReadyForArchive = Boolean(formReadyForPreview && productImage && productForm.source_url.trim() && sourceImage && productForm.note.trim());

  useEffect(() => {
    if (!apiBase) return;
    void loadSettings();
    void queryProducts("");
    void restoreImportSessions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiBase, token]);

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
    URL.revokeObjectURL(objectUrl);
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
    if (!data.save_root) {
      data.save_root = defaultSaveRoot;
    }
    setSettings(data);
    setSiteSettings(extractSiteSettings(data, site));
  }

  const saveSiteSettings = () => withBusy("保存当前站点公式", async () => {
    const payload: Record<string, unknown> = {
      expected_revision: Number(settings?.revision || 0),
      save_root: String(settings?.save_root || defaultSaveRoot),
    };
    for (const field of siteSettingFields[site]) {
      payload[field.key] = field.transform === "percent" ? Number(siteSettings[field.key] || 0) / 100 : Number(siteSettings[field.key] || 0);
    }
    const data = await request<Record<string, unknown>>("/api/profit-activity/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    setSettings(data);
    setSiteSettings(extractSiteSettings(data, site));
  });

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
      throw new Error("入档前必须填写 SKC/售价/成本/重量，并提供商品主图、货源链接、货源图和备注。");
    }
    if (!productImage || !sourceImage) {
      throw new Error("请选择商品主图和货源图。");
    }
    const form = new FormData();
    form.append("site", site);
    for (const [key, value] of Object.entries({ ...numericProductPayload(productForm), note: productForm.note, source_url: productForm.source_url })) {
      form.append(key, String(value));
    }
    form.append("image", productImage);
    form.append("source_image", sourceImage);
    const data = await request<{ product: ProductRow }>("/api/profit-activity/products", { method: "POST", body: form });
    const savedSkc = data.product.skc;
    const nextRecent = [savedSkc, ...recentSaved].slice(0, 3);
    setRecentSaved(nextRecent);
    localStorage.setItem("profitActivityRecentSaved", JSON.stringify(nextRecent));
    setQuerySkcs(savedSkc);
    await queryProducts(savedSkc);
  }, `${productForm.skc} 入库成功`);

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

  const generateAndDownloadFiltered = () => withBusy("生成并下载可申报产品", async () => {
    if (!activityFile) throw new Error("先选择活动 Excel。");
    const form = new FormData();
    form.append("site", site);
    form.append("scope", scope);
    form.append("file", activityFile);
    const task = await request<FilterTask>("/api/profit-activity/activity-filter", { method: "POST", body: form });
    setFilterTask(task);
    const taskId = task.task_id || task.filter_task_id;
    if (!taskId) throw new Error("生成任务未返回任务编号。");
    const saved = await request<{ saved_path?: string }>(`/api/profit-activity/activity-filter/${taskId}/save?kind=filtered`, { method: "POST" });
    setMessage(`可申报产品已生成并保存到本地保存目录：${saved.saved_path || "-"}`);
  });

  const runRecordFilter = () => withBusy("按数据库产品跑过滤规则", async () => {
    const recordIds = products.filter((item) => selected.has(item.skc)).map((item) => item.id).filter((id): id is number => typeof id === "number");
    const task = await request<FilterTask>("/api/profit-activity/filter-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site_code: site, record_ids: recordIds.length ? recordIds : undefined }),
    });
    setFilterTask(task);
    const retained = task.retained_count ?? 0;
    const excluded = task.excluded_count ?? 0;
    setMessage(`活动过滤完成：共过滤 ${retained + excluded} 条，可申报 ${retained} 条，剔除 ${excluded} 条。`);
  });

  const downloadCatalog = () => withBusy("下载产品档案", async () => {
    await download(`/api/profit-activity/catalog/rebuild?${new URLSearchParams({ site, scope })}`, `${site}_product_catalog.xlsx`);
  });

  const saveFilter = (kind: "filtered" | "removed") => withBusy(`保存${kind === "filtered" ? "可申报" : "剔除"}产品`, async () => {
    const taskId = filterTask?.task_id || filterTask?.filter_task_id;
    if (!taskId) throw new Error("暂无活动过滤任务可保存。请先点击“生成并下载可申报产品”。");
    const saved = await request<{ saved_path?: string }>(`/api/profit-activity/activity-filter/${taskId}/save?kind=${kind}`, { method: "POST" });
    setMessage(`${kind === "filtered" ? "可申报" : "剔除"}产品已保存到本地保存目录：${saved.saved_path || "-"}`);
  });

  return (
    <div className="profit-test-page">
      <section className="profit-workflow-card">
        <div className="profit-section-title">
          <span className="profit-title-icon iconfont icon-moneycollect-fill" aria-hidden="true" />
          <h1>利润活动</h1>
          <button onClick={() => setSettingsOpen((value) => !value)}>{settingsOpen ? "⌃ 收起设置" : "⌄ 展开设置"}</button>
        </div>
        <div className="profit-step-row">
          <StepCard step="1" title="填单品" text="SKC、售价、成本、重量" />
          <StepCard step="2" title="入产品库" text="保存后可查询" />
          <StepCard step="3" title="导活动表" text="上传活动 Excel" />
        </div>
        <div className="profit-info-bar">管理员默认不加载员工资料库；需要查看时请从员工管理进入。当前验证页所有产品查询都来自数据库。</div>
        {settingsOpen && (
          <>
            <h2 className="profit-settings-subtitle">保存目录与默认参数</h2>
            <label className="profit-save-root">本地保存目录<input value={String(settings?.save_root || defaultSaveRoot)} onChange={(event) => setSettings({ ...(settings || {}), save_root: event.target.value })} /></label>
            <p className="profit-formula-note">当前编辑{siteLabels[site]}公式：重量kg × 每kg费用 + 固定费；美国、哥伦比亚和厄瓜多尔互不影响，单品预览、入档和活动申报过滤都会使用保存后的当前站点公式。</p>
            <div className="profit-settings-grid">
              {siteSettingFields[site].map((field) => (
                <label key={field.key}>{field.label}<input type="number" value={siteSettings[field.key] || ""} onChange={(event) => setSiteSettings((current) => ({ ...current, [field.key]: event.target.value }))} /></label>
              ))}
              <button onClick={saveSiteSettings} disabled={!!busy}>保存设置</button>
            </div>
          </>
        )}
      </section>

      <section className="profit-test-toolbar">
        <label>API Base<input value={apiBase} onChange={(event) => { setApiBase(event.target.value); localStorage.setItem("profitActivityApiBase", event.target.value); }} placeholder="留空同源，例如 http://127.0.0.1:8000" /></label>
        <label>Bearer Token<input value={token} onChange={(event) => { setToken(event.target.value); localStorage.setItem("whLocalApiToken", event.target.value); }} /></label>
        <label>查询范围<select value={scope} onChange={(event) => setScope(event.target.value as Scope)}><option value="default">只查本人/默认权限</option><option value="company">查本公司在档产品</option></select></label>
      </section>

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
          <div className="profit-source-head"><h3>货源</h3><button type="button">新增货源</button></div>
          <label className="profit-span-2">货源链接 1<input value={productForm.source_url} onChange={(event) => setProductForm({ ...productForm, source_url: event.target.value })} placeholder="必填" /></label>
          <ImageDrop title="货源1 Ctrl+V" hint="必填：采购页截图可多次粘贴或选择；本页验证第一张货源图" file={sourceImage} onFile={setSourceImage} />
          <label className="profit-span-2">备注<input value={productForm.note} onChange={(event) => setProductForm({ ...productForm, note: event.target.value })} placeholder="必填" /></label>
          <h3>利润预览</h3>
          <PreviewStrip calculation={calculation?.calculation} />
          <div className="profit-actions">
            <button onClick={() => calculateProfit(true)} disabled={!!busy || !formReadyForPreview}>手动刷新预览</button>
            <button className="primary-button" onClick={saveProduct} disabled={!!busy || !formReadyForArchive}>入产品库</button>
          </div>
          {!formReadyForArchive && <p className="muted">入档必填：SKC、售价、成本、重量、商品主图、货源链接、货源图、备注。</p>}
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
          <div className="profit-actions">
            <button className="primary-button" onClick={runRecordFilter} disabled={!!busy}>产品过滤</button>
            <button className="primary-button" onClick={generateAndDownloadFiltered} disabled={!!busy}>生成并下载可申报产品</button>
            <button onClick={() => saveFilter("removed")} disabled={!!busy}>下载剔除产品</button>
          </div>
          {filterTask && <FilterRunSummary task={filterTask} />}
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
      <div className="profit-image-icon">▢</div>
      <div><strong>{title}</strong><span>{file ? file.name : hint}</span></div>
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
  const retained = task.retained_count ?? decisions.filter((item) => item.decision === "eligible").length;
  const excluded = task.excluded_count ?? decisions.filter((item) => item.decision === "excluded").length;
  const total = decisions.length || retained + excluded;
  return (
    <section className="profit-import-summary" aria-label="活动过滤任务结果">
      <div className="profit-import-summary-head">
        <strong>活动过滤结果</strong>
        <span>规则 v{task.rule_version ?? "-"}</span>
      </div>
      <p className="profit-formula-note">
        最低实际利润 {money(task.minimum_net_profit)} 元 · 最低利润率 {percent(task.minimum_profit_rate)} · 任务时间 {formatImportTime(task.created_at) || "-"}
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
    const value = Number(settings[field.key] || 0);
    result[field.key] = String(field.transform === "percent" ? value * 100 : value);
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
