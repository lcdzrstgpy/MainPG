import { useCallback, useEffect, useMemo, useState } from "react";

import type { WorkspaceModuleId } from "../../../app/navigation/modules";
import { AppleAppGlyph } from "../../../shared/components/AppleAppGlyph";
import type { DashboardOverview, DashboardRecentTask, DashboardTrendPoint } from "../api/dashboardApi";
import { getDashboardOverview } from "../api/dashboardApi";
import "./../styles/dashboardStats.css";

type DashboardStatsProps = {
  onOpenModule: (id: WorkspaceModuleId) => void;
  variant?: "classic" | "apple";
};

type KpiTone = "blue" | "cyan" | "violet" | "green" | "amber" | "rose";

type KpiCard = {
  key: string;
  label: string;
  value: number;
  note: string;
  tone: KpiTone;
  glyph: string;
  module: WorkspaceModuleId;
};

type DistItem = { key: string; label: string; count: number; tone?: string };

const REFRESH_MS = 60_000;
const TREND_RANGES = [7, 14, 30] as const;
type TrendRange = (typeof TREND_RANGES)[number];

const TASK_STATUS_TONE: Record<string, string> = {
  completed: "success",
  partial_failure: "warning",
  failed: "danger",
  cancelled: "muted",
  running: "info",
  queued: "info",
};

const CHINA_TIME_ZONE = "Asia/Shanghai";

