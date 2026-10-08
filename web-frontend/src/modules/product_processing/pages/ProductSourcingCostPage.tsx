import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { ppRequest, type ApiContext } from "../api/client";
import { productProcessingApiContext } from "../api/context";
import type { DraftCollectionBatch } from "../api/productProcessingApi";
import type { DraftProcessedPreview, Draft, DraftListResponse, PreviewImageAsset, PreviewItem } from "../types";
import type { ProfitActivityPrefill } from "../../profit_activity/types/products";
import "../styles/ProductProcessingVerifyPage.css";
import "../styles/ProductSourcingCostPage.css";

const API_BASE = "/api/product-processing";

type Props = {
  isActive?: boolean;
  onOpenProfitActivity?: (prefill: ProfitActivityPrefill) => void;
};

type SourcingRow = {
  id: number;
  title: string;
  imageUrl: string;
  sourceUrl: string;
  platform: string;
  shopName: string;
  channel: string;
  batchId: string;
  batchDisplayName: string;
  cost: number | null;
  declaredPrice: number | null;
  currency: string;
  status: string;
  createdAt: string;
  skus: SkuRow[];
};

type SkuRow = {
  key: string;
  skuId: string;
  label: string;
  imageUrl: string;
  price: number | null;
  currency: string;
  quantity: number | null;
};

const CHANNEL_LABELS: Record<string, string> = {
  daily_selection: "每日选品",
  shop_collection: "整店采集",
  plugin_capture: "插件采集",
};

const PLATFORM_LABELS: Record<string, string> = {
  "1688": "1688",
  taobao: "淘宝",
  tmall: "天猫",
  temu: "Temu",
};

const STATUS_OPTIONS = [
  { value: "draft", label: "待处理" },
  { value: "processing", label: "处理中" },
  { value: "processed", label: "已完成" },
];

const PAGE_SIZE_OPTIONS = [10, 20, 50, 100];

// 单品采集（插件单品回传）不建批次，草稿的 selection_run_id 为空。用一个哨兵值把
// 「未分组」也纳入批次筛选，与预检页、后端 list_drafts 的 __unassigned__ 口径一致。
const UNASSIGNED_BATCH_ID = "__unassigned__";
const UNASSIGNED_BATCH_LABEL = "未分组（单品采集）";

function api(): ApiContext {
  return productProcessingApiContext();
}

function channelLabel(channel: string): string {
  if (!channel) return "未知来源";
  return CHANNEL_LABELS[channel] || channel;
}

function platformLabel(platform: string): string {
  if (!platform) return "—";
  return PLATFORM_LABELS[platform.toLowerCase()] || platform;
}

const CURRENCY_SYMBOLS: Record<string, string> = {
  CNY: "¥",
  USD: "$",
  EUR: "€",
  GBP: "£",
  CAD: "CA$",
  AUD: "A$",
  JPY: "¥",
  HKD: "HK$",
  KRW: "₩",
};

function normalizeCurrency(value: unknown): string {
  const code = String(value ?? "").trim();
  if (!code) return "";
  if (code === "￥" || code === "¥" || code.toUpperCase() === "RMB") return "CNY";
  if (code === "$" || code.toUpperCase() === "US$") return "USD";
  return code.toUpperCase();
}

function resolveCurrency(...candidates: unknown[]): string {
  for (const candidate of candidates) {
    const code = normalizeCurrency(candidate);
    if (code) return code;
  }
  return "";
}

/** 草稿级币种：优先结构化字段，缺失时按价格文本里的符号兜底。 */
function draftCurrency(raw: Record<string, any>): string {
  const captured = raw.captured_fields && typeof raw.captured_fields === "object" ? raw.captured_fields : {};
  const explicit = resolveCurrency(
    raw.currency,
    raw.price_currency,
    raw.source_currency,
    captured.currency,
    captured.price_currency,
  );
  if (explicit) return explicit;
  const priceText = `${raw.price ?? ""} ${raw.declared_price ?? ""}`;
  if (priceText.includes("$")) return "USD";
  return "CNY";
}

function formatMoney(value: number | null, currency = "CNY"): string {
  if (value === null || !Number.isFinite(value)) return "—";
  const code = normalizeCurrency(currency) || "CNY";
  const symbol = CURRENCY_SYMBOLS[code] || `${code} `;
  return `${symbol}${value.toFixed(2)}`;
}

