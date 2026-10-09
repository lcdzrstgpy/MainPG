// SKU 批量筛选工具：跨商品/单商品复用的筛选判定与属性聚合。
import type { DraftVariant } from '../types';

export type SkuFilterState = {
  /** 选中的属性名（空 = 任意属性） */
  attrName: string;
  /** 属性值包含关键词（仅当选中属性名时生效） */
  attrValue: string;
  /** 全局关键词：在 SKU 标签（全部属性值拼接）与显示名中匹配 */
  global: string;
};

export const EMPTY_SKU_FILTER: SkuFilterState = { attrName: '', attrValue: '', global: '' };

export function variantLabel(variant: DraftVariant): string {
  const attrs = variant.attributes || {};
  return Object.values(attrs).filter(Boolean).join('/');
}

export function variantKey(variant: DraftVariant, label = variantLabel(variant)): string {
  return String(variant.sku_id || variant.source_sku_id || label);
}

/** 命中判定：属性名+属性值 与 全局关键词可叠加（两者都满足才算命中）。 */
export function matchSku(variant: DraftVariant, filter: SkuFilterState): boolean {
  const attrs = variant.attributes || {};
  const label = variantLabel(variant);
  const attrValue = filter.attrValue.trim().toLowerCase();
  const global = filter.global.trim().toLowerCase();
  if (filter.attrName) {
    const value = String(attrs[filter.attrName] ?? '').toLowerCase();
    if (attrValue && !value.includes(attrValue)) return false;
  }
  if (global) {
    const haystack = `${label} ${variant.display_name || ''}`.toLowerCase();
    if (!haystack.includes(global)) return false;
  }
  return true;
}

export function filterActive(filter: SkuFilterState): boolean {
  return Boolean(filter.attrName || filter.attrValue.trim() || filter.global.trim());
}

/** 聚合一组草稿 SKU 的属性名（去重，按首次出现顺序）。 */
export function collectAttrNames(variantsByDraft: DraftVariant[][]): string[] {
  const names: string[] = [];
  const seen = new Set<string>();
  for (const variants of variantsByDraft) {
    for (const variant of variants) {
      for (const name of Object.keys(variant.attributes || {})) {
        if (!seen.has(name)) {
          seen.add(name);
          names.push(name);
        }
      }
    }
  }
  return names;
}
