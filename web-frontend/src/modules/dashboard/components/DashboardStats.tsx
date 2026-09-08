import { Fragment, useEffect, useMemo, useState } from "react";
import type { WorkspaceModuleId } from "../../../app/navigation/modules";
import { getDashboardStats, type DashboardStats, type DashboardTrendPoint } from "../api/dashboardApi";
import { loadBillingUsageHistory, type BillingUsageEntry } from "../../personal_center/api/personalCenterApi";
import "./../styles/dashboardStats.css";
import { AppleAppGlyph } from "../../../shared/components/AppleAppGlyph";

type DashboardStatsProps = { onOpenModule: (id: WorkspaceModuleId) => void; variant?: "classic" | "apple" };

type TrendMode = "dual" | "sum";

/* ── 折线趋势图（重制版：平滑曲线 + 入场动画 + 跟随式悬浮卡） ── */

/** Catmull-Rom 样条 → 三次贝塞尔：把折线变成顺滑曲线 */
function smoothPath(pts: Array<{ x: number; y: number }>): string {
  if (pts.length === 0) return "";
  if (pts.length < 3) return pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(" ");
  let d = `M${pts[0].x.toFixed(2)} ${pts[0].y.toFixed(2)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[Math.min(pts.length - 1, i + 2)];
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C${c1x.toFixed(2)} ${c1y.toFixed(2)} ${c2x.toFixed(2)} ${c2y.toFixed(2)} ${p2.x.toFixed(2)} ${p2.y.toFixed(2)}`;
  }
  return d;
}

