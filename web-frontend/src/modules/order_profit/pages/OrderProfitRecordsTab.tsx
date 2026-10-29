import { useEffect, useState } from "react";

import { orderProfitApi } from "../api/orderProfitApi";
import { beijingTime, numberText } from "../format";
import type { CaptureBatch } from "../types";

/**
 * 采集记录：从「数据采集」里拆出来的历史视图。
 * 只展示采集结果（最近一次入库 + 批次列表），采集过程仍留在数据采集页。
 */
export function OrderProfitRecordsTab({
  isActive,
  refreshToken,
}: {
  isActive: boolean;
  refreshToken: number;
}) {
  const [latest, setLatest] = useState<Record<string, unknown> | null>(null);
  const [batches, setBatches] = useState<CaptureBatch[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    Promise.all([orderProfitApi.latestCapture(), orderProfitApi.captureBatches()])
      .then(([latestPayload, batchPayload]) => {
        if (cancelled) return;
        setLatest(latestPayload.capture);
        setBatches(batchPayload.batches || []);
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
  }, [isActive, refreshToken]);

  return (
    <section className="op-view">
      {error ? <div className="op-alert is-error">{error}</div> : null}

      <div className="op-card op-capture-result">
        <div className="op-panel-head">
          <strong>最近一次入库</strong>
          <span className="op-chip">{loading ? "读取中…" : `${batches.length} 批`}</span>
        </div>
        {latest ? (
          <div className="op-capture-summary">
            <div><span>父订单</span><strong>{numberText(Number(latest.order_count) || 0)}</strong></div>
            <div><span>子订单</span><strong>{numberText(Number(latest.sub_order_count) || 0)}</strong></div>
            <div><span>入库时间</span><strong>{beijingTime(latest.captured_at as string | undefined)}</strong></div>
            <div><span>批次</span><strong className="op-batch-id" title={String(latest.id || "")}>{String(latest.id || "—")}</strong></div>
          </div>
        ) : (
          <p className="op-hint">还没有采集入库记录。采集成功后这里会显示最近一批。</p>
        )}
      </div>

      <div className="op-card op-capture-result">
        <div className="op-panel-head">
          <strong>采集批次</strong>
          <span className="op-chip">{batches.length} 批</span>
        </div>
        {batches.length ? (
          <div className="op-table-wrap op-batch-wrap">
            <table className="op-table op-table-batches">
              <thead>
                <tr>
                  <th>批次</th>
                  <th>入库时间</th>
                  <th>模式</th>
                  <th className="op-num">父订单</th>
                  <th className="op-num">子订单</th>
                  <th className="op-num">件数</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((batch) => (
                  <tr key={batch.id}>
                    <td className="op-mono">{batch.id}</td>
                    <td className="op-muted">{beijingTime(batch.captured_at || batch.created_at)}</td>
                    <td>{batch.capture_mode === "order_detail" ? "订单详情" : "订单列表"}</td>
                    <td className="op-num">{numberText(batch.order_count)}</td>
                    <td className="op-num">{numberText(batch.sub_order_count)}</td>
                    <td className="op-num">{numberText(batch.item_count)}</td>
                    <td><span className={`op-chip ${batch.status === "ingested" ? "is-ok" : "is-warn"}`}>{batch.status === "ingested" ? "已入库" : batch.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="op-hint">还没有采集批次。列表/详情采集成功后会记录在这里。</p>
        )}
      </div>
    </section>
  );
}
