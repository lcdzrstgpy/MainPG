import { useCallback, useSyncExternalStore } from "react";

/**
 * 侧边栏偏好（个人中心 → 偏好设置）：
 *   - collapsed   是否收起（**首次默认展开**）
 *   - hoverExpand 收起状态下鼠标触碰是否临时展开（默认开，保持既有交互）
 *
 * 与 useEffectPreferences / useUiMode 同一套路：localStorage 持久化 +
 * useSyncExternalStore 广播。顶栏「固定展开」按钮与偏好设置面板共用同一份
 * 状态，任一处改动另一处立即同步。
 *
 * 注意：窄屏（isNarrowDesktop）强制折叠属于布局行为，不受这里影响。
 */

const COLLAPSED_KEY = "mainpg.sidebarCollapsed";
const HOVER_EXPAND_KEY = "mainpg.sidebarHoverExpand";

function readCollapsed(): boolean {
  try {
    // 只认显式 "1"：没有记录 / 值异常时回到默认「展开」。
    return window.localStorage.getItem(COLLAPSED_KEY) === "1";
  } catch {
    return false;
  }
}

function readHoverExpand(): boolean {
  try {
    // 只认显式 "0"：没有记录时保持既有行为（触碰展开）。
    return window.localStorage.getItem(HOVER_EXPAND_KEY) !== "0";
  } catch {
    return true;
  }
}

function persist(key: string, enabled: boolean) {
  try {
    window.localStorage.setItem(key, enabled ? "1" : "0");
  } catch { /* 存储不可用时仅本次会话生效 */ }
}

let currentCollapsed = readCollapsed();
let currentHoverExpand = readHoverExpand();
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function getCollapsed() {
  return currentCollapsed;
}

function getHoverExpand() {
  return currentHoverExpand;
}

function commitCollapsed(next: boolean) {
  if (next === currentCollapsed) return;
  currentCollapsed = next;
  persist(COLLAPSED_KEY, next);
  emit();
}

function commitHoverExpand(next: boolean) {
  if (next === currentHoverExpand) return;
  currentHoverExpand = next;
  persist(HOVER_EXPAND_KEY, next);
  emit();
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== COLLAPSED_KEY && event.key !== HOVER_EXPAND_KEY) return;
    const nextCollapsed = readCollapsed();
    const nextHoverExpand = readHoverExpand();
    if (nextCollapsed === currentCollapsed && nextHoverExpand === currentHoverExpand) return;
    currentCollapsed = nextCollapsed;
    currentHoverExpand = nextHoverExpand;
    emit();
  });
}

export function useSidebarPreferences() {
  const collapsed = useSyncExternalStore(subscribe, getCollapsed, getCollapsed);
  const hoverExpand = useSyncExternalStore(subscribe, getHoverExpand, getHoverExpand);

  const setCollapsed = useCallback((next: boolean) => commitCollapsed(next), []);
  const toggleCollapsed = useCallback(() => commitCollapsed(!currentCollapsed), []);
  const setHoverExpand = useCallback((next: boolean) => commitHoverExpand(next), []);
  const toggleHoverExpand = useCallback(() => commitHoverExpand(!currentHoverExpand), []);

  return { collapsed, hoverExpand, setCollapsed, toggleCollapsed, setHoverExpand, toggleHoverExpand } as const;
}