function DashboardTrendChart({ points }: { points: DashboardTrendPoint[] }) {
  const [days, setDays] = useState(14);
  const [mode, setMode] = useState<TrendMode>("dual");
  const [hover, setHover] = useState<number | null>(null);

  const visiblePoints = useMemo(() => points.slice(-days), [days, points]);

  // 双线：产品处理（红）+ POD 生成（蓝）；汇总模式合成一条线。
  const activeSeries = useMemo(
    () =>
      mode === "sum"
        ? [{ key: "sum", label: "汇总", color: "#e1568a", values: visiblePoints.map((p) => p.inboundCount + p.processedCount) }]
        : [
            { key: "processed", label: "产品处理", color: "#e23b4e", values: visiblePoints.map((p) => p.processedCount) },
            { key: "inbound", label: "POD 生成", color: "#2f6bff", values: visiblePoints.map((p) => p.inboundCount) },
          ],
    [mode, visiblePoints],
  );

  const allValues = activeSeries.flatMap((s) => s.values);
  const total = allValues.reduce((a, b) => a + b, 0);

  /* Y 轴漂亮刻度：步长取 1/2/2.5/5 × 10^n，顶部预留 18% 呼吸空间，曲线不顶格 */
  const rawDataMax = Math.max(...allValues, 1);
  const niceSteps = [1, 2, 2.5, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000];
  const step = niceSteps.find((s) => s >= rawDataMax / 4) ?? niceSteps[niceSteps.length - 1];
  const maxValue = Math.max(Math.ceil((rawDataMax * 1.18) / step) * step, step * 4);
  const yTicks = Array.from({ length: 5 }, (_, i) => step * i);

  /* 画布几何 */
  const width = 760;
  const height = 280;
  const pad = { top: 26, right: 22, bottom: 40, left: 48 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const xAt = (i: number) => pad.left + (i / Math.max(visiblePoints.length - 1, 1)) * plotW;
  const yAt = (v: number) => pad.top + plotH - (v / maxValue) * plotH;

  /* X 轴标签：均匀抽样，避免拥挤 */
  const labelStep = Math.max(1, Math.ceil(visiblePoints.length / 7));
  const xLabels = visiblePoints.map((p, i) => {
    const d = new Date(`${p.date}T00:00:00`);
    return { i, text: `${d.getMonth() + 1}/${d.getDate()}`, show: i % labelStep === 0 || i === visiblePoints.length - 1 };
  });

  const rangeLabel = visiblePoints.length
    ? `${visiblePoints[0].date.replace(/-/g, "/")} – ${visiblePoints[visiblePoints.length - 1]?.date.replace(/-/g, "/")}`
    : "暂无日期";

  const hoveredIdx = hover !== null && hover < visiblePoints.length ? hover : null;
  const hovered = hoveredIdx !== null ? visiblePoints[hoveredIdx] : null;
  const hoverX = hoveredIdx !== null ? xAt(hoveredIdx) : 0;
  const tooltipFlip = hoverX > width * 0.7; // 数据点靠右侧时悬浮卡翻到左边

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
        <div className="dashboard-trend-mode" role="tablist" aria-label="显示方式">
          <button type="button" role="tab" aria-selected={mode === "dual"} className={mode === "dual" ? "is-active" : ""} onClick={() => setMode("dual")}>双线对比</button>
          <button type="button" role="tab" aria-selected={mode === "sum"} className={mode === "sum" ? "is-active" : ""} onClick={() => setMode("sum")}>汇总</button>
        </div>
        <div className="dashboard-trend-total"><span>总计</span><strong className={mode === "sum" ? "is-sum" : ""}>{total}</strong></div>
      </div>

      <div className="dashboard-trend-plot" aria-label={`${rangeLabel}业务趋势，总计${total}`}>
        <svg
          className="trend-svg"
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio="none"
          onMouseLeave={() => setHover(null)}
          onMouseMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const relX = ((event.clientX - rect.left) / rect.width) * width;
            const idx = Math.round(((relX - pad.left) / plotW) * Math.max(visiblePoints.length - 1, 1));
            const clamped = Math.min(Math.max(idx, 0), visiblePoints.length - 1);
            setHover(visiblePoints.length ? clamped : null);
          }}
        >
          <defs>
            {activeSeries.map((s) => (
              <linearGradient key={s.key} id={`tgrad-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={s.color} stopOpacity="0.22" />
                <stop offset="70%" stopColor={s.color} stopOpacity="0.05" />
                <stop offset="100%" stopColor={s.color} stopOpacity="0" />
              </linearGradient>
            ))}
          </defs>

          {/* 水平网格 + Y 轴刻度 */}
          {yTicks.map((t) => (
            <g key={`y-${t}`}>
              <line className="trend-grid" x1={pad.left} x2={width - pad.right} y1={yAt(t)} y2={yAt(t)} />
              <text className="trend-tick" x={pad.left - 10} y={yAt(t) + 3.5} textAnchor="end">{t}</text>
            </g>
          ))}

          {/* 底部基线 */}
          <line className="trend-baseline" x1={pad.left} x2={width - pad.right} y1={pad.top + plotH} y2={pad.top + plotH} />

          {/* X 轴刻度 */}
          {xLabels.map(({ i, text, show }) => show && (
            <text key={`x-${i}`} className="trend-tick" x={xAt(i)} y={height - 12} textAnchor="middle">{text}</text>
          ))}

          {/* 面积 + 平滑曲线：切模式/天数时重放入场动画 */}
          <g key={`${mode}-${days}`}>
            {activeSeries.map((s) => {
              const pts = s.values.map((v, i) => ({ x: xAt(i), y: yAt(v) }));
              const line = smoothPath(pts);
              const lastX = pts.length ? pts[pts.length - 1].x : pad.left;
              const firstX = pts.length ? pts[0].x : pad.left;
              const area = `${line} L${lastX.toFixed(2)} ${(pad.top + plotH).toFixed(2)} L${firstX.toFixed(2)} ${(pad.top + plotH).toFixed(2)} Z`;
              return (
                <Fragment key={s.key}>
                  <path className="trend-area" d={area} fill={`url(#tgrad-${s.key})`} />
                  <path className="trend-line" d={line} stroke={s.color} pathLength={1} />
                </Fragment>
              );
            })}
          </g>

          {/* 数据点：常显小点，悬停放大 */}
          {activeSeries.map((s) =>
            s.values.map((v, i) => (
              <circle
                key={`dot-${s.key}-${i}`}
                className="trend-dot"
                cx={xAt(i)}
                cy={yAt(v)}
                r={hoveredIdx === i ? 4.2 : 2.2}
                fill={s.color}
              />
            )),
          )}

          {/* 悬停指示线 */}
          {hoveredIdx !== null && (
            <line className="trend-cursor" x1={hoverX} x2={hoverX} y1={pad.top} y2={pad.top + plotH} />
          )}
        </svg>

        {total === 0 && <div className="dashboard-trend-empty">当前时间范围暂无业务记录</div>}

        {/* 悬浮卡片：跟随数据点，靠右自动左翻 */}
        {hovered && (
          <div
            className="trend-tip"
            style={{ left: `${(hoverX / width) * 100}%` }}
            data-flip={tooltipFlip ? "true" : undefined}
          >
            <strong>{hovered.date.replace(/-/g, "/")}</strong>
            {mode === "sum" ? (
              <span><i style={{ background: "#e1568a" }} />汇总<b>{hovered.inboundCount + hovered.processedCount}</b></span>
            ) : (
              <>
                <span><i style={{ background: "#e23b4e" }} />产品处理<b>{hovered.processedCount}</b></span>
                <span><i style={{ background: "#2f6bff" }} />POD 生成<b>{hovered.inboundCount}</b></span>
              </>
            )}
          </div>
        )}
      </div>

      <div className="dashboard-trend-legend">
        <div>
          {activeSeries.map((s) => (
            <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>
          ))}
        </div>
        <small>数据每分钟自动更新</small>
      </div>
    </section>
  );
}

/* ── 积分消耗占比扇形图 ──────────────────────────────────────── */
const SEGMENT_META = {
  processing: { label: "产品处理", color: "#4c8df6" },
  pod: { label: "POD 生成", color: "#8a5cf0" },
  combo: { label: "商品组合", color: "#f0a137" },
} as const;

