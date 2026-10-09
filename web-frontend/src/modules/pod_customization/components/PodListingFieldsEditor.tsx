import { isSpecCardConfigured, specCardSummaryText } from "../data/podCustomizationModel";
import type { PodListingFieldsDraft, SpecCardConfig } from "../types";

export type SkuField = "name" | "declared_price" | "weight_g";
export type SkuFieldErrors = Record<string, string>;

// 与后端 contracts.py 对齐的上限：ListingSku.name / ListingFields.category_name 均为 120，
// 复刻产品名 ReplicaTargetCreate.product_name 为 500。
export const SKU_NAME_MAX_LENGTH = 120;
export const LISTING_CATEGORY_MAX_LENGTH = 120;
export const REPLICA_PRODUCT_NAME_MAX_LENGTH = 500;

const SKU_FIELD_LABELS: Record<SkuField, string> = {
  name: "名称",
  declared_price: "申报价",
  weight_g: "重量",
};

// 金额：只接受「正整数或最多两位小数」的纯数字写法，挡住 1e3、0x1、「一万块」这类输入。
const MONEY_PATTERN = /^\d+(\.\d{1,2})?$/;
// 重量/尺寸：正整数或小数，同样必须是纯数字写法。
const PLAIN_NUMBER_PATTERN = /^\d+(\.\d+)?$/;
// 类目必须含中文（拒绝 111、bags 这类纯数字/纯英文），其余字符不限。
const CHINESE_PATTERN = /[\u3400-\u9fff]/;

export function skuErrorKey(index: number, key: SkuField): string {
  return `${index}:${key}`;
}

/**
 * SKU 行校验：名称非空且不超长；申报价与重量必须是大于 0 的纯数字（金额最多两位小数）。
 * 消息按字段就近展示，不再重复 SKU 名与字段名，保持单行，避免换行顶动输入框。
 */
export function validateSkuFields(skus: PodListingFieldsDraft["skus"]): SkuFieldErrors {
  return skus.reduce<SkuFieldErrors>((errors, sku, index) => {
    (Object.keys(SKU_FIELD_LABELS) as SkuField[]).forEach((key) => {
      const value = sku[key].trim();
      const errorKey = skuErrorKey(index, key);
      if (!value) {
        errors[errorKey] = `${SKU_FIELD_LABELS[key]}不能为空。`;
        return;
      }
      if (key === "name") {
        if (value.length > SKU_NAME_MAX_LENGTH) errors[errorKey] = `名称不能超过 ${SKU_NAME_MAX_LENGTH} 个字符。`;
        return;
      }
      const pattern = key === "declared_price" ? MONEY_PATTERN : PLAIN_NUMBER_PATTERN;
      if (!pattern.test(value)) {
        errors[errorKey] = key === "declared_price" ? "只能填数字，最多两位小数。" : "只能填数字。";
        return;
      }
      if (Number(value) <= 0) errors[errorKey] = "必须大于 0。";
    });
    return errors;
  }, {});
}

/**
 * 上架信息整表校验：SKU 行 + 建议售价 + 店小秘类目。
 * 错误键沿用 `index:field`（SKU）与字段名本身（suggested_price_usd / category_name），供编辑器内联展示。
 */
export function validateListingFields(fields: PodListingFieldsDraft): SkuFieldErrors {
  const errors = { ...validateSkuFields(fields.skus) };

  const price = fields.suggested_price_usd.trim();
  if (!price) errors.suggested_price_usd = "不能为空。";
  else if (!MONEY_PATTERN.test(price)) errors.suggested_price_usd = "只能填数字，最多两位小数。";
  else if (Number(price) <= 0) errors.suggested_price_usd = "必须大于 0。";

  const category = fields.category_name.trim();
  if (!category) errors.category_name = "不能为空。";
  else if (category.length > LISTING_CATEGORY_MAX_LENGTH) errors.category_name = `不能超过 ${LISTING_CATEGORY_MAX_LENGTH} 个字符。`;
  else if (!CHINESE_PATTERN.test(category)) errors.category_name = "需包含中文，不能纯英文或纯数字。";

  return errors;
}

const LISTING_FIELDS: Array<{
  key: "suggested_price_usd" | "category_name";
  label: string;
  inputMode?: "decimal" | "numeric";
  maxLength?: number;
}> = [
  { key: "suggested_price_usd", label: "建议售价（USD）", inputMode: "decimal" },
  { key: "category_name", label: "店小秘类目", maxLength: LISTING_CATEGORY_MAX_LENGTH },
];

type Props = {
  listingFields: PodListingFieldsDraft;
  specCard: SpecCardConfig;
  skuFieldErrors: SkuFieldErrors;
  skuLimitReached: boolean;
  /** 提交过一次后连「必填为空」也一起提示；在此之前只提示「已填但不合规」，避免新表单一片红。 */
  showRequiredErrors?: boolean;
  onListingFieldChange: (key: "title_mode" | "suggested_price_usd" | "category_name", value: string) => void;
  onAddSku: () => void;
  onUpdateSku: (index: number, key: SkuField, value: string) => void;
  onRemoveSku: (index: number) => void;
  onOpenSpecCardDrawer: () => void;
};

