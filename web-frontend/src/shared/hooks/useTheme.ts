import { useCallback, useSyncExternalStore } from "react";

export type ThemeId = "classic" | "sunset" | "dessert" | "diamond" | "quirky" | "chinese";

export const THEME_META: Record<ThemeId, { label: string; swatch: string }> = {
  classic: { label: "经典", swatch: "linear-gradient(135deg, #087bf5, #14c8c0)" },
  sunset: { label: "暖阳橙", swatch: "linear-gradient(135deg, #e67e22, #f39c12)" },
  dessert: { label: "焦糖", swatch: "linear-gradient(135deg, #f3e3cf 0 34%, #b8754e 34% 67%, #bd7b82 67%)" },

  diamond: { label: "黑白钻石", swatch: "linear-gradient(135deg, #050505, #737985 55%, #ffffff)" },
  quirky: { label: "怪趣贴纸", swatch: "linear-gradient(135deg, #a3e635 0 34%, #fde047 34% 62%, #f43f5e 62% 78%, #7c3aed 78%)" },
  chinese: { label: "水墨青黛", swatch: "linear-gradient(135deg, #eee9dc 0 36%, #52716c 36% 72%, #a74736 72%)" },
};

const STORAGE_KEY = "mainpg.theme";

function readTheme(): ThemeId {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "sunset" || saved === "dessert" || saved === "diamond" || saved === "quirky" || saved === "chinese" || saved === "classic") return saved;
  } catch { /* ignore */ }
  return "classic";
}

function applyTheme(id: ThemeId) {
  const appliedTheme = document.documentElement.getAttribute("data-ui-mode") === "apple" ? "classic" : id;
  document.documentElement.setAttribute("data-theme", appliedTheme);
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch { /* ignore */ }
}

// ---- external store for cross-tab sync ----
let currentTheme: ThemeId = readTheme();
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function getSnapshot() {
  return currentTheme;
}

// Apply the stored theme on first import (before React mounts)
applyTheme(currentTheme);

// Listen for storage changes from other tabs
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key !== STORAGE_KEY) return;
    const next = readTheme();
    if (next !== currentTheme) {
      currentTheme = next;
      applyTheme(currentTheme);
      listeners.forEach((fn) => fn());
    }
  });
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const setTheme = useCallback((id: ThemeId) => {
    if (id === currentTheme) return;
    currentTheme = id;
    applyTheme(id);
    listeners.forEach((fn) => fn());
  }, []);

  return { theme, setTheme } as const;
}
