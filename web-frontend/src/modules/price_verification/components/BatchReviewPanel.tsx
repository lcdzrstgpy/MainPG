import { Fragment, useMemo, useState } from "react";
import type { QuoteBatchReviewItem } from "../types";
import { SectionHelp } from "./SectionHelp";
import { WorkflowActionBar, useFloatingActionBar } from "./WorkflowActionBar";

type Props = {
  batchId: string;
  items: QuoteBatchReviewItem[];
  busy: boolean;
  onConfirm: (batchId: string, skcIds: string[], maxCandidates: number) => Promise<void>;
  onDelete: (batchId: string, skcId: string) => Promise<void>;
  onDeleteSelected: (batchId: string, skcIds: string[]) => Promise<void>;
  onCopySelectedSkcIds: (skcIds: string[]) => Promise<void>;
};

function money(value?: string | number | null) {
  return value === null || value === undefined || value === "" ? "—" : `¥ ${value}`;
}

function priceRange(min?: string | number | null, max?: string | number | null) {
  const minText = min === null || min === undefined || min === "" ? "—" : `${min}`;
  if (max === null || max === undefined || max === "" || max === min) return `¥ ${minText}`;
  return `¥ ${minText} ~ ${max}`;
}

export function BatchReviewPanel({ batchId, items, busy, onConfirm, onDelete, onDeleteSelected, onCopySelectedSkcIds }: Props) {
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [confirming, setConfirming] = useState(false);
  const [copying, setCopying] = useState(false);
  const [deletingSkcId, setDeletingSkcId] = useState("");
  const [deletingSelected, setDeletingSelected] = useState(false);
  const [expandedSkcIds, setExpandedSkcIds] = useState<Record<string, boolean>>({});
  const [maxCandidates] = useState(5);
  const { actionBarRef, spacerRef } = useFloatingActionBar("top");
  const selectedSkcIds = useMemo(
    () => items.filter((item) => selected[item.skc_id]).map((item) => item.skc_id),
    [items, selected],
  );
  const selectedCount = selectedSkcIds.length;
  const allSelected = items.length > 0 && items.every((item) => selected[item.skc_id]);

  const clamp = (value: number) => Math.min(100, Math.max(1, Number.isFinite(value) ? value : 5));

  const toggle = (skcId: string, checked: boolean) => {
    setSelected((current) => ({ ...current, [skcId]: checked }));
  };

  const toggleAll = (checked: boolean) => {
    setSelected(Object.fromEntries(items.map((item) => [item.skc_id, checked])));
  };

  const toggleSkuDetails = (skcId: string) => {
    setExpandedSkcIds((current) => ({ ...current, [skcId]: !current[skcId] }));
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

  const copySelectedSkcIds = async () => {
    if (!selectedSkcIds.length || copying) return;
    setCopying(true);
    try {
      await onCopySelectedSkcIds(selectedSkcIds);
    } finally {
      setCopying(false);
    }
  };

  return (
    <section className="price-verification-batch-review-panel">
      <div className="price-verification-panel-heading">
        <div>
          <h2>选择并图搜<SectionHelp title="插件采集的 Temu 本页报价经初筛后展示在此。勾选需要处理的 SKC 后，系统会直接保留商品、复用产品库已有货源，并对未命中的 SKC 执行图片搜索。" /></h2>
        </div>
      </div>
      <div ref={spacerRef} className="price-verification-floating-action-spacer" aria-hidden="true" />
      <WorkflowActionBar label="批次审核操作" floating ref={actionBarRef}>
        <div className="price-verification-action-summary"><span>已选商品</span><strong>{selectedCount} / {items.length} 个 SKC</strong></div>
        <div className="price-verification-action-buttons">
          <button className="price-verification-secondary-button" onClick={() => toggleAll(!allSelected)} disabled={!items.length || busy}>{allSelected ? "取消全选" : "全选"}</button>
          <button className="price-verification-secondary-button" onClick={() => void copySelectedSkcIds()} disabled={!selectedSkcIds.length || busy || copying}>{copying ? "复制中…" : `复制 SKC ID${selectedCount ? `（${selectedCount}）` : ""}`}</button>
          <button className="price-verification-danger-button" onClick={() => {
            if (window.confirm(`确认删除选中的 ${selectedCount} 个 SKC？删除后不可恢复。`)) void removeSelected();
          }} disabled={!selectedSkcIds.length || busy || confirming || deletingSelected}>{deletingSelected ? "删除中…" : `删除选中${selectedCount ? `（${selectedCount}）` : ""}`}</button>
          <button className="price-verification-primary-button" onClick={() => void confirm()} disabled={!selectedSkcIds.length || busy || confirming}>{confirming ? "图搜准备中…" : `确认并执行图搜${selectedCount ? `（${selectedCount}）` : ""}`}</button>
        </div>
      </WorkflowActionBar>

      {items.length ? (
        <div className="batch-review-table-wrap">
          <table className="batch-review-table">
            <colgroup>
              <col className="batch-review-col-check" />
              <col className="batch-review-col-product" />
              <col className="batch-review-col-site" />
              <col className="batch-review-col-price" />
              <col className="batch-review-col-price" />
              <col className="batch-review-col-action" />
            </colgroup>
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
              {items.map((item) => {
                const skuPrices = item.sku_prices ?? [];
                const expanded = Boolean(expandedSkcIds[item.skc_id]);
                return (
                  <Fragment key={item.skc_id}>
                    <tr className={selected[item.skc_id] ? "is-selected" : ""}>
                      <td className="batch-review-check-cell"><input type="checkbox" checked={Boolean(selected[item.skc_id])} onChange={(event) => toggle(item.skc_id, event.target.checked)} disabled={busy} /></td>
                      <td className="batch-review-sku-info-cell" data-label="商品">
                        <div className="batch-review-sku-info">
                          {item.main_image_url ? <img src={item.main_image_url} alt="" referrerPolicy="no-referrer" /> : <div className="batch-review-no-image">无图</div>}
                          <div>
                            <strong title={item.product_title}>{item.product_title || "未命名商品"}</strong>
                            <small>SKC：{item.skc_id}</small>
                            {skuPrices.length ? <button type="button" className="batch-review-sku-toggle" onClick={() => toggleSkuDetails(item.skc_id)} aria-expanded={expanded}>{expanded ? `收起下属 SKU（${skuPrices.length}）` : `查看下属 SKU（${skuPrices.length}）`}</button> : null}
                          </div>
                        </div>
                      </td>
                      <td className="batch-review-site-cell" data-label="站点">{item.site || "—"}</td>
                      <td className="batch-review-price-cell" data-label="原申报价格">{priceRange(item.original_min, item.original_max)}</td>
                      <td className="batch-review-price-cell is-adjusted" data-label="调整后申报价格">{priceRange(item.adjusted_min, item.adjusted_max)}</td>
                      <td className="batch-review-action-cell" data-label="操作">
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
                    {expanded ? (
                      <tr className="batch-review-sku-children-row">
                        <td className="batch-review-check-cell" />
                        <td colSpan={5}>
                          <div className="batch-review-sku-children">
                            <strong>下属 SKU（{skuPrices.length}）</strong>
                            <table>
                              <thead><tr><th>SKU 货号</th><th>SKU 属性</th><th>原申报价格（CNY）</th><th>调整后申报价格（CNY）</th></tr></thead>
                              <tbody>
                                {skuPrices.map((sku, index) => (
                                  <tr key={`${sku.sku_id || "sku"}-${index}`}>
                                    <td>{sku.sku_id || "—"}</td>
                                    <td>{sku.sku_attribute_text || "—"}</td>
                                    <td>{money(sku.original_declared_price_cny)}</td>
                                    <td>{money(sku.adjusted_declared_price_cny)}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
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
