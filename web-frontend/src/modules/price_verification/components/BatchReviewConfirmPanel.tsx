import { useMemo } from "react";
import type { BatchSelection } from "../types";
import { SectionHelp } from "./SectionHelp";
import { WorkflowActionBar } from "./WorkflowActionBar";

type Props = {
  batchId: string;
  selections: BatchSelection[];
  busy: boolean;
  sourceSkcIds: string[];
  onSourceSelectionChange: (skcIds: string[]) => void;
  onContinue: () => void;
};

function money(value?: string | number | null) {
  return value === null || value === undefined || value === "" ? "—" : `¥ ${value}`;
}

function priceCell(label: string, min?: string | number | null, max?: string | number | null, highlight = false) {
  const minText = min === null || min === undefined || min === "" ? "—" : `¥ ${min}`;
  const maxText = max === null || max === undefined || max === "" || max === min ? "" : ` ~ ¥ ${max}`;
  return (
    <span className={highlight ? "is-adjusted" : ""}>
      <small>{label}</small>
      <b>{minText}{maxText}</b>
    </span>
  );
}

export function BatchReviewConfirmPanel({ batchId, selections, busy, sourceSkcIds, onSourceSelectionChange, onContinue }: Props) {
  // 进入待审列表即自动保留入草稿池，这里展示所有未删除的商品供勾选图搜
  const visible = useMemo(() => selections.filter((item) => item.status !== "deleted"), [selections]);

  const toggleSource = (skcId: string, checked: boolean) => {
    onSourceSelectionChange(checked ? [...new Set([...sourceSkcIds, skcId])] : sourceSkcIds.filter((id) => id !== skcId));
  };

  const retainedIds = visible.map((item) => item.skc_id);
  const selectedRetained = retainedIds.filter((id) => sourceSkcIds.includes(id));
  const allSelected = retainedIds.length > 0 && selectedRetained.length === retainedIds.length;

  const toggleAllSource = () => {
    onSourceSelectionChange(allSelected
      ? sourceSkcIds.filter((id) => !retainedIds.includes(id))
      : [...new Set([...sourceSkcIds, ...retainedIds])]);
  };

  const renderRow = (selection: BatchSelection) => (
    <article className="price-verification-quote-card is-confirm-review" key={selection.id}>
      <div className="price-verification-quote-image">
        {selection.main_image_url ? <img src={selection.main_image_url} alt="" referrerPolicy="no-referrer" /> : "无图"}
      </div>
      <div className="price-verification-quote-body">
        <strong>{selection.product_title || "未命名商品"}</strong>
        <span>{selection.skc_id}{selection.site ? ` · ${selection.site}` : ""} · {selection.sku_prices.length} 个 SKU · {selection.source_confidence || "待确认"}</span>
        <div className="price-verification-price-compare">
          {priceCell("原申报价", selection.original_min, selection.original_max)}
          {priceCell("调整后申报价", selection.adjusted_min, selection.adjusted_max, true)}
        </div>
        {selection.sku_prices.length > 1 && (
          <details className="price-verification-sku-detail">
            <summary>查看 {selection.sku_prices.length} 个 SKU 明细</summary>
            <div className="price-verification-sku-rows">
              {selection.sku_prices.map((sku) => (
                <div key={sku.sku_id || `${selection.skc_id}-${sku.sku_attribute_text || "?"}`}>
                  <span>{sku.sku_id || sku.sku_attribute_text || "—"}</span>
                  <i>原 {money(sku.original_declared_price_cny)}</i>
                  <b>调 {money(sku.adjusted_declared_price_cny)}</b>
                </div>
              ))}
            </div>
          </details>
        )}
      </div>
      <div className="price-verification-decision">
        <span className="price-verification-selection-badge">已入草稿池</span>
        <label
          className="price-verification-source-check"
          title="勾选后本次图搜会搜索该 SKC；取消勾选可跳过，避免重复图搜"
        >
          <input
            type="checkbox"
            checked={sourceSkcIds.includes(selection.skc_id)}
            onChange={(event) => toggleSource(selection.skc_id, event.target.checked)}
            disabled={busy}
          />
          <span>图搜</span>
        </label>
      </div>
    </article>
  );

  return (
    <section className="price-verification-panel">
      <div className="price-verification-panel-heading">
        <div>
          <p className="eyebrow">STEP 02 · FINAL REVIEW</p>
          <h2>待审商品最终确认<SectionHelp title="第一板块勾选确认的商品已自动保留入草稿池并重组成此待审列表，无需二次确认。图搜相似品数量固定为 5。每行右侧可勾选是否参与图搜，只图搜勾选的 SKC，避免每次全量图搜产生重复。" /></h2>
        </div>
      </div>
      {visible.length ? (
        <div className="price-verification-quote-list">
          {visible.map(renderRow)}
        </div>
      ) : (
        <div className="price-verification-empty-result">
          <span>◇</span>
          <div>
            <h3>待审列表为空</h3>
            <p>请在上方“批次报价审核”中勾选需要保留的商品并确认，这里会展示已自动保留的 SKC，供你勾选图搜。</p>
          </div>
        </div>
      )}
      <WorkflowActionBar label="最终确认操作">
        <div className="price-verification-action-summary"><span>图搜对象</span><strong>{selectedRetained.length} / {retainedIds.length} 个已保留 SKC</strong></div>
        <div className="price-verification-action-buttons">
          <button className="price-verification-secondary-button" onClick={toggleAllSource} disabled={!retainedIds.length || busy}>{allSelected ? "取消全选" : "全选已保留"}</button>
          <button className="price-verification-primary-button" onClick={onContinue} disabled={!selectedRetained.length || busy}>进入货源匹配（{selectedRetained.length}）</button>
        </div>
      </WorkflowActionBar>
    </section>
  );
}
