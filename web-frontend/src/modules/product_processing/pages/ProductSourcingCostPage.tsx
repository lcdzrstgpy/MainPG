import { Fragment, useCallback, useEffect, useMemo, useState } from "react";

import { ppRequest, type ApiContext } from "../api/client";
import { productProcessingApiContext } from "../api/context";
import type { DraftCollectionBatch } from "../api/productProcessingApi";
import type { Draft, DraftListResponse } from "../types";
import type { ProfitActivityPrefill } from "../../profit_activity/types/products";
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
  cost: number | null;
  declaredPrice: number | null;
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

function formatMoney(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return `¥${value.toFixed(2)}`;
}

function formatDate(value: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function parseSkus(raw: Record<string, any>, fallbackImageUrl = ""): SkuRow[] {
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
      const price = Number(record.price_cny);
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
        quantity: Number.isFinite(quantity) ? quantity : null,
      };
    });
}

function rowPrices(row: SourcingRow): number[] {
  return row.skus.map((sku) => sku.price).filter((value): value is number => value !== null);
}

function formatCost(row: SourcingRow): string {
  const prices = rowPrices(row);
  if (!prices.length) return formatMoney(row.cost);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  return min === max ? formatMoney(min) : `${formatMoney(min)} ~ ${formatMoney(max)}`;
}

function rowCost(row: SourcingRow): number | null {
  const prices = rowPrices(row);
  if (!prices.length) return row.cost;
  return Math.min(...prices);
}

function toRow(draft: Draft): SourcingRow {
  const raw = draft.raw_payload || {};
  const sourceUrl = String(raw.source_url || raw.product_link || draft.source_ref || "");
  const imageUrl = draft.image_url || String(raw.main_image_url || "");
  return {
    id: draft.id,
    title: draft.title || draft.product_name || `草稿 #${draft.id}`,
    imageUrl,
    sourceUrl,
    platform: String(raw.source_platform || raw.platform || ""),
    shopName: String(raw.shop_name || ""),
    channel: String(raw.collection_channel || ""),
    batchId: draft.selection_run_id || "",
    cost: draft.cost,
    declaredPrice: draft.declared_price,
    status: draft.status,
    createdAt: draft.created_at,
    skus: parseSkus(raw, imageUrl),
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
  const [onlyMissingCost, setOnlyMissingCost] = useState(false);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

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
        seen.set(row.batchId, batchLabels.get(row.batchId) || row.batchId);
      }
    }
    return Array.from(seen.entries());
  }, [rows, batchLabels]);

  const filteredRows = useMemo(() => {
    const term = keyword.trim().toLowerCase();
    return rows.filter((row) => {
      if (platform && row.platform !== platform) return false;
      if (channel && row.channel !== channel) return false;
      if (batchId && row.batchId !== batchId) return false;
      if (onlyMissingCost && rowCost(row) !== null) return false;
      if (term) {
        const haystack = `${row.title} ${row.shopName} ${row.sourceUrl}`.toLowerCase();
        if (!haystack.includes(term)) return false;
      }
      return true;
    });
  }, [rows, platform, channel, batchId, keyword, onlyMissingCost]);

  const stats = useMemo(() => {
    const withCost = filteredRows.filter((row) => rowCost(row) !== null);
    const totalCost = withCost.reduce((sum, row) => sum + (rowCost(row) || 0), 0);
    return {
      count: filteredRows.length,
      withCost: withCost.length,
      missingCost: filteredRows.length - withCost.length,
      totalCost,
      averageCost: withCost.length ? totalCost / withCost.length : 0,
    };
  }, [filteredRows]);

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
    const price = sku.price ?? rowCost(row);
    onOpenProfitActivity({
      skc: sku.skuId || String(row.id),
      store_name: row.shopName || "",
      cost_price: price !== null ? price.toFixed(2) : "",
      source_url: row.sourceUrl || "",
      source_image_url: sku.imageUrl || row.imageUrl || "",
      note: row.title || "",
    });
  };

  const resetFilters = () => {
    setPlatform("");
    setChannel("");
    setBatchId("");
    setKeyword("");
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
    <section className="psc-page">
      <header className="psc-header">
        <div>
          <span>PRODUCT SOURCING</span>
          <h1>商品货源及成本展示</h1>
          <p>汇总各采集渠道（每日选品 / 整店采集 / 插件采集）的货源链接、店铺与成本价格，便于核对与筛选。</p>
        </div>
        <button type="button" className="psc-refresh" onClick={() => void load(status)} disabled={loading}>
          {loading ? "读取中…" : "刷新"}
        </button>
      </header>

      <div className="psc-stats">
        <div className="psc-stat"><span>商品条数</span><strong>{stats.count}</strong></div>
        <div className="psc-stat"><span>含成本</span><strong>{stats.withCost}</strong></div>
        <div className="psc-stat is-warn"><span>缺成本</span><strong>{stats.missingCost}</strong></div>
        <div className="psc-stat"><span>成本合计</span><strong>{formatMoney(stats.totalCost)}</strong></div>
        <div className="psc-stat"><span>平均成本</span><strong>{formatMoney(stats.averageCost)}</strong></div>
      </div>

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
            </tr>
          </thead>
          <tbody>
            {filteredRows.map((row) => {
              const expanded = expandedIds.has(row.id);
              return (
                <Fragment key={row.id}>
                  <tr className={expanded ? "psc-row is-expanded" : "psc-row"}>
                    <td className="psc-cell-product">
                      {row.imageUrl
                        ? <img src={row.imageUrl} alt="" loading="lazy" />
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
                    <td title={row.batchId || undefined}>{row.batchId ? (batchLabels.get(row.batchId) || row.batchId) : "—"}</td>
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
                    <td>{formatMoney(row.declaredPrice)}</td>
                    <td>{formatDate(row.createdAt)}</td>
                  </tr>
                  {expanded && (
                    <tr className="psc-sku-detail-row">
                      <td colSpan={8}>
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
                                    ? <img src={sku.imageUrl} alt="" loading="lazy" />
                                    : <span className="psc-sku-thumb-placeholder" aria-hidden="true" />}
                                </td>
                                <td>{sku.label || "—"}</td>
                                <td className="psc-sku-price">{formatMoney(sku.price)}</td>
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
        {loading && <p className="psc-empty">正在读取货源数据…</p>}
      </div>
    </section>
  );
}
