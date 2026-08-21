import { useEffect, useMemo, useState } from "react";
import type { WorkspaceModuleId } from "../../../app/navigation/modules";
import { getDashboardStats, type DashboardStats, type DashboardTrendPoint } from "../api/dashboardApi";
import "./../styles/dashboardStats.css";
import { AppleAppGlyph } from "../../../shared/components/AppleAppGlyph";

type DashboardStatsProps = { onOpenModule: (id: WorkspaceModuleId) => void; variant?: "classic" | "apple" };

type TrendMetric = "all" | "inbound" | "processed";

function getTrendValue(point: DashboardTrendPoint, metric: TrendMetric) {
  if (metric === "inbound") return point.inboundCount;
  if (metric === "processed") return point.processedCount;
  return point.inboundCount + point.processedCount;
}

function DashboardTrendChart({ points }: { points: DashboardTrendPoint[] }) {
  const [days, setDays] = useState(14);
  const [metric, setMetric] = useState<TrendMetric>("all");
  const visiblePoints = useMemo(() => points.slice(-days), [days, points]);
  const values = useMemo(() => visiblePoints.map((point) => getTrendValue(point, metric)), [metric, visiblePoints]);
  const total = values.reduce((sum, value) => sum + value, 0);
  const maximum = Math.max(...values, 1);
  const lastPoint = visiblePoints[visiblePoints.length - 1];
  const rangeLabel = visiblePoints.length
    ? `${visiblePoints[0].date.replace(/-/g, "/")} - ${lastPoint?.date.replace(/-/g, "/")}`
    : "暂无日期";

  return (
    <section className="dashboard-trend-card">
      <div className="dashboard-trend-toolbar">
        <label>
          <span>统计范围</span>
          <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
            <option value={7}>近 7 天</option>
            <option value={14}>近 14 天</option>
            <option value={30}>近 30 天</option>
          </select>
        </label>
        <div className="dashboard-trend-range"><span>日期</span><strong>{rangeLabel}</strong></div>
        <label>
          <span>业务类型</span>
          <select value={metric} onChange={(event) => setMetric(event.target.value as TrendMetric)}>
            <option value="all">全部业务</option>
            <option value="inbound">产品入库</option>
            <option value="processed">产品处理</option>
          </select>
        </label>
        <div className="dashboard-trend-total"><span>总计</span><strong>{total}</strong></div>
      </div>

      <div
        className="dashboard-trend-plot"
        style={{ gridTemplateColumns: `repeat(${Math.max(visiblePoints.length, 1)}, minmax(0, 1fr))` }}
        aria-label={`${rangeLabel}业务趋势，总计${total}`}
      >
        {visiblePoints.map((point, index) => {
          const value = values[index];
          const date = new Date(`${point.date}T00:00:00`);
          const label = `${date.getMonth() + 1}/${date.getDate()}`;
          return (
            <div className="dashboard-trend-column" key={point.date} title={`${point.date}：${value}`}>
              <div className="dashboard-trend-value">{value > 0 ? value : ""}</div>
              <div className="dashboard-trend-bar-track"><i style={{ height: `${Math.max(value ? 8 : 0, (value / maximum) * 100)}%` }} /></div>
              <span>{label}</span>
            </div>
          );
        })}
        {total === 0 && <div className="dashboard-trend-empty">当前时间范围暂无业务记录</div>}
      </div>
      <div className="dashboard-trend-legend"><span><i className="is-primary" />当前筛选业务量</span><small>数据每分钟自动更新</small></div>
    </section>
  );
}

export function DashboardStats({ onOpenModule, variant = "classic" }: DashboardStatsProps) {
  const [stats, setStats] = useState<DashboardStats | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      void getDashboardStats().then((data) => {
        if (!cancelled) setStats(data);
      });
    };
    load();
    // 数据概览定时刷新：产品处理任务/入库在别处进行时，这里能跟上变化
    const timer = window.setInterval(load, 60_000);
    const onVisible = () => {
      if (document.visibilityState === "visible") load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const format = (value: number | null | undefined) => (value === null || value === undefined ? "--" : String(value));

  if (variant === "apple") {
    return (
      <section className="mac-glance-section">
        <div className="mac-section-heading"><div><span>AT A GLANCE</span><h2>今日概览</h2></div><small>每分钟自动更新</small></div>
        <div className="mac-glance-grid">
          <button type="button" onClick={() => onOpenModule("profit_activity_products")}>
            <span className="mac-glance-icon is-blue"><AppleAppGlyph name="profit_activity_products" /></span>
            <span><small>产品总数</small><strong>{format(stats?.productCount)}</strong><em>全部市场</em></span>
          </button>
          <button type="button" onClick={() => onOpenModule("profit_activity_products")}>
            <span className="mac-glance-icon is-green"><AppleAppGlyph name="daily_selection" /></span>
            <span><small>今日入库</small><strong>{format(stats?.todayInboundCount)}</strong><em>北京时间</em></span>
          </button>
          <button type="button" onClick={() => onOpenModule("product_processing")}>
            <span className="mac-glance-icon is-violet"><AppleAppGlyph name="product_processing" /></span>
            <span><small>今日处理</small><strong>{format(stats?.todayProcessedCount)}</strong><em>AI 处理任务</em></span>
          </button>
        </div>
      </section>
    );
  }

  return (
    <section className="dashboard-stats-section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">WORKSPACE OVERVIEW</p>
          <h2>数据概览</h2>
        </div>
        <span className="muted">实时统计</span>
      </div>
      <div className="stats-grid">
        <button className="stats-card" onClick={() => onOpenModule("profit_activity_products")} title="点击打开产品库">
          <span className="stats-icon iconfont icon-folder"></span>
          <span className="stats-label">产品库产品总数</span>
          <strong className="stats-value">{format(stats?.productCount)}</strong>
          <span className="stats-note">美区 / 哥伦比亚 / 厄瓜多尔</span>
        </button>

        <button className="stats-card" onClick={() => onOpenModule("profit_activity_products")} title="点击打开产品库">
          <span className="stats-icon iconfont icon-download"></span>
          <span className="stats-label">今日入库数量</span>
          <strong className="stats-value">{format(stats?.todayInboundCount)}</strong>
          <span className="stats-note">按北京时间统计</span>
        </button>

        <button className="stats-card" onClick={() => onOpenModule("product_processing")} title="点击打开产品处理">
          <span className="stats-icon iconfont icon-setting"></span>
          <span className="stats-label">今日产品处理数量</span>
          <strong className="stats-value">{format(stats?.todayProcessedCount)}</strong>
          <span className="stats-note">按北京时间统计</span>
        </button>
      </div>
      <DashboardTrendChart points={stats?.trend ?? []} />
    </section>
  );
}
