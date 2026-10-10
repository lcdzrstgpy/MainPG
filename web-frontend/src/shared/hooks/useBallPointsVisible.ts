import { useCallback, useSyncExternalStore } from "react";

/**
 * 悬浮球「积分数字」是否可见（个人中心 → 偏好设置）。
 *
 * 关掉后球面照旧显示「积分」标签，但数字换成「?」—— 悬浮球一直浮在界面上，
 * 公共场合（截图、投屏、旁边有人）容易被一眼看到余额，这是对应的隐私开关。
 *
 * 作用范围：**只影响悬浮球**。个人中心里的积分总览与流水照旧显示数字
 * （那里本来就是主动点进去看的）。
 *
 * 与 useBallSize 同一套写法：localStorage 持久化 + useSyncExternalStore 广播
 * + 多标签页 storage 事件同步。**不需要 <html> 属性** —— 数字是组件里渲染的，
 * 样式层不参与（区别于 useBallSize / useEffectPreferences 那类要驱动 CSS 的偏好）。
 */

const STORAGE_KEY = "mainpg.ballPointsVisible";

function readVisible(): boolean {
  try {
    // 只认显式 "off"：字段缺失（老版本写入 / 手工改坏）时按可见处理。
    return window.localStorage.getItem(STORAGE_KEY) !== "off";
  } catch {
    return true;
  }
}

let currentVisible = readVisible();
const listeners = new Set<() => void>();

function emit() {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function getSnapshot() {
  return currentVisible;
}

function commit(next: boolean) {
  if (next === currentVisible) return;
  currentVisible = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next ? "on" : "off");
  } catch { /* 存储不可用时仅本次会话生效 */ }
  emit();
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    const next = readVisible();
    if (next === currentVisible) return;
    currentVisible = next;
    emit();
  });
}

export function useBallPointsVisible() {
  const visible = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const setVisible = useCallback((next: boolean) => commit(next), []);
  const toggleVisible = useCallback(() => commit(!currentVisible), []);

  return { visible, setVisible, toggleVisible } as const;
}
