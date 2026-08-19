import { useCallback, useSyncExternalStore } from "react";

export type UiModeId = "classic" | "apple";

export const UI_MODE_META: Record<UiModeId, { label: string; description: string }> = {
  classic: { label: "原版界面", description: "熟悉的侧栏布局" },
  apple: { label: "桌面风格", description: "桌面与 Dock 布局" },
};

const STORAGE_KEY = "mainpg.uiMode";

function readUiMode(): UiModeId {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "apple" ? "apple" : "classic";
  } catch {
    return "classic";
  }
}

function applyUiMode(id: UiModeId) {
  document.documentElement.setAttribute("data-ui-mode", id);
  let originalTheme = "classic";
  try {
    window.localStorage.setItem(STORAGE_KEY, id);
    const savedTheme = window.localStorage.getItem("mainpg.theme");
    originalTheme = savedTheme === "sunset" || savedTheme === "violet" || savedTheme === "diamond" || savedTheme === "quirky" || savedTheme === "chinese"
      ? savedTheme
      : "classic";
  } catch { /* ignore */ }
  document.documentElement.setAttribute("data-theme", id === "apple" ? "classic" : originalTheme);
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
    applyUiMode(next);
    listeners.forEach((listener) => listener());
  });
}

export function useUiMode() {
  const uiMode = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const setUiMode = useCallback((id: UiModeId) => {
    if (id === currentUiMode) return;
    currentUiMode = id;
    applyUiMode(id);
    listeners.forEach((listener) => listener());
  }, []);

  return { uiMode, setUiMode } as const;
}
