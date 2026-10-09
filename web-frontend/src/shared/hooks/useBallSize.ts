import { useCallback, useSyncExternalStore } from "react";

/**
 * 悬浮球（积分 + 操作答疑）显示大小偏好（个人中心 → 偏好设置）。
 *
 * 小 / 中 / 大三档，**默认中（88px，即既有大小）**。
 * 结果同步到 <html data-ball-size="sm|md|lg">，尺寸与内部字号由
 * modules/help_agent/styles/helpAgent.css 用 CSS 变量驱动；
 * 组件侧另有像素值映射（HelpAgentWidget 的 BALANCE_BALL_SIZES）用于拖拽边界夹取。
 */

export type BallSizeId = "sm" | "md" | "lg";

export const BALL_SIZE_OPTIONS: ReadonlyArray<{ id: BallSizeId; label: string }> = [
  { id: "sm", label: "小" },
  { id: "md", label: "中" },
  { id: "lg", label: "大" },
];

const STORAGE_KEY = "mainpg.ballSize";
const DEFAULT_SIZE: BallSizeId = "md";

function readSize(): BallSizeId {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw === "sm" || raw === "lg" ? raw : DEFAULT_SIZE;
  } catch {
    return DEFAULT_SIZE;
  }
}

function applySize(size: BallSizeId) {
  document.documentElement.setAttribute("data-ball-size", size);
}

let currentSize = readSize();
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function getSnapshot() {
  return currentSize;
}

function commit(next: BallSizeId) {
  if (next === currentSize) return;
  currentSize = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch { /* 存储不可用时仅本次会话生效 */ }
  applySize(next);
  emit();
}

applySize(currentSize);

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    const next = readSize();
    if (next === currentSize) return;
    currentSize = next;
    applySize(next);
    emit();
  });
}

export function useBallSize() {
  const size = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const setSize = useCallback((next: BallSizeId) => commit(next), []);

  return { size, setSize } as const;
}
