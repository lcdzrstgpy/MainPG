import type { TemuDraftEntry } from "../types";

/**
 * 草稿当前要用的轮播图。
 *
 * 一条规则，三处共用（批量编辑图片 / 用于发布 / 批量发布），
 * 谁也别自己拼，否则「改完图发出去还是原图」这种问题查不出来。
 *
 * 覆盖用 `!== undefined` 判断：把图删光（[]）是合法编辑结果，
 * 用 `override?.length` 会让删光的行又退回源图。
 */
export function resolveEntryImages(
  entry: TemuDraftEntry,
  overrides?: Record<string, string[]>,
): string[] {
  const override = overrides?.[entry.id];
  if (override !== undefined) return override;
  return entry.carousel_images?.length ? entry.carousel_images : entry.images.slice(0, 1);
}

/** Temu 轮播图张数：3~10 张（少于 3 张会被判图片不足） */
export const CAROUSEL_MIN = 3;
export const CAROUSEL_MAX = 10;

/** 图片编辑的存储 key（按账号 scope 后使用） */
export const DRAFT_IMAGES_KEY = "temu_draft_images_v1";

/** 是否真的和源图不同（相同就不落存储，避免「看着改过其实没改」） */
export function sameImages(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((item, index) => item === b[index]);
}
