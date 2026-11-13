import type { TemuDraftEntry } from "../types";
import { yuanToCents } from "../utils/price.ts";

/**
 * 变种级申报价：一条草稿里按变种单独定价。
 *
 * 背景：一条草稿 4 个颜色，以前只能整条一个价（¥150 全卖 150）。
 * 想「黑色 169、其他 150」做不到 —— 只能整条同价。
 *
 * 存储：`{ 草稿id: { 变种key: 价格(元) } }`，只存**改过价**的变种；
 * 没设的变种回落整条价，所以不用每个都填。
 *
 * ⚠️ 取价只走 `resolveSkuPrice` 这一个函数（批量组装 / 单件预填 / 批量编辑显示
 * 全部用它）。价格是唯一算错就直接亏钱的字段，散着写迟早出现
 * 「界面显示 169、发出去 150」。
 */

/** 一条草稿的变种价：变种 key → 价格（元，字符串，和表单一致） */
export type SkuPriceMap = Record<string, string>;

/** 所有草稿的变种价：草稿 id → 变种价 */
export type SkuPriceStore = Record<string, SkuPriceMap>;

/** 本机存储 key（按账号 scope 后使用） */
export const DRAFT_SKU_PRICES_KEY = "temu_draft_sku_prices_v1";

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
 * 取一个变种的申报价（元）。
 *
 * 顺序：变种价 → 整条价 → 空串。
 * 「变种价」只在**非空**时才算数：界面上把输入框清空表示「取消这个变种的覆盖」，
 * 要回落到整条价，不能当成 0 元发出去。
 */
export function resolveSkuPrice(
  basePrice: string | undefined,
  overrides: SkuPriceMap | undefined,
  key: string,
): string {
  const own = overrides?.[key];
  if (own !== undefined && own.trim() !== "") return own.trim();
  return String(basePrice ?? "").trim();
}

/** 同上，但直接给「分」：批量组装 payload 用这个，避免两处各转一次单位 */
export function resolveSkuPriceCents(
  basePrice: string | undefined,
  overrides: SkuPriceMap | undefined,
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
 * 全都没有 → 返回一个空 key 的「单 SKU」项，调用方按整条价处理。
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
