import { useCallback, useSyncExternalStore } from "react";

// peach(桃花源)主题:背景见 shared/styles/peach-garden.css,交互特效见
// shared/components/PeachGarden.tsx(花瓣飘落/爆裂/涟漪)。已注册进主题选择器。
export type ThemeId = "classic" | "sunset" | "violet" | "dessert" | "diamond" | "quirky" | "chinese" | "peach";

export const THEME_META: Record<ThemeId, { label: string; swatch: string }> = {
  classic: { label: "经典", swatch: "linear-gradient(135deg, #087bf5, #14c8c0)" },
  sunset: { label: "暖阳橙", swatch: "linear-gradient(135deg, #e67e22, #f39c12)" },
  violet: { label: "樱雾粉紫", swatch: "linear-gradient(135deg, #f5d8e9 0 38%, #e7c9f4 38% 70%, #d57eae 70%)" },
  dessert: { label: "焦糖", swatch: "linear-gradient(135deg, #f3e3cf 0 34%, #b8754e 34% 67%, #bd7b82 67%)" },

  diamond: { label: "黑白钻石", swatch: "linear-gradient(135deg, #050505, #737985 55%, #ffffff)" },
  quirky: { label: "怪趣贴纸", swatch: "linear-gradient(135deg, #a3e635 0 34%, #fde047 34% 62%, #f43f5e 62% 78%, #7c3aed 78%)" },
  chinese: { label: "水墨青黛", swatch: "linear-gradient(135deg, #eee9dc 0 36%, #52716c 36% 72%, #a74736 72%)" },
  peach: { label: "桃花源", swatch: "linear-gradient(135deg, #ffe3ec 0 34%, #f48fb0 34% 68%, #7fb89a 68%)" },
};

const STORAGE_KEY = "mainpg.theme";

// 与 global.css 里最长的那一条对齐:卡片交错最晚 0.98s 延迟 + 0.8s 本体 ≈ 1.78s,
// 再算上白板淡入的余量。改 CSS 动画时长/延迟时这里要同步改,否则动画会被 class 移除打断。
const CASCADE_MS = 2000;

let cascadeTimer: number | undefined;

function readTheme(): ThemeId {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === "sunset" || saved === "violet" || saved === "dessert" || saved === "diamond" || saved === "quirky" || saved === "chinese" || saved === "peach" || saved === "classic") return saved;
  } catch { /* ignore */ }
  return "classic";
}

function applyTheme(id: ThemeId, animate = true) {
  const appliedTheme = document.documentElement.getAttribute("data-ui-mode") === "apple" ? "classic" : id;
  document.documentElement.setAttribute("data-theme", appliedTheme);
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
  } catch { /* ignore */ }
  if (animate) {
    // 主题切换入场:短暂挂 class 让主区克制地逐块浮现,结束后移除。
    // 时长必须 >= CSS 里动画总时长(0.85s 本体 + 最晚 0.34s 延迟),否则 class 提前移除会掐断动画、
    // 元素瞬间归位,看起来就是"卡一下"。连续切换时先清掉上一个 timer,避免旧定时器提前清 class。
    const root = document.documentElement;
    root.classList.remove("theme-cascade");
    void root.offsetWidth; // 强制 reflow,保证连续切换时动画重播
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
const listeners = new Set<() => void>();

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
