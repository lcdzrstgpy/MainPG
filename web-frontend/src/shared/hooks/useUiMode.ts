import { useCallback, useSyncExternalStore } from "react";

export type UiModeId = "classic" | "apple";

export const UI_MODE_META: Record<UiModeId, { label: string; description: string }> = {
  classic: { label: "原版界面", description: "熟悉的侧栏布局" },
  apple: { label: "桌面风格", description: "桌面与 Dock 布局" },
};

const STORAGE_KEY = "mainpg.uiMode";

// 切换布局时短暂挂 class 让主区淡入,避免侧栏↔Dock 的硬切。
// 时长与 global.css 的 mode-cascade 动画总时长对齐(0.72s 本体 + 0.16s 延迟 + 余量)。
const MODE_CASCADE_MS = 1100;
let modeCascadeTimer: number | undefined;

function readUiMode(): UiModeId {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "apple" ? "apple" : "classic";
  } catch {
    return "classic";
  }
}

function applyUiMode(id: UiModeId, animate = false) {
  document.documentElement.setAttribute("data-ui-mode", id);
  let originalTheme = "classic";
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
    const savedTheme = window.localStorage.getItem("mainpg.theme");
    originalTheme = savedTheme === "sunset" || savedTheme === "violet" || savedTheme === "dessert" || savedTheme === "diamond" || savedTheme === "quirky" || savedTheme === "chinese" || savedTheme === "peach"
      ? savedTheme
      : "classic";
  } catch { /* ignore */ }
  document.documentElement.setAttribute("data-theme", id === "apple" ? "classic" : originalTheme);
  if (animate) {
    const root = document.documentElement;
    root.classList.remove("mode-cascade");
    void root.offsetWidth; // 强制 reflow,保证连续切换时动画重播
    root.classList.add("mode-cascade");
    if (modeCascadeTimer !== undefined) window.clearTimeout(modeCascadeTimer);
    modeCascadeTimer = window.setTimeout(() => {
      root.classList.remove("mode-cascade");
      modeCascadeTimer = undefined;
    }, MODE_CASCADE_MS);
  }
}

let currentUiMode: UiModeId = readUiMode();
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function getSnapshot() {
  return currentUiMode;
}

applyUiMode(currentUiMode);

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    const next = readUiMode();
    if (next === currentUiMode) return;
    currentUiMode = next;
    applyUiMode(next, true);
    listeners.forEach((listener) => listener());
  });
}

export function useUiMode() {
  const uiMode = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const setUiMode = useCallback((id: UiModeId) => {
    if (id === currentUiMode) return;
    currentUiMode = id;
    applyUiMode(id, true);
    listeners.forEach((listener) => listener());
  }, []);

  return { uiMode, setUiMode } as const;
}
