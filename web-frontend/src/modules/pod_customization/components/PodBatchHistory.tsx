import { batchProgress, podBatchStatusLabel } from "../data/podCustomizationModel";
import type { PodBatchSummary } from "../types";

type Props = {
  batches: PodBatchSummary[];
  activeBatchId?: string;
  loading: boolean;
  onOpen: (batchId: string) => void;
  onRefresh: () => void;
};

export function PodBatchHistory({ batches, activeBatchId, loading, onOpen, onRefresh }: Props) {
  return (
    <section className="pod-history" aria-label="POD 批次历史">
      <header><div><span>BATCH HISTORY</span><h3>最近批次</h3></div><button type="button" onClick={onRefresh} disabled={loading} aria-label="刷新批次历史"><span className={`iconfont icon-sync ${loading ? "is-spinning" : ""}`} /></button></header>
      <div className="pod-history-list">
        {batches.map((batch) => (
          <button type="button" key={batch.id} className={activeBatchId === batch.id ? "is-active" : ""} onClick={() => onOpen(batch.id)}>
            <span className="pod-history-primary"><b>{batch.title || batch.template_name}</b><small>{podBatchStatusLabel(batch.status)}</small></span>
            <span className="pod-history-progress"><i><span style={{ width: `${batchProgress(batch)}%` }} /></i><small>{batch.processed_count}/{batch.count}</small></span>
            <time dateTime={batch.created_at}>{new Date(batch.created_at).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}</time>
          </button>
        ))}
        {!loading && !batches.length && <p>暂无批次；完成首次配置后开始生成。</p>}
        {loading && !batches.length && <p>正在读取批次记录…</p>}
      </div>
    </section>
  );
}
