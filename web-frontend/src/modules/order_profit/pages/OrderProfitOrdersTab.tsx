import { useEffect, useMemo, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { orderProfitApi } from "../api/orderProfitApi";
import { MonthMultiSelect } from "../components/ChartControls";
import { ShopDataGlyph, type ShopDataGlyphName } from "../components/ShopDataGlyph";
import { EmptyBlock, Metric, MetricBand, SectionCard } from "../components/ShopDataPrimitives";
import { amountTone, money, numberText } from "../format";
import type { OrderProfitOrdersResponse, OrderProfitStore } from "../types";

type OrderStatusPreset = "all" | "pending" | "shipped" | "signed" | "cancelled";

const STATUS_TABS: Array<{ value: OrderStatusPreset; label: string }> = [
  { value: "all", label: "全部" },
  { value: "pending", label: "待发货" },
  { value: "shipped", label: "已发货" },
  { value: "signed", label: "已签收" },
  { value: "cancelled", label: "已取消" },
];

const STATUS_PATTERN: Record<Exclude<OrderStatusPreset, "all">, RegExp> = {
  pending: /待发货|未接收|待揽收|待处理/,
  shipped: /已发货|运输|派送|待签收|已揽收/,
  signed: /已签收|已完成|已送达/,
  cancelled: /已取消|取消/,
};

// Temu 订单接口有时只返回数字状态码：仅把已确认的码翻译成中文，其余保留原始码并加说明。
const STATUS_CODE_TEXT: Record<string, string> = { "2": "待发货", "5": "已签收" };

function statusLabel(value: string | null | undefined): string {
  const text = String(value || "").trim();
  if (!text) return "—";
  if (!/^\d+$/.test(text)) return text;
  return STATUS_CODE_TEXT[text] || `状态码 ${text}`;
}

function statusHint(value: string | null | undefined): string {
  const text = String(value || "").trim();
  if (!text || !/^\d+$/.test(text) || STATUS_CODE_TEXT[text]) return "";
  return "Temu 接口返回的原始状态码，暂未映射为中文";
}

function defaultFilters() {
  return {
    months: [] as string[],
    store: "",
    keyword: "",
    orderNumbers: "",
    salesZero: false,
    profitStatus: "all",
    country: "all",
    penalty: "all",
  };
}

export function OrderProfitOrdersTab({
  isActive,
  refreshToken,
  shop = "",
  onOpenCapture,
}: {
  isActive: boolean;
  refreshToken: number;
  shop?: string;
  onOpenCapture?: () => void;
}) {
  const [status, setStatus] = useState<OrderStatusPreset>("all");
  const [draft, setDraft] = useState(defaultFilters);
  const [applied, setApplied] = useState(defaultFilters);
  const [stores, setStores] = useState<OrderProfitStore[]>([]);
  const [data, setData] = useState<OrderProfitOrdersResponse | null>(null);
  const [availableMonths, setAvailableMonths] = useState<string[]>([]);

  // 切换店铺时清空已选月份，交给下面的月份接口重新对齐到该店最近有数据的月份。
  useEffect(() => {
    setApplied((current) => (current.months.length ? { ...current, months: [] } : current));
    setDraft((current) => (current.months.length ? { ...current, months: [] } : current));
  }, [shop]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [reloadToken, setReloadToken] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortKey, setSortKey] = useState<"" | "order_time" | "item_count" | "gmv_total" | "sales_payback" | "profit">("");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    orderProfitApi
      .listStores()
      .then((payload) => {
        if (!cancelled) setStores(payload.stores || []);
      })
      .catch(() => {
        if (!cancelled) setStores([]);
      });
    return () => {
      cancelled = true;
    };
  }, [isActive, refreshToken]);

  useEffect(() => {
    if (!isActive) return;
    // 月份由趋势接口回填后再查明细，避免首次进入用空月份拉全量。
    if (!applied.months.length) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    orderProfitApi
      .orders({
        month: applied.months.join(","),
        store: applied.store,
        keyword: applied.keyword,
        shop,
        limit: 2000,
      })
      .then((payload) => {
        if (!cancelled) setData(payload);
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [isActive, applied, refreshToken, reloadToken, shop]);

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    orderProfitApi
      .ordersMonths(shop)
      .then((payload) => {
        if (cancelled) return;
        const list = payload.months || [];
        setAvailableMonths(list);
        // 首次进入对齐到最近有数据的月份，避免空列表。
        if (list.length) {
          setApplied((current) => (current.months.length ? current : { ...current, months: [list[0]] }));
          setDraft((current) => (current.months.length ? current : { ...current, months: [list[0]] }));
        }
      })
      .catch(() => {
        if (!cancelled) setAvailableMonths([]);
      });
    return () => {
      cancelled = true;
    };
  }, [isActive, refreshToken, reloadToken, shop]);

  const summary = data?.summary;
  const orders = data?.orders || [];
  const countries = useMemo(
    () => Array.from(new Set(orders.map((order) => order.site).filter(Boolean))),
    [orders],
  );
  const visibleOrders = useMemo(() => {
    const numbers = applied.orderNumbers
      .split(/[\s,，;；]+/)
      .map((value) => value.trim())
      .filter(Boolean);
    const statusPattern = status === "all" ? null : STATUS_PATTERN[status];
    return orders.filter((order) => {
      if (statusPattern && !statusPattern.test(order.order_status || "")) return false;
      if (numbers.length) {
        const haystack = `${order.parent_order_sn} ${order.items.map((item) => item.sub_order_sn).join(" ")}`;
        if (!numbers.some((value) => haystack.includes(value))) return false;
      }
      // 缺失值（null）不属于「为 0」，避免把没采到当成 0 参与筛选。
      if (applied.salesZero && (order.sales_payback == null || Number(order.sales_payback) !== 0)) return false;
      if (applied.profitStatus === "profit" && (order.profit == null || order.profit < 0)) return false;
      if (applied.profitStatus === "loss" && (order.profit == null || order.profit >= 0)) return false;
      if (applied.country !== "all" && order.site !== applied.country) return false;
      if (applied.penalty === "has" && Number(order.penalty) <= 0) return false;
      if (applied.penalty === "none" && Number(order.penalty) > 0) return false;
      return true;
    });
  }, [orders, applied, status]);
  // 点表头排序：数字列按数值、订单时间按字符串；再点一次切换升降序。
  const sortedOrders = useMemo(() => {
    if (!sortKey) return visibleOrders;
    const factor = sortDir === "asc" ? 1 : -1;
    return [...visibleOrders].sort((left, right) => {
      if (sortKey === "order_time") {
        return String(left.order_time || "").localeCompare(String(right.order_time || "")) * factor;
      }
      return ((Number(left[sortKey]) || 0) - (Number(right[sortKey]) || 0)) * factor;
    });
  }, [visibleOrders, sortKey, sortDir]);
  const totalPages = Math.max(1, Math.ceil(sortedOrders.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pagedOrders = useMemo(
    () => sortedOrders.slice((currentPage - 1) * pageSize, currentPage * pageSize),
    [sortedOrders, currentPage, pageSize],
  );
  const toggleSort = (key: typeof sortKey) => {
    if (sortKey === key) {
      setSortDir((value) => (value === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setSortDir("desc");
  };
  const sortArrow = (key: typeof sortKey) =>
    sortKey === key ? (sortDir === "asc" ? "▲" : "▼") : "↕";
  // 筛选条件、排序或每页条数变化时回到第一页，避免停在空白页。
  useEffect(() => {
    setPage(1);
  }, [status, applied, pageSize, sortKey, sortDir]);
  // 先给结论：订单量、回款、利润；成本与费用拆到「成本明细」里，避免一屏十个数字。
  const primaryCards = useMemo(
    () => [
      { label: "订单数", value: numberText(summary?.order_count), tone: "is-info", hint: "当前月份与筛选条件下", glyph: "orders" as ShopDataGlyphName },
      { label: "销售回款", value: money(summary?.sales_payback), tone: "is-positive", hint: "Temu 已回款的销售额", glyph: "wallet" as ShopDataGlyphName },
      {
        label: "采集件数",
        value: numberText(orders.reduce((sum, order) => sum + Number(order.item_count || 0), 0)),
        tone: "is-info",
        hint: "当前月份与筛选条件下",
        glyph: "orders" as ShopDataGlyphName,
      },
    ],
    [summary, orders],
  );
  const secondaryCards = useMemo(
    () => [
      { label: "已结算总额", value: money(summary?.settled_total), tone: "is-positive" },
      {
        label: "销售冲回",
        value: money(summary?.sales_reversal == null ? null : -summary.sales_reversal),
        tone: "is-negative",
      },
      { label: "采购成本", value: money(summary?.purchase_cost), tone: "" },
      { label: "头程运费", value: money(summary?.head_freight), tone: "" },
      {
        label: "尾程面单费用",
        value: money(summary?.tail_freight == null ? null : -summary.tail_freight),
        tone: "",
      },
      { label: "平台罚款", value: money(-(summary?.penalty || 0)), tone: "is-negative" },
      { label: "广告费用", value: money(-(summary?.ad_cost || 0)), tone: "is-negative" },
    ],
    [summary],
  );

  const query = () => {
    if (!draft.months.length) {
      showToast("至少选择一个月", "error");
      return;
    }
    setApplied({ ...draft });
  };
  const reset = () => {
    const next = defaultFilters();
    setDraft(next);
    setApplied(next);
  };
  const recalculate = async () => {
    setBusy(true);
    try {
      const result = await orderProfitApi.recalculate();
      showToast(`已按当前台账成本/费率重算 ${result.updated} 行订单`, "success");
      setReloadToken((value) => value + 1);
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setBusy(false);
    }
  };
  const exportCsv = () => {
    const headers = [
      "店铺", "PO单号", "子订单数", "订单状态", "订单时间", "SKU数", "件数",
      "销售回款", "运费回款", "采购成本", "尾程", "总利润",
    ];
    const lines = visibleOrders.map((row) => [
      row.shop_name || row.mall_id, row.parent_order_sn, row.sub_order_count,
      row.order_status, row.order_time, row.sku_count, row.item_count,
      row.sales_payback, row.freight_payback,
      row.purchase_cost, row.tail_freight, row.profit,
    ]);
    const escape = (value: unknown) => `"${String(value ?? "").replace(/"/g, '""')}"`;
    const text = "\uFEFF" + [headers, ...lines].map((line) => line.map(escape).join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `订单利润_${applied.months.join("_") || "全部"}.csv`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const toggle = (parent: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(parent)) next.delete(parent);
      else next.add(parent);
      return next;
    });
  };

  return (
    <section className="op-view">
      <div className="op-view-toolbar">
        <div className="op-tabs">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              className={`op-tab ${status === tab.value ? "is-active" : ""}`}
              onClick={() => setStatus(tab.value)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <span className="op-view-toolbar-meta">
          {loading ? "加载中…" : `共 ${numberText(data?.total ?? 0)} 单${visibleOrders.length !== orders.length ? ` · 筛选后 ${visibleOrders.length}` : ""}`}
        </span>
      </div>

      <div className="op-card op-filter-card">
        <div className="op-filter-row">
          <div className="op-field">
            <span>订单时间（可多选）</span>
            <MonthMultiSelect
              months={availableMonths}
              value={draft.months}
              onChange={(next) => setDraft({ ...draft, months: next })}
            />
          </div>
          <label className="op-field op-field-grow">
            <span>店铺</span>
            <select value={draft.store} onChange={(event) => setDraft({ ...draft, store: event.target.value })}>
              <option value="">全部店铺</option>
              {stores.map((store) => (
                <option key={`${store.id}-${store.name}`} value={store.id || store.name}>
                  {store.name}
                </option>
              ))}
            </select>
          </label>
          <label className="op-field op-field-wide">
            <span>订单号 / SKU / 货号</span>
            <input
              type="text"
              value={draft.keyword}
              placeholder="可输入 PO 单号、子订单号、SKU 或货号"
              onChange={(event) => setDraft({ ...draft, keyword: event.target.value })}
              onKeyDown={(event) => {
                if (event.key === "Enter") query();
              }}
            />
          </label>
          <div className="op-filter-actions">
            <button type="button" className="op-btn op-btn-primary" onClick={query}>
              查询
            </button>
            <button type="button" className="op-btn" onClick={reset}>
              重置
            </button>
          </div>
        </div>

        <details className="op-disclosure op-filter-more">
          <summary>
            <ShopDataGlyph name="search" />
            <span>更多筛选</span>
            <small>盈利状态 · 国家 · 平台罚款 · 批量订单号</small>
          </summary>
          <div className="op-disclosure-body">
            <div className="op-filter-row op-filter-row-secondary">
              <label className="op-field op-field-wide">
                <span>订单号（可批量，换行/空格/逗号分隔）</span>
                <input
                  type="text"
                  value={draft.orderNumbers}
                  placeholder="多个 PO / 子订单号"
                  onChange={(event) => setDraft({ ...draft, orderNumbers: event.target.value })}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") query();
                  }}
                />
              </label>
              <label className="op-field">
                <span>盈利状态</span>
                <select value={draft.profitStatus} onChange={(event) => setDraft({ ...draft, profitStatus: event.target.value })}>
                  <option value="all">全部</option>
                  <option value="profit">仅盈利</option>
                  <option value="loss">仅亏损</option>
                </select>
              </label>
              <label className="op-field">
                <span>国家</span>
                <select value={draft.country} onChange={(event) => setDraft({ ...draft, country: event.target.value })}>
                  <option value="all">全部</option>
                  {countries.map((country) => (
                    <option key={country} value={country}>{country}</option>
                  ))}
                </select>
              </label>
              <label className="op-field">
                <span>平台罚款</span>
                <select value={draft.penalty} onChange={(event) => setDraft({ ...draft, penalty: event.target.value })}>
                  <option value="all">全部</option>
                  <option value="has">有罚款</option>
                  <option value="none">无罚款</option>
                </select>
              </label>
              <label className="op-check">
                <input
                  type="checkbox"
                  checked={draft.salesZero}
                  onChange={(event) => setDraft({ ...draft, salesZero: event.target.checked })}
                />
                <span>销售回款为 0</span>
              </label>
            </div>
          </div>
        </details>
      </div>

      {loading && !data ? (
        <MetricBand>
          {Array.from({ length: 4 }).map((_, index) => (
            <div className="op-skeleton op-skeleton-stat" key={index} />
          ))}
        </MetricBand>
      ) : (
        <MetricBand>
          {primaryCards.map((card) => (
            <Metric key={card.label} label={card.label} value={card.value} tone={card.tone} hint={card.hint} glyph={card.glyph} />
          ))}
        </MetricBand>
      )}

      <details className="op-disclosure">
        <summary>
          <ShopDataGlyph name="cost" />
          <span>成本与费用明细</span>
          <small>已结算 · 冲回 · 采购 · 头程 · 尾程 · 罚款 · 广告</small>
        </summary>
        <div className="op-disclosure-body">
          <div className="op-stats-grid op-secondary-grid">
            {secondaryCards.map((card) => (
              <div className={`op-stat ${card.tone === "is-negative" ? "is-loss" : ""}`} key={card.label}>
                <span>{card.label}</span>
                <strong className={card.tone}>{card.value}</strong>
              </div>
            ))}
          </div>
        </div>
      </details>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      <SectionCard
        className="op-section-table"
        title="采集数据信息"
        desc="筛选历史采集订单；展开可查看订单商品与财务明细。利润核算规则暂未启用。"
        icon="receipt"
        scene="orders"
        meta={
          <div className="op-table-meta">
            {summary?.missing_declared ? <span className="op-chip is-warn">申报价待补 {summary.missing_declared} 行</span> : null}
            {summary?.missing_gmv ? <span className="op-chip is-warn">GMV 待补 {summary.missing_gmv} 行</span> : null}
            {summary?.missing_purchase_sku ? <span className="op-chip is-warn">采购成本待补 {summary.missing_purchase_sku} SKU</span> : null}
            {summary?.missing_weight_sku ? <span className="op-chip is-warn">重量待补 {summary.missing_weight_sku} SKU</span> : null}
            {!summary?.missing_declared && !summary?.missing_gmv && !summary?.missing_purchase_sku && !summary?.missing_weight_sku ? (
              <span className="op-chip is-ok">金额与成本已齐</span>
            ) : null}
          </div>
        }
        action={
          <div className="op-section-actions">
            <button type="button" className="op-btn op-btn-sm" disabled={!visibleOrders.length} onClick={exportCsv}>
              导出
            </button>
            <button type="button" className="op-btn op-btn-sm" onClick={() => setReloadToken((value) => value + 1)}>
              刷新
            </button>
          </div>
        }
      >
        <div className={`op-table-wrap ${loading ? "is-loading" : ""}`}>
          <table className="op-table op-table-orders">
            <thead>
              <tr>
                <th className="op-col-exp" />
                <th>店铺</th>
                <th>PO-单号</th>
                <th>订单状态</th>
                <th><button type="button" className={`op-th-sort ${sortKey === "order_time" ? "is-active" : ""}`} onClick={() => toggleSort("order_time")}>订单时间<i>{sortArrow("order_time")}</i></button></th>
                <th><button type="button" className={`op-th-sort ${sortKey === "item_count" ? "is-active" : ""}`} onClick={() => toggleSort("item_count")}>SKU/件数<i>{sortArrow("item_count")}</i></button></th>
                <th className="op-num"><button type="button" className={`op-th-sort ${sortKey === "sales_payback" ? "is-active" : ""}`} onClick={() => toggleSort("sales_payback")}>销售回款<i>{sortArrow("sales_payback")}</i></button></th>
                <th className="op-num">运费回款</th>
                <th className="op-num">采购成本</th>
                <th className="op-num">尾程</th>
                <th className="op-num"><button type="button" className={`op-th-sort ${sortKey === "profit" ? "is-active" : ""}`} onClick={() => toggleSort("profit")}>总利润<i>{sortArrow("profit")}</i></button></th>
              </tr>
            </thead>
            <tbody>
              {!visibleOrders.length && !loading ? (
                <tr>
                  <td colSpan={11}>
                    <EmptyBlock
                      compact
                      art="orders"
                      title={orders.length ? "当前筛选没有订单" : "还没有订单数据"}
                      desc={
                        orders.length
                          ? "调整订单号、盈利状态、国家或罚款筛选后再试。"
                          : "去「数据采集」页签，先在 Temu 订单列表页采集，再打开订单详情页补金额。"
                      }
                      action={
                        !orders.length && onOpenCapture ? (
                          <button type="button" className="op-btn op-btn-primary op-btn-sm" onClick={onOpenCapture}>
                            去数据采集
                          </button>
                        ) : null
                      }
                    />
                  </td>
                </tr>
              ) : null}
              {pagedOrders.map((row) => {
                const open = expanded.has(row.parent_order_sn);
                return (
                  <FragmentRow
                    key={row.parent_order_sn}
                    row={row}
                    open={open}
                    shop={shop}
                    onToggle={() => toggle(row.parent_order_sn)}
                    onCostSaved={() => setReloadToken((value) => value + 1)}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
        <div className="op-pager">
          <span className="op-pager-info">
            共 {visibleOrders.length} 单 · 第 {currentPage}/{totalPages} 页
          </span>
          <label className="op-pager-size">
            <span>每页</span>
            <select
              value={pageSize}
              onChange={(event) => setPageSize(Number(event.target.value))}
            >
              <option value={20}>20</option>
              <option value={50}>50</option>
              <option value={100}>100</option>
            </select>
          </label>
          <div className="op-pager-actions">
            <button
              type="button"
              className="op-btn op-btn-sm"
              disabled={currentPage <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
            >
              上一页
            </button>
            <button
              type="button"
              className="op-btn op-btn-sm"
              disabled={currentPage >= totalPages}
              onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
            >
              下一页
            </button>
          </div>
        </div>
      </SectionCard>
    </section>
  );
}

function FragmentRow({
  row,
  open,
  shop,
  onToggle,
  onCostSaved,
}: {
  row: OrderProfitOrdersResponse["orders"][number];
  open: boolean;
  shop: string;
  onToggle: () => void;
  onCostSaved: () => void;
}) {
  return (
    <>
      <tr className="op-order-row">
        <td className="op-col-exp">
          <button
            type="button"
            className={`op-expander${open ? " is-open" : ""}`}
            onClick={onToggle}
            aria-expanded={open}
            aria-label={open ? "收起子订单" : "展开子订单"}
          >
            ▶
          </button>
        </td>
        <td>
          <div className="op-store">
            <span className="op-store-name">{row.shop_name || row.mall_id || "—"}</span>
            {row.mall_id && row.shop_name ? <small>{row.mall_id}</small> : null}
          </div>
        </td>
        <td className="op-mono">{row.parent_order_sn}</td>
        <td>
          <span className="op-badge" title={statusHint(row.order_status)}>{statusLabel(row.order_status)}</span>
        </td>
        <td className="op-muted">{row.order_time || "—"}</td>
        <td>{row.sku_count} / {row.item_count}</td>
        <td className="op-num">{money(row.sales_payback)}</td>
        <td className="op-num">{money(row.freight_payback)}</td>
        <td className="op-num">
          <EditableCost row={row} shop={shop} onSaved={onCostSaved} />
        </td>
        <td className="op-num">{money(row.tail_freight == null ? null : -row.tail_freight)}</td>
        <td className={`op-num ${amountTone(row.profit)}`}>{money(row.profit)}</td>
      </tr>
      {open ? (
        <tr className="op-detail-row">
          <td colSpan={11}>
            <div className="op-sub-table-wrap">
              <table className="op-table op-sub-table">
                <thead>
                  <tr>
                    <th>子订单号</th>
                    <th>SKC</th>
                    <th>SKU / 货号</th>
                    <th>商品</th>
                    <th className="op-num">件数</th>
                    <th className="op-num">申报价</th>
                    <th className="op-num">尾程(CNY)</th>
                    <th className="op-num">采购成本</th>
                    <th className="op-num">预估利润</th>
                    <th>运单号</th>
                  </tr>
                </thead>
                <tbody>
                  {row.items.map((item) => (
                    <tr key={item.id}>
                      <td className="op-mono">{item.sub_order_sn || "—"}</td>
                      <td className="op-mono">{item.skc_id || item.skc_code || "—"}</td>
                      <td>
                        <span className="op-mono">{item.sku_id || "—"}</span>
                        {item.ext_code ? <small className="op-block">{item.ext_code}</small> : null}
                      </td>
                      <td className="op-title" title={item.goods_name}>{item.goods_name || "—"}</td>
                      <td className="op-num">{item.quantity}</td>
                      <td className="op-num">{money(item.declared_price)}</td>
                      <td className="op-num">{money(item.tail_freight_cny)}</td>
                      <td className="op-num">
                        {item.purchase_cost_cny === null || item.purchase_cost_cny === undefined ? (
                          <span className="op-chip is-warn">待维护</span>
                        ) : (
                          money(item.purchase_cost_cny)
                        )}
                      </td>
                      <td className={`op-num ${amountTone(item.profit_estimate_cny)}`}>{money(item.profit_estimate_cny)}</td>
                      <td className="op-mono">{item.tracking_no || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}

/** 采购成本可手工填写：点数字进入编辑，回车保存，留空清除。 */
function EditableCost({
  row,
  shop,
  onSaved,
}: {
  row: OrderProfitOrdersResponse["orders"][number];
  shop: string;
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const current = Number(row.purchase_cost || 0);

  const commit = async () => {
    const text = value.trim();
    const next = text === "" ? null : Number(text);
    if (next !== null && (!Number.isFinite(next) || next < 0)) {
      showToast("采购成本请填 0 或正数；留空表示清除", "error");
      return;
    }
    setSaving(true);
    try {
      await orderProfitApi.updateOrderCost(row.parent_order_sn, next, shop);
      setEditing(false);
      onSaved();
    } catch (cause) {
      showToast(cause instanceof Error ? cause.message : String(cause), "error");
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <button
        type="button"
        className={`op-cost-cell${current > 0 ? "" : " is-empty"}`}
        title="点击修改这单的采购成本（按件数分摊到 SKU）"
        onClick={() => {
          setValue(current > 0 ? String(current) : "");
          setEditing(true);
        }}
      >
        {current > 0 ? money(current) : "待填写"}
      </button>
    );
  }
  return (
    <input
      className="op-cost-input"
      type="number"
      min={0}
      step="0.01"
      autoFocus
      disabled={saving}
      value={value}
      onChange={(event) => setValue(event.target.value)}
      onBlur={() => void commit()}
      onKeyDown={(event) => {
        if (event.key === "Enter") void commit();
        if (event.key === "Escape") setEditing(false);
      }}
    />
  );
}
