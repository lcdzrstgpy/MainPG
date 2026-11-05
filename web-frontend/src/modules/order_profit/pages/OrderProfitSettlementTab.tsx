import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { money, numberText } from "../format";
import type { SettlementResponse, SettlementRow } from "../types";

type SettlementStatus = "pending" | "settled";

export function OrderProfitSettlementTab({
  isActive,
  refreshToken,
  shop = "",
}: {
  isActive: boolean;
  refreshToken: number;
  shop?: string;
}) {
  const [settlement, setSettlement] = useState<SettlementResponse | null>(null);
  const [status, setStatus] = useState<SettlementStatus>("pending");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    orderProfitApi
      .settlementSummary(shop)
      .then((payload) => {
        if (!cancelled) setSettlement(payload);
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
  }, [isActive, refreshToken, shop]);

  const rows: SettlementRow[] = (settlement?.rows || []).filter((row) => row.status === status);
  const totals = status === "settled" ? settlement?.settled : settlement?.pending;

  return (
    <section className="op-view">
      <div className="op-card op-filter-card">
        <p className="op-hint">
          在 Temu「账户资金 → 结算数据」页用插件弹窗「采集结算数据」，会按 PO 单记录销售回款/冲回、运费回款/冲回，并回填到订单利润。
        </p>
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      <div className="op-card op-table-card">
        <div className="op-table-head">
          <strong>结算数据（按 PO 单）</strong>
          <div className="op-tabs">
            <button
              type="button"
              className={`op-tab ${status === "pending" ? "is-active" : ""}`}
              onClick={() => setStatus("pending")}
            >
              待处理款项
            </button>
            <button
              type="button"
              className={`op-tab ${status === "settled" ? "is-active" : ""}`}
              onClick={() => setStatus("settled")}
            >
              已到账款项
            </button>
          </div>
          <small>{loading ? "加载中…" : `${rows.length} 个 PO 单`}</small>
        </div>
        <div className="op-stats-grid">
          <div className="op-stat">
            <span>{status === "settled" ? "已到账总额" : "待处理款项总额"}</span>
            <strong>{money(totals?.total_payback)}</strong>
          </div>
          <div className="op-stat"><span>销售回款</span><strong className="is-positive">{money(totals?.sales_payback)}</strong></div>
          <div className="op-stat"><span>销售冲回</span><strong className="is-negative">{money(-(totals?.sales_reversal || 0))}</strong></div>
          <div className="op-stat"><span>运费回款</span><strong className="is-positive">{money(totals?.freight_payback)}</strong></div>
          <div className="op-stat"><span>运费冲回</span><strong className="is-negative">{money(-(totals?.freight_reversal || 0))}</strong></div>
        </div>
        <div className="op-table-wrap">
          <table className="op-table op-table-finance">
            <thead>
              <tr>
                <th>PO 单号</th>
                <th>商品</th>
                <th className="op-num">件数</th>
                <th className="op-num">销售回款</th>
                <th className="op-num">销售冲回</th>
                <th className="op-num">运费回款</th>
                <th className="op-num">运费冲回</th>
                <th className="op-num">申报价</th>
                <th>币种</th>
              </tr>
            </thead>
            <tbody>
              {!rows.length && !loading ? (
                <tr>
                  <td colSpan={9}>
                    <div className="op-empty">
                      <strong>还没有结算数据</strong>
                      <span>打开 Temu「账户资金 → 结算数据」，用插件弹窗点「采集结算数据」后再查看。</span>
                    </div>
                  </td>
                </tr>
              ) : null}
              {rows.map((row) => (
                <tr key={row.id}>
                  <td className="op-muted">{row.parent_order_sn}</td>
                  <td className="op-title" title={row.goods_name}>{row.goods_name || "—"}</td>
                  <td className="op-num">{numberText(row.quantity)}</td>
                  <td className="op-num is-positive">{money(row.sales_payback)}</td>
                  <td className="op-num is-negative">{money(-(row.sales_reversal || 0))}</td>
                  <td className="op-num is-positive">{money(row.freight_payback)}</td>
                  <td className="op-num is-negative">{money(-(row.freight_reversal || 0))}</td>
                  <td className="op-num">{money(row.declared_price)}</td>
                  <td>{row.currency || "CNY"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
