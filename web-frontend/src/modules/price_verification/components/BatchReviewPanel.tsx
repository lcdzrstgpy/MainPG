import { useEffect, useMemo, useRef, useState } from "react";
import type { QuoteBatchReviewItem } from "../types";
import { SectionHelp } from "./SectionHelp";
import { WorkflowActionBar } from "./WorkflowActionBar";

type Props = {
  batchId: string;
  items: QuoteBatchReviewItem[];
  busy: boolean;
  onConfirm: (batchId: string, skcIds: string[], maxCandidates: number) => Promise<void>;
  onDelete: (batchId: string, skcId: string) => Promise<void>;
  onDeleteSelected: (batchId: string, skcIds: string[]) => Promise<void>;
};

function money(value?: string | number | null) {
  return value === null || value === undefined || value === "" ? "—" : `¥ ${value}`;
}

function priceRange(min?: string | number | null, max?: string | number | null) {
  const minText = min === null || min === undefined || min === "" ? "—" : `${min}`;
  if (max === null || max === undefined || max === "" || max === min) return `¥ ${minText}`;
  return `¥ ${minText} ~ ${max}`;
}

export function BatchReviewPanel({ batchId, items, busy, onConfirm, onDelete, onDeleteSelected }: Props) {
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [confirming, setConfirming] = useState(false);
  const [deletingSkcId, setDeletingSkcId] = useState("");
  const [deletingSelected, setDeletingSelected] = useState(false);
  const [maxCandidates] = useState(5);
  const floatingActionBarRef = useRef<HTMLElement>(null);
  const floatingActionBarSpacerRef = useRef<HTMLDivElement>(null);
  const selectedSkcIds = useMemo(
    () => items.filter((item) => selected[item.skc_id]).map((item) => item.skc_id),
    [items, selected],
  );
  const selectedCount = useMemo(() => Object.values(selected).filter(Boolean).length, [selected]);
  const allSelected = items.length > 0 && items.every((item) => selected[item.skc_id]);

  // .content-card 是实际滚动容器，不能只依赖 CSS sticky；滚过顶部导航后，
  // 将操作栏固定在导航下沿，同时用占位元素维持表格位置。
  useEffect(() => {
    const bar = floatingActionBarRef.current;
    const spacer = floatingActionBarSpacerRef.current;
    const contentCard = document.querySelector<HTMLElement>(".content-card");
    if (!bar || !spacer) return;
    let stuck = false;
    let naturalTop = 0;

    const reset = () => {
      bar.style.position = "";
      bar.style.top = "";
      bar.style.left = "";
      bar.style.width = "";
      spacer.style.height = "";
      bar.classList.remove("is-stuck");
    };

    const update = () => {
      const scrollTop = contentCard && contentCard.scrollHeight > contentCard.clientHeight + 1
        ? contentCard.scrollTop
        : window.scrollY || document.documentElement.scrollTop;
      const topbar = document.querySelector<HTMLElement>(".topbar-card");
      const topbarBottom = Math.max(0, Math.round(topbar?.getBoundingClientRect().bottom ?? 0));
      const threshold = topbarBottom + 8;

      if (!stuck) naturalTop = bar.getBoundingClientRect().top + scrollTop;
      const viewportTop = naturalTop - scrollTop;
      if (!stuck && viewportTop <= threshold) {
        const rect = bar.getBoundingClientRect();
        stuck = true;
        spacer.style.height = `${bar.offsetHeight}px`;
        bar.style.position = "fixed";
        bar.style.top = `${threshold}px`;
        bar.style.left = `${Math.round(rect.left)}px`;
        bar.style.width = `${Math.round(rect.width)}px`;
        bar.classList.add("is-stuck");
      } else if (stuck) {
        const spacerRect = spacer.getBoundingClientRect();
        bar.style.top = `${threshold}px`;
        bar.style.left = `${Math.round(spacerRect.left)}px`;
        bar.style.width = `${Math.round(spacerRect.width)}px`;
        if (viewportTop > threshold) {
          stuck = false;
          reset();
        }
      }
    };

    update();
    window.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
    contentCard?.addEventListener("scroll", update, { passive: true });
    const resizeObserver = new ResizeObserver(update);
    if (contentCard) resizeObserver.observe(contentCard);
    return () => {
      window.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
      contentCard?.removeEventListener("scroll", update);
      resizeObserver.disconnect();
      reset();
    };
  }, []);

  const clamp = (value: number) => Math.min(100, Math.max(1, Number.isFinite(value) ? value : 5));

  const toggle = (skcId: string, checked: boolean) => {
    setSelected((current) => ({ ...current, [skcId]: checked }));
  };

  const toggleAll = (checked: boolean) => {
    setSelected(Object.fromEntries(items.map((item) => [item.skc_id, checked])));
  };

  const confirm = async () => {
    if (!selectedSkcIds.length) return;
    setConfirming(true);
    try {
      await onConfirm(batchId, selectedSkcIds, clamp(maxCandidates));
      setSelected({});
    } finally {
      setConfirming(false);
    }
  };

  const remove = async (skcId: string) => {
    if (deletingSkcId) return;
    setDeletingSkcId(skcId);
    try {
      await onDelete(batchId, skcId);
      setSelected((current) => ({ ...current, [skcId]: false }));
    } finally {
      setDeletingSkcId("");
    }
  };

  const removeSelected = async () => {
    if (deletingSelected) return;
    setDeletingSelected(true);
    try {
      await onDeleteSelected(batchId, selectedSkcIds);
      setSelected({});
    } finally {
      setDeletingSelected(false);
    }
  };

  return (
    <section className="price-verification-panel">
      <div className="price-verification-panel-heading">
        <div>
          <p className="eyebrow">STEP 02 · CONFIRM</p>
          <h2>批次报价审核<SectionHelp title="插件采集的 Temu 本页报价（每页最多 50 个 SKC，各 SKC 可含多条 SKU 报价）经 STEP 01 初筛后展示在此。每个 SKC 一行，原申报价与调整后申报价并排对比；勾选需要保留的商品，确认后重组进入下方“待审商品最终确认”。" /></h2>
        </div>
      </div>
      <div ref={floatingActionBarSpacerRef} className="price-verification-floating-action-spacer" aria-hidden="true" />
      <WorkflowActionBar label="批次审核操作" floating ref={floatingActionBarRef}>
        <div className="price-verification-action-summary"><span>已选商品</span><strong>{selectedCount} / {items.length} 个 SKC</strong></div>
        <div className="price-verification-action-buttons">
          <button className="price-verification-secondary-button" onClick={() => toggleAll(!allSelected)} disabled={!items.length || busy}>{allSelected ? "取消全选" : "全选"}</button>
          <button className="price-verification-danger-button" onClick={() => {
            if (window.confirm(`确认删除选中的 ${selectedCount} 个 SKC？删除后不可恢复。`)) void removeSelected();
          }} disabled={!selectedSkcIds.length || busy || confirming || deletingSelected}>{deletingSelected ? "删除中…" : `删除选中${selectedCount ? `（${selectedCount}）` : ""}`}</button>
          <button className="price-verification-primary-button" onClick={() => void confirm()} disabled={!selectedSkcIds.length || busy || confirming}>{confirming ? "确认中…" : `确认并进入最终确认${selectedCount ? `（${selectedCount}）` : ""}`}</button>
        </div>
      </WorkflowActionBar>

      {items.length ? (
        <div className="batch-review-table-wrap">
          <table className="batch-review-table">
            <thead>
              <tr>
                <th className="batch-review-check-cell"><input type="checkbox" checked={allSelected} onChange={(event) => toggleAll(event.target.checked)} disabled={busy} /></th>
                <th className="batch-review-sku-info-cell">SKC 信息（{items.length}）</th>
                <th className="batch-review-site-cell">站点</th>
                <th className="batch-review-price-cell">原申报价格（CNY）</th>
                <th className="batch-review-price-cell">调整后申报价格（CNY）</th>
                <th className="batch-review-action-cell">操作</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.skc_id} className={selected[item.skc_id] ? "is-selected" : ""}>
                  <td className="batch-review-check-cell"><input type="checkbox" checked={Boolean(selected[item.skc_id])} onChange={(event) => toggle(item.skc_id, event.target.checked)} disabled={busy} /></td>
                  <td className="batch-review-sku-info-cell">
                    <div className="batch-review-sku-info">
                      {item.main_image_url ? <img src={item.main_image_url} alt="" referrerPolicy="no-referrer" /> : <div className="batch-review-no-image">无图</div>}
                      <div>
                        <strong title={item.product_title}>{item.product_title || "未命名商品"}</strong>
                        <small>SKC：{item.skc_id}</small>
                      </div>
                    </div>
                  </td>
                  <td className="batch-review-site-cell">{item.site || "—"}</td>
                  <td className="batch-review-price-cell">{priceRange(item.original_min, item.original_max)}</td>
                  <td className="batch-review-price-cell is-adjusted">{priceRange(item.adjusted_min, item.adjusted_max)}</td>
                  <td className="batch-review-action-cell">
                    <button
                      type="button"
                      className="batch-review-delete-button"
                      disabled={busy || Boolean(deletingSkcId)}
                      onClick={() => {
                        if (window.confirm(`确认删除 SKC ${item.skc_id}？删除后该商品不再出现在本次批次报价审核中。`)) {
                          void remove(item.skc_id);
                        }
                      }}
                    >
                      {deletingSkcId === item.skc_id ? "删除中…" : "删除"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="price-verification-empty-result">
          <span>◇</span>
          <div>
            <h3>等待核价采集结果</h3>
            <p>插件在 Temu“批量查看并确认申报价”页点击“采集核价本页”后，这里会即时显示每个 SKC 的原申报价与调整后申报价对比，供你勾选保留。</p>
          </div>
        </div>
      )}
    </section>
  );
}
