import { useCallback, useEffect, useMemo, useState } from "react";

import { pluginOneboundCaptureApi } from "../api/pluginOneboundCaptureApi";
import {
  canRetryPluginCaptureFailures,
  isActivePluginCaptureStatus,
  isTerminalPluginCaptureStatus,
  pluginCaptureProgress,
  pluginCaptureStatusLabel,
  type PluginOneboundCaptureBatch,
  type PluginOneboundCaptureItem,
  type PluginOneboundCandidate,
  type PluginOneboundSkuRepullState,
} from "../data/pluginOneboundCaptureModel";
import { formatShopCollectionError } from "../data/shopCollectionModel";
import "../styles/shop-collection.css";

type PluginOneboundCapturePanelProps = {
  isActive?: boolean;
  onOpenDraft?: (draftId: number) => void;
};

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function itemStatusLabel(item: PluginOneboundCaptureItem): string {
  const labels: Record<PluginOneboundCaptureItem["status"], string> = {
    pending: "等待采集",
    running: "正在采集",
    succeeded: "已采集候选",
    failed: "采集失败",
    skipped: "已跳过",
    unprocessed: "未处理",
  };
  return labels[item.status];
}

function itemOutcomeLabel(item: PluginOneboundCaptureItem): string {
  const labels: Record<PluginOneboundCaptureItem["outcome"], string> = {
    "": "",
    created: "新建",
    refreshed: "刷新",
    skipped: "跳过",
    failed: "失败",
    unprocessed: "未处理",
  };
  return labels[item.outcome];
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

export function PluginOneboundCapturePanel({ isActive = true, onOpenDraft }: PluginOneboundCapturePanelProps) {
  const [batches, setBatches] = useState<PluginOneboundCaptureBatch[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [items, setItems] = useState<PluginOneboundCaptureItem[]>([]);
  const [candidates, setCandidates] = useState<PluginOneboundCandidate[]>([]);
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

  const selectedBatch = useMemo(
    () => batches.find((batch) => batch.batch_id === selectedBatchId) ?? null,
    [batches, selectedBatchId],
  );

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
      const [batch, page] = await Promise.all([
        pluginOneboundCaptureApi.getBatch(batchId),
        pluginOneboundCaptureApi.listItems(batchId),
      ]);
      setBatches((current) => current.map((item) => item.batch_id === batch.batch_id ? batch : item));
      setItems(page.items);
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
      setItems([]);
      setCandidates([]);
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

  useEffect(() => {
    if (!isActive || !batches.some((batch) => isActivePluginCaptureStatus(batch.status))) return;
    const timer = window.setInterval(() => {
      void refreshBatches();
      if (selectedBatchId) void refreshSelected(selectedBatchId);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [batches, isActive, refreshBatches, refreshSelected, selectedBatchId]);

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

  const selectableCandidates = filteredCandidates.filter((candidate) => candidate.review_status === "pending");
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
      setNotice("采集已启动，进度会自动更新。");
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
    setSelectedOfferIds(selectableCandidates.map((candidate) => candidate.offer_id));
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

  return (
    <section className="shop-collection-panel plugin-capture-panel" aria-label="插件采集">
      <header className="shop-collection-header">
        <div><strong>插件采集批次</strong><p>插件登记 1688 链接后，在这里采集，采集完成后审核候选并确认入池。</p></div>
        <span className="shop-collection-persistent">自动同步进度</span>
      </header>

      {(error || notice) && <p className={`shop-collection-message ${error ? "is-error" : "is-success"}`} role="status">{error || notice}</p>}

      <div className="shop-collection-grid">
        <aside className="shop-batch-list" aria-label="插件采集批次">
          <div className="shop-section-title"><strong>批次</strong><span>{loading ? "读取中…" : `${batches.length} 条`}</span></div>
          {!loading && batches.length === 0 && <p className="shop-empty"><strong>尚无插件采集批次</strong><br />请前往 1688 页面使用浏览器插件发起整页采集。</p>}
          {batches.map((batch) => {
            const batchProgress = pluginCaptureProgress(batch);
            return (
              <button key={batch.batch_id} type="button" className={batch.batch_id === selectedBatchId ? "is-selected" : ""} onClick={() => setSelectedBatchId(batch.batch_id)}>
                <span><strong>{batch.batch_id.slice(0, 8)}</strong><small>{pluginCaptureStatusLabel(batch.status)} · {formatDate(batch.created_at)}</small></span>
                <b>{batchProgress.percent}%</b>
              </button>
            );
          })}
        </aside>

        <div className="shop-batch-detail">
          {!selectedBatch && batches.length > 0 && <div className="shop-empty"><strong>请选择一个插件采集批次</strong><p>这里仅展示插件已经发起的采集任务。</p></div>}
          {selectedBatch && progress && (
            <>
              <div className="shop-batch-summary">
                <div>
                  <span>{pluginCaptureStatusLabel(selectedBatch.status)}</span>
                  <strong>批次 {selectedBatch.batch_id.slice(0, 8)}</strong>
                  <small>
                    创建于 {formatDate(selectedBatch.created_at)}
                    {selectedBatch.page_url && <> · <a href={selectedBatch.page_url} target="_blank" rel="noreferrer">查看采集页面</a></>}
                  </small>
                </div>
                <div className="shop-progress"><span><i style={{ width: `${progress.percent}%` }} /></span><b>{progress.percent}%</b></div>
              </div>
              <div className="shop-batch-stats plugin-capture-stats">
                <span><b>{progress.total}</b>总数</span>
                <span><b>{selectedBatch.created_count}</b>已入池</span>
                <span><b>{selectedBatch.skipped_count}</b>跳过</span>
                <span><b>{selectedBatch.failed_count}</b>失败</span>
                <span><b>{selectedBatch.unprocessed_count}</b>未处理</span>
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
              {(selectedBatch.error_code || batchErrorMessage) && (
                <p className="shop-batch-warning">
                  {selectedBatch.error_code && <>错误代码：{selectedBatch.error_code}</>}
                  {selectedBatch.error_code && batchErrorMessage && " · "}
                  {batchErrorMessage}
                </p>
              )}

              {isTerminalPluginCaptureStatus(selectedBatch.status) && candidates.length > 0 && (
                <div className="plugin-candidate-review">
                  <div className="shop-items-heading plugin-review-heading">
                    <strong>候选商品</strong>
                    <span>{candidates.length} 条</span>
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
                    <label className="select-all-check" title={allCandidatesSelected ? "取消全选" : "全选"}>
                      <input
                        type="checkbox"
                        checked={allCandidatesSelected}
                        disabled={confirming || backfillRunning || selectableCandidates.length === 0}
                        onChange={() => setSelectedOfferIds((current) => {
                          const ids = selectableCandidates.map((candidate) => candidate.offer_id);
                          const allSelected = ids.length > 0 && ids.every((id) => current.includes(id));
                          return allSelected ? current.filter((id) => !ids.includes(id)) : [...new Set([...current, ...ids])];
                        })}
                      />
                    </label>
                    <button type="button" className="select-all-button" disabled={confirming || backfillRunning || selectableCandidates.length === 0} onClick={selectAllCandidates}>全选</button>
                    <button type="button" className="confirm-button" disabled={confirming || backfillRunning || selectedOfferIds.length === 0} onClick={() => void confirmSelected()}>确认入池（{selectedOfferIds.length}）</button>
                  </div>

                  {filteredCandidates.length === 0 && (
                    <p className="shop-empty">没有符合 SKU 筛选条件的候选商品。</p>
                  )}
                  <div className="plugin-candidate-list">
                    {filteredCandidates.map((candidate) => {
                      const selectable = candidate.review_status === "pending";
                      const checked = selectedOfferIds.includes(candidate.offer_id);
                      return (
                        <article key={candidate.offer_id} className={checked ? "is-checked" : ""}>
                          <label className={selectable ? "" : "is-locked"}>
                            <input type="checkbox" checked={checked} disabled={!selectable || backfillRunning} onChange={() => toggleCandidate(candidate.offer_id)} />
                          </label>
                          <div className="plugin-candidate-body">
                            <a href={candidate.source_url} target="_blank" rel="noreferrer">{candidate.source_title.trim() || `商品 ${candidate.offer_id}`}</a>
                            <small>
                              SKU {candidate.sku_count || "未知"} · {candidateReviewLabel(candidate.review_status)}
                            </small>
                          </div>
                          {candidate.draft_id != null && onOpenDraft && (
                            <button type="button" onClick={() => onOpenDraft(candidate.draft_id!)}>打开草稿 #{candidate.draft_id}</button>
                          )}
                        </article>
                      );
                    })}
                  </div>
                </div>
              )}

              <div className="shop-items-heading"><strong>采集明细</strong><span>{items.length ? `${items.length} 条` : "暂无商品"}</span></div>
              <div className="shop-items-list">
                {items.map((item) => {
                  const statusLabel = itemStatusLabel(item);
                  const outcomeLabel = itemOutcomeLabel(item);
                  const itemErrorMessage = item.error_message
                    ? formatShopCollectionError(new Error(item.error_message))
                    : "";
                  return (
                    <article key={item.offer_id}>
                      <div>
                        <strong>{item.source_title.trim() || `商品 ${item.offer_id}`}</strong>
                        <small>
                          {statusLabel}
                          {outcomeLabel && outcomeLabel !== statusLabel ? ` · ${outcomeLabel}` : ""}
                          {item.attempts > 0 ? ` · 尝试 ${item.attempts} 次` : ""}
                        </small>
                        {(item.error_code || itemErrorMessage) && <em>{[item.error_code, itemErrorMessage].filter(Boolean).join(" · ")}</em>}
                      </div>
                      <div className="plugin-capture-item-links">
                        <a href={item.source_url} target="_blank" rel="noreferrer">查看来源</a>
                        {item.draft_id != null && onOpenDraft && (
                          <button type="button" onClick={() => onOpenDraft(item.draft_id!)}>
                            打开产品处理 · 草稿 #{item.draft_id}
                          </button>
                        )}
                      </div>
                    </article>
                  );
                })}
                {items.length === 0 && <p className="shop-empty">插件登记的商品链接会显示在这里。</p>}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
