import { useMemo, useState } from "react";
import type { QuoteBatchReviewItem } from "../types";
import { SectionHelp } from "./SectionHelp";

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
  const [maxCandidates, setMaxCandidates] = useState(5);
  const selectedSkcIds = useMemo(
    () => items.filter((item) => selected[item.skc_id]).map((item) => item.skc_id),
    [items, selected],
  );
  const selectedCount = useMemo(() => Object.values(selected).filter(Boolean).length, [selected]);
  const allSelected = items.length > 0 && items.every((item) => selected[item.skc_id]);

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
        <div className="price-verification-heading-actions">
          <label className="price-verification-count-field is-inline">
            <span>图搜相似品数量</span>
            <input
              type="number"
              min={1}
              max={100}
              value={maxCandidates}
              disabled={busy || confirming}
              onChange={(event) => setMaxCandidates(clamp(Number(event.target.value)))}
            />
          </label>
          <button className="price-verification-secondary-button" onClick={() => toggleAll(!allSelected)} disabled={!items.length || busy}>
            {allSelected ? "取消全选" : "全选"}
          </button>
          <button
            className="price-verification-danger-button"
            onClick={() => {
              if (window.confirm(`确认删除选中的 ${selectedCount} 个 SKC？删除后不可恢复。`)) {
                void removeSelected();
              }
            }}
            disabled={!selectedSkcIds.length || busy || confirming || deletingSelected}
          >
            {deletingSelected ? "删除中…" : `删除选中${selectedCount ? `（${selectedCount}）` : ""}`}
          </button>
          <button className="price-verification-primary-button" onClick={() => void confirm()} disabled={!selectedSkcIds.length || busy || confirming}>
            {confirming ? "确认中…" : `确认加入待审列表${selectedCount ? `（${selectedCount} 个 SKC）` : ""}`}
          </button>
        </div>
      </div>

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
                    {item.official_link_url && <a href={item.official_link_url} target="_blank" rel="noreferrer">官方链接 ↗</a>}
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
