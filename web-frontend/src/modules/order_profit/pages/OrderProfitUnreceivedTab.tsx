import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { amountTone, money, numberText } from "../format";
import type { OperationUnreceived, OrderProfitStore } from "../types";

export function OrderProfitUnreceivedTab({
  isActive,
  refreshToken,
}: {
  isActive: boolean;
  refreshToken: number;
}) {
  const [store, setStore] = useState("");
  const [keyword, setKeyword] = useState("");
  const [applied, setApplied] = useState({ store: "", keyword: "" });
  const [stores, setStores] = useState<OrderProfitStore[]>([]);
  const [data, setData] = useState<OperationUnreceived | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

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
    let cancelled = false;
    setLoading(true);
    setError("");
    orderProfitApi
      .unreceived(applied)
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
  }, [isActive, applied, refreshToken]);

  const summary = data?.summary;
  const orders = data?.orders || [];

  return (
    <section className="op-view">
      <div className="op-card op-filter-card">
        <div className="op-filter-row">
          <label className="op-field op-field-grow">
            <span>店铺</span>
            <select value={store} onChange={(event) => setStore(event.target.value)}>
              <option value="">全部店铺</option>
              {stores.map((item) => (
                <option key={`${item.id}-${item.name}`} value={item.id || item.name}>{item.name}</option>
              ))}
            </select>
          </label>
          <label className="op-field op-field-wide">
            <span>订单号 / SKU</span>
            <input
              type="text"
              value={keyword}
              placeholder="输入 PO 单号、子订单号、SKU 或商品名"
              onChange={(event) => setKeyword(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") setApplied({ store, keyword });
              }}
            />
          </label>
          <div className="op-filter-actions">
            <button type="button" className="op-btn op-btn-primary" onClick={() => setApplied({ store, keyword })}>查询</button>
            <button type="button" className="op-btn" onClick={() => { setStore(""); setKeyword(""); setApplied({ store: "", keyword: "" }); }}>重置</button>
          </div>
        </div>
      </div>

      <div className="op-stat-grid op-stat-grid-four">
        <div className="op-stat"><span>未接收订单</span><strong>{numberText(summary?.order_count)}</strong></div>
        <div className="op-stat"><span>子订单</span><strong>{numberText(orders.reduce((sum, row) => sum + row.sub_order_count, 0))}</strong></div>
        <div className="op-stat"><span>GMV</span><strong>{money(summary?.gmv_total)}</strong></div>
        <div className="op-stat"><span>尾程费用</span><strong className="is-info">{money(summary?.tail_freight)}</strong></div>
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      <div className="op-card op-table-card">
        <div className="op-table-head">
          <strong>未接收订单</strong>
          <span>{loading ? "加载中…" : `${summary?.order_count ?? 0} 单`}</span>
          <small>状态包含待发货、未接收、待处理、待揽收；采集订单列表后自动更新</small>
        </div>
        <div className="op-table-wrap">
          <table className="op-table op-table-unreceived">
            <thead>
              <tr>
                <th>店铺</th>
                <th>PO 单号</th>
                <th>站点</th>
                <th>订单状态</th>
                <th>订单时间</th>
                <th>SKU/件数</th>
                <th className="op-num">GMV</th>
                <th className="op-num">销售回款</th>
                <th className="op-num">尾程</th>
                <th className="op-num">预估利润</th>
              </tr>
            </thead>
            <tbody>
              {!orders.length && !loading ? (
                <tr>
                  <td colSpan={10}>
                    <div className="op-empty">
                      <strong>没有未接收订单</strong>
                      <span>当前筛选下没有待发货/未接收订单，或还没有采集订单列表。</span>
                    </div>
                  </td>
                </tr>
              ) : null}
              {orders.map((row) => (
                <tr key={row.parent_order_sn}>
                  <td>
                    <div className="op-store">
                      <span className="op-store-name">{row.shop_name || row.mall_id || "—"}</span>
                    </div>
                  </td>
                  <td className="op-mono">{row.parent_order_sn}</td>
                  <td>{row.site || "—"}</td>
                  <td><span className="op-badge">{row.order_status || "—"}</span></td>
                  <td className="op-muted">{row.order_time || "—"}</td>
                  <td>{row.sku_count} / {row.item_count}</td>
                  <td className="op-num">{money(row.gmv_total)}</td>
                  <td className="op-num">{money(row.sales_payback)}</td>
                  <td className="op-num">{money(row.tail_freight)}</td>
                  <td className={`op-num ${amountTone(row.profit)}`}>{money(row.profit)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
