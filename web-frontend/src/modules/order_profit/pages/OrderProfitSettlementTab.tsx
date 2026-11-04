import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { EmptyBlock, Metric, MetricBand, SectionCard } from "../components/ShopDataPrimitives";
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
      <div className="op-view-toolbar">
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
        <span className="op-view-toolbar-meta">{loading ? "加载中…" : `${rows.length} 个 PO 单`}</span>
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      <MetricBand>
        <Metric
          label={status === "settled" ? "已到账总额" : "待处理款项总额"}
          value={money(totals?.total_payback)}
          hint="销售与运费回款合计"
          glyph="wallet"
        />
        <Metric label="销售回款" value={money(totals?.sales_payback)} tone="is-positive" hint="商品货款回款" glyph="money" />
        <Metric label="运费回款" value={money(totals?.freight_payback)} tone="is-positive" hint="运费回款" glyph="truck" />
        <Metric
          label="冲回合计"
          value={money(-((totals?.sales_reversal || 0) + (totals?.freight_reversal || 0)))}
          tone="is-negative"
          hint="销售冲回 + 运费冲回"
          glyph="refresh"
        />
      </MetricBand>

      <SectionCard
        className="op-section-table"
        title="结算数据（按 PO 单）"
        desc="一笔 PO 单一行；销售回款/冲回、运费回款/冲回会同步回填到订单利润。"
        meta={<span className="op-chip">{status === "settled" ? "已到账" : "待处理"}</span>}
        icon="receipt"
        scene="settle"
      >
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
                    <EmptyBlock
                      compact
                      art="funds"
                      title="还没有结算数据"
                      desc="打开 Temu「账户资金 → 结算数据」，用插件弹窗点「采集结算数据」后再查看。"
                    />
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
      </SectionCard>

      <p className="op-footnote">
        数据来自 Temu「账户资金 → 结算数据」，按 PO 单记录，每一笔都会参与订单利润计算。
      </p>
    </section>
  );
}
