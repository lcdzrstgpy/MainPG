import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { shortReasonLabel } from "../data/errorExplain";
import { localStore } from "../data/localStore";

/**
 * 失败原因趋势：把本机失败台账按原因归堆，分三个时间窗看涨跌。
 *
 * 用途（2026-11-21 确认要做）：治失败要靠数据——哪类原因在变多、治完一类
 * 有没有回落、下一步该治哪个。此前只有清单，看不出方向。
 *
 * 数据来源：本机失败台账（发布失败才记），按 `at`（zh-CN 时间串）分窗。
 */

/** 三个时间窗：近 7 天 / 上一个 7 天 / 8~30 天 */
const WINDOWS: Array<{ key: string; label: string; fromDays: number; toDays: number }> = [
  { key: "recent", label: "近 7 天", fromDays: 0, toDays: 7 },
  { key: "prev", label: "上周", fromDays: 7, toDays: 14 },
  { key: "older", label: "8~30 天", fromDays: 14, toDays: 30 },
];

type TrendRow = {
  label: string;
  counts: Record<string, number>;
  total: number;
};

function parseAt(text: string): number {
  const at = new Date(text).getTime();
  return Number.isFinite(at) ? at : 0;
}

export function FailureTrendDialog({ onClose }: { onClose: () => void }) {
  const [rows, setRows] = useState<TrendRow[]>([]);
  const [covered, setCovered] = useState(0);

  useEffect(() => {
    const now = Date.now();
    const dayMs = 24 * 60 * 60 * 1000;
    const buckets = new Map<string, Record<string, number>>();
    const failures = localStore.failures();
    let inWindow = 0;
    for (const item of failures) {
      const ageDays = (now - parseAt(item.at)) / dayMs;
      if (!Number.isFinite(ageDays) || ageDays < 0 || ageDays >= 30) continue;
      inWindow += 1;
      const label = shortReasonLabel(item.reason) || "未知原因";
      const counts = buckets.get(label) ?? { recent: 0, prev: 0, older: 0 };
      const window = WINDOWS.find((w) => ageDays >= w.fromDays && ageDays < w.toDays);
      if (window) counts[window.key] += 1;
      buckets.set(label, counts);
    }
    const list: TrendRow[] = Array.from(buckets.entries()).map(([label, counts]) => ({
      label,
      counts,
      total: counts.recent + counts.prev + counts.older,
    }));
    list.sort((a, b) => b.total - a.total);
    setRows(list);
    setCovered(inWindow);
  }, []);

  const maxTotal = useMemo(() => Math.max(1, ...rows.map((row) => row.total)), [rows]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return createPortal(
    <div className="temu-edit-backdrop" onClick={onClose}>
      <div
        className="temu-edit-dialog temu-trend-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="失败原因趋势"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="temu-edit-head">
          <div>
            <strong>失败原因趋势</strong>
            <span>近 30 天失败按原因归堆，分三个时间窗看涨跌；趋势向上的一类优先治理</span>
          </div>
          <button type="button" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="temu-trend-body">
          {rows.length ? (
            <>
              <p className="temu-muted">近 30 天共 {covered} 条失败，归为 {rows.length} 类原因：</p>
              <table className="temu-trend-table">
                <thead>
                  <tr>
                    <th>原因</th>
                    <th>数量</th>
                    <th>近 7 天</th>
                    <th>上周</th>
                    <th>8~30 天</th>
                    <th>趋势</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => {
                    const rising = row.counts.recent > row.counts.prev;
                    const falling = row.counts.recent < row.counts.prev;
                    return (
                      <tr key={row.label}>
                        <td className="temu-trend-label" title={row.label}>
                          {row.label}
                        </td>
                        <td>
                          <div className="temu-trend-bar" aria-hidden="true">
                            <span style={{ width: `${Math.round((row.total / maxTotal) * 100)}%` }} />
                          </div>
                          {row.total}
                        </td>
                        <td>{row.counts.recent || "—"}</td>
                        <td>{row.counts.prev || "—"}</td>
                        <td>{row.counts.older || "—"}</td>
                        <td>{rising ? "↑ 上升" : falling ? "↓ 回落" : "→ 持平"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          ) : (
            <p className="temu-muted">近 30 天没有失败记录。</p>
          )}
        </div>

        <footer className="temu-edit-foot">
          <button type="button" className="temu-btn" onClick={onClose}>
            关闭
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
