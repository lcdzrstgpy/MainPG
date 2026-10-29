import { useId, useMemo } from "react";

/** 图表形态：折线 / 平滑曲线 / 面积 / 分组柱状 / 堆叠柱状 / 堆叠面积。 */
export type ChartKind = "line" | "spline" | "area" | "bar" | "stacked" | "stackedArea";

export type TrendSeries = {
  key: string;
  label: string;
  color: string;
  values: number[];
};

const WIDTH = 860;
const HEIGHT = 280;
const PAD = { top: 18, right: 20, bottom: 38, left: 64 };
const PLOT_W = WIDTH - PAD.left - PAD.right;
const PLOT_H = HEIGHT - PAD.top - PAD.bottom;

function niceMax(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 1;
  const base = 10 ** Math.floor(Math.log10(value));
  const scaled = value / base;
  const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
  return step * base;
}

function tickText(value: number): string {
  const abs = Math.abs(value);
  if (abs >= 1_0000_0000) return `${(value / 1_0000_0000).toFixed(1)}亿`;
  if (abs >= 10000) return `${(value / 10000).toFixed(abs >= 100000 ? 0 : 1)}万`;
  if (abs >= 1000) return `${(value / 1000).toFixed(1)}k`;
  if (Number.isInteger(value)) return String(value);
  return value.toFixed(1);
}

/** Catmull-Rom 转三次贝塞尔，得到平滑曲线。 */
function splinePath(points: Array<[number, number]>): string {
  if (points.length < 2) return points.length ? `M ${points[0][0]} ${points[0][1]}` : "";
  let d = `M ${points[0][0]} ${points[0][1]}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const p0 = points[index - 1] || points[index];
    const p1 = points[index];
    const p2 = points[index + 1];
    const p3 = points[index + 2] || p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${c1x.toFixed(2)} ${c1y.toFixed(2)}, ${c2x.toFixed(2)} ${c2y.toFixed(2)}, ${p2[0].toFixed(2)} ${p2[1].toFixed(2)}`;
  }
  return d;
}

function linePath(points: Array<[number, number]>): string {
  return points.map((point, index) => `${index === 0 ? "M" : "L"} ${point[0]} ${point[1]}`).join(" ");
}

