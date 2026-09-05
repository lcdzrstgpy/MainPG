import { useEffect } from "react";

import { PodBatchHistory } from "./PodBatchHistory";
import type { PodBatchSummary } from "../types";

type Props = {
  open: boolean;
  batches: PodBatchSummary[];
  activeBatchId?: string;
  loading: boolean;
  busyAction: string;
  selectedIds: string[];
  onToggleSelect: (batchId: string) => void;
  onDeleteSelected: () => void;
  onOpen: (batchId: string) => void;
  onRefresh: () => void;
  onClose: () => void;
};

export function PodBatchHistoryDrawer({ open, batches, activeBatchId, loading, busyAction, selectedIds, onToggleSelect, onDeleteSelected, onOpen, onRefresh, onClose }: Props) {
  useEffect(() => {
    if (!open) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="pod-history-drawer-layer">
      <button type="button" className="pod-history-drawer-backdrop" onClick={onClose} aria-label="关闭定制记录历史" />
      <aside className="pod-history-drawer" role="dialog" aria-modal="true" aria-label="定制记录历史">
        <header className="pod-history-drawer-header">
          <div><span>BATCH HISTORY</span><h2>定制记录历史</h2><p>点击任一批次切换到对应视图；勾选后可在右侧删除。</p></div>
          <button type="button" onClick={onClose} aria-label="关闭">×</button>
        </header>
        <div className="pod-history-drawer-body">
          <PodBatchHistory
            batches={batches}
            activeBatchId={activeBatchId}
            loading={loading}
            busyAction={busyAction}
            selectedIds={selectedIds}
            onToggleSelect={onToggleSelect}
            onDeleteSelected={onDeleteSelected}
            onOpen={onOpen}
            onRefresh={onRefresh}
          />
        </div>
      </aside>
    </div>
  );
}