type SegmentKey = keyof typeof SEGMENT_META;

function categorizeUsage(entry: BillingUsageEntry): SegmentKey {
  const fk = String(entry.feature_key || "");
  const ref = `${entry.usage_id || ""} ${entry.source_ref || ""}`;
  if (ref.includes("combo-kit:")) return "combo";
  if (fk === "pod_customization.batch" || entry.billing_profile === "pod_random_v1") return "pod";
  return "processing";
}

function polarPoint(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = ((angleDeg - 90) * Math.PI) / 180;
  return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
}

function donutArc(cx: number, cy: number, r: number, startDeg: number, endDeg: number) {
  const s = polarPoint(cx, cy, r, startDeg);
  const e = polarPoint(cx, cy, r, endDeg);
  const large = endDeg - startDeg > 180 ? 1 : 0;
  return `M${s.x.toFixed(2)},${s.y.toFixed(2)} A${r},${r} 0 ${large} 1 ${e.x.toFixed(2)},${e.y.toFixed(2)}`;
}

function DashboardPointsPie() {
  const [segments, setSegments] = useState<Array<{ key: SegmentKey; value: number }>>([]);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    loadBillingUsageHistory({ limit: 100 })
      .then((res) => {
        if (cancelled) return;
        const totals: Record<SegmentKey, number> = { processing: 0, pod: 0, combo: 0 };
        for (const item of res.items ?? []) {
          const pts = Math.max(item.charged_points ?? 0, 0);
          totals[categorizeUsage(item)] += pts;
        }
        setSegments(Object.keys(totals).map((k) => ({ key: k as SegmentKey, value: totals[k as SegmentKey] })));
        setLoaded(true);
        setLoading(false);
      })
      .catch(() => {
        if (!cancelled) { setLoaded(true); setLoading(false); }
      });
    return () => { cancelled = true; };
  }, []);

  const hasData = segments.some((s) => s.value > 0);
  const total = segments.reduce((sum, s) => sum + s.value, 0);

  const cx = 88, cy = 88, r = 66, thickness = 30;
  let angle = 0;
  const arcs = segments
    .filter((s) => s.value > 0)
    .map((s, i) => {
      const sweep = (s.value / total) * 360;
      const start = angle + 2; // 每段留 2° 间隔
      const end = angle + sweep - 2;
      angle += sweep;
      const meta = SEGMENT_META[s.key];
      return { ...s, meta, start, end, sweep };
    });

  return (
    <section className="dashboard-points-card">
      <div className="dashboard-points-heading">
        <span className="iconfont icon-piechart" aria-hidden="true"></span>
        <strong>积分消耗占比</strong>
        <small>产品处理 · POD · 商品组合</small>
      </div>

      <div className="dashboard-points-body">
        <div className="dashboard-points-donut">
          {hasData ? (
            <svg viewBox={`0 0 ${cx * 2} ${cy * 2}`} width={176} height={176}>
              {arcs.map((a) => (
                <path key={a.key} d={donutArc(cx, cy, r, a.start, a.end)}
                  fill="none" stroke={a.meta.color} strokeWidth={thickness} strokeLinecap="round"
                  className="dashboard-pie-seg" />
              ))}
              <text className="dashboard-pie-total" x={cx} y={cy - 2} textAnchor="middle">{total.toLocaleString()}</text>
              <text className="dashboard-pie-total-label" x={cx} y={cy + 16} textAnchor="middle">积分</text>
            </svg>
          ) : (
            <div className="dashboard-points-empty"><span className="iconfont icon-fund" aria-hidden="true"></span>{loading ? "加载中…" : "暂无消耗数据"}</div>
          )}
        </div>

        <ul className="dashboard-points-legend">
          {segments.map((s) => {
            const pct = total ? Math.round((s.value / total) * 100) : 0;
            return (
              <li key={s.key}>
                <i style={{ background: SEGMENT_META[s.key].color }} />
                <span>{SEGMENT_META[s.key].label}</span>
                <b>{pct}%</b>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}

/* ── 数据概览 ────────────────────────────────────────────────── */
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
  const productSites = stats?.productSites.length ? stats.productSites.join(" / ") : "暂无产品站点";

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
          <h2>数据概览</h2>
        </div>
        <span className="muted"><span className="iconfont icon-linechart" aria-hidden="true"></span> 实时统计</span>
      </div>

      <div className="stats-container">
        <div className="stats-grid">
          <button className="stats-card" onClick={() => onOpenModule("profit_activity_products")} title="点击打开产品库">
            <span className="stats-icon iconfont icon-container"></span>
            <span className="stats-label">产品库产品总数</span>
            <strong className="stats-value">{format(stats?.productCount)}</strong>
            <span className="stats-note">{productSites}</span>
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
      </div>

      <div className="dashboard-chart-layout">
        <DashboardTrendChart points={stats?.trend ?? []} />
        <DashboardPointsPie />
      </div>
    </section>
  );
}
