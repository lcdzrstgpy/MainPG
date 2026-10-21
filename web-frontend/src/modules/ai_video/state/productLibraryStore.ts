import { useEffect, useSyncExternalStore } from "react";

import type { ProductItem } from "../api/types";
import {
  PRODUCT_LIBRARY_STORAGE_KEY,
  readProductsFrom,
  writeProductsTo,
  type ProductStorage,
} from "./productLibraryData";

/** 商品库 store：localStorage + useSyncExternalStore，与 settingsStore 同一套路。 */

function browserStorage(): ProductStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

let current: ProductItem[] = readProductsFrom(browserStorage());
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot() {
  return current;
}

function commit(next: ProductItem[]) {
  current = next;
  writeProductsTo(browserStorage(), current);
  listeners.forEach((listener) => listener());
}

export const productLibraryActions = {
  addProduct(product: ProductItem) {
    if (current.some((item) => item.id === product.id)) return;
    commit([product, ...current]);
  },
  updateProduct(id: string, updates: Partial<ProductItem>) {
    commit(current.map((item) => (item.id === id ? { ...item, ...updates } : item)));
  },
  removeProduct(id: string) {
    commit(current.filter((item) => item.id !== id));
  },
  incrementVideoCount(id: string) {
    commit(current.map((item) => (item.id === id ? { ...item, videoCount: item.videoCount + 1 } : item)));
  },
} as const;

export type ProductLibraryActions = typeof productLibraryActions;

export function getProductsSnapshot(): ProductItem[] {
  return current;
}

export function reloadProductsFromStorage(): void {
  const next = readProductsFrom(browserStorage());
  if (JSON.stringify(next) === JSON.stringify(current)) return;
  current = next;
  listeners.forEach((listener) => listener());
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== PRODUCT_LIBRARY_STORAGE_KEY) return;
    reloadProductsFromStorage();
  });
}

export function useProductLibraryStore(): { products: ProductItem[] } & ProductLibraryActions {
  const products = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  // 跨标签页写入时由 storage 事件刷新；挂载时再读一次，保证从 /start 回来后看到最新库存。
  useEffect(() => {
    reloadProductsFromStorage();
  }, []);
  return { products, ...productLibraryActions };
}
