import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { ChartKindSwitch } from "../components/ChartControls";
import { TrendChart, type ChartKind, type TrendSeries } from "../components/TrendChart";
import { beijingTime, numberText } from "../format";
import type { FluxTrend, TrafficSnapshot, TrafficSummaryResponse } from "../types";

type Scope = "product" | "shop";

const SCOPE_LABEL: Record<Scope, string> = {
  product: "商品流量",
  shop: "店铺流量",
};

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
      <div className="op-card op-filter-card">
        <div className="op-filter-row">
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
          <span className="op-hint">
            {snapshot
              ? `${snapshot.period || "—"} · 统计日期 ${snapshot.stat_date || "—"} · 采集于 ${beijingTime(snapshot.captured_at)}`
              : "尚未采集"}
          </span>
        </div>
        <p className="op-hint">
          在 Temu「经营分析 → 商品流量 / 店铺流量」页用插件弹窗「采集流量数据」，会按当前选中的周期（实时 / 近1日 / 近7日 / 近30日 / 本周 / 本月）和统计日期保存一份汇总快照。
        </p>
      </div>

      {error ? <div className="op-alert is-error">{error}</div> : null}

      <div className="op-card op-chart-card">
        <div className="op-table-head">
          <strong>{SCOPE_LABEL[scope]}趋势</strong>
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

      {flux && fluxTrendSeries.length ? (
        <div className="op-card op-chart-card">
          <div className="op-table-head">
            <strong>流量趋势（按小时）</strong>
            <span>曝光量 · {fluxTrendSeries.map((item) => item.label).join(" / ")}</span>
            <small>{snapshot?.period || ""}</small>
          </div>
          <TrendChart
            labels={fluxTrendLabels}
            series={fluxTrendSeries}
            kind="spline"
            formatValue={(value) => numberText(value)}
            emptyText="采集一次「商品流量」后即可看到按小时趋势"
          />
        </div>
      ) : null}

      {flux && fluxChannelSeries.length ? (
        <div className="op-card op-chart-card">
          <div className="op-table-head">
            <strong>渠道构成（按小时）</strong>
            <span>{fluxChannelSeries.map((item) => item.label).join(" / ")}</span>
            <small>{snapshot?.period || ""}</small>
          </div>
          <TrendChart
            labels={fluxChannelLabels}
            series={fluxChannelSeries}
            kind="stackedArea"
            formatValue={(value) => numberText(value)}
            emptyText="采集一次「商品流量」后即可看到渠道构成"
          />
        </div>
      ) : null}

      <div className="op-card op-table-card">
        <div className="op-table-head">
          <strong>{SCOPE_LABEL[scope]}汇总</strong>
          <span>{snapshot?.shop_name || ""}</span>
          <small>{loading ? "加载中…" : snapshot ? `${metrics.length} 项指标` : "无数据"}</small>
        </div>
        <div className="op-stats-grid">
          {snapshot
            ? metrics.map(([label, value]) => {
                const change = snapshot.metrics[`${label}(环比)`];
                return (
                  <div className="op-stat" key={label}>
                    <span>{label}</span>
                    <strong>{value}</strong>
                    {change ? <small className="op-muted">较前一期 {change}</small> : null}
                  </div>
                );
              })
            : null}
          {!snapshot && !loading ? (
            <div className="op-empty">
              <strong>还没有{SCOPE_LABEL[scope]}数据</strong>
              <span>打开 Temu「经营分析 → 商品流量」，用插件弹窗点「采集流量数据」后再查看。</span>
            </div>
          ) : null}
        </div>
      </div>

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
                    <div className="op-empty">
                      <strong>还没有历史快照</strong>
                      <span>每采集一次会按「周期 + 统计日期」覆盖或新增一条。</span>
                    </div>
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
    </section>
  );
}
