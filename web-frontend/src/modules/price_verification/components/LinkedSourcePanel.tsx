import type { ProductLibraryMatch, ProductLibrarySource } from "../types";
import { SectionHelp } from "./SectionHelp";

type Props = { products: ProductLibraryMatch[] };

function money(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? `¥${number.toFixed(2)}` : "—";
}

function siteLabel(site?: string) {
  if (site === "US") return "美国站";
  if (site === "CO") return "哥伦比亚站";
  if (site === "EC") return "厄瓜多尔站";
  return site || "—";
}

function sourceGroups(product: ProductLibraryMatch): ProductLibrarySource[] {
  if (product.source_groups?.length) return product.source_groups;
  return product.source_url ? [{ source_url: product.source_url }] : [];
}

export function LinkedSourcePanel({ products }: Props) {
  return <section className="price-verification-panel price-verification-linked-panel"><div className="price-verification-panel-heading"><div><p className="eyebrow">STEP 04 · SOURCED</p><h2>已关联 1688 货源<SectionHelp title="只展示本轮勾选 SKC 在产品库中已存在或刚完成入库的 1688 货源，不混入其他批次。" /></h2></div><div className="price-verification-linked-summary"><div><span>已关联 SKC</span><strong>{products.length}</strong></div><div><span>已关联 1688 链接</span><strong>{products.reduce((sum, product) => sum + sourceGroups(product).length, 0)}</strong></div></div></div>
    {products.length ? <div className="price-verification-linked-groups">{products.map((product) => {
      const groups = sourceGroups(product);
      return <div className="price-verification-linked-group" key={product.id || product.skc}><div className="price-verification-source-group-head"><strong>{product.skc}</strong><small>{siteLabel(product.site)} · 调整后申报价 {money(product.selling_price)} · 已入产品库</small></div><div className="price-verification-source-linked">{groups.length ? groups.map((source, index) => <div key={`${product.skc}-${source.offer_id || source.source_url || index}`}><a href={source.source_url} target="_blank" rel="noreferrer">{source.main_image_url ? <img src={source.main_image_url} alt="" loading="lazy" referrerPolicy="no-referrer" /> : <span />}<span><b className="source-link-title">{source.source_title || source.offer_id || "1688 货源"}</b><small className="source-link-offer">{source.offer_id ? `offer ${source.offer_id}` : "已关联"}</small></span></a><span className="source-link-source-price"><small>源价</small><b>{money(source.price_cny)}</b></span></div>) : <small>产品库中未保存明细链接</small>}</div></div>;
    })}</div> : <p className="price-verification-source-empty-line">本轮暂无已入库的 1688 货源。完成上方图搜候选的关联后会展示在这里。</p>}
  </section>;
}
