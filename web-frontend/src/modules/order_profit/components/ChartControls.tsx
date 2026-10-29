import { useEffect, useRef, useState } from "react";

import type { ChartKind } from "./TrendChart";

export const CHART_KINDS: Array<{ value: ChartKind; label: string }> = [
  { value: "line", label: "折线" },
  { value: "spline", label: "曲线" },
  { value: "area", label: "面积" },
  { value: "bar", label: "柱状" },
  { value: "stacked", label: "堆叠" },
];

/** 图表形态切换：折线 / 曲线 / 面积 / 柱状 / 堆叠。 */
export function ChartKindSwitch({
  value,
  onChange,
}: {
  value: ChartKind;
  onChange: (value: ChartKind) => void;
}) {
  return (
    <div className="op-seg" role="tablist" aria-label="图表形态">
      {CHART_KINDS.map((item) => (
        <button
          key={item.value}
          type="button"
          role="tab"
          aria-selected={value === item.value}
          className={`op-seg-item ${value === item.value ? "is-active" : ""}`}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function monthLabel(month: string): string {
  const [year, rest] = String(month || "").split("-");
  if (!rest) return month;
  return `${year.slice(2)}年${Number(rest)}月`;
}

/** 月份多选：按钮 + 浮层复选框，最多可选 limit 个月。 */
export function MonthMultiSelect({
  months,
  value,
  onChange,
  limit = 12,
  placeholder = "选择月份",
}: {
  months: string[];
  value: string[];
  onChange: (next: string[]) => void;
  limit?: number;
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (boxRef.current && !boxRef.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  const toggle = (month: string) => {
    if (value.includes(month)) {
      const next = value.filter((item) => item !== month);
      onChange(next.length ? next : value);
      return;
    }
    const next = [...value, month].sort().slice(-limit);
    onChange(next);
  };

  const summary = !value.length
    ? placeholder
    : value.length === 1
      ? monthLabel(value[0])
      : `${monthLabel(value[0])} 等 ${value.length} 个月`;

  return (
    <div className="op-multi" ref={boxRef}>
      <button
        type="button"
        className={`op-multi-trigger ${open ? "is-open" : ""}`}
        onClick={() => setOpen((current) => !current)}
        aria-haspopup="listbox"
        aria-expanded={open}
      >
        <span>{summary}</span>
        <small>{open ? "收起" : "多选"}</small>
      </button>
      {open ? (
        <div className="op-multi-panel" role="listbox" aria-multiselectable="true">
          {!months.length ? <div className="op-multi-empty">还没有订单月份数据</div> : null}
          {months.map((month) => {
            const checked = value.includes(month);
            return (
              <label key={month} className={`op-multi-option ${checked ? "is-checked" : ""}`}>
                <input type="checkbox" checked={checked} onChange={() => toggle(month)} />
                <span>{monthLabel(month)}</span>
                <small>{month}</small>
              </label>
            );
          })}
          <div className="op-multi-actions">
            <button
              type="button"
              className="op-link"
              onClick={() => onChange(months.slice(0, limit))}
            >
              最近 {Math.min(months.length, limit)} 个月
            </button>
            <button type="button" className="op-link" onClick={() => setOpen(false)}>
              完成
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
