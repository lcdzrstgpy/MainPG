import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import { pluginOneboundCaptureApi } from "../api/pluginOneboundCaptureApi";
import {
  canRetryPluginCaptureFailures,
  isActivePluginCaptureStatus,
  isTerminalPluginCaptureStatus,
  pluginCaptureProgress,
  pluginCaptureStatusLabel,
  type PluginOneboundCaptureBatch,
  type PluginOneboundCandidate,
  type PluginOneboundSkuRepullState,
} from "../data/pluginOneboundCaptureModel";
import { formatShopCollectionError } from "../data/shopCollectionModel";
import "../styles/shop-collection.css";

type PluginOneboundCapturePanelProps = {
  isActive?: boolean;
  onOpenDraft?: (draftId: number) => void;
  onBatchCountChange?: (count: number) => void;
};

export type PluginOneboundCapturePanelHandle = {
  openBatchManager: () => void;
};

const PLUGIN_PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const;

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  return `¥${value.toFixed(2)}`;
}

function candidateReviewLabel(status: PluginOneboundCandidate["review_status"]): string {
  if (status === "confirmed") return "已入池";
  if (status === "pending") return "待审核";
  return "历史记录";
}

function numberOrNull(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export const PluginOneboundCapturePanel = forwardRef<PluginOneboundCapturePanelHandle, PluginOneboundCapturePanelProps>(function PluginOneboundCapturePanel({ isActive = true, onOpenDraft, onBatchCountChange }, ref) {
  const [batches, setBatches] = useState<PluginOneboundCaptureBatch[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [candidates, setCandidates] = useState<PluginOneboundCandidate[]>([]);
  const [candidateOffset, setCandidateOffset] = useState(0);
  const [candidatePageSize, setCandidatePageSize] = useState<(typeof PLUGIN_PAGE_SIZE_OPTIONS)[number]>(10);
  const [skuRepull, setSkuRepull] = useState<PluginOneboundSkuRepullState | null>(null);
  const [selectedOfferIds, setSelectedOfferIds] = useState<string[]>([]);
  const [skuFilterMin, setSkuFilterMin] = useState("");
  const [skuFilterMax, setSkuFilterMax] = useState("");
  const [appliedSkuFilter, setAppliedSkuFilter] = useState<{ min: number | null; max: number | null }>({ min: null, max: null });
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [skuRepullBusy, setSkuRepullBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [toastLeaving, setToastLeaving] = useState(false);
  const [batchManagerOpen, setBatchManagerOpen] = useState(false);

  const selectedBatch = useMemo(
    () => batches.find((batch) => batch.batch_id === selectedBatchId) ?? null,
    [batches, selectedBatchId],
  );

  useImperativeHandle(ref, () => ({ openBatchManager: () => setBatchManagerOpen(true) }), []);

  useEffect(() => {
    onBatchCountChange?.(batches.length);
  }, [batches.length, onBatchCountChange]);

  useEffect(() => {
    if (!batchManagerOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setBatchManagerOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [batchManagerOpen]);

  // 右上角浮动提示：15 秒后自动消失；新提示会替换旧提示并重新计时。
  // 提前 300ms 进入淡出态，避免到点后生硬消失。
  useEffect(() => {
    if (!error && !notice) {
      setToastLeaving(false);
      return;
    }
    setToastLeaving(false);
    const leaveTimer = window.setTimeout(() => setToastLeaving(true), 14700);
    const timer = window.setTimeout(() => {
      setError("");
      setNotice("");
    }, 15000);
    return () => {
      window.clearTimeout(leaveTimer);
      window.clearTimeout(timer);
    };
  }, [error, notice]);

  const refreshBatches = useCallback(async (selectFirst = false) => {
    try {
      const page = await pluginOneboundCaptureApi.listBatches();
      setBatches(page.items);
      setSelectedBatchId((current) => {
        if (current && page.items.some((batch) => batch.batch_id === current)) return current;
        return selectFirst || !current ? page.items[0]?.batch_id ?? "" : current;
      });
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshSelected = useCallback(async (batchId: string) => {
    try {
      const batch = await pluginOneboundCaptureApi.getBatch(batchId);
      setBatches((current) => current.map((item) => item.batch_id === batch.batch_id ? batch : item));
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
    }
  }, []);

  const refreshCandidates = useCallback(async (batchId: string) => {
    try {
      const [candidatePage, state] = await Promise.all([
        pluginOneboundCaptureApi.listCandidates(batchId),
        pluginOneboundCaptureApi.getSkuRepullState(batchId),
      ]);
      setCandidates(candidatePage.items);
      setSkuRepull(state);
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
    }
  }, []);

  useEffect(() => {
    if (!isActive) return;
    void refreshBatches(true);
  }, [isActive, refreshBatches]);

  useEffect(() => {
    if (!isActive || !selectedBatchId) {
      setCandidates([]);
      setCandidateOffset(0);
      setSkuRepull(null);
      setSelectedOfferIds([]);
      setAppliedSkuFilter({ min: null, max: null });
      setSkuFilterMin("");
      setSkuFilterMax("");
      return;
    }
    void refreshSelected(selectedBatchId);
    void refreshCandidates(selectedBatchId);
  }, [isActive, refreshSelected, refreshCandidates, selectedBatchId]);

  // 同整店采集面板：依赖布尔量而非 batches 数组，避免每轮刷新结果把 2s 定时器反复清理重建。
  const hasActiveBatch = useMemo(
    () => batches.some((batch) => isActivePluginCaptureStatus(batch.status)),
    [batches],
  );

  useEffect(() => {
    if (!isActive || !hasActiveBatch) return;
    const timer = window.setInterval(() => {
      void refreshBatches();
      if (selectedBatchId) void refreshSelected(selectedBatchId);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [hasActiveBatch, isActive, refreshBatches, refreshSelected, selectedBatchId]);

  // 批次从采集中转为终态后，上面的 active 轮询会停止，需主动刷新一次候选与 SKU 补齐状态，
  // 否则终态后候选区会保持空白，用户必须切换批次或刷新页面才能审核。
  const selectedBatchTerminal = selectedBatch ? isTerminalPluginCaptureStatus(selectedBatch.status) : false;
  useEffect(() => {
    if (!isActive || !selectedBatchId || !selectedBatchTerminal) return;
    void refreshCandidates(selectedBatchId);
  }, [isActive, selectedBatchId, selectedBatchTerminal, refreshCandidates]);

  useEffect(() => {
    if (!isActive || !selectedBatchId || skuRepull?.status !== "running") return;
    const timer = window.setInterval(() => {
      void refreshCandidates(selectedBatchId);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [isActive, refreshCandidates, selectedBatchId, skuRepull?.status]);

  const filteredCandidates = useMemo(() => {
    return candidates.filter((candidate) => {
      if (appliedSkuFilter.min !== null && candidate.sku_count < appliedSkuFilter.min) return false;
      if (appliedSkuFilter.max !== null && candidate.sku_count > appliedSkuFilter.max) return false;
      return true;
    });
  }, [candidates, appliedSkuFilter]);

  useEffect(() => {
    setCandidateOffset(0);
  }, [appliedSkuFilter, candidatePageSize, selectedBatchId]);

  useEffect(() => {
    if (candidateOffset === 0 || candidateOffset < filteredCandidates.length) return;
    setCandidateOffset(Math.max(0, Math.floor(Math.max(0, filteredCandidates.length - 1) / candidatePageSize) * candidatePageSize));
  }, [candidateOffset, candidatePageSize, filteredCandidates.length]);

  const selectableCandidates = filteredCandidates.filter((candidate) => candidate.review_status === "pending");
  const pagedCandidates = filteredCandidates.slice(candidateOffset, candidateOffset + candidatePageSize);
  const candidateCurrentPage = filteredCandidates.length === 0 ? 1 : Math.floor(candidateOffset / candidatePageSize) + 1;
  const candidateTotalPages = Math.max(1, Math.ceil(filteredCandidates.length / candidatePageSize));
  const backfillRunning = skuRepull?.status === "running";
  const allCandidatesSelected = selectableCandidates.length > 0
    && selectableCandidates.every((candidate) => selectedOfferIds.includes(candidate.offer_id));

  async function retryFailed() {
    if (!selectedBatch || !canRetryPluginCaptureFailures(selectedBatch)) return;
    setRetrying(true);
    setError("");
    setNotice("");
    try {
      const child = await pluginOneboundCaptureApi.retryFailed(selectedBatch.batch_id);
      await refreshBatches();
      setSelectedBatchId(child.batch_id);
      setNotice("失败商品已创建为新的重试批次。");
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
    } finally {
      setRetrying(false);
    }
  }

  async function startBatch() {
    if (!selectedBatch || selectedBatch.status !== "prepared") return;
    setStarting(true);
    setError("");
    setNotice("");
    try {
      await pluginOneboundCaptureApi.startBatch(selectedBatch.batch_id);
      await Promise.all([refreshBatches(), refreshSelected(selectedBatch.batch_id)]);
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
      await refreshSelected(selectedBatch.batch_id);
    } finally {
      setStarting(false);
    }
  }

  async function startSkuRepullNow() {
    if (!selectedBatch || backfillRunning) return;
    setSkuRepullBusy(true);
    setError("");
    try {
      const state = await pluginOneboundCaptureApi.startSkuRepull(selectedBatch.batch_id);
      setSkuRepull(state);
      await refreshCandidates(selectedBatch.batch_id);
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
    } finally {
      setSkuRepullBusy(false);
    }
  }

  async function cancelSkuRepullNow() {
    if (!selectedBatch) return;
    setSkuRepullBusy(true);
    try {
      const state = await pluginOneboundCaptureApi.cancelSkuRepull(selectedBatch.batch_id);
      setSkuRepull(state);
      await refreshCandidates(selectedBatch.batch_id);
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
    } finally {
      setSkuRepullBusy(false);
    }
  }

  function toggleCandidate(offerId: string) {
    setSelectedOfferIds((current) => current.includes(offerId)
      ? current.filter((id) => id !== offerId)
      : [...current, offerId]);
  }

  function selectAllCandidates() {
    setSelectedOfferIds((current) => {
      const ids = selectableCandidates.map((candidate) => candidate.offer_id);
      const allSelected = ids.length > 0 && ids.every((id) => current.includes(id));
      return allSelected ? current.filter((id) => !ids.includes(id)) : [...new Set([...current, ...ids])];
    });
  }

  async function confirmSelected() {
    if (!selectedBatch || selectedOfferIds.length === 0) return;
    setConfirming(true);
    setError("");
    setNotice("");
    try {
      const result = await pluginOneboundCaptureApi.confirmCandidates(selectedBatch.batch_id, selectedOfferIds);
      setNotice(`已确认 ${result.confirmed_count} 个候选入池。`);
      setSelectedOfferIds([]);
      await Promise.all([refreshCandidates(selectedBatch.batch_id), refreshSelected(selectedBatch.batch_id)]);
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
    } finally {
      setConfirming(false);
    }
  }

  const progress = selectedBatch ? pluginCaptureProgress(selectedBatch) : null;
  const batchErrorMessage = selectedBatch?.error_message
    ? formatShopCollectionError(new Error(selectedBatch.error_message))
    : "";

  // 插件采集完成后商品会直接进入候选池，候选列表是唯一视图；
  // 统计只保留「总数 / 成功」两项（成功 = 总数 - 失败 - 跳过 - 未处理）。
  const succeededCount = selectedBatch && progress
    ? Math.max(0, progress.total - selectedBatch.failed_count - selectedBatch.skipped_count - selectedBatch.unprocessed_count)
    : 0;
  const batchStats = selectedBatch && progress ? (
    <div className="shop-batch-stats plugin-capture-stats">
      <span><b>{progress.total}</b>总数</span>
      <span><b>{succeededCount}</b>成功</span>
    </div>
  ) : null;

  return (
    <section className="shop-collection-panel plugin-capture-panel" aria-label="插件采集">
      <div className="shop-collection-grid">
        <div className="shop-batch-detail">
          {!selectedBatch && batches.length > 0 && <div className="shop-empty"><strong>请选择一个插件采集批次</strong><p>这里仅展示插件已经发起的采集任务。</p></div>}
          {selectedBatch && progress && (
            <>
              <div className="shop-batch-summary">
                <div className="shop-batch-info">
                  <div className="shop-batch-idline">
                    <span className="shop-batch-status">{pluginCaptureStatusLabel(selectedBatch.status)}</span>
                    <span className="shop-batch-fact"><em>批次</em><b>{selectedBatch.batch_id.slice(0, 8)}</b></span>
                    <small className="shop-batch-created">创建于 {formatDate(selectedBatch.created_at)}</small>
                    {selectedBatch.page_url && <a className="plugin-link-pill" href={selectedBatch.page_url} target="_blank" rel="noreferrer">查看采集页面</a>}
                  </div>
                  <div className="shop-progress"><span><i style={{ width: `${progress.percent}%` }} /></span><b>{progress.percent}%</b></div>
                </div>
                {(selectedBatch.status === "prepared" || canRetryPluginCaptureFailures(selectedBatch)) && (
                  <div className="shop-batch-actions">
                    {selectedBatch.status === "prepared" && (
                      <button type="button" disabled={starting} onClick={() => void startBatch()}>
                        {starting ? "正在启动采集…" : "启动采集"}
                      </button>
                    )}
                    {canRetryPluginCaptureFailures(selectedBatch) && (
                      <button type="button" disabled={retrying} onClick={() => void retryFailed()}>{retrying ? "正在重试…" : "重试失败项"}</button>
                    )}
                  </div>
                )}
              </div>
              {(selectedBatch.error_code || batchErrorMessage) && (
                <p className="shop-batch-warning">
                  {selectedBatch.error_code && <>错误代码：{selectedBatch.error_code}</>}
                  {selectedBatch.error_code && batchErrorMessage && " · "}
                  {batchErrorMessage}
                </p>
              )}

              <div className="plugin-candidate-review">
                  <div className="plugin-review-tools">
                    <div className="sku-filter">
                      <span className="sku-filter-label">SKU筛选</span>
                      <input type="number" min={0} placeholder="最小" value={skuFilterMin} onChange={(event) => setSkuFilterMin(event.target.value)} disabled={backfillRunning} />
                      <span className="sku-filter-separator">-</span>
                      <input type="number" min={0} placeholder="最大" value={skuFilterMax} onChange={(event) => setSkuFilterMax(event.target.value)} disabled={backfillRunning} />
                      <button type="button" className="sku-filter-button" disabled={backfillRunning} onClick={() => {
                        setAppliedSkuFilter({ min: numberOrNull(skuFilterMin), max: numberOrNull(skuFilterMax) });
                      }}>筛选</button>
                    </div>
                    <div className={`sku-repull-control ${backfillRunning ? "is-running" : ""}`}>
                      <button type="button" className="sku-repull-button" disabled={skuRepullBusy || backfillRunning} onClick={() => void startSkuRepullNow()} title="对 SKU 规格未读取成功的候选重新拉取详情">↻ SKU补齐</button>
                      {skuRepull && skuRepull.status !== "idle" && (
                        <span className="sku-repull-state">
                          {backfillRunning ? (
                            <>
                              <b>第 {skuRepull.round} 轮</b>
                              <i>{skuRepull.done}/{skuRepull.total} · {Math.round((skuRepull.done / Math.max(1, skuRepull.total)) * 100)}%</i>
                              <button type="button" className="sku-repull-cancel" disabled={skuRepullBusy} onClick={() => void cancelSkuRepullNow()}>中断</button>
                            </>
                          ) : (
                            <i>
                              {skuRepull.status === "completed"
                                ? `第 ${skuRepull.round} 轮完成（成功 ${skuRepull.succeeded} / 失败 ${skuRepull.failed}）`
                                : skuRepull.status === "cancelled"
                                  ? `第 ${skuRepull.round} 轮已中断（完成 ${skuRepull.done}/${skuRepull.total}）`
                                  : skuRepull.message}
                            </i>
                          )}
                        </span>
                      )}
                    </div>
                    <button type="button" className="select-all-button" disabled={confirming || backfillRunning || selectableCandidates.length === 0} onClick={selectAllCandidates}>{allCandidatesSelected ? "取消全选" : "全选"}</button>
                    <div className="plugin-review-actions">
                      {batchStats}
                      <button type="button" className="confirm-button" disabled={confirming || backfillRunning || selectedOfferIds.length === 0} onClick={() => void confirmSelected()}>确认入池</button>
                    </div>
                  </div>

                  {filteredCandidates.length === 0 && (
                    <p className="shop-empty">{isTerminalPluginCaptureStatus(selectedBatch.status) ? "没有符合 SKU 筛选条件的候选商品。" : "采集完成后，候选商品会显示在这里。"}</p>
                  )}
                  <div className="plugin-candidate-list">
                    {pagedCandidates.map((candidate) => {
                      const selectable = candidate.review_status === "pending";
                      const checked = selectedOfferIds.includes(candidate.offer_id);
                      return (
                        <article key={candidate.offer_id} className={checked ? "is-checked" : ""}>
                          <label className={selectable ? "" : "is-locked"}>
                            <input type="checkbox" checked={checked} disabled={!selectable || backfillRunning} onChange={() => toggleCandidate(candidate.offer_id)} />
                          </label>
                          <div className="plugin-candidate-body">
                            <a className="plugin-title-link" href={candidate.source_url} target="_blank" rel="noreferrer">{candidate.source_title.trim() || `商品 ${candidate.offer_id}`}</a>
                            <small>
                              {[
                                candidate.price_cny != null ? `售价 ${formatPrice(candidate.price_cny)}` : "",
                                candidate.shop_name,
                                `SKU ${candidate.sku_count || "未知"}`,
                                candidateReviewLabel(candidate.review_status),
                              ].filter(Boolean).join(" · ")}
                            </small>
                          </div>
                          <div className="plugin-candidate-links">
                            <a className="plugin-candidate-source" href={candidate.source_url} target="_blank" rel="noreferrer">查看来源</a>
                            {candidate.draft_id != null && onOpenDraft && (
                              <button type="button" onClick={() => onOpenDraft(candidate.draft_id!)}>打开草稿 #{candidate.draft_id}</button>
                            )}
                          </div>
                        </article>
                      );
                    })}
                  </div>
                  <div className="shop-pagination plugin-pagination">
                    <span className="shop-pagination-status">第 {candidateCurrentPage} 页 / 共 {candidateTotalPages} 页</span>
                    <label className="shop-page-size">每页
                      <select value={candidatePageSize} onChange={(event) => {
                        setCandidatePageSize(Number(event.target.value) as (typeof PLUGIN_PAGE_SIZE_OPTIONS)[number]);
                        setCandidateOffset(0);
                      }}>
                        {PLUGIN_PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{size} 条</option>)}
                      </select>
                    </label>
                    <button type="button" disabled={candidateOffset === 0} onClick={() => setCandidateOffset(Math.max(0, candidateOffset - candidatePageSize))}>上一页</button>
                    <button type="button" disabled={candidateOffset + candidatePageSize >= filteredCandidates.length} onClick={() => setCandidateOffset(candidateOffset + candidatePageSize)}>下一页</button>
                  </div>
                </div>
            </>
          )}
        </div>
      </div>

      {batchManagerOpen && createPortal(
        <div className="shop-batch-drawer-layer">
          <button type="button" className="shop-batch-drawer-backdrop" onClick={() => setBatchManagerOpen(false)} aria-label="关闭批次管理" />
          <aside className="shop-batch-drawer" role="dialog" aria-modal="true" aria-label="插件采集批次管理">
            <header className="shop-batch-drawer-header">
              <div><span>PLUGIN BATCHES</span><h2>批次管理</h2><p>选择一个插件采集批次，继续审核候选和确认入池。</p></div>
              <button type="button" onClick={() => setBatchManagerOpen(false)} aria-label="关闭批次管理">×</button>
            </header>
            <div className="shop-batch-drawer-summary"><span>插件采集批次</span><b>{loading ? "读取中…" : `${batches.length} 条`}</b></div>
            <div className="shop-batch-drawer-list">
              {!loading && batches.length === 0 && <p className="shop-empty"><strong>尚无插件采集批次</strong><br />请前往 1688 或淘宝页面使用浏览器插件发起整页采集。</p>}
              {batches.map((batch) => {
                const batchProgress = pluginCaptureProgress(batch);
                return (
                  <button
                    key={batch.batch_id}
                    type="button"
                    className={batch.batch_id === selectedBatchId ? "is-selected" : ""}
                    onClick={() => {
                      setSelectedBatchId(batch.batch_id);
                      setCandidateOffset(0);
                      setBatchManagerOpen(false);
                    }}
                  >
                    <span><strong>{batch.display_name || batch.batch_id.slice(0, 8)}</strong><small>{pluginCaptureStatusLabel(batch.status)} · {formatDate(batch.created_at)}</small></span>
                    <b>{batchProgress.percent}%</b>
                  </button>
                );
              })}
            </div>
          </aside>
        </div>,
        document.body,
      )}

      {(error || notice) && createPortal(
        <div className={`daily-message ${error ? "is-error" : "is-success"} ${toastLeaving ? "is-leaving" : ""}`} role="status">
          <span aria-hidden="true">{error ? "!" : "✓"}</span>
          <strong>{error || notice}</strong>
        </div>,
        document.body,
      )}
    </section>
  );
});
