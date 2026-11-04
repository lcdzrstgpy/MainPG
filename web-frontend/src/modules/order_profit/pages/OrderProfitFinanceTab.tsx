import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { ShopDataGlyph } from "../components/ShopDataGlyph";
import { EmptyBlock, Metric, MetricBand, SectionCard } from "../components/ShopDataPrimitives";
import { money, numberText } from "../format";
import type { FinanceResponse } from "../types";

const CURRENT_MONTH = new Date().toISOString().slice(0, 7);

const CATEGORY_LABEL: Record<string, string> = {
  promotion: "推广服务费",
  penalty: "平台罚款",
  settlement: "结算",
  other: "其他",
};

export function OrderProfitFinanceTab({
  isActive,
  refreshToken,
  shop = "",
}: {
  isActive: boolean;
  refreshToken: number;
  shop?: string;
}) {
  const [month, setMonth] = useState(CURRENT_MONTH);
  const [appliedMonth, setAppliedMonth] = useState(CURRENT_MONTH);
  const [data, setData] = useState<FinanceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    orderProfitApi
      .financeSummary(appliedMonth, shop)
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
  }, [isActive, appliedMonth, refreshToken, shop]);

  const summary = data?.summary;
  const entries = data?.items || [];

  return (
    <section className="op-view">
      <div className="op-card op-filter-card">
        <div className="op-filter-row">
          <label className="op-field">
            <span>账务月份</span>
            <input type="month" value={month} onChange={(event) => setMonth(event.target.value)} />
          </label>
          <div className="op-filter-actions">
            <button type="button" className="op-btn op-btn-primary" onClick={() => setAppliedMonth(month)}>查询</button>
            <button type="button" className="op-btn" onClick={() => { setMonth(CURRENT_MONTH); setAppliedMonth(CURRENT_MONTH); }}>重置</button>
          </div>
          <span className="op-view-toolbar-meta">
            {loading ? "加载中…" : `${appliedMonth} · ${entries.length} 条流水`}
          </span>
        </div>
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      <MetricBand>
        <Metric label="流入金额" value={money(summary?.inflow)} tone="is-positive" hint="本月进入账户的金额" glyph="wallet" />
        <Metric label="流出金额" value={money(-(summary?.outflow || 0))} tone="is-negative" hint="含推广费、罚款等扣款" glyph="refresh" />
        <Metric label="广告费用" value={money(-(summary?.ad_cost || 0))} tone="is-negative" hint="推广服务费" glyph="ad" />
        <Metric label="平台罚款" value={money(-(summary?.penalty || 0))} tone="is-negative" hint="罚款类流水" glyph="penalty" />
      </MetricBand>

      <details className="op-disclosure">
        <summary>
          <ShopDataGlyph name="cost" />
          <span>金额构成补充</span>
          <small>结算金额 · 流水条数</small>
        </summary>
        <div className="op-disclosure-body">
          <div className="op-stats-grid">
            <div className="op-stat"><span>结算金额</span><strong className="is-positive">{money(summary?.settlement)}</strong></div>
            <div className="op-stat"><span>流水条数</span><strong>{numberText(summary?.entries)}</strong></div>
          </div>
        </div>
      </details>

      <SectionCard
        className="op-section-table"
        title="财务流水"
        desc="逐笔看钱扣在哪；金额为 CNY，推广服务费计入广告费用，罚款类计入平台罚款。"
        meta={<span className="op-chip">{loading ? "读取中…" : `${entries.length} 条`}</span>}
        icon="ledger"
        scene="finance"
      >
        <div className="op-table-wrap">
          <table className="op-table op-table-finance">
            <thead>
              <tr>
                <th>账务时间</th>
                <th>账务类型</th>
                <th className="op-num">金额</th>
                <th>币种</th>
                <th>分类</th>
                <th>备注</th>
              </tr>
            </thead>
            <tbody>
              {!entries.length && !loading ? (
                <tr>
                  <td colSpan={6}>
                    <EmptyBlock
                      compact
                      art="funds"
                      title="该月份还没有财务流水"
                      desc="打开 Temu 对账中心，用插件弹窗采集财务流水后再查看。"
                    />
                  </td>
                </tr>
              ) : null}
              {entries.map((entry) => (
                <tr key={entry.id}>
                  <td className="op-muted">{entry.entry_time || "—"}</td>
                  <td>{entry.entry_type || "—"}</td>
                  <td className={`op-num ${entry.amount >= 0 ? "is-positive" : "is-negative"}`}>
                    {money(entry.amount)}
                  </td>
                  <td>{entry.currency || "CNY"}</td>
                  <td>
                    <span className={`op-chip ${entry.category === "penalty" ? "is-error" : entry.category === "promotion" ? "is-warn" : entry.category === "settlement" ? "is-ok" : ""}`}>
                      {CATEGORY_LABEL[entry.category] || entry.category || "其他"}
                    </span>
                  </td>
                  <td className="op-title" title={entry.note}>{entry.note || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </SectionCard>

      <p className="op-footnote">
        数据来自 Temu 对账中心；店铺指标与 PO 单结算回款在经营概览与「结算回款」中查看。
      </p>
    </section>
  );
}
