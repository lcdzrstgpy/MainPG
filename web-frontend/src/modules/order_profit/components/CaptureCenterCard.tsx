import { captureStatusChip, RETRYABLE_CAPTURE_STEPS, summarizeCaptureResults } from "../data/captureTaskModel";
import { beijingTime } from "../format";
import type { CaptureRunSummary } from "../types";

const RUN_STEP_STATE: Record<string, string> = {
  ok: "完成",
  skipped: "跳过",
  failed: "失败",
  running: "进行中",
  pending: "等待",
};

const RESOURCE_LABELS: Record<string, string> = {
  store: "店铺首页",
  traffic: "商品流量",
  sales: "销量分析",
  finance: "对账中心",
  settlement: "结算数据",
  orders: "订单列表",
};

function runModeLabel(run: CaptureRunSummary): string {
  if (run.command_type === "temu_capture_all") return "全量采集";
  if (run.command_type === "temu_capture_incremental") return "增量采集";
  if (run.command_type === "temu_capture_resource") {
    return run.resource ? `补采 ${RESOURCE_LABELS[run.resource] || run.resource}` : "单步补采";
  }
  return "采集";
}

function runFallbackText(run: CaptureRunSummary): string {
  if (run.status === "queued" || run.status === "sent" || run.status === "running") return "进行中";
  if (run.ok) return "已完成";
  return run.error || "未完成";
}

/** 采集中心：保留每次采集的步骤结果，失败项可一键批量补采。 */
export function CaptureCenterCard({
  runs,
  loading,
  busy,
  onRetry,
  onRefresh,
}: {
  runs: CaptureRunSummary[];
  loading: boolean;
  busy: boolean;
  onRetry: (run: CaptureRunSummary) => void;
  onRefresh: () => void;
}) {
  return (
    <div className="op-card op-capture-result">
      <div className="op-panel-head">
        <strong>采集中心</strong>
        <span className="op-chip">{loading ? "读取中…" : `${runs.length} 次`}</span>
        <button type="button" className="op-link" disabled={loading} onClick={onRefresh}>
          刷新
        </button>
      </div>

      {!runs.length ? (
        <p className="op-hint">
          还没有采集记录。同步一次后，每一步的结果会留在这里，失败项可以一键补采。
        </p>
      ) : (
        <div className="op-run-list">
          {runs.map((run) => {
            const chip = captureStatusChip(run.status, run.ok, run.results);
            const summary = summarizeCaptureResults(run.results);
            const failed = run.results.filter(
              (row) => row.state === "failed" && RETRYABLE_CAPTURE_STEPS.has(row.key),
            );
            return (
              <article className="op-run-row" key={run.command_id}>
                <div className="op-run-head">
                  <strong>{runModeLabel(run)}</strong>
                  <span className={`op-chip ${chip.tone}`}>{chip.label}</span>
                  {run.shop_name ? <span className="op-run-shop">{run.shop_name}</span> : null}
                  <time>{beijingTime(run.created_at).slice(5, 16)}</time>
                </div>

                {run.results.length ? (
                  <div className="op-run-steps">
                    {run.results.map((row) => (
                      <span
                        key={row.key}
                        className={`op-run-step is-${row.state}`}
                        title={row.detail || ""}
                      >
                        {row.label || row.key} · {RUN_STEP_STATE[row.state] || row.state}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="op-run-text">{run.status_text || runFallbackText(run)}</p>
                )}

                <div className="op-run-foot">
                  <span>
                    {summary.total
                      ? `成功 ${summary.ok} · 跳过 ${summary.skipped} · 失败 ${summary.failed}`
                      : run.status_text || runFallbackText(run)}
                  </span>
                  {failed.length ? (
                    <button
                      type="button"
                      className="op-btn op-btn-sm"
                      disabled={busy}
                      onClick={() => onRetry(run)}
                    >
                      补采失败项（{failed.length}）
                    </button>
                  ) : null}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </div>
  );
}