/**
 * 决定某个字段此刻是否展示错误：已填但不合规立即提示（实时），必填为空则在提交过一次后提示。
 * 错误槽位始终保留固定高度，因此提示出现/消失都不会顶动输入框。
 */
function visibleFieldError(error: string | undefined, value: string, showRequiredErrors: boolean): string {
  return error && (value.trim() !== "" || showRequiredErrors) ? error : "";
}

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
  showRequiredErrors = false,
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
        {LISTING_FIELDS.map((field) => {
          const fieldError = visibleFieldError(skuFieldErrors[field.key], listingFields[field.key], showRequiredErrors);
          return (
            <label key={field.key}>
              <span>{field.label}<em>*</em></span>
              <div className="pod-listing-field-input">
                <input value={listingFields[field.key]} inputMode={field.inputMode} maxLength={field.maxLength} aria-invalid={Boolean(fieldError)} aria-describedby={fieldError ? `pod-listing-error-${field.key}` : undefined} onChange={(event) => onListingFieldChange(field.key, event.target.value)} />
                <small id={`pod-listing-error-${field.key}`} className="pod-listing-field-error">{fieldError}</small>
              </div>
            </label>
          );
        })}
      </div>
      <div className="pod-sku-editor" aria-label="SKU 预设">
        <div className="pod-sku-editor-heading"><span>SKU 预设<small>每个 SKU 需填写名称、申报价与重量</small></span><button type="button" onClick={onAddSku} disabled={skuLimitReached} aria-describedby={skuLimitReached ? "pod-sku-limit-notice" : undefined} title={skuLimitReached ? "最多可添加 100 个 SKU" : undefined}><span className="iconfont icon-plus" aria-hidden="true" />新增 SKU</button></div>
        {skuLimitReached && <p id="pod-sku-limit-notice" className="pod-sku-limit-notice" role="status">已达到 100 个 SKU 上限。</p>}
        <div className="pod-sku-inputs">
          {listingFields.skus.map((sku, index) => {
            const nameError = visibleFieldError(skuFieldErrors[skuErrorKey(index, "name")], sku.name, showRequiredErrors);
            const priceError = visibleFieldError(skuFieldErrors[skuErrorKey(index, "declared_price")], sku.declared_price, showRequiredErrors);
            const weightError = visibleFieldError(skuFieldErrors[skuErrorKey(index, "weight_g")], sku.weight_g, showRequiredErrors);
            return (
              <div key={index} className="pod-sku-input-row">
                <label>
                  <span>SKU 名称 {index + 1}</span>
                  <input value={sku.name} maxLength={SKU_NAME_MAX_LENGTH} onChange={(event) => onUpdateSku(index, "name", event.target.value)} aria-label="SKU 名称" aria-invalid={Boolean(nameError)} aria-describedby={nameError ? `pod-sku-error-${index}-name` : undefined} />
                  <small id={`pod-sku-error-${index}-name`} className="pod-sku-field-error">{nameError}</small>
                </label>
                <label>
                  <span>申报价</span>
                  <input value={sku.declared_price} inputMode="decimal" onChange={(event) => onUpdateSku(index, "declared_price", event.target.value)} aria-label="SKU 申报价" aria-invalid={Boolean(priceError)} aria-describedby={priceError ? `pod-sku-error-${index}-declared_price` : undefined} />
                  <small id={`pod-sku-error-${index}-declared_price`} className="pod-sku-field-error">{priceError}</small>
                </label>
                <label>
                  <span>重量（g）</span>
                  <input value={sku.weight_g} inputMode="decimal" onChange={(event) => onUpdateSku(index, "weight_g", event.target.value)} aria-label="SKU 重量（g）" aria-invalid={Boolean(weightError)} aria-describedby={weightError ? `pod-sku-error-${index}-weight_g` : undefined} />
                  <small id={`pod-sku-error-${index}-weight_g`} className="pod-sku-field-error">{weightError}</small>
                </label>
                <button type="button" onClick={() => onRemoveSku(index)} aria-label="删除 SKU">×</button>
              </div>
            );
          })}
        </div>
      </div>
      <div className="pod-spec-card-entry" aria-label="规格卡配置">
        <button type="button" className="pod-spec-card-entry-button" onClick={onOpenSpecCardDrawer}>批量添加尺寸<em>*</em></button>
        <span className={isSpecCardConfigured(specCard) ? "pod-spec-card-entry-summary" : "pod-spec-card-entry-summary is-warning"}>{specCardSummaryText(specCard)}</span>
      </div>
    </section>
  );
}