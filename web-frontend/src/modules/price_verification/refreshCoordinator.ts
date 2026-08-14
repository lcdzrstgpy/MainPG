import type { BatchSourcingState, SourcePreview } from "./types";

type RefreshLoader<T, O> = (options: O) => Promise<T>;
type RefreshHandler<T, O> = (value: T, options: O) => void;
type RefreshErrorHandler<O> = (error: unknown, options: O) => void;

export type LatestRefreshRunner<O> = {
  run: (options: O) => Promise<void>;
};

export function createLatestRefreshRunner<T, O>(
  load: RefreshLoader<T, O>,
  onSuccess: RefreshHandler<T, O>,
  onError: RefreshErrorHandler<O>,
): LatestRefreshRunner<O> {
  let requestedGeneration = 0;
  let latestOptions: O;
  let inFlight: Promise<void> | null = null;

  const drain = async () => {
    while (true) {
      const generation = requestedGeneration;
      const options = latestOptions;
      try {
        const value = await load(options);
        if (generation === requestedGeneration) onSuccess(value, options);
      } catch (error) {
        if (generation === requestedGeneration) onError(error, options);
      }
      if (generation === requestedGeneration) return;
    }
  };

  return {
    run(options: O) {
      requestedGeneration += 1;
      latestOptions = options;
      if (!inFlight) {
        const pending = drain();
        const tracked = pending.finally(() => {
          if (inFlight === tracked) inFlight = null;
        });
        inFlight = tracked;
      }
      return inFlight;
    },
  };
}

export function sourceSearchResultNotice(preview: SourcePreview) {
  const candidates = Number(preview.counts?.candidate_count ?? 0);
  const failed = Number(preview.counts?.failed_quotes ?? 0);
  if (failed > 0) {
    return `图搜返回 ${candidates} 个候选，但有 ${failed} 个 SKC 失败；旧数据未清空，请点击“重新图搜”重试。`;
  }
  return `货源图搜完成，获得 ${candidates} 个候选货源。请选择候选后完成入库。`;
}

export function isSourcingFullyResolved(state: BatchSourcingState, hadWork = true) {
  return hadWork
    && state.unresolved_skc_ids.length === 0
    && state.selected_candidates.length === 0
    && state.preview === null;
}

export function incompleteSourcingNotice(state: BatchSourcingState) {
  const unresolved = state.unresolved_skc_ids.length;
  const pendingSelections = state.selected_candidates.length;
  const pendingPreview = state.preview !== null;
  const details = [
    unresolved ? `${unresolved} 个 SKC 未解决` : "",
    pendingSelections ? `${pendingSelections} 个候选尚未完成入库` : "",
    pendingPreview ? "图搜临时结果尚未清理" : "",
  ].filter(Boolean);
  return `关联尚未全部完成：${details.join("，") || "服务端未确认完成状态"}。请检查失败项后重试。`;
}
