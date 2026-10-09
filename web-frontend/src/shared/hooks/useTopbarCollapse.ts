import { useCallback, useSyncExternalStore } from "react";

/**
 * 顶栏「滚动收起」偏好（个人中心 → 偏好设置）。
 *
 *  开：页面往下滚，顶栏收起、只留标签行贴在顶边（桌面版原本就是这个行为，原版由
 *      本次新增的样式提供同样效果）。
 *  关（默认）：顶栏保持完整，只有普通吸顶。
 *
 * 结果同步到 <html data-topbar-collapse="on|off">，具体收起样式交给 CSS：
 *   - 桌面版：shared/styles/apple-workspace.css
 *   - 原版  ：shared/styles/global.css
 */
const STORAGE_KEY = "mainpg.topbarCollapse";

function readEnabled(): boolean {
  try {
    // 只认显式 "1"：没有记录时保持默认「不收起」。
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

function applyEnabled(enabled: boolean) {
  document.documentElement.setAttribute("data-topbar-collapse", enabled ? "on" : "off");
}

let currentEnabled = readEnabled();
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function getSnapshot() {
  return currentEnabled;
}

function commit(next: boolean) {
  if (next === currentEnabled) return;
  currentEnabled = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
  } catch { /* 存储不可用时仅本次会话生效 */ }
  applyEnabled(next);
  emit();
}

applyEnabled(currentEnabled);

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    const next = readEnabled();
    if (next === currentEnabled) return;
    currentEnabled = next;
    applyEnabled(next);
    emit();
  });
}

export function useTopbarCollapse() {
  const enabled = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const setEnabled = useCallback((next: boolean) => commit(next), []);
  const toggleEnabled = useCallback(() => commit(!currentEnabled), []);

  return { enabled, setEnabled, toggleEnabled } as const;
}