/** 后端返回的时间可能是 ISO（带时区）或 "YYYY-MM-DD HH:MM:SS"（UTC 无时区），统一按 UTC 解析后转北京时间。 */
function formatBeijingTime(value: string): string {
  if (!value) return "—";
  const iso = /(Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value.replace(" ", "T")}Z`;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: CHINA_TIME_ZONE,
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

function buildKpis(overview: DashboardOverview): KpiCard[] {
  const { kpis, site_distribution: sites } = overview;
  return [
    {
      key: "product_total",
      label: "产品库总数",
      value: kpis.product_total,
      note: `覆盖 ${sites.length} 个站点`,
      tone: "blue",
      glyph: "profit_activity_products",
      module: "profit_activity_products",
    },
    {
      key: "today_inbound",
      label: "今日入库",
      value: kpis.today_inbound,
      note: `产品库累计 ${kpis.product_total} 个`,
      tone: "cyan",
      glyph: "daily_selection",
      module: "profit_activity_products",
    },
    {
      key: "today_task_count",
      label: "今日处理批次",
      value: kpis.today_task_count,
      note: `累计 ${kpis.task_total} 个批次`,
      tone: "violet",
      glyph: "product_processing",
      module: "product_processing",
    },
    {
      key: "today_processed_products",
      label: "今日成功产出",
      value: kpis.today_processed_products,
      note: `失败 ${kpis.today_failed_products} 个`,
      tone: "green",
      glyph: "product_workflow",
      module: "product_processing_history",
    },
    {
      key: "active_tasks",
      label: "进行中任务",
      value: kpis.active_tasks,
      note: `待处理草稿 ${kpis.drafts_pending} 条`,
      tone: "amber",
      glyph: "product_processing_tasks",
      module: "product_processing_tasks",
    },
    {
      key: "attention_required",
      label: "需关注",
      value: kpis.attention_required,
      note: "需要人工确认的处理项",
      tone: "rose",
      glyph: "product_processing_history",
      module: "product_processing_history",
    },
  ];
}

export function DashboardStats({ onOpenModule, variant = "classic" }: DashboardStatsProps) {
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [range, setRange] = useState<TrendRange>(30);

  const load = useCallback(async () => {
    try {
      const next = await getDashboardOverview();
      setOverview(next);
      setError(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "工作台数据加载失败");
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  const kpis = useMemo(() => (overview ? buildKpis(overview) : []), [overview]);
  const trendPoints = useMemo(
    () => (overview ? overview.trend.points.slice(-range) : []),
    [overview, range],
  );
  const statusItems = useMemo<DistItem[]>(
    () => (overview ? overview.task_status.map((item) => ({ ...item, key: item.status, tone: TASK_STATUS_TONE[item.status] ?? "info" })) : []),
    [overview],
  );
  const siteItems = useMemo<DistItem[]>(
    () => (overview ? overview.site_distribution.map((item) => ({ key: item.site_code, label: item.label, count: item.count, tone: "blue" })) : []),
    [overview],
  );

  const isApple = variant === "apple";

  if (!overview) {
    return (
      <div className={`dash-board${isApple ? " dash-board--apple" : ""}`}>
        <section className="dash-kpi-grid" aria-busy="true">
          {Array.from({ length: 6 }, (_, index) => (
            <div key={index} className="dash-kpi dash-skeleton" />
          ))}
        </section>
        <p className="dash-hint">{error ?? "正在加载工作台数据…"}</p>
      </div>
    );
  }

  return (
    <div className={`dash-board${isApple ? " dash-board--apple" : ""}`}>
      <section className="dash-kpi-grid" aria-label="核心指标">
        {kpis.map((card) => (
          <button
            key={card.key}
            type="button"
            className={`dash-kpi is-${card.tone}`}
            onClick={() => onOpenModule(card.module)}
          >
            <span className="dash-kpi-icon" aria-hidden>
              <AppleAppGlyph name={card.glyph} />
            </span>
            <span className="dash-kpi-body">
              <span className="dash-kpi-label">{card.label}</span>
              <strong className="dash-kpi-value">{card.value}</strong>
              <em className="dash-kpi-note">{card.note}</em>
            </span>
          </button>
        ))}
      </section>

      <div className="dash-panels">
        <section className="dash-panel dash-panel--trend">
          <header className="dash-panel-head">
            <div className="dash-panel-title">
              <h3>处理趋势</h3>
              <p>
                近 {range} 天 · 每日处理批次与入库产品数量（
                {overview.trend.start.slice(5)} ~ {overview.trend.end.slice(5)}）
              </p>
            </div>
            <div className="dash-range" role="group" aria-label="趋势统计范围">
              {TREND_RANGES.map((days) => (
                <button
                  key={days}
                  type="button"
                  className={days === range ? "is-active" : ""}
                  onClick={() => setRange(days)}
                >
                  {days} 天
                </button>
              ))}
            </div>
          </header>
          <TrendChart points={trendPoints} />
          <footer className="dash-trend-legend">
            <span className="dash-legend-item is-violet">处理批次</span>
            <span className="dash-legend-item is-cyan">入库产品</span>
            <span className="dash-trend-total">
              区间合计：处理 {trendPoints.reduce((sum, point) => sum + point.processed, 0)} 个批次 · 入库{" "}
              {trendPoints.reduce((sum, point) => sum + point.inbound, 0)} 个产品
            </span>
          </footer>
        </section>

        <section className="dash-panel dash-panel--dist">
          <header className="dash-panel-head">
            <div className="dash-panel-title">
              <h3>任务状态分布</h3>
              <p>累计 {overview.kpis.task_total} 个处理批次</p>
            </div>
          </header>
          <DistributionList items={statusItems} emptyText="暂无处理批次" />

          <header className="dash-panel-head is-sub">
            <div className="dash-panel-title">
              <h3>站点分布</h3>
              <p>产品库产品按站点统计</p>
            </div>
          </header>
          <DistributionList items={siteItems} emptyText="产品库暂无数据" />
        </section>
      </div>

      <section className="dash-panel dash-panel--tasks">
        <header className="dash-panel-head">
          <div className="dash-panel-title">
            <h3>最近处理任务</h3>
            <p>最近 {overview.recent_tasks.length} 个批次的处理结果</p>
          </div>
          <button type="button" className="dash-panel-link" onClick={() => onOpenModule("product_processing_history")}>
            查看全部
          </button>
        </header>
        {overview.recent_tasks.length ? (
          <ul className="dash-task-list">
            {overview.recent_tasks.map((task) => (
              <TaskRow key={task.task_id} task={task} onOpen={() => onOpenModule("product_processing_history")} />
            ))}
          </ul>
        ) : (
          <p className="dash-empty">还没有处理记录，去「AI 产品处理」创建第一个批次吧。</p>
        )}
      </section>
    </div>
  );
}

function TaskRow({ task, onOpen }: { task: DashboardRecentTask; onOpen: () => void }) {
  const tone = TASK_STATUS_TONE[task.status] ?? "info";
  return (
    <li>
      <button type="button" className="dash-task-row" onClick={onOpen}>
        <span className="dash-task-main">
          <strong>{task.title || `批次 #${task.task_id}`}</strong>
          <em>#{task.task_id}</em>
        </span>
        <span className={`dash-status is-${tone}`}>{task.status_label}</span>
        <span className="dash-task-progress">
          成功 {task.success_count} / {task.total_count}
          {task.failed_count > 0 ? <i>· 失败 {task.failed_count}</i> : null}
        </span>
        <span className="dash-task-time">{formatBeijingTime(task.created_at)}</span>
      </button>
    </li>
  );
}

