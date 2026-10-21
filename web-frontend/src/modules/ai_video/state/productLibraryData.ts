/**
 * 商品库本地数据层（T1.3）：纯函数，可脱离 React 单测。
 *
 * 存储键沿用 ClipForge 的 `daihuo-jianshou-products`，格式兼容 zustand persist 的
 * `{state:{products},version}` 包装；但数据落在 MainPG origin，不再随 sidecar 随机端口丢失。
 */

import type { ProductCategory, ProductItem } from "../api/types";

export const PRODUCT_LIBRARY_STORAGE_KEY = "daihuo-jianshou-products";

export interface ProductStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const CATEGORIES: readonly ProductCategory[] = ["beauty", "food", "home", "fashion", "tech", "other"];

function objectOf(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function sanitizeProduct(raw: unknown): ProductItem | null {
  const item = objectOf(raw);
  if (!item) return null;
  const id = text(item.id);
  const name = text(item.name).trim();
  if (!id || !name) return null;
  const category = CATEGORIES.includes(item.category as ProductCategory)
    ? (item.category as ProductCategory)
    : "other";
  const createdAtRaw = item.createdAt;
  const createdAt = typeof createdAtRaw === "string" || createdAtRaw instanceof Date
    ? new Date(createdAtRaw)
    : new Date(0);
  const videoCount = typeof item.videoCount === "number" && Number.isFinite(item.videoCount)
    ? Math.max(0, Math.floor(item.videoCount))
    : 0;
  return {
    id,
    name,
    category,
    images: Array.isArray(item.images)
      ? item.images.filter((image): image is string => typeof image === "string" && image.length > 0).slice(0, 5)
      : [],
    videoCount,
    createdAt: Number.isNaN(createdAt.getTime()) ? new Date(0) : createdAt,
    ...(text(item.description).trim() ? { description: text(item.description).trim() } : {}),
    ...(text(item.price).trim() ? { price: text(item.price).trim() } : {}),
    ...(text(item.targetAudience).trim() ? { targetAudience: text(item.targetAudience).trim() } : {}),
  };
}

/** localStorage 是不可信输入：坏条目直接丢弃，不让页面拿到半截商品。 */
export function sanitizeProducts(raw: unknown): ProductItem[] {
  let list: unknown[] = [];
  if (Array.isArray(raw)) {
    list = raw;
  } else {
    const state = objectOf(objectOf(raw)?.state);
    if (state && Array.isArray(state.products)) list = state.products;
  }
  const seen = new Set<string>();
  const products: ProductItem[] = [];
  for (const entry of list) {
    const product = sanitizeProduct(entry);
    if (!product || seen.has(product.id)) continue;
    seen.add(product.id);
    products.push(product);
  }
  return products;
}

export function readProductsFrom(storage: ProductStorage | null): ProductItem[] {
  if (!storage) return [];
  try {
    const stored = storage.getItem(PRODUCT_LIBRARY_STORAGE_KEY);
    return stored ? sanitizeProducts(JSON.parse(stored)) : [];
  } catch {
    return [];
  }
}

export function writeProductsTo(storage: ProductStorage | null, products: ProductItem[]): void {
  if (!storage) return;
  try {
    // 保持 zustand persist 的结构，未来若从旧 origin 迁移可直接读。
    storage.setItem(
      PRODUCT_LIBRARY_STORAGE_KEY,
      JSON.stringify({ state: { products }, version: 0 }),
    );
  } catch {
    // 隐私模式 / 配额写满：本次会话内仍可用。
  }
}
