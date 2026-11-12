import { forwardRef, type FormEvent, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { shopCollectionApi } from "../api/shopCollectionApi";
import "../styles/shop-collection.css";
import {
  formatShopCollectionError,
  getShopBatchActions,
  isActiveShopBatchStatus,
  isTerminalShopBatchStatus,
  shopBatchProgress,
  shopBatchStatusLabel,
  shopItemDisplayTitle,
  type ShopCollectionBatch,
  type ShopCollectionItem,
} from "../data/shopCollectionModel";

type ShopCollectionPanelProps = {
  isActive?: boolean;
  onBatchCountChange?: (count: number) => void;
};

export type ShopCollectionPanelHandle = {
  openBatchManager: () => void;
};

const ITEM_PAGE_SIZE_OPTIONS = [10, 20, 50, 100] as const;

function detectInputPlatform(value: string): "1688" | "taobao" | null {
  const candidate = value.trim();
  if (!/^https?:\/\//i.test(candidate)) return null;
  try {
    const host = new URL(candidate).hostname.toLowerCase();
    if (host === "taobao.com" || host.endsWith(".taobao.com") || host === "tmall.com" || host.endsWith(".tmall.com")) {
      return "taobao";
    }
    if (host === "1688.com" || host.endsWith(".1688.com")) return "1688";
  } catch {
    return null;
  }
  return null;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  return `¥${value.toFixed(2)}`;
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

export const ShopCollectionPanel = forwardRef<ShopCollectionPanelHandle, ShopCollectionPanelProps>(function ShopCollectionPanel({ isActive = true, onBatchCountChange }, ref) {
  const [shopPlatform, setShopPlatform] = useState<"1688" | "taobao">("1688");
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

  /** 商品列表请求序号：切换批次时旧响应晚到会被丢弃，防止列表显示错批次商品。 */
  const itemsRequestSeq = useRef(0);

  const refreshItems = useCallback(async (batchId: string, offset: number) => {
    const seq = ++itemsRequestSeq.current;
    try {
      const page = await shopCollectionApi.listItems(batchId, itemsPageSize, offset);
      if (seq !== itemsRequestSeq.current) return; // 过期响应：已有更新的请求发出
      setItems(page.items);
      setItemsTotal(page.total);
    } catch (requestError) {
      if (seq !== itemsRequestSeq.current) return;
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

  // 依赖布尔量而不是 batches 数组本身：批次列表每轮刷新都会生成新数组，
  // 若把它放进依赖，定时器会被每轮结果反复清理重建；这里只在“是否存在活跃批次”真正翻转时重启轮询。
  const hasActiveBatch = useMemo(
    () => batches.some((batch) => isActiveShopBatchStatus(batch.status)),
    [batches],
  );

  useEffect(() => {
    if (!isActive || !hasActiveBatch) return;
    const timer = window.setInterval(() => {
      void refreshBatches();
      if (selectedBatchId) void refreshItems(selectedBatchId, itemsOffset);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [hasActiveBatch, isActive, itemsOffset, refreshBatches, refreshItems, selectedBatchId]);

  function handleSourceInputChange(value: string) {
    setSourceInput(value);
    const detected = detectInputPlatform(value);
    if (detected && detected !== shopPlatform) {
      setShopPlatform(detected);
      setNotice(
        detected === "taobao"
          ? "已按链接识别为淘宝/天猫，将按淘宝整店采集。"
          : "已按链接识别为 1688，将按 1688 整店采集。",
      );
    }
  }

  async function createBatch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = sourceInput.trim();
    if (!value) {
      setError(shopPlatform === "taobao" ? "请输入淘宝/天猫商品链接、商品 ID 或店铺线索" : "请输入 1688 商品链接、商品 ID 或店铺线索");
      return;
    }
    setSubmitting(true);
    setError("");
    try {
      const created = await shopCollectionApi.createBatch(value, shopPlatform);
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
  const currentPage = itemsTotal === 0 ? 1 : Math.floor(itemsOffset / itemsPageSize) + 1;
  const totalPages = Math.max(1, Math.ceil(itemsTotal / itemsPageSize));

  return (
    <section className="shop-collection-panel" aria-label="整店采集">
      <form className="shop-collection-create" onSubmit={createBatch}>
        <div className="shop-create-field">
          <span className="shop-create-label">
            商品链接
            <span className="shop-platform-switch" role="group" aria-label="采集平台">
              <button type="button" className={shopPlatform === "1688" ? "is-active" : ""} aria-pressed={shopPlatform === "1688"} onClick={() => setShopPlatform("1688")}>1688</button>
              <button type="button" className={shopPlatform === "taobao" ? "is-active" : ""} aria-pressed={shopPlatform === "taobao"} onClick={() => setShopPlatform("taobao")}>淘宝 / 天猫</button>
            </span>
          </span>
          <input aria-label="商品链接" value={sourceInput} onChange={(event) => handleSourceInputChange(event.target.value)}
            placeholder={shopPlatform === "taobao"
              ? "粘贴淘宝/天猫商品链接或店铺主页，平台按链接自动识别（如 item.taobao.com/item.htm?id=…）"
              : "粘贴 1688 店铺主页、商品链接或商品 ID；数字店铺 SID 请写成 sid:123456（平台按链接自动识别）"} />
        </div>
        <button type="submit" disabled={submitting}>{submitting ? "正在创建…" : "开始采集"}</button>
      </form>
      {(error || notice) && <p className={`shop-collection-message ${error ? "is-error" : "is-success"}`} role="status">{error || notice}</p>}

      <div className="shop-collection-grid">
        <div className="shop-batch-detail">
          {!selectedBatch && (
            <div className="shop-onboard">
              <div className="shop-onboard-copy">
                <span className="shop-onboard-eyebrow">SHOP SWEEP · 整店采集</span>
                <strong>把整个店铺一次性扫一遍</strong>
                <p>粘贴店铺主页或任一商品链接，后台会把店内商品逐个发现、补详情，离开页面也不会中断。</p>
                <ol className="shop-onboard-steps">
                  <li><b>01</b><span><strong>识别店铺</strong><small>按链接自动判断 1688 / 淘宝，先列出店内商品</small></span></li>
                  <li><b>02</b><span><strong>逐款补详情</strong><small>SKU、阶梯价、属性与库存，失败项可单独重试</small></span></li>
                  <li><b>03</b><span><strong>结果归档</strong><small>商品进候选池与草稿，可在批次里回看和续采</small></span></li>
                </ol>
                <div className="shop-onboard-inputs">
                  <span>店铺主页链接</span><span>商品链接</span><span>商品 ID</span><span>sid:123456</span>
                </div>
              </div>
              <div className="shop-onboard-side">
                <svg className="shop-onboard-art" viewBox="0 0 240 168" aria-hidden="true" focusable="false">
                  <defs>
                    <linearGradient id="shopOnboardFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0" stopColor="#63cbe8" stopOpacity=".22" />
                      <stop offset="1" stopColor="#25bda3" stopOpacity=".05" />
                    </linearGradient>
                  </defs>
                  <rect x="30" y="58" width="132" height="86" rx="10" fill="url(#shopOnboardFill)" stroke="#8fd3e6" />
                  <path d="M22 58h148" stroke="#3aa9c9" strokeWidth="2.4" strokeLinecap="round" />
                  <path d="M32 58a10 10 0 0 0 20 0 10 10 0 0 0 20 0 10 10 0 0 0 20 0 10 10 0 0 0 20 0 10 10 0 0 0 20 0 10 10 0 0 0 20 0" fill="none" stroke="#3aa9c9" strokeWidth="1.3" opacity=".7" />
                  <g fill="none" stroke="#7fc8dd" strokeWidth="1.2">
                    <rect x="44" y="76" width="30" height="22" rx="4" />
                    <rect x="82" y="76" width="30" height="22" rx="4" />
                    <rect x="120" y="76" width="30" height="22" rx="4" />
                    <rect x="44" y="106" width="30" height="22" rx="4" />
                    <rect x="82" y="106" width="30" height="22" rx="4" />
                  </g>
                  <circle cx="168" cy="104" r="26" fill="rgba(37,189,163,.07)" stroke="#25bda3" strokeWidth="1.3" />
                  <path d="M147 98h34M147 106h24M147 114h17" stroke="#8fd3e6" strokeWidth="1.2" strokeLinecap="round" opacity=".8" />
                  <path d="M187 123l13 13" stroke="#25bda3" strokeWidth="3.4" strokeLinecap="round" />
                  <g stroke="#9fd8e9" strokeWidth="1" strokeDasharray="3 7" opacity=".6">
                    <path d="M30 40h132" /><path d="M30 30h96" />
                  </g>
                </svg>
                <small className="shop-onboard-note">采集只跑进度；商品详情、导出和核价在产品池里继续。</small>
              </div>
            </div>
          )}
          {selectedBatch && (
            <>
              <div className="shop-batch-summary">
                <div className="shop-batch-info">
                  <div className="shop-batch-idline">
                    <span className="shop-batch-status">{shopBatchStatusLabel(selectedBatch.status)}</span>
                    <span className="shop-batch-fact"><em>店铺名称</em><b title={selectedBatch.shop_name || selectedBatch.shop_sid}>{selectedBatch.shop_name || selectedBatch.shop_sid || "正在识别店铺"}</b></span>
                    <span className="shop-batch-fact"><em>平台</em><b>{selectedBatch.platform === "taobao" ? "淘宝 / 天猫" : "1688"}</b></span>
                    <small className="shop-batch-created">创建于 {formatDate(selectedBatch.created_at)}</small>
                  </div>
                  <div className="shop-progress"><span><i style={{ width: `${shopBatchProgress(selectedBatch)}%` }} /></span><b>{shopBatchProgress(selectedBatch)}%</b></div>
                </div>
                {actions && (
                  <div className="shop-batch-actions">
                    <button type="button" disabled={!actions.pause || Boolean(actionBusy)} onClick={() => void runAction("pause")}>暂停</button>
                    <button type="button" disabled={!actions.resume || Boolean(actionBusy)} onClick={() => void runAction("resume")}>继续</button>
                    <button type="button" disabled={!actions.retryFailed || Boolean(actionBusy)} onClick={() => void runAction("retryFailed")}>重试失败项</button>
                    <button type="button" className="is-danger" disabled={!actions.cancel || Boolean(actionBusy)} onClick={() => void runAction("cancel")}>取消</button>
                  </div>
                )}
              </div>
              <div className="shop-batch-stats">
                <span><b>{selectedBatch.discovered_count}</b>总商品</span><span><b>{selectedBatch.succeeded_count}</b>已采集</span><span><b>{selectedBatch.failed_count}</b>失败数</span><span><b>{selectedBatch.created_count}</b>新建草稿</span>
              </div>
              {selectedBatch.error_message && <p className="shop-batch-warning">{formatShopCollectionError(new Error(selectedBatch.error_message))}</p>}
              <div className="shop-items-list">
                {items.map((item) => (
                  <article key={item.item_id}>
                    <span className="shop-item-name"><em>商品名</em>{shopItemDisplayTitle(item)}</span>
                    <small className="shop-item-meta">
                      {[item.offer_id, formatPrice(item.candidate?.price_cny) && `售价 ${formatPrice(item.candidate?.price_cny)}`, itemStatusLabel(item), item.intake_action !== "none" ? item.intake_action : ""].filter(Boolean).join(" · ")}
                    </small>
                    {item.error_message && <small className="shop-item-meta is-error">{formatShopCollectionError(new Error(item.error_message))}</small>}
                    <a className="shop-item-link" href={item.source_url} target="_blank" rel="noreferrer" title={item.source_url}>{item.source_url}</a>
                  </article>
                ))}
                {items.length === 0 && (
                  <div className={`shop-pending${isTerminalShopBatchStatus(selectedBatch.status) ? " is-done" : ""}`}>
                    <span className="shop-pending-radar" aria-hidden="true"><i /><i /><i /></span>
                    <div>
                      <strong>
                        {isTerminalShopBatchStatus(selectedBatch.status)
                          ? "本批次没有可展示的商品"
                          : selectedBatch.status === "resolving"
                            ? "正在识别店铺"
                            : "正在发现店内商品"}
                      </strong>
                      <p>
                        {isTerminalShopBatchStatus(selectedBatch.status)
                          ? "可能是链接没能识别到店铺，或店内商品全部重复；换一个店铺主页链接重新采集即可。"
                          : "后台会先列出店内商品，再逐款补 SKU 与价格，进度会实时刷新，可以先去处理别的事。"}
                      </p>
                      <small>已发现 {selectedBatch.discovered_count} 个 · 已补全 {selectedBatch.succeeded_count} 个 · 失败 {selectedBatch.failed_count} 个</small>
                    </div>
                  </div>
                )}
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

      {batchManagerOpen && createPortal(
        <div className="shop-batch-drawer-layer">
          <button type="button" className="shop-batch-drawer-backdrop" onClick={() => setBatchManagerOpen(false)} aria-label="关闭批次管理" />
          <aside className="shop-batch-drawer" role="dialog" aria-modal="true" aria-label="整店采集批次管理">
            <header className="shop-batch-drawer-header">
              <div><span>BATCH MANAGER</span><h2>批次管理</h2><p>选择一个批次，即可在工作台查看进度和商品。</p></div>
              <button type="button" onClick={() => setBatchManagerOpen(false)} aria-label="关闭批次管理">×</button>
            </header>
            <div className="shop-batch-drawer-summary"><span>整店采集批次</span><b>{loading ? "读取中…" : `${batches.length} 条`}</b></div>
            <div className="shop-batch-drawer-list">
              {!loading && batches.length === 0 && <p className="shop-empty">尚无整店采集批次。</p>}
              {batches.map((batch) => (
                <button
                  key={batch.batch_id}
                  type="button"
                  className={batch.batch_id === selectedBatchId ? "is-selected" : ""}
                  onClick={() => { setSelectedBatchId(batch.batch_id); setItemsOffset(0); setBatchManagerOpen(false); }}
                >
                  <span><strong>{batch.shop_name || batch.shop_sid || "等待识别店铺"}</strong><small>{shopBatchStatusLabel(batch.status)}</small></span>
                  <b>{shopBatchProgress(batch)}%</b>
                </button>
              ))}
            </div>
          </aside>
        </div>,
        document.body,
      )}
    </section>
  );
});
