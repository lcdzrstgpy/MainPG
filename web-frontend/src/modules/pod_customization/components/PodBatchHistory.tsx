import { batchProgress, canDeletePodBatch, podBatchStatusLabel } from "../data/podCustomizationModel";
import type { PodBatchSummary } from "../types";

type Props = {
  batches: PodBatchSummary[];
  activeBatchId?: string;
  loading: boolean;
  busyAction: string;
  selectedIds: string[];
  onToggleSelect: (batchId: string) => void;
  onDeleteSelected: () => void;
  onOpen: (batchId: string) => void;
  onRefresh: () => void;
};

export function PodBatchHistory({ batches, activeBatchId, loading, busyAction, selectedIds, onToggleSelect, onDeleteSelected, onOpen, onRefresh }: Props) {
  const busy = Boolean(busyAction);
  const selectedCount = selectedIds.length;
  return (
    <section className="pod-history" aria-label="POD 批次历史">
      <header>
        <div><span>BATCH HISTORY</span><h3>最近批次</h3></div>
        <div className="pod-history-header-actions">
          <button type="button" className="pod-history-delete-all" disabled={busy || selectedCount === 0} onClick={onDeleteSelected} title={selectedCount ? `删除选中的 ${selectedCount} 个批次` : "先勾选要删除的批次"}>
            {busyAction === "delete-batch" ? "删除中" : `删除${selectedCount ? `（${selectedCount}）` : ""}`}
          </button>
          <button type="button" onClick={onRefresh} disabled={loading} aria-label="刷新批次历史"><span className={`iconfont icon-sync ${loading ? "is-spinning" : ""}`} /></button>
        </div>
      </header>
      <div className="pod-history-list">
        {batches.map((batch) => {
          const deletable = canDeletePodBatch(batch.status);
          const checked = selectedIds.includes(batch.id);
          return (
            <div key={batch.id} className={`pod-history-item ${activeBatchId === batch.id ? "is-active" : ""} ${checked ? "is-selected" : ""}`}>
              <input type="checkbox" className="pod-history-check" checked={checked} disabled={busy || !deletable} onChange={() => onToggleSelect(batch.id)} aria-label={`选择批次 ${batch.title || batch.template_name || batch.id.slice(0, 8)}`} />
              <button type="button" className="pod-history-open" onClick={() => onOpen(batch.id)}>
                <span className="pod-history-primary"><b>{batch.title || batch.template_name}</b><small>{podBatchStatusLabel(batch.status)}</small></span>
                <span className="pod-history-progress"><i><span style={{ width: `${batchProgress(batch)}%` }} /></i><small>{batch.processed_count}/{batch.count}</small></span>
                <time dateTime={batch.created_at}>{new Date(batch.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
              </button>
            </div>
          );
        })}
        {!loading && !batches.length && <p>暂无批次；完成首次配置后开始生成。</p>}
        {loading && !batches.length && <p>正在读取批次记录…</p>}
      </div>
    </section>
  );
}
