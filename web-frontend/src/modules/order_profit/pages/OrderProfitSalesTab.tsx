import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { ChartKindSwitch } from "../components/ChartControls";
import { TrendChart, type ChartKind, type TrendSeries } from "../components/TrendChart";
import { beijingTime, numberText } from "../format";
import type { SalesSnapshot, SalesSummaryResponse } from "../types";

const HEALTH_FIELDS = ["售罄健康分数", "无法报名活动商品数", "待反馈", "待补货", "未按期补货", "爆款商品"];

/** 快照指标是带单位/占比的字符串，取前面的数字用于画趋势。 */
function metricNumber(value: string | undefined): number {
  const match = String(value || "").replace(/,/g, "").match(/-?\d+(?:\.\d+)?/);
  return match ? Number(match[0]) : 0;
}

export function OrderProfitSalesTab({
  isActive,
  refreshToken,
  shop = "",
}: {
  isActive: boolean;
  refreshToken: number;
  shop?: string;
}) {
  const [data, setData] = useState<SalesSummaryResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [chartKind, setChartKind] = useState<ChartKind>("area");

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    orderProfitApi
      .salesSummary(shop)
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
  }, [isActive, refreshToken, shop]);

  const latest: SalesSnapshot | null = data?.latest ?? null;
  const metrics = Object.entries(latest?.metrics || {}).filter(
    ([label]) => !label.includes("(较昨日)") && !label.includes("(较近7日)"),
  );
  const history = data?.history || [];
  const trendRows = [...history]
    .sort((left, right) => String(left.stat_date).localeCompare(String(right.stat_date)))
    .slice(-14);
  const trendLabels = trendRows.map((row) => String(row.stat_date || "").slice(5) || "—");
  const trendSeries: TrendSeries[] = [
    {
      key: "sales",
      label: "今日总销量",
      color: "#10b981",
      values: trendRows.map((row) => metricNumber(row.metrics["今日总销量"])),
    },
    {
      key: "soldout",
      label: "SKU 售罄数",
      color: "#ef4444",
      values: trendRows.map((row) => metricNumber(row.metrics["SKU售罄数&占比"])),
    },
    {
      key: "lowstock",
      label: "低库存预警",
      color: "#f59e0b",
      values: trendRows.map((row) => metricNumber(row.metrics["低库存预警SKU数&占比"])),
    },
  ];

  return (
    <section className="op-view">
      <div className="op-card op-filter-card">
        <div className="op-filter-row">
          <span className="op-hint">
            {latest
              ? `${latest.region || "全球"} · 统计日期 ${latest.stat_date || "—"}${
                  latest.health["最近更新时间"] ? ` · 页面更新 ${latest.health["最近更新时间"]}` : ""
                }`
              : "尚未采集"}
          </span>
        </div>
        <p className="op-hint">
          在 Temu「经营分析 → 销量分析」页用插件弹窗「采集销量分析」，会读取数据概览的销量/售罄/库存指标和售罄健康分数。若页面提示「该区暂无权限」，插件会自动切到「全球」再采。
        </p>
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      <div className="op-card op-chart-card">
        <div className="op-table-head">
          <strong>销量与库存趋势</strong>
          <span>最近 {trendRows.length} 次快照</span>
          <ChartKindSwitch value={chartKind} onChange={setChartKind} />
        </div>
        {loading ? (
          <div className="op-skeleton op-skeleton-chart" />
        ) : (
          <TrendChart
            labels={trendLabels}
            series={trendSeries}
            kind={chartKind}
            formatValue={(value) => numberText(value)}
            emptyText="采集两次以上才会形成趋势"
          />
        )}
      </div>

      <div className="op-card op-table-card">
        <div className="op-table-head">
          <strong>数据概览</strong>
          <span>{latest?.shop_name || "未采集"}</span>
          <small>{loading ? "加载中…" : latest ? `${metrics.length} 项指标` : "无数据"}</small>
        </div>
        <div className="op-stats-grid">
          {latest
            ? metrics.map(([label, value]) => {
                const day = latest.metrics[`${label}(较昨日)`];
                const week = latest.metrics[`${label}(较近7日)`];
                return (
                  <div className="op-stat" key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                    {day || week ? (
                      <small>
                        较昨日 {day || "—"} · 较近7日 {week || "—"}
                      </small>
                    ) : null}
                  </div>
                );
              })
            : null}
          {!latest && !loading ? (
            <div className="op-empty">
              <strong>还没有销量分析数据</strong>
              <span>打开 Temu「经营分析 → 销量分析」，用插件弹窗点「采集销量分析」后再查看。</span>
            </div>
          ) : null}
        </div>
      </div>

      {latest ? (
        <div className="op-card op-table-card">
          <div className="op-table-head">
            <strong>售罄健康</strong>
            <small>{latest.captured_at ? `采集于 ${beijingTime(latest.captured_at)}` : ""}</small>
          </div>
          <div className="op-stats-grid">
            {HEALTH_FIELDS.map((label) => (
              <div className="op-stat" key={label}>
                <span>{label}</span>
                <strong>{latest.health[label] || "—"}</strong>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="op-card op-table-card">
        <div className="op-table-head">
          <strong>历史快照</strong>
          <small>{history.length} 条</small>
        </div>
        <div className="op-table-wrap">
          <table className="op-table op-table-finance">
            <thead>
              <tr>
                <th>统计日期</th>
                <th>区域</th>
                <th className="op-num">今日总销量</th>
                <th className="op-num">SKU售罄</th>
                <th className="op-num">低库存预警</th>
                <th className="op-num">售罄影响GMV</th>
                <th className="op-num">库存可售天数</th>
                <th className="op-num">10天满足率</th>
                <th>采集时间</th>
              </tr>
            </thead>
            <tbody>
              {!history.length && !loading ? (
                <tr>
                  <td colSpan={9}>
                    <div className="op-empty">
                      <strong>还没有历史快照</strong>
                      <span>每次采集会按「区域 + 统计日期」覆盖或新增一条。</span>
                    </div>
                  </td>
                </tr>
              ) : null}
              {history.map((row) => (
                <tr key={row.id}>
                  <td>{row.stat_date || "—"}</td>
                  <td>{row.region || "—"}</td>
                  <td className="op-num">{row.metrics["今日总销量"] || "—"}</td>
                  <td className="op-num">{row.metrics["SKU售罄数&占比"] || "—"}</td>
                  <td className="op-num">{row.metrics["低库存预警SKU数&占比"] || "—"}</td>
                  <td className="op-num">{row.metrics["售罄影响GMV&占比"] || "—"}</td>
                  <td className="op-num">{row.metrics["库存可售天数"] || "—"}</td>
                  <td className="op-num">{row.metrics["10天库存满足率"] || "—"}</td>
                  <td className="op-muted">{beijingTime(row.captured_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
