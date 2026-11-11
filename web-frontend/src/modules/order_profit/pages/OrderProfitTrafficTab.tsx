import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { ChartKindSwitch } from "../components/ChartControls";
import { ShopDataGlyph, type ShopDataGlyphName } from "../components/ShopDataGlyph";
import { EmptyBlock, Metric, MetricBand, SectionCard } from "../components/ShopDataPrimitives";
import { TrendChart, type ChartKind, type TrendSeries } from "../components/TrendChart";
import { beijingTime, numberText } from "../format";
import type { FluxTrend, TrafficSnapshot, TrafficSummaryResponse } from "../types";

type Scope = "product" | "shop";

const SCOPE_LABEL: Record<Scope, string> = {
  product: "商品流量",
  shop: "店铺流量",
};

/** 把抓取到的指标名映射到细线图标，避免指标带只剩一排数字。 */
function trafficGlyph(label: string): ShopDataGlyphName {
  if (/点击率|转化/.test(label)) return "trend";
  if (/点击|访客点击/.test(label)) return "cursor";
  if (/访客|人数/.test(label)) return "visitor";
  if (/曝光/.test(label)) return "eye";
  if (/支付订单|订单/.test(label)) return "orders";
  if (/支付件数|件数|商品/.test(label)) return "box";
  if (/金额|GMV|销售额/.test(label)) return "money";
  return "chart";
}

/** 商品/店铺流量页最该先看的指标；其余指标收进「更多指标」。 */
const CORE_METRIC_PATTERN = /曝光|点击|访客|支付订单|成交|转化|下单/;

/** Temu 图表配色：搜索/近1日 = 橙，推荐/前1日 = 深蓝，其他 = 黄。 */
const FLUX_TREND_COLORS = ["#fb7701", "#486482", "#8b5cf6"];
const FLUX_CHANNEL_COLORS = ["#fb7701", "#486482", "#f9d013", "#10b981", "#8b5cf6"];

function hourLabel(time: string): string {
  const text = String(time || "").trim();
  const match = text.match(/(\d{1,2})(?::\d{2})?$/);
  return match ? `${match[1].padStart(2, "0")}:00` : text;
}

function readFluxTrend(snapshot: TrafficSnapshot | null): FluxTrend | null {
  if (!snapshot) return null;
  const raw = (snapshot.metrics as unknown as Record<string, unknown>)["trend"];
  if (!raw || typeof raw !== "object") return null;
  const trend = raw as FluxTrend;
  const hasSeries = Array.isArray(trend.series) && trend.series.length > 0;
  const hasChannels = Array.isArray(trend.channels) && trend.channels.length > 0;
  return hasSeries || hasChannels ? trend : null;
}

