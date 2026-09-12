import { useCallback, useMemo, useSyncExternalStore } from "react";

// peach(桃花源)主题:背景见 shared/styles/peach-garden.css,交互特效见
// shared/components/PeachGarden.tsx(花瓣飘落/爆裂/涟漪)。已注册进主题选择器。
export type ThemeId = "classic" | "sunset" | "violet" | "dessert" | "diamond" | "quirky" | "chinese" | "peach";

export interface ThemeMeta {
  id: ThemeId;
  label: string;
  swatch: string;
  description?: string;
  version?: string;
}

export const THEME_META: Record<ThemeId, Omit<ThemeMeta, "id">> = {
  classic: { label: "经典", swatch: "linear-gradient(135deg, #087bf5, #14c8c0)" },
  sunset: { label: "暖阳橙", swatch: "linear-gradient(135deg, #e67e22, #f39c12)" },
  peach: { label: "桃花源", swatch: "linear-gradient(135deg, #ffe3ec 0 34%, #f48fb0 34% 68%, #7fb89a 68%)" },
  violet: { label: "樱雾粉紫", swatch: "linear-gradient(135deg, #f5d8e9 0 38%, #e7c9f4 38% 70%, #d57eae 70%)" },
  dessert: { label: "焦糖", swatch: "linear-gradient(135deg, #f3e3cf 0 34%, #b8754e 34% 67%, #bd7b82 67%)" },
  diamond: { label: "黑白钻石", swatch: "linear-gradient(135deg, #050505, #737985 55%, #ffffff)" },
  quirky: { label: "怪趣贴纸", swatch: "linear-gradient(135deg, #a3e635 0 34%, #fde047 34% 62%, #f43f5e 62% 78%, #7c3aed 78%)" },
  chinese: { label: "水墨青黛", swatch: "linear-gradient(135deg, #eee9dc 0 36%, #52716c 36% 72%, #a74736 72%)" },
};

/** 安装即内置、在快捷面板直接展示的主题。 */
export const BUILTIN_THEME_IDS: readonly ThemeId[] = ["classic", "sunset", "peach"];

/** 需要通过「主题商店」从后端下载后才能使用的主题（服务器可能返回不同集合）。 */
export const KNOWN_DOWNLOADABLE_IDS: readonly ThemeId[] = ["violet", "dessert", "diamond", "quirky", "chinese"];

const STORAGE_KEY = "mainpg.theme";
const DOWNLOADED_THEMES_KEY = "mainpg.downloadedThemes";

// 与 global.css 里最长的那一条对齐:卡片交错最晚 0.98s 延迟 + 0.8s 本体 ≈ 1.78s,
// 再算上白板淡入的余量。改 CSS 动画时长/延迟时这里要同步改,否则动画会被 class 移除打断。
const CASCADE_MS = 2000;

let cascadeTimer: number | undefined;

function isThemeId(value: string): value is ThemeId {
  return value in THEME_META;
}

function readTheme(): ThemeId {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && isThemeId(saved)) return saved;
  } catch { /* ignore */ }
  return "classic";
}

interface DownloadedTheme {
  id: ThemeId;
  css: string;
  label: string;
  swatch: string;
  description?: string;
  version?: string;
}

function readDownloadedThemes(): Map<ThemeId, DownloadedTheme> {
  try {
    const raw = window.localStorage.getItem(DOWNLOADED_THEMES_KEY);
    if (!raw) return new Map();
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return new Map();
    const map = new Map<ThemeId, DownloadedTheme>();
    for (const item of parsed) {
      if (
        item &&
        typeof item === "object" &&
        "id" in item &&
        typeof item.id === "string" &&
        isThemeId(item.id) &&
        "css" in item &&
        typeof item.css === "string"
      ) {
        map.set(item.id, item as DownloadedTheme);
      }
    }
    return map;
  } catch { /* ignore */ }
  return new Map();
}

function saveDownloadedThemes(themes: Map<ThemeId, DownloadedTheme>) {
  try {
    window.localStorage.setItem(DOWNLOADED_THEMES_KEY, JSON.stringify([...themes.values()]));
  } catch { /* ignore */ }
}

function injectThemeCss(id: ThemeId, css: string) {
  const styleId = `theme-css-${id}`;
  let style = document.getElementById(styleId) as HTMLStyleElement | null;
  if (!style) {
    style = document.createElement("style");
    style.id = styleId;
    style.setAttribute("data-theme-id", id);
    document.head.appendChild(style);
  }
  style.textContent = css;
}

function removeThemeCss(id: ThemeId) {
  const style = document.getElementById(`theme-css-${id}`);
  if (style) style.remove();
}

