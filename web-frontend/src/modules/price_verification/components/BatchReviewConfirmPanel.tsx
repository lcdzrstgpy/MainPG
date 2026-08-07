import { useMemo, useState } from "react";
import type { BatchSelection } from "../types";
import { SectionHelp } from "./SectionHelp";

type Props = {
  batchId: string;
  selections: BatchSelection[];
  busy: boolean;
  sourceSkcIds: string[];
  onSourceSelectionChange: (skcIds: string[]) => void;
  onReview: (batchId: string, selectionId: number, decision: "retained" | "deleted", maxCandidates: number) => Promise<void>;
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

export function BatchReviewConfirmPanel({ batchId, selections, busy, sourceSkcIds, onSourceSelectionChange, onReview }: Props) {
  const [globalCount, setGlobalCount] = useState(5);
  const [counts, setCounts] = useState<Record<number, number>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const visible = useMemo(() => selections.filter((item) => item.status === "pending" || item.status === "retained"), [selections]);
  const pending = useMemo(() => visible.filter((item) => item.status === "pending"), [visible]);
  const retained = useMemo(() => visible.filter((item) => item.status === "retained"), [visible]);

  const clamp = (value: number) => Math.min(10, Math.max(1, Number.isFinite(value) ? value : 5));

  const countOf = (selection: BatchSelection) => counts[selection.id] ?? selection.max_candidates;

  const applyAll = () => {
    setCounts(Object.fromEntries(pending.map((item) => [item.id, clamp(globalCount)])));
  };

  const toggleSource = (skcId: string, checked: boolean) => {
    onSourceSelectionChange(checked ? [...new Set([...sourceSkcIds, skcId])] : sourceSkcIds.filter((id) => id !== skcId));
  };

  const retainedIds = retained.map((item) => item.skc_id);
  const selectedRetained = retainedIds.filter((id) => sourceSkcIds.includes(id));
  const allSelected = retainedIds.length > 0 && selectedRetained.length === retainedIds.length;

  const toggleAllSource = () => {
    onSourceSelectionChange(allSelected
      ? sourceSkcIds.filter((id) => !retainedIds.includes(id))
      : [...new Set([...sourceSkcIds, ...retainedIds])]);
  };

  const review = async (selection: BatchSelection, decision: "retained" | "deleted") => {
    if (busyId) return;
    setBusyId(selection.id);
    try {
      await onReview(batchId, selection.id, decision, countOf(selection));
    } finally {
      setBusyId(null);
    }
  };

  const renderRow = (selection: BatchSelection) => (
    <article className="price-verification-quote-card is-confirm-review" key={selection.id}>
      <label className="price-verification-source-check" title={selection.status === "retained" ? "勾选后本次图搜会搜索该 SKC；取消勾选可跳过，避免重复图搜" : "请先点击“保留 · 入草稿池”，保留后才能勾选图搜"}>
        <input type="checkbox" checked={selection.status === "retained" && sourceSkcIds.includes(selection.skc_id)} onChange={(event) => toggleSource(selection.skc_id, event.target.checked)} disabled={busyId !== null || selection.status !== "retained"} />
        <span>图搜</span>
      </label>
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
        {selection.official_link_url && <a href={selection.official_link_url} target="_blank" rel="noreferrer">查看官方链接 ↗</a>}
      </div>
      <div className="price-verification-decision">
        {selection.status === "retained" ? (
          <>
            <span className="price-verification-selection-badge">已保留 · 已入草稿池</span>
            <small className="price-verification-decision-hint">图搜相似品数量：{countOf(selection)} 条</small>
            <button className="is-selected reject" onClick={() => void review(selection, "deleted")} disabled={busyId !== null}>
              {busyId === selection.id ? "处理中…" : "撤销保留并删除"}
            </button>
          </>
        ) : (
          <>
            <label className="price-verification-count-field">
              <span>图搜相似品数量</span>
              <input
                type="number"
                min={1}
                max={10}
                value={countOf(selection)}
                disabled={busyId !== null}
                onChange={(event) => setCounts((current) => ({ ...current, [selection.id]: clamp(Number(event.target.value)) }))}
              />
            </label>
            <div className="price-verification-decision-actions">
              <button className="is-selected" onClick={() => void review(selection, "retained")} disabled={busyId !== null}>
                {busyId === selection.id ? "处理中…" : "保留 · 入草稿池"}
              </button>
              <button className="reject" onClick={() => void review(selection, "deleted")} disabled={busyId !== null}>
                删除
              </button>
            </div>
          </>
        )}
      </div>
    </article>
  );

  return (
    <section className="price-verification-panel">
      <div className="price-verification-panel-heading">
        <div>
          <p className="eyebrow">STEP 02 · FINAL REVIEW</p>
          <h2>待审商品最终确认<SectionHelp title="第一板块勾选确认的商品已重组成此待审列表，这里是最终裁决：点“保留 · 入草稿池”才写入草稿池，点“删除”即放弃；同时可为每个 SKC 指定图搜相似品数量。每行左侧可勾选是否参与图搜（默认不勾选，需先保留后才能勾选），只图搜勾选的 SKC，避免每次全量图搜产生重复。" /></h2>
        </div>
        <div className="price-verification-heading-actions">
          <label className="price-verification-count-field is-inline">
            <span>全局图搜数量</span>
            <input
              type="number"
              min={1}
              max={10}
              value={globalCount}
              disabled={busy}
              onChange={(event) => setGlobalCount(clamp(Number(event.target.value)))}
            />
            <small className="price-verification-count-hint">上限 10 个</small>
          </label>
          <button className="price-verification-secondary-button" onClick={applyAll} disabled={!pending.length || busy}>
            应用到全部待审
          </button>
          <button className="price-verification-secondary-button" onClick={toggleAllSource} disabled={!retainedIds.length || busy}>
            {allSelected ? "取消全选" : "全选已保留"}
          </button>
          <small className="price-verification-source-selected-hint">已选 {selectedRetained.length}/{retainedIds.length} 个保留 SKC 参与图搜</small>
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
            <p>请在上方“批次报价审核”中勾选需要保留的商品并确认，这里会重组展示这些 SKC，供你逐条做最终保留或删除。</p>
          </div>
        </div>
      )}
    </section>
  );
}
