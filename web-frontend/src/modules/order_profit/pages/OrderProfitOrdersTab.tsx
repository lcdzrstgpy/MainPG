import { useEffect, useMemo, useState } from "react";

import { showToast } from "../../../shared/components/toastStore";
import { orderProfitApi } from "../api/orderProfitApi";
import { MonthMultiSelect } from "../components/ChartControls";
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
}: {
  isActive: boolean;
  refreshToken: number;
  shop?: string;
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
  const [busy, setBusy] = useState(false);
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
      if (applied.salesZero && Number(order.sales_payback) !== 0) return false;
      if (applied.profitStatus === "profit" && Number(order.profit) < 0) return false;
      if (applied.profitStatus === "loss" && Number(order.profit) >= 0) return false;
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
  const cards = useMemo(    () => [
      { label: "订单数", value: numberText(summary?.order_count), tone: "is-info" },
      { label: "销售回款", value: money(summary?.sales_payback), tone: "is-positive" },
      { label: "已结算总额", value: money(summary?.settled_total), tone: "is-positive" },
      { label: "销售冲回", value: money(-(summary?.sales_reversal || 0)), tone: "is-negative" },
      { label: "采购成本", value: money(summary?.purchase_cost), tone: "" },
      { label: "头程运费", value: money(summary?.head_freight), tone: "" },
      { label: "尾程面单费用", value: money(-(summary?.tail_freight || 0)), tone: "" },
      { label: "平台罚款", value: money(-(summary?.penalty || 0)), tone: "is-negative" },
      { label: "广告费用", value: money(-(summary?.ad_cost || 0)), tone: "is-negative" },
      {
        label: "总利润",
        value: money(summary?.profit),
        tone: (summary?.profit || 0) >= 0 ? "is-positive" : "is-negative",
      },
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
      "店铺", "PO单号", "子订单数", "国家", "订单状态", "订单时间", "SKU数", "件数",
      "GMV", "销售回款", "销售冲回", "运费回款", "运费冲回", "采购成本", "头程", "尾程", "总利润",
    ];
    const lines = visibleOrders.map((row) => [
      row.shop_name || row.mall_id, row.parent_order_sn, row.sub_order_count, row.site,
      row.order_status, row.order_time, row.sku_count, row.item_count,
      row.gmv_total, row.sales_payback, row.sales_reversal, row.freight_payback,
      row.freight_reversal, row.purchase_cost, row.head_freight, row.tail_freight, row.profit,
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
            <button type="button" className="op-btn" disabled={busy} onClick={() => void recalculate()}>
              {busy ? "计算中…" : "一键计算利润"}
            </button>
            <button type="button" className="op-btn" disabled={!visibleOrders.length} onClick={exportCsv}>
              导出
            </button>
            <button type="button" className="op-btn" onClick={() => setReloadToken((value) => value + 1)}>
              刷新
            </button>
          </div>
        </div>
        <div className="op-filter-row op-filter-row-secondary">
          <label className="op-field op-field-wide">
            <span>订单号（可批量，换行/空格/逗号分隔）</span>
            <input
              type="text"
              value={draft.orderNumbers}
              placeholder="PO-... 211-..."
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

      {loading && !data ? (
        <div className="op-stats-grid">
          {Array.from({ length: 10 }).map((_, index) => (
            <div className="op-skeleton op-skeleton-stat" key={index} />
          ))}
        </div>
      ) : (
        <div className="op-stats-grid">
          {cards.map((card) => (
            <div className={`op-stat ${card.tone === "is-negative" ? "is-loss" : ""}`} key={card.label}>
              <span>{card.label}</span>
              <strong className={card.tone}>{card.value}</strong>
            </div>
          ))}
        </div>
      )}

      {error ? <div className="op-alert is-error">{error}</div> : null}

      <div className="op-card op-table-card">
        <div className="op-table-head">
          <strong>订单明细</strong>
          <span>
            {loading ? "加载中…" : `共 ${data?.total ?? 0} 单${visibleOrders.length !== orders.length ? ` · 筛选后 ${visibleOrders.length}` : ""}`}
            {summary?.missing_declared ? ` · 申报价待补 ${summary.missing_declared} 行` : ""}
            {summary?.missing_gmv ? ` · GMV待补 ${summary.missing_gmv} 行` : ""}
            {summary?.missing_purchase_sku ? ` · 采购成本待补 ${summary.missing_purchase_sku} SKU` : ""}
            {summary?.missing_weight_sku ? ` · 重量待补 ${summary.missing_weight_sku} SKU` : ""}
          </span>
          <small>点行首箭头展开子订单；GMV/申报价来自订单详情页采集</small>
        </div>
        <div className={`op-table-wrap ${loading ? "is-loading" : ""}`}>
          <table className="op-table op-table-orders">
            <thead>
              <tr>
                <th className="op-col-exp" />
                <th>店铺</th>
                <th>PO-单号</th>
                <th>站点</th>
                <th>订单状态</th>
                <th><button type="button" className={`op-th-sort ${sortKey === "order_time" ? "is-active" : ""}`} onClick={() => toggleSort("order_time")}>订单时间<i>{sortArrow("order_time")}</i></button></th>
                <th><button type="button" className={`op-th-sort ${sortKey === "item_count" ? "is-active" : ""}`} onClick={() => toggleSort("item_count")}>SKU/件数<i>{sortArrow("item_count")}</i></button></th>
                <th className="op-num"><button type="button" className={`op-th-sort ${sortKey === "gmv_total" ? "is-active" : ""}`} onClick={() => toggleSort("gmv_total")}>GMV<i>{sortArrow("gmv_total")}</i></button></th>
                <th className="op-num"><button type="button" className={`op-th-sort ${sortKey === "sales_payback" ? "is-active" : ""}`} onClick={() => toggleSort("sales_payback")}>销售回款<i>{sortArrow("sales_payback")}</i></button></th>
                <th className="op-num">销售冲回</th>
                <th className="op-num">运费回款</th>
                <th className="op-num">运费冲回</th>
                <th className="op-num">采购成本</th>
                <th className="op-num">头程</th>
                <th className="op-num">尾程</th>
                <th className="op-num"><button type="button" className={`op-th-sort ${sortKey === "profit" ? "is-active" : ""}`} onClick={() => toggleSort("profit")}>总利润<i>{sortArrow("profit")}</i></button></th>
              </tr>
            </thead>
            <tbody>
              {!visibleOrders.length && !loading ? (
                <tr>
                  <td colSpan={16}>
                    <div className="op-empty">
                      <strong>{orders.length ? "当前筛选没有订单" : "还没有订单数据"}</strong>
                      <span>
                        {orders.length
                          ? "调整订单号、盈利状态、国家或罚款筛选后再试。"
                          : "去「数据采集」页签，先在 Temu 订单列表页采集，再打开订单详情页补金额。"}
                      </span>
                    </div>
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
      </div>
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
        <td>{row.site || "—"}</td>
        <td>
          <span className="op-badge">{row.order_status || "—"}</span>
        </td>
        <td className="op-muted">{row.order_time || "—"}</td>
        <td>{row.sku_count} / {row.item_count}</td>
        <td className="op-num">{money(row.gmv_total)}</td>
        <td className="op-num">{money(row.sales_payback)}</td>
        <td className={`op-num is-negative`}>{money(-(row.sales_reversal || 0))}</td>
        <td className="op-num">{money(row.freight_payback)}</td>
        <td className={`op-num is-negative`}>{money(-(row.freight_reversal || 0))}</td>
        <td className="op-num">
          <EditableCost row={row} shop={shop} onSaved={onCostSaved} />
        </td>
        <td className="op-num">{money(row.head_freight)}</td>
        <td className="op-num">{money(-(row.tail_freight || 0))}</td>
        <td className={`op-num ${amountTone(row.profit)}`}>{money(row.profit)}</td>
      </tr>
      {open ? (
        <tr className="op-detail-row">
          <td colSpan={16}>
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
                    <th className="op-num">GMV</th>
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
                      <td className="op-num">{money(item.gmv_cny)}</td>
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
