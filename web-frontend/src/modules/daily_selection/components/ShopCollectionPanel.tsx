import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { shopCollectionApi } from "../api/shopCollectionApi";
import "../styles/shop-collection.css";
import {
  formatShopCollectionError,
  getShopBatchActions,
  isActiveShopBatchStatus,
  shopBatchProgress,
  shopBatchStatusLabel,
  type ShopCollectionBatch,
  type ShopCollectionItem,
} from "../data/shopCollectionModel";

type ShopCollectionPanelProps = {
  isActive?: boolean;
};

const ITEM_PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const;

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function itemStatusLabel(item: ShopCollectionItem): string {
  const labels: Record<ShopCollectionItem["detail_status"], string> = {
    pending: "等待补全",
    running: "正在补全",
    succeeded: "已入池",
    failed: "补全失败",
    cancelled: "已取消",
  };
  return labels[item.detail_status];
}

export function ShopCollectionPanel({ isActive = true }: ShopCollectionPanelProps) {
  const [sourceInput, setSourceInput] = useState("");
  const [batches, setBatches] = useState<ShopCollectionBatch[]>([]);
  const [selectedBatchId, setSelectedBatchId] = useState("");
  const [items, setItems] = useState<ShopCollectionItem[]>([]);
  const [itemsTotal, setItemsTotal] = useState(0);
  const [itemsOffset, setItemsOffset] = useState(0);
  const [itemsPageSize, setItemsPageSize] = useState<(typeof ITEM_PAGE_SIZE_OPTIONS)[number]>(20);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [actionBusy, setActionBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const selectedBatch = useMemo(
    () => batches.find((batch) => batch.batch_id === selectedBatchId) ?? null,
    [batches, selectedBatchId],
  );

  const refreshBatches = useCallback(async (selectFirst = false) => {
    try {
      const page = await shopCollectionApi.listBatches();
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

  const refreshItems = useCallback(async (batchId: string, offset: number) => {
    try {
      const page = await shopCollectionApi.listItems(batchId, itemsPageSize, offset);
      setItems(page.items);
      setItemsTotal(page.total);
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
    }
  }, [itemsPageSize]);

  useEffect(() => {
    if (!isActive) return;
    void refreshBatches(true);
  }, [isActive, refreshBatches]);

  useEffect(() => {
    if (!isActive || !selectedBatchId) {
      setItems([]);
      setItemsTotal(0);
      return;
    }
    void refreshItems(selectedBatchId, itemsOffset);
  }, [isActive, itemsOffset, refreshItems, selectedBatchId]);

  useEffect(() => {
    if (!isActive || !batches.some((batch) => isActiveShopBatchStatus(batch.status))) return;
    const timer = window.setInterval(() => {
      void refreshBatches();
      if (selectedBatchId) void refreshItems(selectedBatchId, itemsOffset);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [batches, isActive, itemsOffset, refreshBatches, refreshItems, selectedBatchId]);

  async function createBatch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = sourceInput.trim();
    if (!value) {
      setError("请输入 1688 商品链接、商品 ID 或店铺线索");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const created = await shopCollectionApi.createBatch(value);
      setSourceInput("");
      setSelectedBatchId(created.batch_id);
      setItemsOffset(0);
      setNotice("整店采集已创建，后台会持续处理，离开页面也不会中断。");
      await refreshBatches();
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
    } finally {
      setSubmitting(false);
    }
  }

  async function runAction(action: "pause" | "resume" | "cancel" | "retryFailed") {
    if (!selectedBatch) return;
    setActionBusy(action);
    setError("");
    try {
      await shopCollectionApi[action](selectedBatch.batch_id);
      setNotice(action === "retryFailed" ? "失败商品已加入重试队列。" : "批次状态已更新。");
      await refreshBatches();
      await refreshItems(selectedBatch.batch_id, itemsOffset);
    } catch (requestError) {
      setError(formatShopCollectionError(requestError));
    } finally {
      setActionBusy("");
    }
  }

  const actions = selectedBatch ? getShopBatchActions(selectedBatch) : null;
  const pageStart = itemsTotal === 0 ? 0 : itemsOffset + 1;
  const pageEnd = Math.min(itemsOffset + items.length, itemsTotal);
  const currentPage = itemsTotal === 0 ? 1 : Math.floor(itemsOffset / itemsPageSize) + 1;
  const totalPages = Math.max(1, Math.ceil(itemsTotal / itemsPageSize));

  return (
    <section className="shop-collection-panel" aria-label="整店采集">
      <header className="shop-collection-header">
        <div><strong>整店采集</strong><p>从一个商品定位店铺，后台分页采集并将成功商品直接写入产品处理草稿池。</p></div>
        <span className="shop-collection-persistent">后台持续执行</span>
      </header>

      <form className="shop-collection-create" onSubmit={createBatch}>
        <label>
          <span>1688 店铺主页、商品链接或商品 ID</span>
          <input value={sourceInput} onChange={(event) => setSourceInput(event.target.value)} placeholder="数字店铺 SID 请写成 sid:123456；纯数字默认按商品 ID 识别" />
        </label>
        <button type="submit" disabled={submitting}>{submitting ? "正在创建…" : "开始整店采集"}</button>
      </form>
      {(error || notice) && <p className={`shop-collection-message ${error ? "is-error" : "is-success"}`} role="status">{error || notice}</p>}

      <div className="shop-collection-grid">
        <aside className="shop-batch-list" aria-label="整店采集批次">
          <div className="shop-section-title"><strong>批次</strong><span>{loading ? "读取中…" : `${batches.length} 条`}</span></div>
          {!loading && batches.length === 0 && <p className="shop-empty">尚无整店采集批次。</p>}
          {batches.map((batch) => (
            <button key={batch.batch_id} type="button" className={batch.batch_id === selectedBatchId ? "is-selected" : ""} onClick={() => { setSelectedBatchId(batch.batch_id); setItemsOffset(0); }}>
              <span><strong>{batch.shop_name || batch.shop_sid || "等待识别店铺"}</strong><small>{shopBatchStatusLabel(batch.status)}</small></span>
              <b>{shopBatchProgress(batch)}%</b>
            </button>
          ))}
        </aside>

        <div className="shop-batch-detail">
          {!selectedBatch && <div className="shop-empty"><strong>选择或创建一个批次</strong><p>这里仅管理采集进度；商品详情和导出请在产品池中查看。</p></div>}
          {selectedBatch && (
            <>
              <div className="shop-batch-summary">
                <div><span>{shopBatchStatusLabel(selectedBatch.status)}</span><strong>{selectedBatch.shop_name || selectedBatch.shop_sid || "正在识别店铺"}</strong><small>创建于 {formatDate(selectedBatch.created_at)}</small></div>
                <div className="shop-progress"><span><i style={{ width: `${shopBatchProgress(selectedBatch)}%` }} /></span><b>{shopBatchProgress(selectedBatch)}%</b></div>
              </div>
              <div className="shop-batch-stats">
                <span><b>{selectedBatch.discovered_count}</b>已发现</span><span><b>{selectedBatch.succeeded_count}</b>成功</span><span><b>{selectedBatch.failed_count}</b>失败</span><span><b>{selectedBatch.created_count}</b>新建草稿</span>
              </div>
              {actions && <div className="shop-batch-actions">
                <button type="button" disabled={!actions.pause || Boolean(actionBusy)} onClick={() => void runAction("pause")}>暂停</button>
                <button type="button" disabled={!actions.resume || Boolean(actionBusy)} onClick={() => void runAction("resume")}>继续</button>
                <button type="button" disabled={!actions.retryFailed || Boolean(actionBusy)} onClick={() => void runAction("retryFailed")}>重试失败项</button>
                <button type="button" className="is-danger" disabled={!actions.cancel || Boolean(actionBusy)} onClick={() => void runAction("cancel")}>取消</button>
              </div>}
              {selectedBatch.error_message && <p className="shop-batch-warning">{formatShopCollectionError(new Error(selectedBatch.error_message))}</p>}
              <div className="shop-items-heading"><strong>批次商品</strong><span>{itemsTotal ? `${pageStart}–${pageEnd} / ${itemsTotal}` : "暂无商品"}</span></div>
              <div className="shop-items-list">
                {items.map((item) => <article key={item.item_id}><div><strong>{item.source_title || `商品 ${item.offer_id}`}</strong><small>{item.offer_id} · {itemStatusLabel(item)}{item.intake_action !== "none" ? ` · ${item.intake_action}` : ""}</small>{item.error_message && <em>{formatShopCollectionError(new Error(item.error_message))}</em>}</div><a href={item.source_url} target="_blank" rel="noreferrer">查看来源</a></article>)}
                {items.length === 0 && <p className="shop-empty">商品将在店铺列表发现完成后显示。</p>}
              </div>
              <div className="shop-pagination">
                <span className="shop-pagination-status">第 {currentPage} 页 / 共 {totalPages} 页</span>
                <label className="shop-page-size">
                  <span>每页</span>
                  <select
                    value={itemsPageSize}
                    onChange={(event) => {
                      setItemsPageSize(Number(event.target.value) as (typeof ITEM_PAGE_SIZE_OPTIONS)[number]);
                      setItemsOffset(0);
                    }}
                  >
                    {ITEM_PAGE_SIZE_OPTIONS.map((size) => <option key={size} value={size}>{size} 条</option>)}
                  </select>
                </label>
                <button type="button" disabled={itemsOffset === 0} onClick={() => setItemsOffset((value) => Math.max(0, value - itemsPageSize))}>上一页</button>
                <button type="button" disabled={itemsOffset + items.length >= itemsTotal} onClick={() => setItemsOffset((value) => value + itemsPageSize)}>下一页</button>
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
