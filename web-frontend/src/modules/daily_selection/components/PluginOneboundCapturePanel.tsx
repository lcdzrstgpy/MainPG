import { useCallback, useEffect, useMemo, useState } from "react";

import { pluginOneboundCaptureApi } from "../api/pluginOneboundCaptureApi";
import {
  canRetryPluginCaptureFailures,
  isActivePluginCaptureStatus,
  pluginCaptureProgress,
  pluginCaptureStatusLabel,
  type PluginOneboundCaptureBatch,
  type PluginOneboundCaptureItem,
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
    succeeded: "已入池",
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

export function PluginOneboundCapturePanel({ isActive = true, onOpenDraft }: PluginOneboundCapturePanelProps) {
  const [batches, setBatches] = useState<PluginOneboundCaptureBatch[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [items, setItems] = useState<PluginOneboundCaptureItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [starting, setStarting] = useState(false);
  const [retrying, setRetrying] = useState(false);
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

  useEffect(() => {
    if (!isActive) return;
    void refreshBatches(true);
  }, [isActive, refreshBatches]);

  useEffect(() => {
    if (!isActive || !selectedBatchId) {
      setItems([]);
      return;
    }
    void refreshSelected(selectedBatchId);
  }, [isActive, refreshSelected, selectedBatchId]);

  useEffect(() => {
    if (!isActive || !batches.some((batch) => isActivePluginCaptureStatus(batch.status))) return;
    const timer = window.setInterval(() => {
      void refreshBatches();
      if (selectedBatchId) void refreshSelected(selectedBatchId);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [batches, isActive, refreshBatches, refreshSelected, selectedBatchId]);

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

  const progress = selectedBatch ? pluginCaptureProgress(selectedBatch) : null;
  const batchErrorMessage = selectedBatch?.error_message
    ? formatShopCollectionError(new Error(selectedBatch.error_message))
    : "";

  return (
    <section className="shop-collection-panel plugin-capture-panel" aria-label="插件采集">
      <header className="shop-collection-header">
        <div><strong>插件采集批次</strong><p>插件登记 1688 链接后，在这里采集并查看草稿写入结果。</p></div>
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
                <span><b>{selectedBatch.created_count}</b>新建</span>
                <span><b>{selectedBatch.refreshed_count}</b>刷新</span>
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
              <div className="shop-items-heading"><strong>批次商品</strong><span>{items.length ? `${items.length} 条` : "暂无商品"}</span></div>
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
