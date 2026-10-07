import { isSpecCardConfigured, specCardSummaryText } from "../data/podCustomizationModel";
import type { PodListingFieldsDraft, SpecCardConfig } from "../types";

export type SkuField = "name" | "declared_price" | "weight_g";
export type SkuFieldErrors = Record<string, string>;

const SKU_FIELD_LABELS: Record<SkuField, string> = {
  name: "名称",
  declared_price: "申报价",
  weight_g: "重量",
};

export function skuErrorKey(index: number, key: SkuField): string {
  return `${index}:${key}`;
}

export function validateSkuFields(skus: PodListingFieldsDraft["skus"]): SkuFieldErrors {
  return skus.reduce<SkuFieldErrors>((errors, sku, index) => {
    const skuLabel = sku.name.trim() || `第 ${index + 1} 个 SKU`;
    (Object.keys(SKU_FIELD_LABELS) as SkuField[]).forEach((key) => {
      const value = sku[key].trim();
      if (!value) {
        errors[skuErrorKey(index, key)] = `SKU「${skuLabel}」的${SKU_FIELD_LABELS[key]}不能为空。`;
      } else if (key !== "name" && (!Number.isFinite(Number(value)) || Number(value) <= 0)) {
        errors[skuErrorKey(index, key)] = `SKU「${skuLabel}」的${SKU_FIELD_LABELS[key]}必须是大于 0 的有效数字。`;
      }
    });
    return errors;
  }, {});
}

const LISTING_FIELDS: Array<{
  key: "suggested_price_usd" | "category_name";
  label: string;
  inputMode?: "decimal" | "numeric";
}> = [
  { key: "suggested_price_usd", label: "建议售价（USD）", inputMode: "decimal" },
  { key: "category_name", label: "店小秘类目" },
];

type Props = {
  listingFields: PodListingFieldsDraft;
  specCard: SpecCardConfig;
  skuFieldErrors: SkuFieldErrors;
  skuLimitReached: boolean;
  onListingFieldChange: (key: "title_mode" | "suggested_price_usd" | "category_name", value: string) => void;
  onAddSku: () => void;
  onUpdateSku: (index: number, key: SkuField, value: string) => void;
  onRemoveSku: (index: number) => void;
  onOpenSpecCardDrawer: () => void;
};

/**
 * 受控的「店小秘上架信息」编辑器：售价、标题模式、店小秘类目、SKU（名称/申报价/重量）与规格卡入口。
 * 全定制页与爆款复刻页共用同一套字段与校验，尺寸-SKU 同步仍由宿主页面调用 podCustomizationModel 的
 * buildSpecCardCells 完成（本组件只管渲染与回调，不持有自己的尺寸规则）。
 */
export function PodListingFieldsEditor({
  listingFields,
  specCard,
  skuFieldErrors,
  skuLimitReached,
  onListingFieldChange,
  onAddSku,
  onUpdateSku,
  onRemoveSku,
  onOpenSpecCardDrawer,
}: Props) {
  return (
    <section className="pod-listing-fields" aria-labelledby="pod-dianxiaomi-listing-title">
      <div className="pod-listing-fields-heading"><span>DIANXIAOMI LISTING</span><h3 id="pod-dianxiaomi-listing-title">店小秘上架信息</h3><small>创建批次时保存为不可缺失的上架快照</small></div>
      <div className="pod-title-mode" role="radiogroup" aria-label="标题模式">
        <span>标题模式<em>*</em></span>
        <div>
          <button type="button" role="radio" aria-checked={listingFields.title_mode === "long"} className={listingFields.title_mode === "long" ? "is-active" : ""} onClick={() => onListingFieldChange("title_mode", "long")}><b>长标题</b></button>
          <button type="button" role="radio" aria-checked={listingFields.title_mode === "short"} className={listingFields.title_mode === "short" ? "is-active" : ""} onClick={() => onListingFieldChange("title_mode", "short")}><b>短标题</b></button>
        </div>
      </div>
      <div className="pod-business-fields">
        {LISTING_FIELDS.map((field) => <label key={field.key}><span>{field.label}<em>*</em></span><input value={listingFields[field.key]} inputMode={field.inputMode} onChange={(event) => onListingFieldChange(field.key, event.target.value)} /></label>)}
      </div>
      <div className="pod-sku-editor" aria-label="SKU 预设">
        <div className="pod-sku-editor-heading"><span>SKU 预设<small>每个 SKU 需填写名称、申报价与重量</small></span><button type="button" onClick={onAddSku} disabled={skuLimitReached} aria-describedby={skuLimitReached ? "pod-sku-limit-notice" : undefined} title={skuLimitReached ? "最多可添加 100 个 SKU" : undefined}><span className="iconfont icon-plus" aria-hidden="true" />新增 SKU</button></div>
        {skuLimitReached && <p id="pod-sku-limit-notice" className="pod-sku-limit-notice" role="status">已达到 100 个 SKU 上限。</p>}
        <div className="pod-sku-inputs">
          {listingFields.skus.map((sku, index) => <div key={index} className="pod-sku-input-row">
            <label><span>SKU 名称 {index + 1}</span><input value={sku.name} onChange={(event) => onUpdateSku(index, "name", event.target.value)} aria-label="SKU 名称" aria-invalid={Boolean(skuFieldErrors[skuErrorKey(index, "name")])} aria-describedby={skuFieldErrors[skuErrorKey(index, "name")] ? `pod-sku-error-${index}-name` : undefined} />{skuFieldErrors[skuErrorKey(index, "name")] && <small id={`pod-sku-error-${index}-name`} className="pod-sku-field-error">{skuFieldErrors[skuErrorKey(index, "name")]}</small>}</label>
            <label><span>申报价</span><input value={sku.declared_price} inputMode="decimal" onChange={(event) => onUpdateSku(index, "declared_price", event.target.value)} aria-label="SKU 申报价" aria-invalid={Boolean(skuFieldErrors[skuErrorKey(index, "declared_price")])} aria-describedby={skuFieldErrors[skuErrorKey(index, "declared_price")] ? `pod-sku-error-${index}-declared_price` : undefined} />{skuFieldErrors[skuErrorKey(index, "declared_price")] && <small id={`pod-sku-error-${index}-declared_price`} className="pod-sku-field-error">{skuFieldErrors[skuErrorKey(index, "declared_price")]}</small>}</label>
            <label><span>重量（g）</span><input value={sku.weight_g} inputMode="decimal" onChange={(event) => onUpdateSku(index, "weight_g", event.target.value)} aria-label="SKU 重量（g）" aria-invalid={Boolean(skuFieldErrors[skuErrorKey(index, "weight_g")])} aria-describedby={skuFieldErrors[skuErrorKey(index, "weight_g")] ? `pod-sku-error-${index}-weight_g` : undefined} />{skuFieldErrors[skuErrorKey(index, "weight_g")] && <small id={`pod-sku-error-${index}-weight_g`} className="pod-sku-field-error">{skuFieldErrors[skuErrorKey(index, "weight_g")]}</small>}</label>
            <button type="button" onClick={() => onRemoveSku(index)} aria-label="删除 SKU">×</button>
          </div>)}
        </div>
      </div>
      <div className="pod-spec-card-entry" aria-label="规格卡配置">
        <button type="button" className="pod-spec-card-entry-button" onClick={onOpenSpecCardDrawer}>批量添加尺寸<em>*</em></button>
        <span className={isSpecCardConfigured(specCard) ? "pod-spec-card-entry-summary" : "pod-spec-card-entry-summary is-warning"}>{specCardSummaryText(specCard)}</span>
      </div>
    </section>
  );
}