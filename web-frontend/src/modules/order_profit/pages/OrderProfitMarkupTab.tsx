import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { money, numberText, percent } from "../format";
import type { MarkupResponse, OrderProfitStore } from "../types";

const CURRENT_MONTH = new Date().toISOString().slice(0, 7);

export function OrderProfitMarkupTab({
  isActive,
  refreshToken,
}: {
  isActive: boolean;
  refreshToken: number;
}) {
  const [draft, setDraft] = useState({ month: CURRENT_MONTH, store: "" });
  const [applied, setApplied] = useState(draft);
  const [stores, setStores] = useState<OrderProfitStore[]>([]);
  const [data, setData] = useState<MarkupResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    orderProfitApi.listStores()
      .then((payload) => { if (!cancelled) setStores(payload.stores || []); })
      .catch(() => { if (!cancelled) setStores([]); });
    return () => { cancelled = true; };
  }, [isActive, refreshToken]);

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    orderProfitApi.markup(applied)
      .then((payload) => { if (!cancelled) setData(payload); })
      .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [isActive, applied, refreshToken]);

  const summary = data?.summary;
  const rows = data?.items || [];

  return (
    <section className="op-view">
      <div className="op-card op-filter-card">
        <div className="op-filter-row">
          <label className="op-field">
            <span>订单时间</span>
            <input type="month" value={draft.month} onChange={(event) => setDraft({ ...draft, month: event.target.value })} />
          </label>
          <label className="op-field op-field-grow">
            <span>店铺</span>
            <select value={draft.store} onChange={(event) => setDraft({ ...draft, store: event.target.value })}>
              <option value="">全部店铺</option>
              {stores.map((item) => (
                <option key={`${item.id}-${item.name}`} value={item.id || item.name}>{item.name}</option>
              ))}
            </select>
          </label>
          <div className="op-filter-actions">
            <button type="button" className="op-btn op-btn-primary" onClick={() => setApplied({ ...draft })}>查询</button>
            <button type="button" className="op-btn" onClick={() => { const next = { month: CURRENT_MONTH, store: "" }; setDraft(next); setApplied(next); }}>重置</button>
          </div>
        </div>
      </div>

      <div className="op-stat-grid op-stat-grid-four">
        <div className="op-stat"><span>SKU 数</span><strong>{numberText(summary?.sku_count)}</strong></div>
        <div className="op-stat"><span>已匹配成本</span><strong>{numberText(summary?.matched_count)}</strong></div>
        <div className="op-stat"><span>缺采购价</span><strong className="is-warning">{numberText(summary?.missing_cost_count)}</strong></div>
        <div className="op-stat"><span>平均加价率</span><strong>{percent(summary?.avg_markup_rate)}</strong></div>
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      <div className="op-card op-table-card">
        <div className="op-panel-head op-panel-head-padded">
          <strong>加价率</strong>
          <span className="op-chip">按 SKU 汇总申报价与货源成本</span>
        </div>
        <div className="op-table-wrap">
          <table className="op-table op-table-markup">
            <thead>
              <tr>
                <th>SKU / 货号</th>
                <th>SKC</th>
                <th>商品</th>
                <th>站点</th>
                <th className="op-num">件数</th>
                <th className="op-num">申报价</th>
                <th className="op-num">采购成本</th>
                <th className="op-num">加价率</th>
                <th className="op-num">毛利</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {!rows.length && !loading ? (
                <tr>
                  <td colSpan={10}>
                    <div className="op-empty">
                      <strong>还没有可计算的 SKU</strong>
                      <span>采集订单列表和详情页，或在货源台账补采购价后再看加价率。</span>
                    </div>
                  </td>
                </tr>
              ) : null}
              {rows.map((row) => (
                <tr key={`${row.sku_id}-${row.skc_id}-${row.ext_code}`}>
                  <td>
                    <span className="op-mono">{row.sku_id || "—"}</span>
                    {row.ext_code ? <small className="op-block">{row.ext_code}</small> : null}
                  </td>
                  <td className="op-mono">{row.skc_id || "—"}</td>
                  <td className="op-title" title={row.goods_name}>{row.goods_name || "—"}</td>
                  <td>{row.site || "—"}</td>
                  <td className="op-num">{row.quantity}</td>
                  <td className="op-num">{money(row.declared_price)}</td>
                  <td className="op-num">
                    {row.unit_cost_cny === null || row.unit_cost_cny === undefined ? (
                      <span className="op-chip is-warn">待维护</span>
                    ) : money(row.unit_cost_cny)}
                  </td>
                  <td className="op-num">{percent(row.markup_rate)}</td>
                  <td className={`op-num ${(row.gross_profit_cny || 0) >= 0 ? "is-positive" : "is-negative"}`}>
                    {money(row.gross_profit_cny)}
                  </td>
                  <td>
                    <span className={`op-chip ${row.status === "matched" ? "is-ok" : "is-warn"}`}>
                      {row.status === "matched" ? "已匹配" : row.status === "needs_cost" ? "缺成本" : "缺申报价"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
