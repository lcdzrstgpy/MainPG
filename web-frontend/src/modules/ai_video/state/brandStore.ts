import { useEffect, useSyncExternalStore } from "react";

import {
  BRAND_STORAGE_KEY,
  readBrandFrom,
  writeBrandTo,
  type BrandConfig,
  type BrandStorage,
  type BrandWatermarkConfig,
} from "./brandData";

/** 品牌 store：localStorage + useSyncExternalStore；导出页与设置页共用。 */

function browserStorage(): BrandStorage | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

let current: BrandConfig = readBrandFrom(browserStorage());
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

function commit(next: BrandConfig) {
  current = next;
  writeBrandTo(browserStorage(), current);
  listeners.forEach((listener) => listener());
}

export const brandActions = {
  updateBrand(updates: Partial<BrandConfig>) {
    commit({ ...current, ...updates, watermark: updates.watermark ?? current.watermark });
  },
  updateWatermark(updates: Partial<BrandWatermarkConfig>) {
    commit({ ...current, watermark: { ...current.watermark, ...updates } });
  },
} as const;

export type BrandActions = typeof brandActions;

export function reloadBrandFromStorage(): void {
  const next = readBrandFrom(browserStorage());
  if (JSON.stringify(next) === JSON.stringify(current)) return;
  current = next;
  listeners.forEach((listener) => listener());
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== BRAND_STORAGE_KEY) return;
    reloadBrandFromStorage();
  });
}

export function useBrandStore(): { brand: BrandConfig } & BrandActions {
  const brand = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  useEffect(() => {
    reloadBrandFromStorage();
  }, []);
  return { brand, ...brandActions };
}
