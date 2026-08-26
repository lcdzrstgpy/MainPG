import { useEffect, useMemo, useRef, useState } from "react";
import type { ClipboardEvent as ReactClipboardEvent, MouseEvent as ReactMouseEvent, ReactNode } from "react";
import { createPortal } from "react-dom";

import {
  createProfitActivitySite,
  deleteProfitActivityProducts,
  getProfitActivitySettings,
  listProfitActivitySites,
  listProfitActivityProducts,
  loadProductImage,
  recalculateProfitActivityProducts,
  saveProfitActivitySettings,
  updateProfitActivityProduct,
  updateProfitActivitySite,
  saveProfitActivityProductEdit,
} from "../api/profitActivityApi";
import type { ProfitActivityProduct, ProfitActivityScope, ProfitActivitySite } from "../types/products";
import { ProductSourceDrawer } from "../components/ProductSourceDrawer";
import "../styles/profitActivityProducts.css";

const siteLabels: Record<string, string> = { US: "美区", CO: "哥伦比亚", EC: "厄瓜多尔" };
const allSites: ProfitActivitySite[] = ["US", "CO", "EC"];
const pageSizeOptions = [10, 50, 100] as const;
type ProductSourceFilter = "manual" | "price_verification" | "all";
const productKey = (item: ProfitActivityProduct) => `${item.site || item.site_code || "US"}-${item.skc}`;
const productIdText = (item: ProfitActivityProduct) => item.product_id ?? item.skc;
const productCreatedTime = (item: ProfitActivityProduct) => {
  const value = Date.parse(item.library_created_at || item.created_at || item.updated_at || "");
  return Number.isFinite(value) ? value : 0;
};
const sortProductsByCreatedDesc = (items: ProfitActivityProduct[]) => [...items].sort((left, right) => (
  productCreatedTime(right) - productCreatedTime(left) || (right.id ?? 0) - (left.id ?? 0)
));
type ProductTableColumnKey =
  | "select" | "site" | "storeName" | "productId" | "createdAt" | "productImage"
  | "sellingPrice" | "costPrice" | "weightKg" | "netProfit" | "profitRate"
  | "note" | "source" | "attachmentImage";
type ProductTableColumn = { key: ProductTableColumnKey; label: string; width: number; minWidth: number };
type InlineEditField = "site" | "store_name" | "product_id" | "selling_price" | "cost_price" | "weight_kg" | "note" | "product_image" | "attachment_image";
type InlineEditState =
  | { key: string; field: "site"; value: ProfitActivitySite }
  | { key: string; field: "store_name" | "product_id" | "selling_price" | "cost_price" | "weight_kg" | "note"; value: string }
  | { key: string; field: "product_image" | "attachment_image"; file: File | null; clear: boolean };
type SiteSettingField = { key: string; label: string; transform?: "percent" };
type SiteSettingProfile = {
  id: ProfitActivitySite;
  label: string;
  fields: SiteSettingField[];
  builtin: boolean;
  data?: Record<string, unknown>;
};
const productTableColumns: ProductTableColumn[] = [
  { key: "select", label: "选择", width: 56, minWidth: 48 },
  { key: "site", label: "站点", width: 128, minWidth: 96 },
  { key: "storeName", label: "店铺", width: 160, minWidth: 110 },
  { key: "productId", label: "商品ID", width: 220, minWidth: 150 },
  { key: "createdAt", label: "入库日期", width: 128, minWidth: 108 },
  { key: "productImage", label: "商品图", width: 100, minWidth: 82 },
  { key: "sellingPrice", label: "售价", width: 120, minWidth: 96 },
  { key: "costPrice", label: "成本", width: 120, minWidth: 96 },
  { key: "weightKg", label: "重量", width: 112, minWidth: 90 },
  { key: "netProfit", label: "利润", width: 120, minWidth: 96 },
  { key: "profitRate", label: "利润率", width: 126, minWidth: 102 },
  { key: "note", label: "备注", width: 260, minWidth: 150 },
  { key: "source", label: "货源", width: 130, minWidth: 104 },
  { key: "attachmentImage", label: "图片", width: 100, minWidth: 82 },
];
const defaultColumnWidths = productTableColumns.reduce<Record<ProductTableColumnKey, number>>((acc, column) => {
  acc[column.key] = column.width;
  return acc;
}, {} as Record<ProductTableColumnKey, number>);
const productColumnWidthsStorageKey = "profitActivityProducts.columnWidths.v1";
const genericSiteSettingFields: SiteSettingField[] = [
  { key: "first_mile_rate", label: "当前站点头程每kg" },
  { key: "first_mile_fixed", label: "当前站点头程固定费" },
  { key: "domestic_fee", label: "国内操作费" },
  { key: "shipping_subsidy", label: "运费补贴" },
  { key: "end_fee", label: "尾程固定费" },
  { key: "refund_rate", label: "退款率 %", transform: "percent" },
];
const builtinSiteSettingProfiles: SiteSettingProfile[] = [
  {
    id: "US",
    label: "美区",
    builtin: true,
    fields: [
      { key: "us_first_mile_rate", label: "当前站点头程每kg" },
      { key: "us_first_mile_fixed", label: "当前站点头程固定费" },
      { key: "us_domestic_fee", label: "国内操作费" },
      { key: "us_shipping_subsidy", label: "运费补贴" },
      { key: "us_refund_rate", label: "退款率 %", transform: "percent" },
    ],
  },
  {
    id: "CO",
    label: "哥伦比亚",
    builtin: true,
    fields: [
      { key: "co_first_mile_rate", label: "当前站点头程每kg" },
      { key: "co_first_mile_fixed", label: "当前站点头程固定费" },
      { key: "co_domestic_fee", label: "国内操作费" },
      { key: "co_shipping_subsidy", label: "运费补贴" },
      { key: "co_refund_rate", label: "退款率 %", transform: "percent" },
    ],
  },
  {
    id: "EC",
    label: "厄瓜多尔",
    builtin: true,
    fields: [
      { key: "ec_first_mile_rate", label: "当前站点头程每kg" },
      { key: "ec_first_mile_fixed", label: "当前站点头程固定费" },
      { key: "ec_domestic_fee", label: "国内操作费" },
      { key: "ec_shipping_subsidy", label: "运费补贴" },
      { key: "ec_shipping_subsidy_price_limit", label: "补贴售价上限（含）" },
      { key: "ec_end_fee", label: "尾程固定费" },
      { key: "ec_refund_rate", label: "退款率 %", transform: "percent" },
    ],
  },
];
const defaultProfitSettings: Record<string, number> = {
  domestic_fee: 2.5,
  shipping_subsidy: 21,
  refund_rate: 0.05,
  us_first_mile_rate: 72,
  us_first_mile_fixed: 5,
  us_domestic_fee: 2.5,
  us_shipping_subsidy: 21,
  us_refund_rate: 0.05,
  co_first_mile_rate: 80,
  co_first_mile_fixed: 0,
  co_domestic_fee: 2.5,
  co_shipping_subsidy: 21,
  co_refund_rate: 0.05,
  ec_domestic_fee: 2.5,
  ec_shipping_subsidy: 15,
  ec_shipping_subsidy_price_limit: 120,
  ec_first_mile_rate: 108,
  ec_first_mile_fixed: 0,
  ec_end_fee: 27,
  ec_refund_rate: 0.05,
};

