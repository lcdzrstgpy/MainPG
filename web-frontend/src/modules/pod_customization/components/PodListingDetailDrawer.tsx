import { useEffect } from "react";

import { podItemStatusLabel, podStyleTitleStatusLabel, type PodStyleRow } from "../data/podCustomizationModel";
import { PodAssetImage } from "../data/usePodAssetUrl";
import type { PodBatch } from "../types";

type Props = {
  batch: PodBatch;
  style?: PodStyleRow;
  onClose: () => void;
};

const ROLE_LABELS = ["主图", "细节图 A", "细节图 B", "场景图"] as const;

type LegacyListingDimensions = {
  length_cm?: number;
  width_cm?: number;
  height_cm?: number;
  weight_g?: number;
};

export function PodListingDetailDrawer({ batch, style, onClose }: Props) {
  useEffect(() => {
    if (!style) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose, style]);

  if (!style) return null;

  const listingFields = batch.listing_fields;
  const legacyListingFields = listingFields as (typeof listingFields & LegacyListingDimensions) | undefined;
  const skuDimensions = listingFields?.skus ?? [];
  const listingDetails = listingFields ? [
    ["申报价", listingFields.declared_price.toFixed(2)],
    ["建议售价（USD）", listingFields.suggested_price_usd.toFixed(2)],
    ["店小秘类目", listingFields.category_name],
  ] as const : [];
  const legacyWeight = legacyListingFields && skuDimensions.length === 0 && legacyListingFields.weight_g !== undefined
    ? ["重量（g）", String(legacyListingFields.weight_g)] as const
    : null;
  const legacyDimensions = legacyListingFields && skuDimensions.length === 0
    && legacyListingFields.length_cm !== undefined
    && legacyListingFields.width_cm !== undefined
    && legacyListingFields.height_cm !== undefined
    ? [
      ["长（cm）", String(legacyListingFields.length_cm)],
      ["宽（cm）", String(legacyListingFields.width_cm)],
      ["高（cm）", String(legacyListingFields.height_cm)],
    ] as const
    : [];

  return <div className="pod-listing-detail-layer">
    <button type="button" className="pod-listing-detail-backdrop" onClick={onClose} aria-label="关闭款式上架链接详情" />
    <aside className="pod-listing-detail-drawer" role="dialog" aria-modal="true" aria-label="款式上架链接详情">
      <header className="pod-listing-detail-header">
        <div><span>LISTING DETAILS</span><h2>款式 #{String(style.index).padStart(3, "0")}</h2><p>{podStyleTitleStatusLabel(style.title_status, style.listing_ready)} · 批次 {batch.id.slice(0, 8)}</p></div>
        <button type="button" onClick={onClose} aria-label="关闭">×</button>
      </header>

      <div className="pod-listing-detail-body">
        <section className="pod-listing-detail-title">
          <span>完整商品标题</span>
          <h3>{style.title}</h3>
          {style.title_error_message && <p>{style.title_error_message}</p>}
        </section>

        <section className="pod-listing-detail-snapshot">
          <header><span>DIANXIAOMI LISTING</span><h3>店小秘上架信息</h3><small>创建批次时冻结的上架快照</small></header>
          {listingDetails.length ? <dl>
            {[...listingDetails, ...(legacyWeight ? [legacyWeight] : [])].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}
          </dl> : <p>该旧批次没有保存店小秘上架快照。</p>}
          {skuDimensions.length > 0 && <div className="pod-listing-sku-dimensions">
            <h4>SKU 规格、尺寸与重量</h4>
            <div role="table" aria-label="SKU 规格、尺寸与重量">
              <div role="row" className="pod-listing-sku-dimensions-header"><span role="columnheader">SKU 名称</span><span role="columnheader">长（cm）</span><span role="columnheader">宽（cm）</span><span role="columnheader">高（cm）</span><span role="columnheader">重量（g）</span></div>
              {skuDimensions.map((sku, index) => <div role="row" key={`${sku.name}-${index}`}><span role="cell">{sku.name}</span><span role="cell">{sku.length_cm}</span><span role="cell">{sku.width_cm}</span><span role="cell">{sku.height_cm}</span><span role="cell">{sku.weight_g}</span></div>)}
            </div>
          </div>}
          {legacyDimensions.length > 0 && <div className="pod-listing-legacy-dimensions"><h4>商品尺寸</h4><dl>{legacyDimensions.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl></div>}
        </section>

        <section className="pod-listing-detail-links">
          <header><div><span>LISTING IMAGES</span><h3>四张上架图片</h3></div><small>{style.results.filter((item) => item?.public_url).length} / 4 个公网链接</small></header>
          <div>
            {style.results.map((item, offset) => {
              const preview = item?.composite_preview_url || item?.pattern_preview_url;
              return <article key={item?.id ?? `listing-detail-${style.index}-${offset}`}>
                <div className="pod-listing-detail-preview">{preview ? <PodAssetImage path={preview} alt={`${ROLE_LABELS[offset]}预览`} loading="lazy" decoding="async" /> : <span>暂无图片</span>}</div>
                <div className="pod-listing-detail-link-copy">
                  <b>{ROLE_LABELS[offset]}</b>
                  <small>{podItemStatusLabel(item?.status ?? "queued")}</small>
                  {item?.public_url ? <a href={item.public_url} target="_blank" rel="noreferrer" title={item.public_url}>查看公网图片链接</a> : <em>公网链接尚未生成</em>}
                  {item?.public_url && <code title={item.public_url}>{item.public_url}</code>}
                </div>
              </article>;
            })}
          </div>
        </section>
      </div>
    </aside>
  </div>;
}