export function TrendChart({
  labels,
  series,
  kind,
  height = HEIGHT,
  formatValue,
  emptyText = "暂无可视化的数据",
}: {
  labels: string[];
  series: TrendSeries[];
  kind: ChartKind;
  height?: number;
  formatValue?: (value: number) => string;
  emptyText?: string;
}) {
  const gradientId = useId().replace(/[:]/g, "");
  const format = formatValue || ((value: number) => tickText(value));
  const stackedArea = kind === "stackedArea";
  const stacked = kind === "stacked" || stackedArea;
  const isBar = kind === "bar" || kind === "stacked";

  const model = useMemo(() => {
    const columns = Math.max(labels.length, 1);
    const totals = labels.map((_, columnIndex) =>
      stacked
        ? series.reduce((sum, item) => sum + Math.max(0, item.values[columnIndex] || 0), 0)
        : 0,
    );
    const flatValues = series.flatMap((item) => item.values);
    const maxValue = Math.max(0, ...(stacked ? totals : flatValues));
    const minValue = Math.min(0, ...flatValues);
    const top = niceMax(maxValue || 1);
    const bottom = minValue < 0 ? -niceMax(Math.abs(minValue)) : 0;
    const span = top - bottom || 1;
    const y = (value: number) => PAD.top + ((top - value) / span) * PLOT_H;
    const bandStep = labels.length > 1 ? PLOT_W / (labels.length - 1) : PLOT_W;
    const bandCenter = (index: number) =>
      labels.length > 1 ? PAD.left + index * bandStep : PAD.left + PLOT_W / 2;
    const bandWidth = Math.min(64, (PLOT_W / columns) * 0.62);
    const ticks = Array.from({ length: 5 }, (_, index) => {
      const value = bottom + (span * index) / 4;
      return { value, y: y(value) };
    });
    return { y, bandCenter, bandWidth, ticks, top, bottom, totals };
  }, [labels, series, stacked]);

  if (!labels.length || !series.length) {
    return <div className="op-chart-empty">{emptyText}</div>;
  }

  const labelStep = Math.max(1, Math.ceil(labels.length / 10));

  return (
    <div className="op-chart" style={{ height }}>
      <div className="op-legend">
        {series.map((item) => (
          <span key={item.key} className="op-legend-item">
            <i style={{ background: item.color }} />
            {item.label}
          </span>
        ))}
      </div>
      <svg
        className="op-chart-svg"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        preserveAspectRatio="xMidYMid meet"
        role="img"
      >
        <defs>
          {series.map((item) => (
            <linearGradient key={item.key} id={`${gradientId}-${item.key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={item.color} stopOpacity="0.34" />
              <stop offset="100%" stopColor={item.color} stopOpacity="0.02" />
            </linearGradient>
          ))}
        </defs>
        {model.ticks.map((tick) => (
          <g key={tick.value}>
            <line
              className="op-chart-gridline"
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={tick.y}
              y2={tick.y}
            />
            <text className="op-chart-axis" x={PAD.left - 10} y={tick.y + 4} textAnchor="end">
              {tickText(tick.value)}
            </text>
          </g>
        ))}
        {labels.map((label, index) =>
          index % labelStep === 0 ? (
            <text
              key={label}
              className="op-chart-axis"
              x={model.bandCenter(index)}
              y={HEIGHT - 14}
              textAnchor="middle"
            >
              {label}
            </text>
          ) : null,
        )}

        {stackedArea
          ? (() => {
              let previous = labels.map(() => 0);
              return series.map((item) => {
                const lower = [...previous];
                const upper = previous.map(
                  (value, index) => value + Math.max(0, item.values[index] || 0),
                );
                previous = upper;
                const topPoints = upper.map(
                  (value, index) => `${model.bandCenter(index)} ${model.y(value)}`,
                );
                const bottomPoints = lower
                  .map((value, index) => `${model.bandCenter(index)} ${model.y(value)}`)
                  .reverse();
                return (
                  <path
                    key={item.key}
                    d={`M ${topPoints.join(" L ")} L ${bottomPoints.join(" L ")} Z`}
                    fill={item.color}
                    fillOpacity={0.46}
                    stroke={item.color}
                    strokeWidth={1.2}
                    strokeLinejoin="round"
                  >
                    <title>{item.label}</title>
                  </path>
                );
              });
            })()
          : null}

        {isBar
          ? labels.map((label, columnIndex) => {
              const center = model.bandCenter(columnIndex);
              if (stacked) {
                let cursor = model.y(0);
                return (
                  <g key={label}>
                    {series.map((item) => {
                      const value = Math.max(0, item.values[columnIndex] || 0);
                      const barHeight = Math.abs(model.y(value) - model.y(0));
                      cursor -= barHeight;
                      return barHeight <= 0 ? null : (
                        <rect
                          key={item.key}
                          x={center - model.bandWidth / 2}
                          y={cursor}
                          width={model.bandWidth}
                          height={barHeight}
                          rx={3}
                          fill={item.color}
                          fillOpacity={0.85}
                        >
                          <title>{`${label} · ${item.label}：${format(value)}`}</title>
                        </rect>
                      );
                    })}
                  </g>
                );
              }
              const slot = model.bandWidth / series.length;
              return (
                <g key={label}>
                  {series.map((item, seriesIndex) => {
                    const value = item.values[columnIndex] || 0;
                    const zero = model.y(0);
                    const y = model.y(value);
                    const barHeight = Math.abs(y - zero);
                    return barHeight <= 0 ? null : (
                      <rect
                        key={item.key}
                        x={center - model.bandWidth / 2 + slot * seriesIndex + 1}
                        y={Math.min(y, zero)}
                        width={Math.max(2, slot - 2)}
                        height={barHeight}
                        rx={3}
                        fill={item.color}
                        fillOpacity={0.85}
                      >
                        <title>{`${label} · ${item.label}：${format(value)}`}</title>
                      </rect>
                    );
                  })}
                </g>
              );
            })
          : series.map((item) => {
              const points = item.values.map(
                (value, index) => [model.bandCenter(index), model.y(value)] as [number, number],
              );
              const path = kind === "spline" || kind === "area" ? splinePath(points) : linePath(points);
              const areaPath = `${path} L ${points[points.length - 1][0]} ${model.y(model.bottom)} L ${points[0][0]} ${model.y(model.bottom)} Z`;
              return (
                <g key={item.key} className="op-chart-series">
                  {kind === "area" ? (
                    <path d={areaPath} fill={`url(#${gradientId}-${item.key})`} stroke="none" />
                  ) : null}
                  <path
                    d={path}
                    fill="none"
                    stroke={item.color}
                    strokeWidth={2.2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  {item.values.map((value, index) => (
                    <circle
                      key={`${item.key}-${index}`}
                      cx={model.bandCenter(index)}
                      cy={model.y(value)}
                      r={3.2}
                      fill="var(--op-surface, #fff)"
                      stroke={item.color}
                      strokeWidth={1.8}
                    >
                      <title>{`${labels[index]} · ${item.label}：${format(value)}`}</title>
                    </circle>
                  ))}
                </g>
              );
            })}
      </svg>
    </div>
  );
}