function storedProductColumnWidths(): Record<ProductTableColumnKey, number> | null {
  try {
    const raw = window.localStorage.getItem(productColumnWidthsStorageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Record<ProductTableColumnKey, unknown>>;
    const widths = { ...defaultColumnWidths };
    for (const column of productTableColumns) {
      const value = Number(parsed[column.key]);
      if (Number.isFinite(value)) widths[column.key] = Math.max(column.minWidth, value);
    }
    return widths;
  } catch {
    return null;
  }
}

function saveProductColumnWidths(widths: Record<ProductTableColumnKey, number>) {
  try {
    window.localStorage.setItem(productColumnWidthsStorageKey, JSON.stringify(widths));
  } catch {
    // localStorage may be disabled; resizing should still work for the session.
  }
}

function fieldsForSiteSettings(site: ProfitActivitySite) {
  return builtinSiteSettingProfiles.find((profile) => profile.id === site)?.fields ?? genericSiteSettingFields;
}

function toSiteSettingProfile(data: Record<string, unknown>): SiteSettingProfile {
  const id = String(data.site_code || "").toUpperCase();
  const builtin = builtinSiteSettingProfiles.find((profile) => profile.id === id);
  return builtin || {
    id,
    label: String(data.display_name || id),
    fields: genericSiteSettingFields,
    builtin: false,
    data,
  };
}

function extractSiteSettings(settings: Record<string, unknown>, site: ProfitActivitySite) {
  const result: Record<string, string> = {};
  for (const field of fieldsForSiteSettings(site)) {
    const raw = settings[field.key] == null ? (defaultProfitSettings[field.key] ?? 0) : settings[field.key];
    const value = Number(raw);
    result[field.key] = String(field.transform === "percent" ? value * 100 : value);
  }
  return result;
}

function adaptiveTextWidth(value: unknown, min: number, max: number, extra = 0) {
  const text = String(value ?? "");
  const weightedLength = Array.from(text).reduce((total, char) => total + (char.charCodeAt(0) > 255 ? 2 : 1), 0);
  return Math.min(max, Math.max(min, weightedLength * 7 + extra));
}

function buildAdaptiveColumnWidths(items: ProfitActivityProduct[], siteLabel: (value: ProfitActivitySite) => string) {
  const widths = { ...defaultColumnWidths };
  const sample = items.slice(0, 100);
  for (const item of sample) {
    const site = (item.site || item.site_code || "US") as ProfitActivitySite;
    const sourceCount = (item.source_groups ?? []).filter((group) => group?.source_url).length;
    widths.site = Math.max(widths.site, adaptiveTextWidth(siteLabel(site), 110, 170, 48));
    widths.storeName = Math.max(widths.storeName, adaptiveTextWidth(item.store_name || "-", 120, 240, 30));
    widths.productId = Math.max(widths.productId, adaptiveTextWidth(productIdText(item), 180, 280, 18));
    widths.createdAt = Math.max(widths.createdAt, adaptiveTextWidth(libraryDate(item.library_created_at || item.created_at), 118, 150, 16));
    widths.sellingPrice = Math.max(widths.sellingPrice, adaptiveTextWidth(money(item.selling_price), 110, 150, 48));
    widths.costPrice = Math.max(widths.costPrice, adaptiveTextWidth(money(item.cost_price), 110, 150, 48));
    widths.weightKg = Math.max(widths.weightKg, adaptiveTextWidth(money(item.weight_kg), 104, 140, 48));
    widths.netProfit = Math.max(widths.netProfit, adaptiveTextWidth(money(item.net_profit), 110, 150, 16));
    widths.profitRate = Math.max(widths.profitRate, adaptiveTextWidth(percent(item.profit_rate), 118, 160, 16));
    widths.note = Math.max(widths.note, adaptiveTextWidth(item.note || "-", 180, 340, 52));
    widths.source = Math.max(widths.source, adaptiveTextWidth(`打开（${sourceCount}）`, 120, 160, 10));
  }
  return widths;
}

// 产品库跨挂载缓存：切换页面再返回时立即展示上次数据，避免每次空表 + 重新等待查询
type LibraryPageSize = (typeof pageSizeOptions)[number];
const productLibraryCache: {
  sites?: ProfitActivitySite[];
  scope?: ProfitActivityScope;
  querySkcs?: string;
  storeFilter?: string;
  shopOptions?: string[];
  products?: ProfitActivityProduct[];
  page?: number;
  pageSize?: LibraryPageSize;
} = {};

export function ProfitActivityProductsPage({ isActive = true }: { isActive?: boolean }) {
  const [sites, setSites] = useState<Set<ProfitActivitySite>>(() => new Set(productLibraryCache.sites ?? allSites));
  const [siteOptions, setSiteOptions] = useState(() => allSites.map((site) => ({ site_code: site, display_name: siteLabels[site], builtin: true })));
  const [siteProfiles, setSiteProfiles] = useState<SiteSettingProfile[]>(builtinSiteSettingProfiles);
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);
  const [settingsDialogOpen, setSettingsDialogOpen] = useState(false);
  const [settingsSite, setSettingsSite] = useState<ProfitActivitySite>("US");
  const [settingsDraft, setSettingsDraft] = useState<Record<string, string>>({});
  const [saveRootDraft, setSaveRootDraft] = useState("");
  const [newSiteOpen, setNewSiteOpen] = useState(false);
  const [newSiteCode, setNewSiteCode] = useState("");
  const [newSiteName, setNewSiteName] = useState("");
  const [newSiteCodeInvalid, setNewSiteCodeInvalid] = useState(false);
  const [scope] = useState<ProfitActivityScope>(productLibraryCache.scope ?? "default");
  const [sourceFilter, setSourceFilter] = useState<ProductSourceFilter>("all");
  const [querySkcs, setQuerySkcs] = useState(productLibraryCache.querySkcs ?? "");
  const [storeFilter, setStoreFilter] = useState(productLibraryCache.storeFilter ?? "");
  const [shopOptions, setShopOptions] = useState<string[]>(productLibraryCache.shopOptions ?? []);
  const [products, setProducts] = useState<ProfitActivityProduct[]>(productLibraryCache.products ?? []);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [batchStoreOpen, setBatchStoreOpen] = useState(false);
  const [batchStoreName, setBatchStoreName] = useState("");
  const [page, setPage] = useState(productLibraryCache.page ?? 1);
  const [pageSize, setPageSize] = useState<(typeof pageSizeOptions)[number]>(productLibraryCache.pageSize ?? 10);
  // 页码输入框的草稿值：允许用户自由填写，回车/失焦/点“跳转”才生效
  const [pageInput, setPageInput] = useState("1");
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("输入商品ID（支持 SKU、SKC、SPU）查询；留空展示数据库中当前权限可见产品。");
  const [activeProduct, setActiveProduct] = useState<ProfitActivityProduct | null>(null);
  const [inlineEdit, setInlineEdit] = useState<InlineEditState | null>(null);
  const [previewImage, setPreviewImage] = useState<{ images: PreviewImage[]; index: number } | null>(null);
  const tableWrapRef = useRef<HTMLDivElement>(null);
  const tableScrollbarRef = useRef<HTMLDivElement>(null);
  const theadRef = useRef<HTMLTableSectionElement>(null);
  const fixedHeaderRef = useRef<HTMLDivElement>(null);
  const fixedHeaderScrollRef = useRef<HTMLDivElement>(null);
  const columnWidthTouchedRef = useRef(storedProductColumnWidths() !== null);
  // 其他模块成功入库时发出浏览器事件；产品库未激活则等用户切回后再刷新。
  const pendingLibraryRefreshRef = useRef(false);
  const refreshProductsRef = useRef<() => Promise<void>>(async () => undefined);
  const [headerStuck, setHeaderStuck] = useState(false);
  // 鼠标拖拽横向平移表格：记录按下时的起点与初始横向偏移
  const tableDragRef = useRef<{ startX: number; startScrollLeft: number } | null>(null);
  const [tableDragging, setTableDragging] = useState(false);
  // 跟随视口的横向滚动条：track 宽度等于表格实际宽度
  const [tableScrollWidth, setTableScrollWidth] = useState(0);
  const [columnWidths, setColumnWidths] = useState<Record<ProductTableColumnKey, number>>(
    () => storedProductColumnWidths() ?? defaultColumnWidths,
  );
  const tableMinWidth = productTableColumns.reduce((total, column) => total + columnWidths[column.key], 0);
  const startColumnResize = (column: ProductTableColumn, event: ReactMouseEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    columnWidthTouchedRef.current = true;
    const startX = event.clientX;
    const startWidth = columnWidths[column.key];
    const onMove = (moveEvent: MouseEvent) => {
      const nextWidth = Math.max(column.minWidth, startWidth + moveEvent.clientX - startX);
      setColumnWidths((current) => {
        const next = { ...current, [column.key]: nextWidth };
        saveProductColumnWidths(next);
        return next;
      });
    };
    const onUp = () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  useEffect(() => {
    if (isActive) return;
    setActiveProduct(null);
    setInlineEdit(null);
    setPreviewImage(null);
    setSettingsDialogOpen(false);
    setNewSiteOpen(false);
  }, [isActive]);

  useEffect(() => {
    void listProfitActivitySites().then((items) => {
      if (items.length) {
        setSiteOptions(items);
        setSiteProfiles(items.map((item) => toSiteSettingProfile(item as unknown as Record<string, unknown>)));
      }
    }).catch(() => undefined);
    void loadSettings().catch(() => undefined);
  }, []);

  const availableSites = siteOptions.map((item) => item.site_code);
  const siteLabel = (value: ProfitActivitySite) => siteOptions.find((item) => item.site_code === value)?.display_name || siteLabels[value] || value;

  const totalPages = Math.max(1, Math.ceil(products.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageProducts = useMemo(
    () => products.slice((safePage - 1) * pageSize, safePage * pageSize),
    [products, safePage, pageSize],
  );
  const adaptiveColumnWidths = useMemo(
    () => buildAdaptiveColumnWidths(pageProducts, siteLabel),
    [pageProducts, siteOptions],
  );
  const selectedProducts = useMemo(
    () => products.filter((product) => selected.has(productKey(product))),
    [products, selected],
  );
  const selectedCount = selectedProducts.length;
  const pageSelected = pageProducts.length > 0 && pageProducts.every((item) => selected.has(productKey(item)));

  async function loadSettings() {
    const data = await getProfitActivitySettings();
    setSettings(data);
    setSaveRootDraft(String(data.save_root || ""));
    return data;
  }

  useEffect(() => {
    if (columnWidthTouchedRef.current) return;
    setColumnWidths(adaptiveColumnWidths);
  }, [adaptiveColumnWidths]);

  // 页码输入框与当前页保持同步（翻页按钮/查询刷新后同步显示），但不打断输入过程
  useEffect(() => {
    setPageInput(String(safePage));
  }, [safePage]);

  const goToPageInput = () => {
    const target = Math.max(1, Math.min(totalPages, Math.floor(Number(pageInput) || 1)));
    setPage(target);
    setPageInput(String(target));
  };

  const withBusy = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setMessage(`${label}中...`);
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("");
    }
  };

  const putSettings = async (payload: Record<string, unknown>) => {
    const save = () => saveProfitActivitySettings(payload);
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

  const openSettingsDialog = () => {
    const firstSite = [...sites][0] || availableSites[0] || "US";
    const profile = siteProfiles.find((item) => item.id === firstSite);
    setSettingsSite(firstSite);
    setSettingsDraft(extractSiteSettings(profile?.builtin ? settings || {} : profile?.data || {}, firstSite));
    setSaveRootDraft(String(settings?.save_root || ""));
    setSettingsDialogOpen(true);
  };

  const selectSettingsSite = (nextSite: ProfitActivitySite) => {
    const profile = siteProfiles.find((item) => item.id === nextSite);
    setSettingsSite(nextSite);
    setSettingsDraft(extractSiteSettings(profile?.builtin ? settings || {} : profile?.data || {}, nextSite));
  };

  const createSite = () => withBusy("新增站点", async () => {
    const siteCode = newSiteCode.trim().toUpperCase();
    const displayName = newSiteName.trim();
    if (!/^[A-Z0-9_]{2,12}$/.test(siteCode) || !displayName) {
      setNewSiteCodeInvalid(true);
      return;
    }
    const data = await createProfitActivitySite({ site_code: siteCode, display_name: displayName });
    const created = toSiteSettingProfile(data.site);
    setSiteProfiles((items) => [...items, created]);
    setSiteOptions((items) => [...items, { site_code: created.id, display_name: created.label, builtin: false }]);
    setSites((current) => new Set([...current, created.id]));
    setNewSiteCode("");
    setNewSiteName("");
    setNewSiteCodeInvalid(false);
    setNewSiteOpen(false);
    setSettingsSite(created.id);
    setSettingsDraft(extractSiteSettings(created.data || {}, created.id));
    setMessage("新站点已创建并切换，可直接设置费率。");
  });

  const recalculateProductsForSettingsSite = async () => {
    const result = await recalculateProfitActivityProducts({ sites: [settingsSite], scope });
    await refreshProducts();
    const updated = Number(result.updated || 0);
    const failed = Number(result.failed || 0);
    setMessage(failed ? `站点费率已保存，已重算 ${updated} 个产品，失败 ${failed} 个。` : `站点费率已保存，已重算 ${updated} 个产品。`);
  };

  const saveSiteSettings = () => withBusy("保存站点费率", async () => {
    const profile = siteProfiles.find((item) => item.id === settingsSite);
    if (!profile) throw new Error("站点不存在，请刷新后重试。");
    if (!profile.builtin) {
      const currentSettings = await putSettings({
        expected_revision: Number(settings?.revision || 0),
        save_root: saveRootDraft,
      });
      const body = {
        site_code: profile.id,
        display_name: profile.label,
        ...Object.fromEntries(fieldsForSiteSettings(profile.id).map((field) => [field.key, field.transform === "percent" ? Number(settingsDraft[field.key] || 0) / 100 : Number(settingsDraft[field.key] || 0)])),
      };
      const result = await updateProfitActivitySite(profile.id, body);
      const updated = toSiteSettingProfile(result.site);
      setSettings(currentSettings);
      setSaveRootDraft(String(currentSettings.save_root || ""));
      setSiteProfiles((items) => items.map((item) => item.id === updated.id ? updated : item));
      setSettingsDraft(extractSiteSettings(updated.data || {}, updated.id));
      await recalculateProductsForSettingsSite();
      return;
    }
    const payload: Record<string, unknown> = {
      expected_revision: Number(settings?.revision || 0),
      save_root: saveRootDraft,
    };
    for (const field of fieldsForSiteSettings(settingsSite)) {
      payload[field.key] = field.transform === "percent" ? Number(settingsDraft[field.key] || 0) / 100 : Number(settingsDraft[field.key] || 0);
    }
    const data = await putSettings(payload);
    setSettings(data);
    setSettingsDraft(extractSiteSettings(data, settingsSite));
    setSaveRootDraft(String(data.save_root || ""));
    await recalculateProductsForSettingsSite();
  });

  const restoreDefaultSettings = () => withBusy("恢复默认费率", async () => {
    const profile = siteProfiles.find((item) => item.id === settingsSite);
    if (!profile) throw new Error("站点不存在，请刷新后重试。");
    if (!profile.builtin) {
      const currentSettings = await putSettings({
        expected_revision: Number(settings?.revision || 0),
        save_root: saveRootDraft,
      });
      const body = {
        site_code: profile.id,
        display_name: profile.label,
        ...Object.fromEntries(fieldsForSiteSettings(profile.id).map((field) => [field.key, 0])),
      };
      const result = await updateProfitActivitySite(profile.id, body);
      const updated = toSiteSettingProfile(result.site);
      setSettings(currentSettings);
      setSaveRootDraft(String(currentSettings.save_root || ""));
      setSiteProfiles((items) => items.map((item) => item.id === updated.id ? updated : item));
      setSettingsDraft(extractSiteSettings(updated.data || {}, updated.id));
      await recalculateProductsForSettingsSite();
      return;
    }
    const payload: Record<string, unknown> = {
      expected_revision: Number(settings?.revision || 0),
      save_root: saveRootDraft,
    };
    for (const field of fieldsForSiteSettings(settingsSite)) {
      payload[field.key] = defaultProfitSettings[field.key] ?? 0;
    }
    const data = await putSettings(payload);
    setSettings(data);
    setSettingsDraft(extractSiteSettings(data, settingsSite));
    setSaveRootDraft(String(data.save_root || ""));
    await recalculateProductsForSettingsSite();
  });

  const fetchProducts = async () => {
    if (!sites.size) return [];
    // 单个站点查询失败不应导致整个产品库显示为空：失败站点提示，其余站点正常展示
    const settled = await Promise.allSettled(
      [...sites].map((site) => listProfitActivityProducts({ site, scope, skcs: querySkcs })),
    );
    const results: ProfitActivityProduct[] = [];
    const failures: string[] = [];
    settled.forEach((item, index) => {
      if (item.status === "fulfilled") results.push(...item.value);
      else {
        const site = [...sites][index];
        failures.push(`${siteLabel(site)}: ${item.reason instanceof Error ? item.reason.message : String(item.reason)}`);
      }
    });
    if (failures.length) setMessage(`部分站点查询失败：${failures.join("；")}`);
    const sourceFiltered = sourceFilter === "all"
      ? results
      : results.filter((item) => sourceFilter === "price_verification"
        ? item.source_type === "price_verification"
        : item.source_type !== "price_verification");
    setShopOptions([...new Set(sourceFiltered.map((item) => (item.store_name || "").trim()).filter(Boolean))]
      .sort((left, right) => left.localeCompare(right, "zh-CN")));
    const filtered = storeFilter === "__empty__"
      ? sourceFiltered.filter((item) => !(item.store_name || "").trim())
      : storeFilter
      ? sourceFiltered.filter((item) => (item.store_name || "").trim() === storeFilter)
      : sourceFiltered;
    return sortProductsByCreatedDesc(filtered);
  };

  const queryProducts = () => withBusy("查询产品", async () => {
    if (!sites.size) {
      setProducts([]);
      setSelected(new Set());
      setPage(1);
      setMessage("请至少勾选一个站点。");
      return;
    }
    const nextProducts = await fetchProducts();
    setProducts(nextProducts);
    setSelected(new Set());
    setPage(1);
    setMessage(`查询数据库产品 完成。\n已查询到 ${nextProducts.length} 个产品。`);
  });

  const refreshDatabaseProducts = () => withBusy("刷新产品", async () => {
    if (!sites.size) {
      setProducts([]);
      setSelected(new Set());
      setPage(1);
      setMessage("请至少勾选一个站点。");
      return;
    }
    const nextProducts = await fetchProducts();
    const nextKeys = new Set(nextProducts.map(productKey));
    setProducts(nextProducts);
    setSelected((current) => new Set([...current].filter((key) => nextKeys.has(key))));
    setPage((current) => Math.min(current, Math.max(1, Math.ceil(nextProducts.length / pageSize))));
    setMessage(`已刷新数据库产品，共 ${nextProducts.length} 个产品。`);
  });

  useEffect(() => {
    // 有缓存：立即展示上次数据并在后台静默刷新（不置 busy、不重置页码/选择）；
    // 无缓存：首次进入自动查询。
    if (productLibraryCache.products) {
      void (async () => {
        try {
          const next = await fetchProducts();
          setProducts(next);
          setMessage(`已刷新为最新产品数据，共 ${next.length} 个产品。`);
        } catch {
          setMessage("后台刷新失败，当前展示上次缓存数据；可点击“查询产品”重试。");
        }
      })();
    } else {
      void queryProducts();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 页面状态变化时同步到跨挂载缓存，切走再切回可立即恢复上次展示
  useEffect(() => {
    productLibraryCache.sites = [...sites];
    productLibraryCache.scope = scope;
    productLibraryCache.querySkcs = querySkcs;
    productLibraryCache.storeFilter = storeFilter;
    productLibraryCache.shopOptions = shopOptions;
    productLibraryCache.products = products;
    productLibraryCache.page = page;
    productLibraryCache.pageSize = pageSize;
  }, [sites, scope, querySkcs, storeFilter, shopOptions, products, page, pageSize]);

  // 刷新产品列表但保留页码/选择/未保存修改：用于图片上传等局部更新，避免把用户“弹回”首页。
  const refreshProducts = async () => {
    if (!sites.size) return;
    const nextProducts = await fetchProducts();
    setProducts(nextProducts);
  };

  refreshProductsRef.current = refreshProducts;

  useEffect(() => {
    const refreshFromLibraryChange = () => {
      if (!isActive) {
        pendingLibraryRefreshRef.current = true;
        return;
      }
      void refreshProductsRef.current()
        .then(() => setMessage("产品库已自动刷新为最新入库数据。"))
        .catch(() => setMessage("产品已入库，但自动刷新失败；可重新打开产品库后重试。"));
    };
    window.addEventListener("profit-activity-products-changed", refreshFromLibraryChange);
    return () => window.removeEventListener("profit-activity-products-changed", refreshFromLibraryChange);
  }, [isActive]);

  useEffect(() => {
    if (!isActive || !pendingLibraryRefreshRef.current) return;
    pendingLibraryRefreshRef.current = false;
    void refreshProductsRef.current()
      .then(() => setMessage("产品库已自动刷新为最新入库数据。"))
      .catch(() => setMessage("产品已入库，但自动刷新失败；可重新打开产品库后重试。"));
  }, [isActive]);

  // 跟随视口的横向滚动条：与表格容器的 scrollLeft 双向同步，track 宽度 = 表格实际宽度。
  // 这样滚动条始终贴在视口顶部可见，不用滑到页面最底部去拖动。
  useEffect(() => {
    const wrap = tableWrapRef.current;
    const bar = tableScrollbarRef.current;
    if (!wrap || !bar) return;
    const syncBar = () => {
      if (bar.scrollLeft !== wrap.scrollLeft) bar.scrollLeft = wrap.scrollLeft;
    };
    const syncWrap = () => {
      if (wrap.scrollLeft !== bar.scrollLeft) wrap.scrollLeft = bar.scrollLeft;
    };
    const updateWidth = () => setTableScrollWidth(wrap.scrollWidth);
    const resizeObserver = new ResizeObserver(updateWidth);
    wrap.addEventListener("scroll", syncBar, { passive: true });
    bar.addEventListener("scroll", syncWrap, { passive: true });
    resizeObserver.observe(wrap);
    updateWidth();
    return () => {
      wrap.removeEventListener("scroll", syncBar);
      bar.removeEventListener("scroll", syncWrap);
      resizeObserver.disconnect();
    };
  }, []);

  // 独立固定表头：原表头滚出可视区时，显示一份固定在顶部的克隆表头，
  // 保证滚动到第 20 行等位置时仍能看到"选择/站点/SKC..."标题行。
  useEffect(() => {
    const wrap = tableWrapRef.current;
    const contentCard = wrap?.closest(".profit-products-workspace") as HTMLElement | null;
    const clone = fixedHeaderRef.current;
    const thead = theadRef.current;
    if (!contentCard || !wrap || !clone || !thead) return;

    const update = () => {
      const contentRect = contentCard.getBoundingClientRect();
      const wrapRect = wrap.getBoundingClientRect();
      const theadRect = thead.getBoundingClientRect();
      // 克隆表头横向与内容区对齐，内层再留出表格左边距，使列与原表头完全对齐
      clone.style.left = `${Math.round(contentRect.left)}px`;
      clone.style.width = `${Math.round(contentRect.width)}px`;
      clone.style.paddingLeft = `${Math.round(wrapRect.left - contentRect.left)}px`;
      // 顶部位置始终以当前可见的工作台顶栏下沿为准。
      // 顶栏不一定带 is-pinned 类，不能依赖该状态判断，否则克隆表头会被顶栏遮住。
      let top = 8;
      const topbar = document.querySelector(".topbar-card") as HTMLElement | null;
      const topbarRect = topbar?.getBoundingClientRect();
      if (topbarRect && topbarRect.bottom > 0 && topbarRect.top < window.innerHeight) {
        top = Math.round(topbarRect.bottom) + 6;
      } else {
        const scrollbar = document.querySelector(".profit-table-scrollbar") as HTMLElement | null;
        if (scrollbar) {
          const scrollbarBottom = Math.round(scrollbar.getBoundingClientRect().bottom);
          if (scrollbarBottom > 0) top = scrollbarBottom + 4;
        }
      }
      clone.style.top = `${top}px`;
      // 表头一进入顶部工作区就由克隆表头接管，不等整行标题完全滚出后才显示。
      // 同时在表格底部前隐藏，避免翻页区域出现孤立的标题行。
      setHeaderStuck(theadRect.top < top && wrapRect.bottom > top + theadRect.height);
    };

    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    contentCard.addEventListener("scroll", update, { passive: true });
    const resizeObserver = new ResizeObserver(update);
    resizeObserver.observe(contentCard);
    resizeObserver.observe(wrap);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      contentCard.removeEventListener("scroll", update);
      resizeObserver.disconnect();
    };
  }, []);

  useEffect(() => {
    const thead = theadRef.current;
    const fixedHeader = fixedHeaderRef.current;
    if (!thead || !fixedHeader) return;
    const syncWidths = () => {
      const sourceTable = thead.closest("table") as HTMLTableElement | null;
      const cloneTable = fixedHeader.querySelector("table") as HTMLTableElement | null;
      if (sourceTable && cloneTable) {
        cloneTable.style.width = `${sourceTable.offsetWidth}px`;
        cloneTable.style.minWidth = `${sourceTable.offsetWidth}px`;
        cloneTable.style.tableLayout = "fixed";
      }
      const sourceThs = thead.querySelectorAll("th");
      const cloneThs = fixedHeader.querySelectorAll("th");
      sourceThs.forEach((th, index) => {
        const cloneTh = cloneThs[index] as HTMLElement | undefined;
        if (cloneTh) {
          const width = `${(th as HTMLElement).offsetWidth}px`;
          cloneTh.style.width = width;
          cloneTh.style.minWidth = width;
        }
      });
    };
    syncWidths();
    const resizeObserver = new ResizeObserver(syncWidths);
    resizeObserver.observe(thead);
    return () => resizeObserver.disconnect();
  }, [pageProducts, headerStuck, columnWidths]);

  useEffect(() => {
    if (tableWrapRef.current) setTableScrollWidth(tableWrapRef.current.scrollWidth);
  }, [columnWidths, pageProducts.length]);

  useEffect(() => {
    const wrap = tableWrapRef.current;
    const cloneScroll = fixedHeaderScrollRef.current;
    if (!wrap || !cloneScroll) return;
    const syncClone = () => {
      if (cloneScroll.scrollLeft !== wrap.scrollLeft) cloneScroll.scrollLeft = wrap.scrollLeft;
    };
    const syncWrap = () => {
      if (wrap.scrollLeft !== cloneScroll.scrollLeft) wrap.scrollLeft = cloneScroll.scrollLeft;
    };
    wrap.addEventListener("scroll", syncClone, { passive: true });
    cloneScroll.addEventListener("scroll", syncWrap, { passive: true });
    return () => {
      wrap.removeEventListener("scroll", syncClone);
      cloneScroll.removeEventListener("scroll", syncWrap);
    };
  }, []);

  // 鼠标拖拽横向平移表格：命中文本内容时保留浏览器默认选中/复制；
  // 只有落在空白处（单元格留白、表格周边空白、滚动条条带）才触发平移。
  const tableDraggingRef = useRef(false);
  // 判断指针落点是否在文本上，用于区分"选择复制"与"拖拽平移"
  const pointerOverText = (x: number, y: number) => {
    const range = document.caretRangeFromPoint?.(x, y);
    if (range) return range.startContainer.nodeType === Node.TEXT_NODE;
    const position = (
      document as Document & {
        caretPositionFromPoint?: (clientX: number, clientY: number) =>
          | { offsetNode: Node }
          | null;
      }
    ).caretPositionFromPoint?.(x, y);
    return !!position && position.offsetNode.nodeType === Node.TEXT_NODE;
  };
  const onTablePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    // 表单控件、链接、按钮均不触发横向平移
    if (target.closest("input, textarea, select, button, a, label")) return;
    const wrap = tableWrapRef.current;
    if (!wrap || wrap.scrollWidth <= wrap.clientWidth || event.button !== 0) return;
    // 按下位置命中文本 → 让浏览器正常选择/复制，不进入平移
    if (pointerOverText(event.clientX, event.clientY)) return;
    tableDragRef.current = { startX: event.clientX, startScrollLeft: wrap.scrollLeft };
    tableDraggingRef.current = false;
  };
  const onTablePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = tableDragRef.current;
    const wrap = tableWrapRef.current;
    if (!drag || !wrap) return;
    const deltaX = event.clientX - drag.startX;
    if (!tableDraggingRef.current && Math.abs(deltaX) < 6) return;
    tableDraggingRef.current = true;
    setTableDragging(true);
    wrap.scrollLeft = drag.startScrollLeft - deltaX;
  };
  const endTableDrag = () => {
    tableDragRef.current = null;
    tableDraggingRef.current = false;
    setTableDragging(false);
  };

  const togglePageSelected = () => {
    setSelected((current) => {
      const next = new Set(current);
      for (const product of pageProducts) {
        const key = productKey(product);
        if (pageSelected) next.delete(key);
        else next.add(key);
      }
      return next;
    });
  };

  const deleteSelected = () => withBusy("删除已选产品", async () => {
    if (!selectedCount) return;
    const confirmed = window.confirm(`确认删除已选 ${selectedCount} 个产品？`);
    if (!confirmed) {
      setMessage("已取消删除。");
      return;
    }
    const bySite = new Map<ProfitActivitySite, string[]>();
    for (const product of products) {
      if (!selected.has(productKey(product))) continue;
      const site = (product.site || product.site_code || "US") as ProfitActivitySite;
      bySite.set(site, [...(bySite.get(site) || []), product.skc]);
    }
    let deleted = 0;
    for (const [site, skcs] of bySite) {
      const result = await deleteProfitActivityProducts({ site, skcs });
      deleted += result.deleted ?? skcs.length;
    }
    const nextProducts = await fetchProducts();
    setProducts(nextProducts);
    setSelected(new Set());
    setPage((current) => Math.min(current, Math.max(1, Math.ceil(nextProducts.length / pageSize))));
    setMessage(`删除完成，后端确认删除 ${deleted} 个产品。`);
  });

  const copySelectedProductIds = () => withBusy("复制商品ID", async () => {
    const ids = selectedProducts
      .map(productIdText)
      .filter(Boolean);
    if (!ids.length) return;
    await navigator.clipboard.writeText(ids.join("\n"));
    setMessage(`已复制 ${ids.length} 个商品ID。`);
  });

  const openBatchStoreDialog = () => {
    if (!selectedCount) {
      setMessage("请先选择产品。");
      return;
    }
    setBatchStoreName("");
    setBatchStoreOpen(true);
  };

  const submitBatchStoreName = () => withBusy("批量修改店铺", async () => {
    if (!selectedProducts.length) {
      setBatchStoreOpen(false);
      setMessage("请先选择产品。");
      return;
    }
    const storeName = batchStoreName.trim();
    let success = 0;
    let failed = 0;
    for (const product of selectedProducts) {
      if (product.can_edit === false) {
        failed += 1;
        continue;
      }
      try {
        await updateProfitActivityProduct({
          site: (product.site || product.site_code || "US") as ProfitActivitySite,
          skc: product.skc,
          store_name: storeName,
        });
        success += 1;
      } catch {
        failed += 1;
      }
    }
    const nextProducts = await fetchProducts();
    setProducts(nextProducts);
    setSelected(new Set());
    setBatchStoreOpen(false);
    setBatchStoreName("");
    setPage((current) => Math.min(current, Math.max(1, Math.ceil(nextProducts.length / pageSize))));
    setMessage(failed
      ? `已修改 ${success} 个产品的店铺名称，${failed} 个产品修改失败。`
      : `已修改 ${success} 个产品的店铺名称。`);
  });

  const beginInlineEdit = (item: ProfitActivityProduct, field: InlineEditField) => {
    const key = productKey(item);
    if (field === "site") {
      setInlineEdit({ key, field, value: (item.site || item.site_code || "US") as ProfitActivitySite });
      return;
    }
    if (field === "product_image" || field === "attachment_image") {
      setInlineEdit({ key, field, file: null, clear: false });
      return;
    }
    const values = {
      store_name: item.store_name ?? "",
      product_id: productIdText(item),
      selling_price: item.selling_price == null ? "" : String(item.selling_price),
      cost_price: item.cost_price == null ? "" : String(item.cost_price),
      weight_kg: item.weight_kg == null ? "" : String(item.weight_kg),
      note: item.note ?? "",
    };
    setInlineEdit({ key, field, value: values[field] });
  };

  const saveInlineEdit = async (item: ProfitActivityProduct) => {
    if (!inlineEdit || inlineEdit.key !== productKey(item)) return;
    const currentSite = (item.site || item.site_code || "US") as ProfitActivitySite;
    const next = {
      site: currentSite,
      storeName: item.store_name ?? "",
      productId: productIdText(item),
      sellingPrice: item.selling_price == null ? "" : String(item.selling_price),
      costPrice: item.cost_price == null ? "" : String(item.cost_price),
      weightKg: item.weight_kg == null ? "" : String(item.weight_kg),
      note: item.note ?? "",
      productImage: null as File | null,
      attachmentImage: null as File | null,
    };
    if (inlineEdit.field === "site") next.site = inlineEdit.value;
    if (inlineEdit.field === "store_name") next.storeName = inlineEdit.value;
    if (inlineEdit.field === "product_id") next.productId = inlineEdit.value;
    if (inlineEdit.field === "selling_price") next.sellingPrice = inlineEdit.value;
    if (inlineEdit.field === "cost_price") next.costPrice = inlineEdit.value;
    if (inlineEdit.field === "weight_kg") next.weightKg = inlineEdit.value;
    if (inlineEdit.field === "note") next.note = inlineEdit.value;
    if (inlineEdit.field === "product_image") next.productImage = inlineEdit.file;
    if (inlineEdit.field === "attachment_image") next.attachmentImage = inlineEdit.file;
    if ((inlineEdit.field === "product_image" || inlineEdit.field === "attachment_image") && !inlineEdit.file && !inlineEdit.clear) {
      setMessage("请先选择或 Ctrl+V 粘贴一张图片。");
      return;
    }
    const numbers = [next.sellingPrice, next.costPrice, next.weightKg].map(Number);
    if (numbers.some((value) => !Number.isFinite(value) || value < 0)) {
      setMessage("售价、成本和重量必须为大于或等于 0 的数字。");
      return;
    }
    setBusy("保存产品");
    try {
      const result = await saveProfitActivityProductEdit({
        site: next.site,
        currentSite,
        skc: item.skc,
        productId: next.productId,
        sellingPrice: next.sellingPrice,
        costPrice: next.costPrice,
        weightKg: next.weightKg,
        note: next.note,
        storeName: next.storeName,
        productImage: next.productImage,
        attachmentImage: next.attachmentImage,
        clearProductImage: inlineEdit.field === "product_image" && inlineEdit.clear,
        clearAttachmentImage: inlineEdit.field === "attachment_image" && inlineEdit.clear,
      });
      const oldKey = productKey(item);
      setProducts((current) => sortProductsByCreatedDesc(current.map((product) => productKey(product) === oldKey ? result.product : product)));
      setActiveProduct((current) => current && productKey(current) === oldKey ? result.product : current);
      setInlineEdit(null);
      setMessage(`已保存 ${result.product.skc} 的修改。`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy("");
    }
  };

  const setInlineValue = (value: string) => {
    setInlineEdit((current) => current && "value" in current ? { ...current, value } : current);
  };

  const setInlineFile = (file: File) => {
    setInlineEdit((current) => current && "file" in current ? { ...current, file, clear: false } : current);
  };

  const clearInlineImage = () => {
    setInlineEdit((current) => current && "file" in current ? { ...current, file: null, clear: true } : current);
  };

  return (
    <div className="profit-products-page">
      <section className="profit-products-head">
        <div>
          <h1>产品库</h1>
        <p>查询、管理产品库数据。</p>
        </div>
        <div className="profit-products-hero-actions">
          <button className="profit-products-settings-toggle" type="button" aria-haspopup="dialog" onClick={openSettingsDialog} disabled={!!busy}>
            站点费率设置
          </button>
        </div>
      </section>

      <section className="profit-products-workspace">
        <div className="profit-products-search-bar">
          <label className="profit-products-search-field">
            <span>商品ID查询</span>
            <textarea value={querySkcs} onChange={(event) => setQuerySkcs(event.target.value)} placeholder="输入 SKU、SKC、SPU；多条可用空格、逗号或换行分隔" />
          </label>
          <label className="profit-products-select-field">
            <span>店铺</span>
            <select value={storeFilter} onChange={(event) => setStoreFilter(event.target.value)}>
              <option value="">全部店铺</option>
              <option value="__empty__">未填写店铺</option>
              {shopOptions.map((store) => <option key={store} value={store}>{store}</option>)}
            </select>
          </label>
          <label className="profit-products-select-field">
            <span>地区</span>
            <select
              value={sites.size === 1 ? [...sites][0] : "all"}
              onChange={(event) => setSites(event.target.value === "all" ? new Set(availableSites) : new Set([event.target.value as ProfitActivitySite]))}
            >
              <option value="all">全部站点</option>
              {siteOptions.map((item) => <option key={item.site_code} value={item.site_code}>{item.display_name}</option>)}
            </select>
          </label>
          <label className="profit-products-select-field">
            <span>查询范围</span>
            <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value as ProductSourceFilter)}>
              <option value="manual">手动</option>
              <option value="price_verification">核价</option>
              <option value="all">核价 + 手动</option>
            </select>
          </label>
          <div className="profit-products-search-actions">
            <button className="primary-button profit-products-query-button" onClick={queryProducts} disabled={!!busy}>查询产品</button>
            <button className="profit-products-refresh-button" onClick={refreshDatabaseProducts} disabled={!!busy}>刷新</button>
          </div>
        </div>
        <p className="profit-products-message">{busy || message.split("\n").map((line, index) => (<span key={index}>{line}<br /></span>))}</p>

        <div className="profit-table-scrollbar" ref={tableScrollbarRef} aria-label="横向滚动产品表格">
          <div className="profit-table-scrollbar-track" style={{ width: tableScrollWidth || "100%" }} />
        </div>
        {createPortal(
          <div
            className={`profit-table-head-sticky ${headerStuck ? "is-visible" : ""}`}
            ref={fixedHeaderRef}
            aria-hidden="true"
          >
            <div className="profit-table-head-scroll" ref={fixedHeaderScrollRef}>
              <table className="profit-table" style={{ minWidth: tableMinWidth, width: tableMinWidth }}>
                <ProductTableColumns widths={columnWidths} />
                <thead>
                  <ProductTableHeader />
                </thead>
              </table>
            </div>
          </div>,
          document.body,
        )}
        <div
          className={`profit-table-wrap ${tableDragging ? "is-dragging" : ""}`}
          ref={tableWrapRef}
          onPointerDown={onTablePointerDown}
          onPointerMove={onTablePointerMove}
          onPointerUp={endTableDrag}
          onPointerCancel={endTableDrag}
          onPointerLeave={endTableDrag}
        >
          <table className="profit-table" style={{ minWidth: tableMinWidth, width: tableMinWidth }}>
            <ProductTableColumns widths={columnWidths} />
            <thead ref={theadRef}>
              <ProductTableHeader onResizeStart={startColumnResize} />
            </thead>
            <tbody>
              {pageProducts.length ? pageProducts.map((item) => {
                const key = productKey(item);
                return (
                  <tr key={key}>
                    <td><input type="checkbox" checked={selected.has(key)} onChange={(event) => setSelected(toggleSet(selected, key, event.target.checked))} /></td>
                    <td><EditableCell field="site" item={item} siteOptions={siteOptions} inlineEdit={inlineEdit} onEdit={beginInlineEdit} onSave={saveInlineEdit} onCancel={() => setInlineEdit(null)} onValueChange={setInlineValue} disabled={item.can_edit === false}>
                      {siteLabel((item.site || item.site_code || "US") as ProfitActivitySite)}
                    </EditableCell></td>
                    <td><EditableCell field="store_name" item={item} inlineEdit={inlineEdit} onEdit={beginInlineEdit} onSave={saveInlineEdit} onCancel={() => setInlineEdit(null)} onValueChange={setInlineValue} disabled={item.can_edit === false}>
                      {item.store_name || "-"}
                    </EditableCell></td>
                    <td><EditableCell field="product_id" item={item} inlineEdit={inlineEdit} onEdit={beginInlineEdit} onSave={saveInlineEdit} onCancel={() => setInlineEdit(null)} onValueChange={setInlineValue} disabled={item.can_edit === false}>
                      {productIdText(item)}{item.source_type === "price_verification" && <em className="profit-source-badge" title="来自核价及货源板块自动入库">核价</em>}
                    </EditableCell></td>
                    <td>{libraryDate(item.library_created_at || item.created_at)}</td>
                    <td><EditableCell field="product_image" item={item} inlineEdit={inlineEdit} onEdit={beginInlineEdit} onSave={saveInlineEdit} onCancel={() => setInlineEdit(null)} onFileChange={setInlineFile} onClearImage={clearInlineImage} disabled={item.can_edit === false}>
                      <ProductImageCell
                        item={item}
                        onPreview={(image) => setPreviewImage({ images: [image], index: 0 })}
                        onEdit={() => beginInlineEdit(item, "product_image")}
                      />
                    </EditableCell></td>
                    <td><EditableCell field="selling_price" item={item} inlineEdit={inlineEdit} onEdit={beginInlineEdit} onSave={saveInlineEdit} onCancel={() => setInlineEdit(null)} onValueChange={setInlineValue} disabled={item.can_edit === false}>{money(item.selling_price)}</EditableCell></td>
                    <td><EditableCell field="cost_price" item={item} inlineEdit={inlineEdit} onEdit={beginInlineEdit} onSave={saveInlineEdit} onCancel={() => setInlineEdit(null)} onValueChange={setInlineValue} disabled={item.can_edit === false}>{money(item.cost_price)}</EditableCell></td>
                    <td><EditableCell field="weight_kg" item={item} inlineEdit={inlineEdit} onEdit={beginInlineEdit} onSave={saveInlineEdit} onCancel={() => setInlineEdit(null)} onValueChange={setInlineValue} disabled={item.can_edit === false}>{money(item.weight_kg)}</EditableCell></td>
                    <td className={(item.net_profit ?? 0) >= 0 ? "profit-good" : "profit-bad"}>{money(item.net_profit)}</td>
                    <td>{percent(item.profit_rate)}</td>
                    <td className="profit-note-cell" title={item.note || ""}><EditableCell field="note" item={item} inlineEdit={inlineEdit} onEdit={beginInlineEdit} onSave={saveInlineEdit} onCancel={() => setInlineEdit(null)} onValueChange={setInlineValue} disabled={item.can_edit === false}>{shortNote(item.note) || "-"}</EditableCell></td>
                    <td className="profit-source-cell"><button className="profit-source-open" onClick={() => setActiveProduct(item)} title="查看/编辑该 SKC 的货源链接">打开（{(item.source_groups ?? []).filter((group) => group?.source_url).length}）</button></td>
                    <td><EditableCell field="attachment_image" item={item} inlineEdit={inlineEdit} onEdit={beginInlineEdit} onSave={saveInlineEdit} onCancel={() => setInlineEdit(null)} onFileChange={setInlineFile} onClearImage={clearInlineImage} disabled={item.can_edit === false}>
                      <AttachmentImageCell
                        item={item}
                        onPreview={(image) => setPreviewImage({ images: [image], index: 0 })}
                        onEdit={() => beginInlineEdit(item, "attachment_image")}
                      />
                    </EditableCell></td>
                  </tr>
                );
              }) : (
                <tr><td colSpan={productTableColumns.length}>暂无产品。输入商品ID（SKU、SKC 或 SPU）后查询，或留空查询当前权限可见产品。</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="profit-products-pagination">
          <div className="profit-products-pagination-summary">
            <span>共 {products.length} 条，当前第 {safePage} / {totalPages} 页</span>
            <button onClick={togglePageSelected} disabled={!pageProducts.length}>{pageSelected ? "取消本页" : "全选本页"}</button>
            <button onClick={copySelectedProductIds} disabled={!selectedCount || !!busy}>复制已选商品ID</button>
            <button onClick={openBatchStoreDialog} disabled={!selectedCount || !!busy}>批量修改店铺</button>
            <button className="danger-button" onClick={deleteSelected} disabled={!selectedCount || !!busy}>删除已选 {selectedCount}</button>
            <label className="profit-products-page-size">每页
              <select value={pageSize} onChange={(event) => { setPageSize(Number(event.target.value) as typeof pageSize); setPage(1); }}>
                {pageSizeOptions.map((value) => <option key={value} value={value}>{value} 条</option>)}
              </select>
            </label>
          </div>
          <div className="profit-products-page-controls">
            <button onClick={() => setPage(1)} disabled={safePage <= 1}>首页</button>
            <button onClick={() => setPage((current) => Math.max(1, current - 1))} disabled={safePage <= 1}>上一页</button>
            <label className="profit-page-jump">第
              <input
                value={pageInput}
                type="number"
                min={1}
                max={totalPages}
                onChange={(event) => setPageInput(event.target.value)}
                onKeyDown={(event) => { if (event.key === "Enter") goToPageInput(); }}
                onBlur={goToPageInput}
                aria-label="跳转页码"
              />
              页</label>
            <button onClick={goToPageInput}>跳转</button>
            <button onClick={() => setPage((current) => Math.min(totalPages, current + 1))} disabled={safePage >= totalPages}>下一页</button>
            <button onClick={() => setPage(totalPages)} disabled={safePage >= totalPages}>末页</button>
          </div>
        </div>
      </section>
      <ProductSourceDrawer
        product={activeProduct}
        onClose={() => setActiveProduct(null)}
        onChanged={refreshProducts}
      />
      {settingsDialogOpen ? createPortal(
        <div className="profit-products-settings-backdrop" role="presentation" onMouseDown={() => !busy && setSettingsDialogOpen(false)}>
          <section className="profit-products-settings-dialog" role="dialog" aria-modal="true" aria-labelledby="profit-products-settings-title" onMouseDown={(event) => event.stopPropagation()}>
            <div className="profit-products-settings-head">
              <div>
                <h2 id="profit-products-settings-title">站点费率设置</h2>
                <p>保存后会按当前费率重算该站点产品库的利润和利润率。</p>
              </div>
              <button className="profit-products-settings-close" type="button" aria-label="关闭站点费率设置" onClick={() => setSettingsDialogOpen(false)} disabled={!!busy}><span aria-hidden="true">×</span></button>
            </div>
            <label className="profit-products-save-root">本地保存目录
              <input value={saveRootDraft} onChange={(event) => setSaveRootDraft(event.target.value)} placeholder="例如 /Users/xxx/outputs/profit_activity" />
            </label>
            <div className="profit-products-settings-tabs" role="tablist" aria-label="站点费率">
              {siteProfiles.map((profile) => (
                <button key={profile.id} type="button" role="tab" aria-selected={settingsSite === profile.id} className={settingsSite === profile.id ? "is-active" : ""} onClick={() => selectSettingsSite(profile.id)}>{profile.label}</button>
              ))}
              <button className="profit-products-add-site-button" type="button" onClick={() => setNewSiteOpen((value) => !value)}>+ 新增站点</button>
            </div>
            {newSiteOpen ? (
              <div className="profit-products-new-site-form">
                <label>站点代码
                  <input className={newSiteCodeInvalid ? "is-invalid" : undefined} aria-invalid={newSiteCodeInvalid} value={newSiteCode} maxLength={12} onChange={(event) => { setNewSiteCode(event.target.value.toUpperCase()); setNewSiteCodeInvalid(false); }} placeholder="例如 BR" />
                </label>
                <label>站点名称
                  <input value={newSiteName} maxLength={80} onChange={(event) => setNewSiteName(event.target.value)} placeholder="例如 巴西" />
                </label>
                <button type="button" onClick={createSite} disabled={!!busy}>创建站点</button>
              </div>
            ) : null}
            <p className="profit-products-formula-note">正在编辑 {siteProfiles.find((profile) => profile.id === settingsSite)?.label || siteLabel(settingsSite)} 的费率；未设置的费率默认按 0 计算。</p>
            <div className="profit-products-settings-fields">
              {fieldsForSiteSettings(settingsSite).map((field) => (
                <label key={field.key}>{field.label}
                  <input type="number" min="0" step={field.transform === "percent" ? "0.01" : "0.01"} value={settingsDraft[field.key] ?? ""} onChange={(event) => setSettingsDraft((current) => ({ ...current, [field.key]: event.target.value }))} />
                </label>
              ))}
            </div>
            <div className="profit-products-settings-actions">
              <button type="button" className="primary-button" onClick={saveSiteSettings} disabled={!!busy}>保存并重算</button>
              <button type="button" onClick={restoreDefaultSettings} disabled={!!busy}>恢复默认并重算</button>
            </div>
          </section>
        </div>,
        document.body,
      ) : null}
      {batchStoreOpen ? createPortal(
        <div className="profit-batch-store-mask" role="presentation" onClick={() => !busy && setBatchStoreOpen(false)}>
          <div className="profit-batch-store-dialog" role="dialog" aria-modal="true" aria-labelledby="profit-batch-store-title" onClick={(event) => event.stopPropagation()}>
            <div className="profit-batch-store-head">
              <h2 id="profit-batch-store-title">批量修改店铺</h2>
              <button type="button" aria-label="关闭批量修改店铺" onClick={() => setBatchStoreOpen(false)} disabled={!!busy}>×</button>
            </div>
            <p className="profit-batch-store-count">已选择 {selectedCount} 个产品。</p>
            <label className="profit-batch-store-field">店铺名称
              <input value={batchStoreName} maxLength={120} onChange={(event) => setBatchStoreName(event.target.value)} placeholder="例如：美区一店；留空表示清空" autoFocus />
            </label>
            <div className="profit-batch-store-actions">
              <button type="button" onClick={() => setBatchStoreOpen(false)} disabled={!!busy}>取消</button>
              <button type="button" className="primary-button" onClick={() => void submitBatchStoreName()} disabled={!!busy}>确认修改</button>
            </div>
          </div>
        </div>,
        document.body,
      ) : null}
      {previewImage ? (
        <ImagePreviewModal
          images={previewImage.images}
          index={previewImage.index}
          onIndexChange={(index) => setPreviewImage((current) => current ? { ...current, index } : current)}
          onClose={() => setPreviewImage(null)}
        />
      ) : null}
    </div>
  );
}

function ProductTableColumns({ widths }: { widths: Record<ProductTableColumnKey, number> }) {
  return (
    <colgroup>
      {productTableColumns.map((column) => (
        <col key={column.key} style={{ width: widths[column.key] }} />
      ))}
    </colgroup>
  );
}

function ProductTableHeader({ onResizeStart }: { onResizeStart?: (column: ProductTableColumn, event: ReactMouseEvent<HTMLButtonElement>) => void }) {
  return (
    <tr>
      {productTableColumns.map((column) => (
        <th key={column.key} title={column.label}>
          <span className="profit-table-th-label">{column.label}</span>
          {onResizeStart ? (
            <button
              type="button"
              className="profit-table-resize-handle"
              aria-label={`调整${column.label}列宽`}
              onMouseDown={(event) => onResizeStart(column, event)}
            />
          ) : null}
        </th>
      ))}
    </tr>
  );
}

function EditableCell({
  children,
  disabled,
  field,
  item,
  siteOptions,
  inlineEdit,
  onEdit,
  onSave,
  onCancel,
  onValueChange,
  onFileChange,
  onClearImage,
}: {
  children: ReactNode;
  disabled?: boolean;
  field: InlineEditField;
  item: ProfitActivityProduct;
  siteOptions?: Array<{ site_code: ProfitActivitySite; display_name?: string }>;
  inlineEdit: InlineEditState | null;
  onEdit: (item: ProfitActivityProduct, field: InlineEditField) => void;
  onSave: (item: ProfitActivityProduct) => Promise<void>;
  onCancel: () => void;
  onValueChange?: (value: string) => void;
  onFileChange?: (file: File) => void;
  onClearImage?: () => void;
}) {
  const isEditing = inlineEdit?.key === productKey(item) && inlineEdit.field === field;
  const isImageField = field === "product_image" || field === "attachment_image";
  const pasteImage = (event: ReactClipboardEvent<HTMLDivElement>) => {
    const file = [...event.clipboardData.files].find((itemFile) => itemFile.type.startsWith("image/"));
    if (!file || !onFileChange) return;
    event.preventDefault();
    onFileChange(file);
  };
  if (isEditing) {
    return (
      <span className={`profit-inline-editor ${isImageField ? "is-image" : ""}`}>
        {field === "site" && "value" in inlineEdit ? (
          <select value={inlineEdit.value} onChange={(event) => onValueChange?.(event.target.value)}>
            {(siteOptions?.length ? siteOptions : allSites.map((site) => ({ site_code: site, display_name: siteLabels[site] }))).map((site) => (
              <option key={site.site_code} value={site.site_code}>{site.display_name || siteLabels[site.site_code] || site.site_code}</option>
            ))}
          </select>
        ) : null}
        {(field === "product_id" || field === "store_name") && "value" in inlineEdit ? (
          <input type="text" value={inlineEdit.value} onChange={(event) => onValueChange?.(event.target.value)} autoFocus />
        ) : null}
        {(field === "selling_price" || field === "cost_price" || field === "weight_kg") && "value" in inlineEdit ? (
          <input type="number" min="0" step="any" value={inlineEdit.value} onChange={(event) => onValueChange?.(event.target.value)} autoFocus />
        ) : null}
        {field === "note" && "value" in inlineEdit ? (
          <textarea maxLength={500} value={inlineEdit.value} onChange={(event) => onValueChange?.(event.target.value)} autoFocus />
        ) : null}
        {isImageField && "file" in inlineEdit ? (
          <div className="profit-inline-image-drop" tabIndex={0} onPaste={pasteImage} title="点击后可 Ctrl+V 粘贴图片">
            <span>{inlineEdit.clear ? "已标记删除" : inlineEdit.file?.name || "Ctrl+V 粘贴图片"}</span>
            <input type="file" accept="image/*" onChange={(event) => { const file = event.target.files?.[0]; if (file) onFileChange?.(file); }} />
          </div>
        ) : null}
        {isImageField && "file" in inlineEdit && !inlineEdit.clear ? <button className="profit-inline-remove-image" type="button" onClick={onClearImage} title="删除当前图片">×</button> : null}
        <button className="profit-inline-save" type="button" onClick={() => void onSave(item)}>保存</button>
        <button className="profit-inline-cancel" type="button" onClick={onCancel}>撤销</button>
      </span>
    );
  }
  return (
    <span className={`profit-editable-cell ${disabled ? "is-readonly" : ""}`} role={disabled ? undefined : "button"} tabIndex={disabled ? -1 : 0} onClick={(event) => {
      if (disabled || (event.target as HTMLElement).closest("button, input, select, textarea, a")) return;
      onEdit(item, field);
    }} onKeyDown={(event) => {
      if (!disabled && (event.key === "Enter" || event.key === " ")) {
        event.preventDefault();
        onEdit(item, field);
      }
    }}>
      <span className="profit-editable-value">{children}</span>
    </span>
  );
}

type PreviewImage = { url: string; label: string };

function firstSourceMainImageUrl(item: ProfitActivityProduct) {
  return String((item.source_groups ?? [])
    .map((group) => group?.main_image_url)
    .find((value) => String(value || "").trim()) || "").trim();
}

/** 大图预览浮层：始终位于当前可视区域中央，可在货源图片之间切换。 */
function ImagePreviewModal({ images, index, onIndexChange, onClose }: { images: PreviewImage[]; index: number; onIndexChange: (index: number) => void; onClose: () => void }) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const current = images[index] ?? images[0];
  if (!current) return null;

  // 使用实际可视区域限制预览尺寸，让整张图始终能在当前屏幕内看全。
  const viewportWidth = document.documentElement.clientWidth;
  const viewportHeight = document.documentElement.clientHeight;
  const previewWidth = Math.min(460, Math.max(240, viewportWidth - 32));
  const previewHeight = Math.min(460, Math.max(240, viewportHeight - 32));

  return createPortal(
    <div className="profit-image-preview-mask" onClick={onClose}>
      <div className="profit-image-preview-modal" style={{ left: "50%", top: "50%", width: previewWidth, height: previewHeight, transform: "translate(-50%, -50%)" }} onClick={(event) => event.stopPropagation()}>
        <button type="button" className="profit-image-preview-close" onClick={onClose} title="关闭">×</button>
        <img className="profit-image-preview-img" src={current.url} alt={current.label} />
        {images.length > 1 ? (
          <div className="profit-image-preview-pager">
            <button type="button" onClick={() => onIndexChange((index - 1 + images.length) % images.length)}>上一张</button>
            <span>{index + 1} / {images.length}</span>
            <button type="button" onClick={() => onIndexChange((index + 1) % images.length)}>下一张</button>
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}

function toggleSet(source: Set<string>, value: string, checked: boolean) {
  const next = new Set(source);
  if (checked) next.add(value);
  else next.delete(value);
  return next;
}

/** 商品图单元格：只展示唯一的商品主图，不再混入货源图片。 */
function ProductImageCell({ item, onPreview, onEdit }: { item: ProfitActivityProduct; onPreview?: (image: PreviewImage) => void; onEdit?: () => void }) {
  const site = (item.site || item.site_code || "US") as ProfitActivitySite;
  const [url, setUrl] = useState("");
  const previewTimerRef = useRef<number | null>(null);
  // 核价入库没有本地商品图时，复用执行图搜前的 Temu 主图作为产品库商品图。
  const sourcingImageUrl = item.source_type === "price_verification"
    ? String(item.source_main_image_url || "").trim()
    : "";

  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";
    setUrl(sourcingImageUrl);
    if (!item.image_path) return undefined;
    loadProductImage({ skc: item.skc, site, kind: "product", version: item.image_path })
      .then((loaded) => {
          if (cancelled) {
            URL.revokeObjectURL(loaded);
            return;
          }
          objectUrl = loaded;
          setUrl(loaded);
        })
      .catch(() => setUrl(sourcingImageUrl));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item.skc, site, item.image_path, sourcingImageUrl]);

  useEffect(() => () => {
    if (previewTimerRef.current !== null) window.clearTimeout(previewTimerRef.current);
  }, []);

  const openPreview = (image: PreviewImage) => {
    if (!onPreview) return;
    if (previewTimerRef.current !== null) window.clearTimeout(previewTimerRef.current);
    previewTimerRef.current = window.setTimeout(() => {
      previewTimerRef.current = null;
      onPreview(image);
    }, 180);
  };

  const startEdit = () => {
    if (previewTimerRef.current !== null) {
      window.clearTimeout(previewTimerRef.current);
      previewTimerRef.current = null;
    }
    onEdit?.();
  };

  const content = url ? (
    <img className="profit-product-image" src={url} alt={`${item.skc} 商品图`} />
  ) : <span className="profit-product-image-empty">无图</span>;

  if (!url || !onPreview) {
    if (!onEdit) return <span className="profit-product-image-cell">{content}</span>;
    return (
      <button type="button" className="profit-product-image-cell" onClick={(event) => { event.stopPropagation(); startEdit(); }} title="点击上传或粘贴商品图">
        {content}
      </button>
    );
  }
  return (
    <button type="button" className="profit-product-image-cell" onClick={(event) => { event.stopPropagation(); openPreview({ url, label: `${productIdText(item)} 商品图` }); }} onDoubleClick={(event) => { event.preventDefault(); event.stopPropagation(); startEdit(); }} title="点击查看商品图，双击更换">
      {content}
    </button>
  );
}

function AttachmentImageCell({ item, onPreview, onEdit }: { item: ProfitActivityProduct; onPreview?: (image: PreviewImage) => void; onEdit?: () => void }) {
  const site = (item.site || item.site_code || "US") as ProfitActivitySite;
  const [url, setUrl] = useState("");
  const previewTimerRef = useRef<number | null>(null);
  const sourceMainImageUrl = firstSourceMainImageUrl(item);

  useEffect(() => {
    let cancelled = false;
    let objectUrl = "";
    setUrl(sourceMainImageUrl);
    if (!item.attachment_image_path) return undefined;
    loadProductImage({ skc: item.skc, site, kind: "attachment", version: item.attachment_image_path })
      .then((loaded) => {
        if (cancelled) {
          URL.revokeObjectURL(loaded);
          return;
        }
        objectUrl = loaded;
        setUrl(loaded);
      })
      .catch(() => setUrl(sourceMainImageUrl));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [item.skc, site, item.attachment_image_path, sourceMainImageUrl]);

  useEffect(() => () => {
    if (previewTimerRef.current !== null) window.clearTimeout(previewTimerRef.current);
  }, []);

  const openPreview = (image: PreviewImage) => {
    if (!onPreview) return;
    if (previewTimerRef.current !== null) window.clearTimeout(previewTimerRef.current);
    previewTimerRef.current = window.setTimeout(() => {
      previewTimerRef.current = null;
      onPreview(image);
    }, 180);
  };

  const startEdit = () => {
    if (previewTimerRef.current !== null) {
      window.clearTimeout(previewTimerRef.current);
      previewTimerRef.current = null;
    }
    onEdit?.();
  };

  const content = url ? (
    <img className="profit-product-image" src={url} alt={`${item.skc} 图片`} />
  ) : <span className="profit-product-image-empty">无图</span>;

  if (!url || !onPreview) {
    if (!onEdit) return <span className="profit-product-image-cell">{content}</span>;
    return (
      <button type="button" className="profit-product-image-cell" onClick={(event) => { event.stopPropagation(); startEdit(); }} title="点击上传或粘贴图片">
        {content}
      </button>
    );
  }
  return (
    <button type="button" className="profit-product-image-cell" onClick={(event) => { event.stopPropagation(); openPreview({ url, label: `${productIdText(item)} 图片` }); }} onDoubleClick={(event) => { event.preventDefault(); event.stopPropagation(); startEdit(); }} title="点击查看图片，双击更换">
      {content}
    </button>
  );
}

function libraryDate(value?: string) {
  const date = value ? new Date(value) : null;
  if (!date || !Number.isFinite(date.valueOf())) return "-";
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function shortNote(value?: string) {
  const note = value?.trim() ?? "";
  return note.length > 4 ? `${note.slice(0, 4)}…` : note;
}

function money(value: unknown) {
  return typeof value === "number" ? value.toFixed(2) : "-";
}

function percent(value: unknown) {
  return typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "-";
}