function DistributionList({ items, emptyText }: { items: DistItem[]; emptyText: string }) {
  const total = items.reduce((sum, item) => sum + item.count, 0);
  if (!items.length || total === 0) {
    return <p className="dash-empty">{emptyText}</p>;
  }
  return (
    <ul className="dash-dist-list">
      {items.map((item) => {
        const percent = total ? Math.round((item.count / total) * 100) : 0;
        return (
          <li key={item.key} className={`dash-dist-row is-${item.tone ?? "blue"}`}>
            <span className="dash-dist-label">{item.label}</span>
            <span className="dash-dist-bar" aria-hidden>
              <i style={{ width: `${Math.max(percent, 2)}%` }} />
            </span>
            <span className="dash-dist-value">
              {item.count}
              <em>{percent}%</em>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

const CHART_WIDTH = 720;
const CHART_HEIGHT = 236;
const CHART_PAD = { top: 18, right: 16, bottom: 26, left: 38 };

function TrendChart({ points }: { points: DashboardTrendPoint[] }) {
  const [hover, setHover] = useState<number | null>(null);

  const maxValue = Math.max(1, ...points.flatMap((point) => [point.inbound, point.processed]));
  const niceMax = niceCeil(maxValue);
  const innerWidth = CHART_WIDTH - CHART_PAD.left - CHART_PAD.right;
  const innerHeight = CHART_HEIGHT - CHART_PAD.top - CHART_PAD.bottom;
  const step = points.length > 1 ? innerWidth / (points.length - 1) : 0;

  const xAt = (index: number) => CHART_PAD.left + index * step;
  const yAt = (value: number) => CHART_PAD.top + innerHeight - (value / niceMax) * innerHeight;

  const line = (key: "inbound" | "processed") =>
    monotonePath(points.map((point, index) => [xAt(index), yAt(point[key])]));

  const gridValues = [0, 0.25, 0.5, 0.75, 1].map((ratio) => Math.round(niceMax * ratio));
  const labelEvery = Math.max(1, Math.ceil(points.length / 6));
  const active = hover !== null ? points[hover] : null;

  return (
    <div className="dash-trend-chart">
      <svg viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`} role="img" aria-label="近 30 天处理趋势">
        {gridValues.map((value) => (
          <g key={value}>
            <line x1={CHART_PAD.left} x2={CHART_WIDTH - CHART_PAD.right} y1={yAt(value)} y2={yAt(value)} className="dash-grid-line" />
            <text x={CHART_PAD.left - 8} y={yAt(value) + 4} className="dash-axis-text" textAnchor="end">
              {value}
            </text>
          </g>
        ))}

        {points.map((point, index) =>
          index % labelEvery === 0 || index === points.length - 1 ? (
            <text key={point.date} x={xAt(index)} y={CHART_HEIGHT - 8} className="dash-axis-text" textAnchor="middle">
              {point.label}
            </text>
          ) : null,
        )}

        <path d={line("processed")} className="dash-line is-processed" />
        <path d={line("inbound")} className="dash-line is-inbound" />

        {active ? (
          <g>
            <line x1={xAt(hover!)} x2={xAt(hover!)} y1={CHART_PAD.top} y2={CHART_PAD.top + innerHeight} className="dash-hover-line" />
            <circle cx={xAt(hover!)} cy={yAt(active.processed)} r={4} className="dash-point is-processed" />
            <circle cx={xAt(hover!)} cy={yAt(active.inbound)} r={4} className="dash-point is-inbound" />
          </g>
        ) : null}

        <rect
          x={CHART_PAD.left}
          y={CHART_PAD.top}
          width={innerWidth}
          height={innerHeight}
          fill="transparent"
          onMouseLeave={() => setHover(null)}
          onMouseMove={(event) => {
            const rect = event.currentTarget.getBoundingClientRect();
            const ratio = (event.clientX - rect.left) / Math.max(rect.width, 1);
            setHover(Math.min(points.length - 1, Math.max(0, Math.round(ratio * (points.length - 1)))));
          }}
        />
      </svg>

      {active ? (
        <div className="dash-trend-tip" style={{ left: `${(xAt(hover!) / CHART_WIDTH) * 100}%` }}>
          <strong>{active.date}</strong>
          <span className="is-violet">处理批次 {active.processed}</span>
          <span className="is-cyan">入库产品 {active.inbound}</span>
        </div>
      ) : null}
    </div>
  );
}

function niceCeil(value: number): number {
  if (value <= 5) return 5;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const factor of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    const candidate = magnitude * factor;
    if (candidate >= value) return candidate;
  }
  return magnitude * 10;
}

/**
 * 单调三次插值（Fritsch–Carlson）转成 cubic Bézier 路径，让折线变成平滑曲线。
 *
 * 不用直连线段是因为尖角生硬；但也不能直接用普通样条——它会在峰谷处过冲，
 * 把 0 拉到负值、或在孤立尖峰两侧造出并不存在的假峰。单调插值保证曲线
 * 始终被夹在相邻数据点之间，平滑的同时不改变数据本身的形状。
 */
function monotonePath(coords: Array<[number, number]>): string {
  const count = coords.length;
  if (count === 0) return "";
  if (count === 1) return `M${coords[0][0].toFixed(1)},${coords[0][1].toFixed(1)}`;

  const slopes: number[] = [];
  for (let index = 0; index < count - 1; index += 1) {
    const [x0, y0] = coords[index];
    const [x1, y1] = coords[index + 1];
    slopes.push(x1 === x0 ? 0 : (y1 - y0) / (x1 - x0));
  }

  const tangents: number[] = new Array<number>(count);
  tangents[0] = slopes[0];
  tangents[count - 1] = slopes[count - 2];
  for (let index = 1; index < count - 1; index += 1) {
    const previous = slopes[index - 1];
    const next = slopes[index];
    // 斜率反号说明该点是极值，切线取 0，曲线才会在这里回旋而不是冲过去。
    tangents[index] = previous * next <= 0 ? 0 : (previous + next) / 2;
  }
  // Fritsch–Carlson 限幅：切线不得超过相邻割线斜率的 3 倍，否则仍会过冲。
  for (let index = 0; index < count - 1; index += 1) {
    const slope = slopes[index];
    if (slope === 0) {
      tangents[index] = 0;
      tangents[index + 1] = 0;
      continue;
    }
    const alpha = tangents[index] / slope;
    const beta = tangents[index + 1] / slope;
    const magnitude = alpha * alpha + beta * beta;
    if (magnitude > 9) {
      const scale = 3 / Math.sqrt(magnitude);
      tangents[index] = scale * alpha * slope;
      tangents[index + 1] = scale * beta * slope;
    }
  }

  const [startX, startY] = coords[0];
  let path = `M${startX.toFixed(1)},${startY.toFixed(1)}`;
  for (let index = 0; index < count - 1; index += 1) {
    const [x0, y0] = coords[index];
    const [x1, y1] = coords[index + 1];
    // Hermite → Bézier：控制点落在两端切线上，距离取区间长度的三分之一。
    const third = (x1 - x0) / 3;
    const c1x = x0 + third;
    const c1y = y0 + tangents[index] * third;
    const c2x = x1 - third;
    const c2y = y1 - tangents[index + 1] * third;
    path += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
  }
  return path;
}
