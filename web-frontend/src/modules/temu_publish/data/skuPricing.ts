import type { TemuDraftEntry } from "../types";
import { yuanToCents } from "../utils/price.ts";

/**
 * 变种级 SKU 字段：一条草稿里按变种单独定价 / 定库存 / 定尺寸重量 / 定货号。
 *
 * 背景：衣服鞋包按尺码、箱包按款式，长宽高重量本来就不同；不同颜色库存也不同。
 * 「整条一个价、一个尺寸」只能应付单 SKU 商品。
 *
 * 存储：`{ 草稿id: { 变种key: {字段: 值} } }`，只存**改过**的字段；
 * 没设的字段回落整条值，所以不用每个都填。
 *
 * ⚠️ 取值只走 `resolveSkuField` 这一个函数（批量组装 / 单件预填 / 批量编辑显示
 * 全部用它）。价格是唯一算错就直接亏钱的字段，散着写迟早出现
 * 「界面显示 169、发出去 150」。
 */

/** 能按变种单独设的字段（都和 TemuDraftEdit / VariantRow 对齐） */
export type SkuField =
  | "declared_price"
  | "stock"
  | "weight_g"
  | "length_cm"
  | "width_cm"
  | "height_cm"
  | "sku";

/** 一个变种改过的字段：字段 → 值（空串 = 取消覆盖，回落整条） */
export type SkuVariantFields = Partial<Record<SkuField, string>>;

/** 一条草稿的变种覆盖：变种 key → 改过的字段 */
export type SkuFieldMap = Record<string, SkuVariantFields>;

/** 所有草稿的变种覆盖：草稿 id → 变种覆盖 */
export type SkuFieldStore = Record<string, SkuFieldMap>;

/** 本机存储 key（按账号 scope 后使用） */
export const DRAFT_SKU_FIELDS_KEY = "temu_draft_sku_fields_v1";

/** 按变种拆行时可改的列（顺序 = 表格列序） */
export const SKU_FIELD_COLUMNS: Array<{
  key: SkuField;
  label: string;
  width: number;
  kind: "text" | "number" | "sku";
  decimals?: number;
}> = [
  { key: "declared_price", label: "申报价格(元)", width: 112, kind: "number", decimals: 2 },
  { key: "sku", label: "SKU货号", width: 132, kind: "sku" },
  { key: "stock", label: "库存", width: 86, kind: "number", decimals: 0 },
  { key: "weight_g", label: "重量(g)", width: 92, kind: "number", decimals: 0 },
  { key: "length_cm", label: "长(cm)", width: 88, kind: "number", decimals: 1 },
  { key: "width_cm", label: "宽(cm)", width: 88, kind: "number", decimals: 1 },
  { key: "height_cm", label: "高(cm)", width: 88, kind: "number", decimals: 1 },
];

/**
 * 变种 key：颜色 + 尺码。
 *
 * 用 `|` 拼接；值里本身带 `|` 的极少数情况替换成 `/`，避免拼出二义性的 key
 * （比如「黑|白」+「M」和「黑」+「白|M」会撞成同一个字符串）。
 */
export function skuKeyOf(color?: string | null, size?: string | null): string {
  const clean = (value?: string | null) => String(value ?? "").trim().replace(/\|/g, "/");
  const c = clean(color);
  const s = clean(size);
  return s ? `${c}|${s}` : c;
}

/**
 * 取一个变种某个字段的值。
 *
 * 顺序：变种值 → 整条值 → 空串。
 * 「变种值」只在**非空**时才算数：界面上把输入框清空表示「取消这个变种的覆盖」，
 * 要回落到整条值，不能当成 0 / 空发出去。
 */
export function resolveSkuField(
  variant: SkuVariantFields | undefined,
  field: SkuField,
  baseValue: string | number | undefined | null,
): string {
  const own = variant?.[field];
  if (own !== undefined && String(own).trim() !== "") return String(own).trim();
  return String(baseValue ?? "").trim();
}

/** 申报价（元）：便捷写法 */
export function resolveSkuPrice(
  basePrice: string | undefined,
  overrides: SkuFieldMap | undefined,
  key: string,
): string {
  return resolveSkuField(overrides?.[key], "declared_price", basePrice);
}

/** 申报价（分）：批量组装 payload 用，避免两处各转一次单位 */
export function resolveSkuPriceCents(
  basePrice: string | undefined,
  overrides: SkuFieldMap | undefined,
  key: string,
  label = "申报价格",
): number {
  return yuanToCents(resolveSkuPrice(basePrice, overrides, key), label);
}

/** 一个变种的展示名（表格行首用）：有尺码就「颜色 | 尺码」 */
export function variantLabel(color?: string | null, size?: string | null): string {
  const c = String(color ?? "").trim();
  const s = String(size ?? "").trim();
  if (c && s) return `${c} | ${s}`;
  return c || s || "单 SKU";
}

/**
 * 一条草稿有哪些变种（顺序稳定：先颜色后尺码，和发布时的生成顺序一致）。
 *
 * 优先用 `variants`（AI/POD 导出的真实变种列表）；没有就按
 * `spec_values_1 × spec_values_2` 铺组合；再没有就用单值字段。
 * 全都没有 → 返回一个空 key 的「单 SKU」项，调用方按整条值处理。
 */
export function variantKeysOf(
  entry: Pick<TemuDraftEntry, "variants" | "spec_values_1" | "spec_values_2" | "spec_value_1" | "spec_value_2">,
): Array<{ key: string; color: string; size: string; label: string }> {
  const out: Array<{ key: string; color: string; size: string; label: string }> = [];
  const push = (color?: string | null, size?: string | null) => {
    const key = skuKeyOf(color, size);
    if (out.some((item) => item.key === key)) return;
    out.push({
      key,
      color: String(color ?? "").trim(),
      size: String(size ?? "").trim(),
      label: variantLabel(color, size),
    });
  };

  const variants = entry.variants ?? [];
  if (variants.length) {
    variants.forEach((item) => push(item.color, item.size));
    return out;
  }

  const colors = (entry.spec_values_1 ?? []).map((item) => String(item).trim()).filter(Boolean);
  const sizes = (entry.spec_values_2 ?? []).map((item) => String(item).trim()).filter(Boolean);
  const fallbackColor = String(entry.spec_value_1 ?? "").trim();
  const fallbackSize = String(entry.spec_value_2 ?? "").trim();

  const colorList = colors.length ? colors : fallbackColor ? [fallbackColor] : [];
  const sizeList = sizes.length ? sizes : fallbackSize ? [fallbackSize] : [];

  if (colorList.length && sizeList.length) {
    colorList.forEach((color) => sizeList.forEach((size) => push(color, size)));
  } else if (colorList.length) {
    colorList.forEach((color) => push(color, ""));
  } else if (sizeList.length) {
    sizeList.forEach((size) => push("", size));
  } else {
    push("", "");
  }
  return out;
}