function ensureDownloadedCssInjected(themes: Map<ThemeId, DownloadedTheme>) {
  for (const [id, meta] of themes) {
    injectThemeCss(id, meta.css);
  }
}

function applyTheme(id: ThemeId, animate = true) {
  const appliedTheme = document.documentElement.getAttribute("data-ui-mode") === "apple" ? "classic" : id;
  document.documentElement.setAttribute("data-theme", appliedTheme);
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch { /* ignore */ }
  if (animate) {
    const root = document.documentElement;
    root.classList.remove("theme-cascade");
    void root.offsetWidth;
    root.classList.add("theme-cascade");
    if (cascadeTimer !== undefined) window.clearTimeout(cascadeTimer);
    cascadeTimer = window.setTimeout(() => {
      root.classList.remove("theme-cascade");
      cascadeTimer = undefined;
    }, CASCADE_MS);
  }
}

// ---- external store for cross-tab sync ----
let currentTheme: ThemeId = readTheme();
let downloadedThemes: Map<ThemeId, DownloadedTheme> = readDownloadedThemes();
const listeners = new Set<() => void>();
const downloadedListeners = new Set<() => void>();

// Re-inject CSS for downloaded themes on module load (after HMR/refresh)
ensureDownloadedCssInjected(downloadedThemes);

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function getSnapshot() {
  return currentTheme;
}

// Apply the stored theme on first import (before React mounts)
applyTheme(currentTheme, false);

// Listen for storage changes from other tabs
if (typeof window !== "undefined") {
  window.addEventListener("storage", (e) => {
    if (e.key === STORAGE_KEY) {
      const next = readTheme();
      if (next !== currentTheme) {
        currentTheme = next;
        applyTheme(currentTheme);
        listeners.forEach((fn) => fn());
      }
    }
    if (e.key === DOWNLOADED_THEMES_KEY) {
      const next = readDownloadedThemes();
      downloadedThemes = next;
      ensureDownloadedCssInjected(downloadedThemes);
      downloadedListeners.forEach((fn) => fn());
    }
  });
}

// ---- backend API ----

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export interface ThemeListItemFromServer {
  id: string;
  label: string;
  description: string;
  swatch: string;
  version: string;
  installed: boolean;
}

export interface ThemePackageFromServer {
  manifest: ThemeListItemFromServer;
  css: string;
}

export async function fetchThemeList(): Promise<ThemeListItemFromServer[]> {
  const res = await fetch(`${API_BASE}/themes`);
  if (!res.ok) throw new Error(`Failed to fetch theme list: ${res.status}`);
  const data = (await res.json()) as { themes: ThemeListItemFromServer[] };
  return data.themes;
}

export async function fetchThemePackage(id: string): Promise<ThemePackageFromServer> {
  const res = await fetch(`${API_BASE}/themes/${encodeURIComponent(id)}/package`);
  if (!res.ok) throw new Error(`Failed to fetch theme ${id}: ${res.status}`);
  return (await res.json()) as ThemePackageFromServer;
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  const setTheme = useCallback((id: ThemeId) => {
    if (id === currentTheme) return;
    currentTheme = id;
    applyTheme(id);
    listeners.forEach((fn) => fn());
  }, []);

  const downloaded = useSyncExternalStore(
    useCallback((cb) => {
      downloadedListeners.add(cb);
      return () => { downloadedListeners.delete(cb); };
    }, []),
    useCallback(() => downloadedThemes, []),
    useCallback(() => downloadedThemes, [])
  );

  const isDownloaded = useCallback((id: ThemeId) => {
    return BUILTIN_THEME_IDS.includes(id) || downloaded.has(id);
  }, [downloaded]);

  const downloadTheme = useCallback(async (id: ThemeId): Promise<void> => {
    if (BUILTIN_THEME_IDS.includes(id) || downloadedThemes.has(id)) return;
    const pkg = await fetchThemePackage(id);
    injectThemeCss(id, pkg.css);
    const meta: DownloadedTheme = {
      id,
      css: pkg.css,
      label: pkg.manifest.label,
      swatch: pkg.manifest.swatch,
      description: pkg.manifest.description,
      version: pkg.manifest.version,
    };
    downloadedThemes = new Map(downloadedThemes);
    downloadedThemes.set(id, meta);
    saveDownloadedThemes(downloadedThemes);
    downloadedListeners.forEach((fn) => fn());
  }, []);

  const downloadedMeta = useMemo(() => [...downloaded.values()], [downloaded]);

  return {
    theme,
    setTheme,
    downloadedThemes: downloadedMeta,
    downloadedThemeMap: downloaded,
    isDownloaded,
    downloadTheme,
  } as const;
}