function formatDate(value: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function localDateKey(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function assetImageUrl(asset: PreviewImageAsset | undefined): string {
  if (!asset) return "";
  return asset.preview_url || asset.public_url || "";
}

/** 处理后图片分组：主图 / 轮播图 / 详情图 / 素材库 / 来源图，均取自预检同款资产投影。 */
function processedImageSections(item: PreviewItem): { label: string; urls: string[] }[] {
  const assetById = new Map<string, PreviewImageAsset>();
  for (const asset of item.assets || []) assetById.set(asset.id, asset);
  const resolve = (ids?: string[]) =>
    (ids || []).map((id) => assetImageUrl(assetById.get(id))).filter(Boolean);
  const manifest = item.image_manifest;
  const main = assetImageUrl(assetById.get(manifest?.main_asset_id || "")) || item.main_image || "";
  return [
    { label: "处理后主图", urls: main ? [main] : [] },
    { label: "轮播图", urls: resolve(manifest?.carousel_asset_ids) },
    { label: "详情图", urls: resolve(manifest?.detail_asset_ids) },
    { label: "素材库", urls: resolve(manifest?.library_asset_ids) },
    { label: "来源图", urls: item.source_image_urls || [] },
  ].filter((section) => section.urls.length > 0);
}

function coreFieldEntries(item: PreviewItem): { label: string; value: string }[] {
  const core = item.core_fields || {};
  const entries: { label: string; value: unknown }[] = [
    { label: "SKU货号", value: core.sku },
    { label: "物流包裹长(cm)", value: core.length_cm },
    { label: "物流包裹宽(cm)", value: core.width_cm },
    { label: "物流包裹高(cm)", value: core.height_cm },
    { label: "重量(g)", value: core.weight_g },
  ];
  return entries.map((entry) => ({
    label: entry.label,
    value: entry.value === null || entry.value === undefined || entry.value === "" ? "—" : String(entry.value),
  }));
}

function ProcessedDetailContent({ item, onPreview }: { item: PreviewItem; onPreview: (url: string) => void }) {
  const sections = processedImageSections(item);
  return (
    <div className="psc-detail-content">
      <section className="psc-detail-block">
        <h3>标题</h3>
        <p className="psc-detail-text">{item.title || "—"}</p>
      </section>
      <section className="psc-detail-block">
        <h3>产品描述</h3>
        <p className="psc-detail-text is-pre">{item.description || "—"}</p>
      </section>
      <section className="psc-detail-block">
        <h3>核心字段</h3>
        <div className="psc-detail-fields">
          {coreFieldEntries(item).map((field) => (
            <div key={field.label}>
              <span>{field.label}</span>
              <strong title={field.value}>{field.value}</strong>
            </div>
          ))}
        </div>
      </section>
      {sections.map((section) => (
        <section className="psc-detail-block" key={section.label}>
          <h3>{section.label}<em>{section.urls.length}</em></h3>
          <div className="psc-detail-images">
            {section.urls.map((url, index) => (
              <button key={`${url}-${index}`} type="button" onClick={() => onPreview(url)}>
                <img src={url} alt="" loading="lazy" referrerPolicy="no-referrer" />
              </button>
            ))}
          </div>
        </section>
      ))}
      {item.source_url && (
        <section className="psc-detail-block">
          <h3>处理前商品地址</h3>
          <a className="psc-detail-link" href={item.source_url} target="_blank" rel="noreferrer">{item.source_url}</a>
        </section>
      )}
    </div>
  );
}

function parseSkus(raw: Record<string, any>, fallbackImageUrl = "", fallbackCurrency = "CNY"): SkuRow[] {
  const records = Array.isArray(raw.source_variant_records) ? raw.source_variant_records : [];
  return records
    .filter((record): record is Record<string, any> => !!record && typeof record === "object")
    .map((record, index) => {
      const attributes = record.attributes && typeof record.attributes === "object" ? record.attributes : {};
      const label =
        Object.values(attributes)
          .map((value) => String(value ?? "").trim())
          .filter(Boolean)
          .join(" / ") ||
        String(record.spec_text || record.sku_id || `SKU ${index + 1}`);
      const recordCurrency = normalizeCurrency(record.source_currency);
      const currency = recordCurrency || fallbackCurrency;
      const priceCny = Number(record.price_cny);
      const sourcePrice = Number(record.source_price);
      // 明确非人民币时按原币价展示（历史数据可能只把原币价写进 price_cny，故回退它）；
      // 人民币或缺失 SKU 级币种时按 price_cny 解释，币种沿用草稿级兜底。
      let price = Number.NaN;
      if (recordCurrency && recordCurrency !== "CNY") {
        price = Number.isFinite(sourcePrice) ? sourcePrice : priceCny;
      } else {
        price = Number.isFinite(priceCny) ? priceCny : sourcePrice;
      }
      const rawQuantity = record.quantity;
      const quantity =
        rawQuantity === null || rawQuantity === undefined || rawQuantity === "" ? NaN : Number(rawQuantity);
      return {
        key: String(record.sku_id || record.source_sku_id || index),
        skuId: String(record.sku_id || record.source_sku_id || ""),
        label,
        // 部分渠道（如整页兜底采用万邦清单）没有规格图，回落到商品主图。
        imageUrl: String(record.image_url || fallbackImageUrl || ""),
        price: Number.isFinite(price) && price > 0 ? price : null,
        currency,
        quantity: Number.isFinite(quantity) ? quantity : null,
      };
    });
}

// 成本列只统计人民币货源价；非人民币（如 Temu 目的地售价）不参与，避免混淆。
function rowCnyPrices(row: SourcingRow): number[] {
  return row.skus
    .filter((sku) => sku.currency === "CNY")
    .map((sku) => sku.price)
    .filter((value): value is number => value !== null);
}

function formatCost(row: SourcingRow): string {
  const prices = rowCnyPrices(row);
  if (prices.length) {
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    return min === max ? formatMoney(min, "CNY") : `${formatMoney(min, "CNY")} ~ ${formatMoney(max, "CNY")}`;
  }
  return formatMoney(row.cost, "CNY");
}

function rowCost(row: SourcingRow): number | null {
  const prices = rowCnyPrices(row);
  if (prices.length) return Math.min(...prices);
  return row.cost;
}

function toRow(draft: Draft): SourcingRow {
  const raw = draft.raw_payload || {};
  const sourceUrl = String(raw.source_url || raw.product_link || draft.source_ref || "");
  const imageUrl = draft.image_url || String(raw.main_image_url || "");
  const currency = draftCurrency(raw);
  return {
    id: draft.id,
    title: draft.title || draft.product_name || `草稿 #${draft.id}`,
    imageUrl,
    sourceUrl,
    platform: String(raw.source_platform || raw.platform || ""),
    shopName: String(raw.shop_name || ""),
    channel: String(raw.collection_channel || ""),
    batchId: draft.selection_run_id || "",
    batchDisplayName: draft.batch_display_name || "",
    cost: draft.cost,
    declaredPrice: draft.declared_price,
    currency,
    status: draft.status,
    createdAt: draft.created_at,
    skus: parseSkus(raw, imageUrl, currency),
  };
}

export function ProductSourcingCostPage({ isActive = true, onOpenProfitActivity }: Props) {
  const [rows, setRows] = useState<SourcingRow[]>([]);
  const [batches, setBatches] = useState<DraftCollectionBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("draft");
  const [platform, setPlatform] = useState("");
  const [channel, setChannel] = useState("");
  const [batchId, setBatchId] = useState("");
  const [keyword, setKeyword] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [onlyMissingCost, setOnlyMissingCost] = useState(false);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());
  const [detailDraftId, setDetailDraftId] = useState<number | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [detail, setDetail] = useState<DraftProcessedPreview | null>(null);
  const [lightboxUrl, setLightboxUrl] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const load = useCallback(async (nextStatus: string) => {
    setLoading(true);
    setError("");
    const ctx = api();
    try {
      const [draftResp, batchResp] = await Promise.all([
        ppRequest<DraftListResponse>(
          ctx,
          `${API_BASE}/drafts?view=summary&limit=500&status=${encodeURIComponent(nextStatus)}`,
        ),
        ppRequest<{ batches: DraftCollectionBatch[] }>(ctx, `${API_BASE}/draft-batches?limit=200`).catch(
          () => ({ batches: [] as DraftCollectionBatch[] }),
        ),
      ]);
      setRows((draftResp.drafts || []).map(toRow));
      setBatches(batchResp.batches || []);
    } catch (cause) {
      setRows([]);
      setBatches([]);
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isActive) return;
    void load(status);
  }, [isActive, status, load]);

  useEffect(() => {
    setPlatform("");
    setChannel("");
    setBatchId("");
  }, [status]);

  const batchLabels = useMemo(() => {
    const map = new Map<string, string>();
    for (const batch of batches) {
      if (batch.batch_id) {
        map.set(batch.batch_id, batch.channel_name || channelLabel(batch.collection_channel));
      }
    }
    return map;
  }, [batches]);

  const platformOptions = useMemo(
    () => Array.from(new Set(rows.map((row) => row.platform).filter(Boolean))).sort(),
    [rows],
  );
  const channelOptions = useMemo(
    () => Array.from(new Set(rows.map((row) => row.channel).filter(Boolean))).sort(),
    [rows],
  );
  const batchOptions = useMemo(() => {
    const seen = new Map<string, string>();
    for (const row of rows) {
      if (row.batchId && !seen.has(row.batchId)) {
        seen.set(row.batchId, batchLabels.get(row.batchId) || row.batchDisplayName || row.batchId);
      }
    }
    const options = Array.from(seen.entries());
    // 当前视图里存在未归批的草稿时，才补上「未分组」这一项，避免空选项。
    // 放在最前面，方便单品采集一眼就能筛到。
    if (rows.some((row) => !row.batchId)) {
      options.unshift([UNASSIGNED_BATCH_ID, UNASSIGNED_BATCH_LABEL]);
    }
    return options;
  }, [rows, batchLabels]);

  const filteredRows = useMemo(() => {
    const term = keyword.trim().toLowerCase();
    return rows.filter((row) => {
      if (platform && row.platform !== platform) return false;
      if (channel && row.channel !== channel) return false;
      if (batchId === UNASSIGNED_BATCH_ID) {
        if (row.batchId) return false;
      } else if (batchId && row.batchId !== batchId) {
        return false;
      }
      if (onlyMissingCost && rowCost(row) !== null) return false;
      if (dateFrom || dateTo) {
        const day = localDateKey(row.createdAt);
        if (!day) return false;
        if (dateFrom && day < dateFrom) return false;
        if (dateTo && day > dateTo) return false;
      }
      if (term) {
        const haystack = `${row.title} ${row.shopName} ${row.sourceUrl}`.toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      return true;
    });
  }, [rows, platform, channel, batchId, keyword, dateFrom, dateTo, onlyMissingCost]);

  const stats = useMemo(() => {
    const withCost = filteredRows.filter((row) => rowCost(row) !== null);
    return {
      count: filteredRows.length,
      withCost: withCost.length,
      missingCost: filteredRows.length - withCost.length,
    };
  }, [filteredRows]);

  const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
  const pageRows = useMemo(
    () => filteredRows.slice((page - 1) * pageSize, page * pageSize),
    [filteredRows, page, pageSize],
  );

  // 筛选条件或每页条数变化时回到第一页，避免停留在越界页码上。
  useEffect(() => {
    setPage(1);
  }, [status, platform, channel, batchId, keyword, dateFrom, dateTo, onlyMissingCost, pageSize]);

  useEffect(() => {
    setPage((current) => (current > totalPages ? totalPages : current));
  }, [totalPages]);

  const copyLink = async (row: SourcingRow) => {
    if (!row.sourceUrl) return;
    try {
      await navigator.clipboard.writeText(row.sourceUrl);
      setCopiedId(row.id);
      window.setTimeout(() => setCopiedId((current) => (current === row.id ? null : current)), 1500);
    } catch {
      setError("复制链接失败，请手动复制");
    }
  };

  const openProfitDetail = (row: SourcingRow, sku: SkuRow) => {
    if (!onOpenProfitActivity) return;
    // 利润核算以人民币成本为准：非人民币的 SKU 价格不能当作成本价传入。
    const cnyPrice = sku.currency === "CNY" ? sku.price : null;
    const price = cnyPrice ?? rowCost(row);
    onOpenProfitActivity({
      skc: sku.skuId || String(row.id),
      store_name: row.shopName || "",
      cost_price: price !== null ? price.toFixed(2) : "",
      source_url: row.sourceUrl || "",
      source_image_url: sku.imageUrl || row.imageUrl || "",
      note: row.title || "",
    });
  };

  const openProcessedDetail = async (row: SourcingRow) => {
    setDetailDraftId(row.id);
    setDetail(null);
    setDetailError("");
    setLightboxUrl("");
    setDetailLoading(true);
    try {
      const resp = await ppRequest<DraftProcessedPreview>(
        api(),
        `${API_BASE}/drafts/${row.id}/processed-preview`,
      );
      setDetail(resp);
    } catch (cause) {
      setDetailError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDetailLoading(false);
    }
  };

  const closeProcessedDetail = () => {
    setDetailDraftId(null);
    setDetail(null);
    setDetailError("");
    setLightboxUrl("");
  };

  const deleteRow = async (row: SourcingRow) => {
    if (!window.confirm(`确认删除「${row.title}」？该记录会从数据库彻底删除，不可恢复。`)) return;
    setDeletingId(row.id);
    setError("");
    try {
      await ppRequest(api(), `${API_BASE}/drafts/${row.id}/purge`, { method: "DELETE" });
      setRows((current) => current.filter((item) => item.id !== row.id));
      setExpandedIds((current) => {
        if (!current.has(row.id)) return current;
        const next = new Set(current);
        next.delete(row.id);
        return next;
      });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setDeletingId(null);
    }
  };

  const resetFilters = () => {
    setPlatform("");
    setChannel("");
    setBatchId("");
    setKeyword("");
    setDateFrom("");
    setDateTo("");
    setOnlyMissingCost(false);
  };

  const toggleExpanded = (id: number) => {
    setExpandedIds((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  return (
    <section className="verify-page">
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <h1>商品货源及成本展示</h1>
          <p>汇总各采集渠道（每日选品 / 整店采集 / 插件采集）的货源链接、店铺与成本价格，便于核对与筛选。</p>
        </div>
        <div className="psc-header-aside">
          <div className="verify-command-stats">
            <span><i className="iconfont icon-appstore" aria-hidden="true" /><strong>{stats.count}</strong><em>商品条数</em></span>
            <span><i className="iconfont icon-check-circle" aria-hidden="true" /><strong>{stats.withCost}</strong><em>含成本</em></span>
            <span className="is-warn"><i className="iconfont icon-warning-circle" aria-hidden="true" /><strong>{stats.missingCost}</strong><em>缺成本</em></span>
          </div>
          <button type="button" className="psc-refresh" onClick={() => void load(status)} disabled={loading}>
            {loading ? <><i className="app-spinner is-sm" aria-hidden="true" />读取中…</> : "刷新"}
          </button>
        </div>
      </header>

      <div className="psc-filters">
        <label>
          <span>状态</span>
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            {STATUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </label>
        <label>
          <span>平台</span>
          <select value={platform} onChange={(event) => setPlatform(event.target.value)}>
            <option value="">全部平台</option>
            {platformOptions.map((value) => <option key={value} value={value}>{platformLabel(value)}</option>)}
          </select>
        </label>
        <label>
          <span>采集渠道</span>
          <select value={channel} onChange={(event) => setChannel(event.target.value)}>
            <option value="">全部渠道</option>
            {channelOptions.map((value) => <option key={value} value={value}>{channelLabel(value)}</option>)}
          </select>
        </label>
        <label>
          <span>采集批次</span>
          <select value={batchId} onChange={(event) => setBatchId(event.target.value)}>
            <option value="">全部批次</option>
            {batchOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </label>
        <label className="psc-filter-date">
          <span>采集时间</span>
          <div className="psc-date-range">
            <input
              type="date"
              value={dateFrom}
              max={dateTo || undefined}
              onChange={(event) => setDateFrom(event.target.value)}
            />
            <em>~</em>
            <input
              type="date"
              value={dateTo}
              min={dateFrom || undefined}
              onChange={(event) => setDateTo(event.target.value)}
            />
          </div>
        </label>
        <label className="psc-filter-search">
          <span>关键词</span>
          <input
            type="search"
            value={keyword}
            placeholder="标题 / 店铺 / 链接"
            onChange={(event) => setKeyword(event.target.value)}
          />
        </label>
        <label className="psc-filter-toggle">
          <input type="checkbox" checked={onlyMissingCost} onChange={(event) => setOnlyMissingCost(event.target.checked)} />
          <span>只看缺失成本</span>
        </label>
        <button type="button" className="psc-filter-reset" onClick={resetFilters}>重置</button>
      </div>

      {error && <p className="psc-message is-error" role="status">{error}</p>}

      <div className="psc-table-wrap">
        <table className="psc-table">
          <thead>
            <tr>
              <th>商品</th>
              <th>平台</th>
              <th>店铺</th>
              <th>采集渠道</th>
              <th>采集批次</th>
              <th>成本</th>
              <th>申报价</th>
              <th>采集时间</th>
              <th>处理后</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {pageRows.map((row) => {
              const expanded = expandedIds.has(row.id);
              return (
                <Fragment key={row.id}>
                  <tr className={expanded ? "psc-row is-expanded" : "psc-row"}>
                    <td className="psc-cell-product">
                      {row.imageUrl
                        ? <img src={row.imageUrl} alt="" loading="lazy" referrerPolicy="no-referrer" />
                        : <span className="psc-thumb-placeholder" aria-hidden="true" />}
                      <div>
                        <strong title={row.title}>{row.title}</strong>
                        <small>
                          {row.sourceUrl
                            ? <a href={row.sourceUrl} target="_blank" rel="noreferrer">查看货源</a>
                            : <em>无货源链接</em>}
                          {row.sourceUrl && (
                            <button type="button" onClick={() => void copyLink(row)}>
                              {copiedId === row.id ? "已复制" : "复制链接"}
                            </button>
                          )}
                        </small>
                      </div>
                    </td>
                    <td>{platformLabel(row.platform)}</td>
                    <td>{row.shopName || "—"}</td>
                    <td>{channelLabel(row.channel)}</td>
                    <td title={row.batchId || undefined}>{row.batchId ? (batchLabels.get(row.batchId) || row.batchDisplayName || row.batchId) : "未分组"}</td>
                    <td className={rowCost(row) === null ? "psc-cost-missing" : "psc-cost"}>
                      <span className="psc-cost-main">{formatCost(row)}</span>
                      {row.skus.length > 1 && (
                        <button
                          type="button"
                          className="psc-sku-toggle"
                          aria-expanded={expanded}
                          onClick={() => toggleExpanded(row.id)}
                        >
                          {expanded ? "收起" : `${row.skus.length} 个 SKU`}
                        </button>
                      )}
                    </td>
                    <td>{formatMoney(row.declaredPrice, row.currency)}</td>
                    <td>{formatDate(row.createdAt)}</td>
                    <td>
                      <button
                        type="button"
                        className="psc-processed-btn"
                        onClick={() => void openProcessedDetail(row)}
                      >
                        查看处理后详情
                      </button>
                    </td>
                    <td>
                      <button
                        type="button"
                        className="psc-delete-btn"
                        onClick={() => void deleteRow(row)}
                        disabled={deletingId === row.id}
                      >
                        {deletingId === row.id ? <><i className="app-spinner is-sm" aria-hidden="true" />删除中…</> : "删除"}
                      </button>
                    </td>
                  </tr>
                  {expanded && (
                    <tr className="psc-sku-detail-row">
                      <td colSpan={10}>
                        <table className="psc-sku-table">
                          <thead>
                            <tr>
                              <th>图片</th>
                              <th>规格</th>
                              <th>价格</th>
                              <th>库存</th>
                              {onOpenProfitActivity && <th>操作</th>}
                            </tr>
                          </thead>
                          <tbody>
                            {row.skus.map((sku) => (
                              <tr key={sku.key}>
                                <td className="psc-sku-cell-image">
                                  {sku.imageUrl
                                    ? <img src={sku.imageUrl} alt="" loading="lazy" referrerPolicy="no-referrer" />
                                    : <span className="psc-sku-thumb-placeholder" aria-hidden="true" />}
                                </td>
                                <td>{sku.label || "—"}</td>
                                <td className="psc-sku-price">{formatMoney(sku.price, sku.currency)}</td>
                                <td>{sku.quantity === null ? "—" : sku.quantity}</td>
                                {onOpenProfitActivity && (
                                  <td>
                                    <button
                                      type="button"
                                      className="psc-sku-profit"
                                      onClick={() => openProfitDetail(row, sku)}
                                    >
                                      查看利润明细
                                    </button>
                                  </td>
                                )}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
        {!loading && filteredRows.length === 0 && (
          <p className="psc-empty">{rows.length === 0 ? "暂无货源数据，先去采集商品吧。" : "没有符合筛选条件的商品。"}</p>
        )}
        {loading && (
          <div className="app-loading-block">
            <i className="app-spinner" aria-hidden="true" />
            正在读取货源数据…
          </div>
        )}
      </div>

      <footer className="psc-pagination">
        <label className="psc-page-size">
          <i className="iconfont icon-appstore" aria-hidden="true" />
          <select
            value={pageSize}
            title="每页条数"
            onChange={(event) => setPageSize(Number(event.target.value))}
          >
            {PAGE_SIZE_OPTIONS.map((size) => (
              <option key={size} value={size}>每页 {size} 条</option>
            ))}
          </select>
        </label>
        <span>共 {filteredRows.length} 条 · 第 {page} / {totalPages} 页</span>
        <button type="button" onClick={() => setPage((value) => Math.max(1, value - 1))} disabled={page <= 1 || loading}>
          上一页
        </button>
        <button type="button" onClick={() => setPage((value) => Math.min(totalPages, value + 1))} disabled={page >= totalPages || loading}>
          下一页
        </button>
      </footer>

      {detailDraftId !== null && createPortal(
        <div className="psc-detail-overlay" role="dialog" aria-modal="true" aria-label="处理后详情" onClick={closeProcessedDetail}>
          <div className="psc-detail-panel" onClick={(event) => event.stopPropagation()}>
            <header className="psc-detail-head">
              <div>
                <span>AI 处理后详情</span>
                <h2>{detail?.item?.skc || `草稿 #${detailDraftId}`}</h2>
              </div>
              <button type="button" className="psc-detail-close" aria-label="关闭" onClick={closeProcessedDetail}>×</button>
            </header>
            <div className="psc-detail-body">
              {detailLoading && (
                <div className="app-loading-block">
                  <i className="app-spinner" aria-hidden="true" />
                  正在读取处理后数据…
                </div>
              )}
              {!detailLoading && detailError && <p className="psc-detail-hint is-error">读取失败：{detailError}</p>}
              {!detailLoading && !detailError && detail && !detail.processed && (
                <p className="psc-detail-hint">该商品尚未进行 AI 处理，暂无处理后的标题与图片。</p>
              )}
              {!detailLoading && !detailError && detail?.processed && detail.item && detail.item.status !== "completed" && (
                <p className="psc-detail-note is-warn">
                  该商品本次 AI 处理未成功{detail.item.reason ? `（${detail.item.reason}）` : ""}，暂无处理后的标题与图片。
                </p>
              )}
              {!detailLoading && !detailError && detail?.processed && detail.matched_by === "title" && detail.item?.status === "completed" && (
                <p className="psc-detail-note">
                  本条草稿未直接处理，以下为同名已处理草稿 #{detail.matched_draft_id} 的 AI 处理结果。
                </p>
              )}
              {!detailLoading && !detailError && detail?.processed && detail.item?.status === "completed" && (
                <ProcessedDetailContent item={detail.item} onPreview={setLightboxUrl} />
              )}
            </div>
          </div>
        </div>,
        document.body,
      )}

      {lightboxUrl && createPortal(
        <div className="psc-detail-lightbox" role="dialog" aria-modal="true" aria-label="图片预览" onClick={() => setLightboxUrl("")}>
          <img src={lightboxUrl} alt="预览大图" referrerPolicy="no-referrer" onClick={(event) => event.stopPropagation()} />
          <button type="button" className="psc-detail-lightbox-close" aria-label="关闭" onClick={() => setLightboxUrl("")}>×</button>
        </div>,
        document.body,
      )}
    </section>
  );
}