export function OrderProfitTrafficTab({
  isActive,
  refreshToken,
  shop = "",
}: {
  isActive: boolean;
  refreshToken: number;
  shop?: string;
}) {
  const [data, setData] = useState<TrafficSummaryResponse | null>(null);
  const [scope, setScope] = useState<Scope>("product");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [chartKind, setChartKind] = useState<ChartKind>("spline");

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    orderProfitApi
      .trafficSummary(shop)
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

  const snapshot: TrafficSnapshot | null =
    scope === "shop" ? data?.shop ?? null : data?.product ?? null;
  const metrics = Object.entries(snapshot?.metrics || {}).filter(
    // charts/trend 是嵌套对象，只展示文本型指标，避免把对象渲染到页面上。
    ([label, value]) => !label.includes("(环比)") && typeof value === "string",
  );
  const coreMetrics = metrics.filter(([label]) => CORE_METRIC_PATTERN.test(label)).slice(0, 4);
  const extraMetrics = metrics.filter(([label]) => !coreMetrics.some(([core]) => core === label));
  const history = (data?.history || []).filter((row) => row.scope === scope);
  const trendRows = [...history]
    .sort((left, right) => String(left.stat_date).localeCompare(String(right.stat_date)))
    .slice(-14);
  const trendLabels = trendRows.map((row) => String(row.stat_date || "").slice(5) || "—");
  const trendSeries: TrendSeries[] = [
    { key: "exposure", label: "曝光量", color: "#6366f1", values: trendRows.map((row) => Number(row.exposure || 0)) },
    { key: "clicks", label: "点击量", color: "#0ea5e9", values: trendRows.map((row) => Number(row.clicks || 0)) },
    { key: "visitors", label: "访客数", color: "#10b981", values: trendRows.map((row) => Number(row.visitors || 0)) },
  ];

  // Temu 商品流量页按小时的趋势 / 渠道图（数据来自采集时抓到的 /api/flow/analysis/trend）。
  const flux = readFluxTrend(snapshot);
  const fluxTrendLabels = (flux?.series?.[0]?.points || []).map((point) => hourLabel(point.time));
  const fluxTrendSeries: TrendSeries[] = (flux?.series || []).map((item, index) => ({
    key: item.key,
    label: item.key,
    color: FLUX_TREND_COLORS[index % FLUX_TREND_COLORS.length],
    values: item.points.map((point) => Number(point.value || 0)),
  }));
  const fluxChannelLabels = (flux?.channels?.[0]?.points || []).map((point) => hourLabel(point.time));
  const fluxChannelSeries: TrendSeries[] = (flux?.channels || []).map((item, index) => ({
    key: item.key,
    label: item.key,
    color: FLUX_CHANNEL_COLORS[index % FLUX_CHANNEL_COLORS.length],
    values: item.points.map((point) => Number(point.value || 0)),
  }));

  return (
    <section className="op-view">
      <div className="op-view-toolbar">
        <div className="op-tabs">
          <button
            type="button"
            className={`op-tab ${scope === "product" ? "is-active" : ""}`}
            onClick={() => setScope("product")}
          >
            商品流量
          </button>
          <button
            type="button"
            className={`op-tab ${scope === "shop" ? "is-active" : ""}`}
            onClick={() => setScope("shop")}
          >
            店铺流量
          </button>
        </div>
        <span className="op-view-toolbar-meta">
          {snapshot
            ? `${snapshot.period || "—"} · 统计日期 ${snapshot.stat_date || "—"} · 采集于 ${beijingTime(snapshot.captured_at)}`
            : "尚未采集"}
        </span>
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      {!snapshot && !loading ? (
        <EmptyBlock
          art="traffic"
          title={`还没有${SCOPE_LABEL[scope]}数据`}
          desc="在 Temu「经营分析 → 商品流量 / 店铺流量」打开页面后，用插件弹窗采集一次，核心指标和趋势会出现在这里。"
        />
      ) : (
        <MetricBand>
          {coreMetrics.length ? (
            coreMetrics.map(([label, value]) => {
              const change = snapshot?.metrics[`${label}(环比)`];
              return (
                <Metric
                  key={label}
                  label={label}
                  value={value}
                  hint={change ? `较前一期 ${change}` : undefined}
                  glyph={trafficGlyph(label)}
                />
              );
            })
          ) : (
            <Metric label={`${SCOPE_LABEL[scope]}指标`} value={loading ? "…" : "—"} hint="采集后展示" />
          )}
        </MetricBand>
      )}

      <SectionCard
        title={`${SCOPE_LABEL[scope]}趋势`}
        desc={`按统计日期看最近 ${trendRows.length || 0} 次快照，先判断走势再拆渠道。`}
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

      {flux && fluxTrendSeries.length ? (
        <SectionCard
          title="流量趋势（按小时）"
          desc={`${fluxTrendSeries.map((item) => item.label).join(" / ")}，看一天内的流量高峰。`}
          meta={<span className="op-chip">{snapshot?.period || "当前周期"}</span>}
          icon="clock"
        >
          <TrendChart
            labels={fluxTrendLabels}
            series={fluxTrendSeries}
            kind="spline"
            formatValue={(value) => numberText(value)}
            emptyText="采集一次「商品流量」后即可看到按小时趋势"
          />
        </SectionCard>
      ) : null}

      {flux && fluxChannelSeries.length ? (
        <SectionCard
          title="渠道构成（按小时）"
          desc="判断流量主要来自搜索、推荐还是活动，再决定投放与主图优化方向。"
          meta={<span className="op-chip">{fluxChannelSeries.map((item) => item.label).join(" / ")}</span>}
          icon="channel"
        >
          <TrendChart
            labels={fluxChannelLabels}
            series={fluxChannelSeries}
            kind="stackedArea"
            formatValue={(value) => numberText(value)}
            emptyText="采集一次「商品流量」后即可看到渠道构成"
          />
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
                const change = snapshot?.metrics[`${label}(环比)`];
                return (
                  <div className="op-stat" key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                    {change ? <small className="op-muted">较前一期 {change}</small> : null}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="op-hint">当前周期没有更多指标。</p>
          )}

          <div className="op-table-wrap">
            <table className="op-table op-table-finance">
              <thead>
                <tr>
                  <th>统计日期</th>
                  <th>周期</th>
                  <th className="op-num">曝光量</th>
                  <th className="op-num">点击量</th>
                  <th className="op-num">访客数</th>
                  <th className="op-num">支付订单</th>
                  <th className="op-num">支付件数</th>
                  <th className="op-num">点击率</th>
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
                        desc="每采集一次会按「周期 + 统计日期」覆盖或新增一条。"
                      />
                    </td>
                  </tr>
                ) : null}
                {history.map((row) => (
                  <tr key={row.id}>
                    <td>{row.stat_date || "—"}</td>
                    <td>{row.period || "—"}</td>
                    <td className="op-num">{numberText(row.exposure)}</td>
                    <td className="op-num">{numberText(row.clicks)}</td>
                    <td className="op-num">{row.visitors ? numberText(row.visitors) : "—"}</td>
                    <td className="op-num">{numberText(row.paid_orders)}</td>
                    <td className="op-num">{numberText(row.paid_items)}</td>
                    <td className="op-num">{row.click_rate === null ? "—" : `${row.click_rate}%`}</td>
                    <td className="op-muted">{beijingTime(row.captured_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </details>

      <p className="op-footnote">
        数据来自 Temu「经营分析 → 商品流量 / 店铺流量」，按当前选中的周期与统计日期保存快照。
      </p>
    </section>
  );
}
