import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { ChartKindSwitch } from "../components/ChartControls";
import { ShopDataGlyph, type ShopDataGlyphName } from "../components/ShopDataGlyph";
import { EmptyBlock, Metric, MetricBand, SectionCard } from "../components/ShopDataPrimitives";
import { TrendChart, type ChartKind, type TrendSeries } from "../components/TrendChart";
import { beijingTime, numberText } from "../format";
import type { SalesSnapshot, SalesSummaryResponse } from "../types";

const HEALTH_FIELDS = ["售罄健康分数", "无法报名活动商品数", "待反馈", "待补货", "未按期补货", "爆款商品"];

/** 销量页指标 → 细线图标。 */
function salesGlyph(label: string): ShopDataGlyphName {
  if (/售罄|低库存|预警/.test(label)) return "warning";
  if (/可售天数|10天/.test(label)) return "calendar";
  if (/GMV|金额/.test(label)) return "money";
  if (/满足率|健康/.test(label)) return "shield";
  if (/销量|商品/.test(label)) return "box";
  return "chart";
}

/** 销量页优先展示的指标：销量、售罄、低库存、售罄影响。 */
const CORE_METRIC_PATTERN = /今日总销量|售罄|低库存|库存可售天数|10天/;

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
  const coreMetrics = metrics.filter(([label]) => CORE_METRIC_PATTERN.test(label)).slice(0, 4);
  const extraMetrics = metrics.filter(([label]) => !coreMetrics.some(([core]) => core === label));
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
      <div className="op-view-toolbar">
        <span className="op-view-toolbar-meta">
          {latest
            ? `${latest.region || "全球"} · 统计日期 ${latest.stat_date || "—"}${
                latest.health["最近更新时间"] ? ` · 页面更新 ${latest.health["最近更新时间"]}` : ""
              }`
            : "尚未采集"}
        </span>
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      {!latest && !loading ? (
        <EmptyBlock
          art="sales"
          title="还没有销量分析数据"
          desc="在 Temu「经营分析 → 销量分析」打开页面后，到数据采集页重新采集「销量分析」这一步。"
        />
      ) : (
        <MetricBand>
          {coreMetrics.length ? (
            coreMetrics.map(([label, value]) => {
              const day = latest?.metrics[`${label}(较昨日)`];
              return <Metric key={label} label={label} value={value} hint={day ? `较昨日 ${day}` : undefined} glyph={salesGlyph(label)} />;
            })
          ) : (
            <Metric label="销量指标" value={loading ? "…" : "—"} hint="采集后展示" />
          )}
        </MetricBand>
      )}

      <SectionCard
        title="销量与库存趋势"
        desc={`按统计日期看最近 ${trendRows.length || 0} 次快照：销量是否起来，售罄与低库存是否在恶化。`}
        action={<ChartKindSwitch value={chartKind} onChange={setChartKind} />}
        icon="trend"
      >
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
      </SectionCard>

      {latest ? (
        <SectionCard
          title="售罄健康"
          desc="决定还能不能正常报名活动、是否需要补货；异常项优先处理。"
          meta={<span className="op-chip">{latest.captured_at ? `采集于 ${beijingTime(latest.captured_at)}` : ""}</span>}
          icon="shield"
        >
          <div className="op-stats-grid op-stats-nine">
            {HEALTH_FIELDS.map((label) => (
              <div className="op-stat" key={label}>
                <span>{label}</span>
                <strong>{latest.health[label] || "—"}</strong>
              </div>
            ))}
          </div>
        </SectionCard>
      ) : null}

      <details className="op-disclosure">
        <summary>
          <ShopDataGlyph name="ledger" />
          <span>更多指标与历史快照</span>
          <small>{metrics.length} 项指标 · {history.length} 条快照</small>
        </summary>
        <div className="op-disclosure-body">
          {extraMetrics.length ? (
            <div className="op-stats-grid op-stats-nine">
              {extraMetrics.map(([label, value]) => {
                const day = latest?.metrics[`${label}(较昨日)`];
                const week = latest?.metrics[`${label}(较近7日)`];
                return (
                  <div className="op-stat" key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                    {day || week ? (
                      <small>较昨日 {day || "—"} · 较近7日 {week || "—"}</small>
                    ) : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="op-hint">当前快照没有更多指标。</p>
          )}

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
                      <EmptyBlock
                        compact
                        title="还没有历史快照"
                        desc="每次采集会按「区域 + 统计日期」覆盖或新增一条。"
                      />
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
      </details>

      <p className="op-footnote">
        数据来自 Temu「经营分析 → 销量分析」；若页面提示「该区暂无权限」，插件会自动切到「全球」再采。
      </p>
    </section>
  );
}
