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
  summary?: Record<string, number>;
  rows?: Array<Record<string, unknown>>;
};

type FilterTask = {
  task_id?: number;
  filter_task_id?: number;
  status?: string;
  kept_skc_count?: number;
  removed_skc_count?: number;
  filtered_path?: string;
  removed_path?: string;
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
  const [apiBase, setApiBase] = useState(localStorage.getItem("profitActivityApiBase") || "http://127.0.0.1:8000");
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
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importPreview, setImportPreview] = useState<ImportPreview | null>(null);
  const [activityFile, setActivityFile] = useState<File | null>(null);
  const [filterTask, setFilterTask] = useState<FilterTask | null>(null);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("输入 SKC、售价、成本、重量后会自动预览利润。");
  const [log, setLog] = useState<string[]>([]);
  const debounceRef = useRef<number | undefined>();

  const selectedSkcs = useMemo(() => [...selected], [selected]);
  const formReadyForPreview = productForm.skc.trim() && positive(productForm.selling_price) && positive(productForm.cost_price) && positive(productForm.weight_kg);
  const formReadyForArchive = Boolean(formReadyForPreview && productImage && productForm.source_url.trim() && sourceImage && productForm.note.trim());

  useEffect(() => {
    if (!apiBase) return;
    void loadSettings();
    void queryProducts("");
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

  const withBusy = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setMessage(`${label} 中...`);
    try {
      await action();
      setMessage(`${label} 完成。`);
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

  const queryProducts = (overrideSkcs = querySkcs) => withBusy("查询数据库产品", async () => {
    const params = new URLSearchParams({ site, scope, skcs: overrideSkcs });
    const data = await request<{ products: ProductRow[] }>(`/api/profit-activity/products?${params}`);
    setProducts(data.products || []);
    setSelected(new Set());
  });

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
    setQuerySkcs(data.product.skc);
    await queryProducts(data.product.skc);
  });

  const deleteSelected = () => withBusy("删除已选产品", async () => {
    await request("/api/profit-activity/products", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site, skcs: selectedSkcs }),
    });
    await queryProducts();
  });

  const previewImport = () => withBusy("产品 Excel 导入预览", async () => {
    if (!importFile) throw new Error("请选择产品 Excel 文件。");
    const form = new FormData();
    form.append("site", site);
    form.append("file", importFile);
    setImportPreview(await request<ImportPreview>("/api/profit-activity/products/import/preview", { method: "POST", body: form }));
  });

  const confirmImport = () => withBusy("确认导入产品", async () => {
    if (!importPreview?.import_id) throw new Error("请先预览导入文件。");
    await request("/api/profit-activity/products/import/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ import_id: importPreview.import_id, on_conflict: "replace" }),
    });
    await queryProducts();
  });

  const uploadActivityFilter = () => withBusy("生成可申报模板", async () => {
    if (!activityFile) throw new Error("先选择活动 Excel。");
    const form = new FormData();
    form.append("site", site);
    form.append("scope", scope);
    form.append("file", activityFile);
    setFilterTask(await request<FilterTask>("/api/profit-activity/activity-filter", { method: "POST", body: form }));
  });

  const runRecordFilter = () => withBusy("按数据库产品跑过滤规则", async () => {
    const recordIds = products.filter((item) => selected.has(item.skc)).map((item) => item.id).filter((id): id is number => typeof id === "number");
    setFilterTask(await request<FilterTask>("/api/profit-activity/filter-runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ site_code: site, record_ids: recordIds.length ? recordIds : undefined }),
    }));
  });

  const downloadCatalog = () => withBusy("下载产品档案", async () => {
    await download(`/api/profit-activity/catalog/rebuild?${new URLSearchParams({ site, scope })}`, `${site}_product_catalog.xlsx`);
  });

  const downloadFilter = (kind: "filtered" | "removed") => withBusy(`下载${kind === "filtered" ? "可申报" : "剔除"}结果`, async () => {
    const taskId = filterTask?.task_id || filterTask?.filter_task_id;
    if (!taskId) throw new Error("暂无活动过滤任务可下载。");
    await download(`/api/profit-activity/activity-filter/${taskId}/download?kind=${kind}`, `profit_activity_${kind}_${taskId}.xlsx`);
  });

  return (
    <div className="profit-test-page">
      <section className="profit-workflow-card">
        <div className="profit-section-title">
          <span>▣</span>
          <h1>保存目录与默认参数</h1>
          <button onClick={() => setSettingsOpen((value) => !value)}>{settingsOpen ? "⌃ 收起设置" : "⌄ 展开设置"}</button>
        </div>
        <div className="profit-step-row">
          <StepCard step="1" title="填单品" text="SKC、售价、成本、重量" />
          <StepCard step="2" title="入产品库" text="保存后可查询" active />
          <StepCard step="3" title="导活动表" text="上传活动 Excel" />
        </div>
        <div className="profit-info-bar">管理员默认不加载员工资料库；需要查看时请从员工管理进入。当前验证页所有产品查询都来自数据库。</div>
        {settingsOpen && (
          <>
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
        <button onClick={() => queryProducts()} disabled={!!busy}>刷新产品库</button>
      </section>

      <section className="profit-business-grid">
        <article className="profit-test-card">
          <div className="profit-card-title"><span>▦</span><h2>单品利润</h2><SiteTabs site={site} onSite={setSite} /></div>
          <div className="profit-form-grid">
            <label>SKC ID<input value={productForm.skc} onChange={(event) => setProductForm({ ...productForm, skc: event.target.value })} placeholder="必填" /></label>
            <label>售价<input value={productForm.selling_price} onChange={(event) => setProductForm({ ...productForm, selling_price: event.target.value })} /></label>
            <label>成本<input value={productForm.cost_price} onChange={(event) => setProductForm({ ...productForm, cost_price: event.target.value })} /></label>
            <label>重量 KG<input value={productForm.weight_kg} onChange={(event) => setProductForm({ ...productForm, weight_kg: event.target.value })} /></label>
          </div>
          <ImageDrop title="商品主图 Ctrl+V" hint="粘贴、拖入或选择图片" file={productImage} onFile={setProductImage} />
          <div className="profit-source-head"><h3>货源</h3><button type="button">新增货源</button></div>
          <label className="profit-span-2">货源链接 1<input value={productForm.source_url} onChange={(event) => setProductForm({ ...productForm, source_url: event.target.value })} placeholder="采购页链接" /></label>
          <ImageDrop title="货源1 Ctrl+V（可选）" hint="采购页截图可多次粘贴或选择；本页验证第一张货源图" file={sourceImage} onFile={setSourceImage} />
          <label className="profit-span-2">备注<input value={productForm.note} onChange={(event) => setProductForm({ ...productForm, note: event.target.value })} placeholder="填写备注后才能成功入档" /></label>
          <h3>利润预览</h3>
          <PreviewStrip calculation={calculation?.calculation} />
          <div className="profit-actions">
            <button onClick={() => calculateProfit(true)} disabled={!!busy || !formReadyForPreview}>手动刷新预览</button>
            <button className="primary-button" onClick={saveProduct} disabled={!!busy || !formReadyForArchive}>入产品库</button>
          </div>
          {!formReadyForArchive && <p className="muted">入档必填：SKC、售价、成本、重量、商品主图、货源链接、货源图、备注。</p>}
        </article>

        <article className="profit-test-card">
          <div className="profit-card-title"><span>▤</span><h2>活动过滤</h2></div>
          <div className="profit-upload-row">
            <label>活动 Excel<input type="file" accept=".xlsx,.xlsm" onChange={(event) => setActivityFile(event.target.files?.[0] || null)} /></label>
            <button className="primary-button" onClick={uploadActivityFilter} disabled={!!busy}>⇧ 生成可申报模板</button>
          </div>
          <p className="profit-warn">{activityFile ? `已选择：${activityFile.name}` : "先选择活动 Excel。"}</p>
          <p className="muted">上传活动表后，后端会用数据库产品和当前站点利润设置生成可申报模板。</p>
          <div className="profit-upload-row">
            <label>产品资料 Excel<input type="file" accept=".xlsx,.xlsm" onChange={(event) => setImportFile(event.target.files?.[0] || null)} /></label>
            <button onClick={previewImport} disabled={!!busy}>预览入档</button>
            <button onClick={confirmImport} disabled={!!busy || !importPreview?.import_id}>确认导入</button>
          </div>
          <div className="profit-actions">
            <button onClick={runRecordFilter} disabled={!!busy}>用下方数据库产品跑过滤</button>
            <button onClick={() => downloadFilter("filtered")}>下载可申报</button>
            <button onClick={() => downloadFilter("removed")}>下载剔除</button>
          </div>
          {importPreview && <ResultPanel title="导入预览" data={importPreview} />}
          {filterTask && <ResultPanel title="过滤任务" data={filterTask} />}
        </article>
      </section>

      <section className="profit-test-card">
        <div className="profit-query-bar">
          <textarea value={querySkcs} onChange={(event) => setQuerySkcs(event.target.value)} placeholder="输入 SKC，支持换行、空格、逗号批量查询；留空展示数据库里当前权限可见产品" />
          <div>
            <button className="primary-button" onClick={() => queryProducts()} disabled={!!busy}>查询产品</button>
            <button onClick={() => setSelected(new Set(products.map((item) => item.skc)))} disabled={!products.length}>全选结果</button>
            <button className="danger-button" onClick={deleteSelected} disabled={!selectedSkcs.length || !!busy}>删除已选 {selectedSkcs.length}</button>
            <button onClick={downloadCatalog} disabled={!!busy}>下载产品档案</button>
          </div>
        </div>
        <p className="muted">产品查询展示的是数据库返回结果；只读资料不能删除，批量删除只处理当前 token 有权限的产品。</p>
        <ProductTable products={products} selected={selected} onSelected={setSelected} />
      </section>

      <section className="profit-test-grid">
        <article className="profit-test-card"><h2>接口状态</h2><p>{busy || message}</p><ul className="profit-log">{log.map((item) => <li key={item}>{item}</li>)}</ul></article>
        <article className="profit-test-card"><h2>当前设置快照</h2><pre>{settings ? JSON.stringify(settings, null, 2) : "尚未读取 settings"}</pre></article>
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
  return typeof value === "number" ? value.toFixed(2) : "-";
}

function percent(value: unknown) {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "-";
}

function formatValue(value: unknown) {
  return typeof value === "number" ? value.toFixed(2) : value === undefined || value === null ? "-" : String(value);
}
