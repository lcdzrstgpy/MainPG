import { useCallback, useSyncExternalStore } from "react";

/**
 * 全局特效偏好（个人中心 → 偏好设置）。
 *
 * 与 useTheme / useUiMode 一致：localStorage 持久化 + useSyncExternalStore 广播，
 * 同时把结果写到 <html> 的 data-fx-tap / data-fx-ambient 属性上，供样式层做动画降级
 * （见 shared/styles/fx-preference.css）。
 *
 *  - tap     点击特效：水墨青黛点击晕墨、桃花源单击花瓣爆裂 / 双击桃花雨
 *  - ambient 全屏特效：桃花源花瓣飘落 + 薄雾 + 光标风力、水墨青黛画卷漂移/墨韵呼吸、
 *                      暖阳橙背景漂移与流光扫过等主题常驻装饰动画
 *
 * 关掉只是「不渲染/不动」，主题配色、纹理与静态背景照旧，随时可再打开。
 */

export type EffectPreferences = {
  /** 点击特效：点击/双击时的瞬时反馈。 */
  tap: boolean;
  /** 全屏特效：铺满视口的常驻装饰动画。 */
  ambient: boolean;
};

const STORAGE_KEY = "mainpg.effects";

const DEFAULT_PREFERENCES: EffectPreferences = { tap: true, ambient: true };

function readPreferences(): EffectPreferences {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_PREFERENCES };
    const parsed = JSON.parse(raw) as Partial<EffectPreferences> | null;
    if (!parsed || typeof parsed !== "object") return { ...DEFAULT_PREFERENCES };
    // 只认显式 false：字段缺失（老版本写入/手工改坏）时按默认开启处理。
    return {
      tap: parsed.tap !== false,
      ambient: parsed.ambient !== false,
    };
  } catch {
    return { ...DEFAULT_PREFERENCES };
  }
}

function applyPreferences(preferences: EffectPreferences) {
  const root = document.documentElement;
  root.setAttribute("data-fx-tap", preferences.tap ? "on" : "off");
  root.setAttribute("data-fx-ambient", preferences.ambient ? "on" : "off");
}

let currentPreferences = readPreferences();
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function getSnapshot() {
  return currentPreferences;
}

applyPreferences(currentPreferences);

if (typeof window !== "undefined") {
  // 多标签页/多窗口同步：一边改了，另一边立刻跟着变。
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    const next = readPreferences();
    if (next.tap === currentPreferences.tap && next.ambient === currentPreferences.ambient) return;
    currentPreferences = next;
    applyPreferences(next);
    listeners.forEach((listener) => listener());
  });
}

export function useEffectPreferences() {
  const preferences = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  const setEffects = useCallback((patch: Partial<EffectPreferences>) => {
    const next: EffectPreferences = {
      tap: patch.tap ?? currentPreferences.tap,
      ambient: patch.ambient ?? currentPreferences.ambient,
    };
    if (next.tap === currentPreferences.tap && next.ambient === currentPreferences.ambient) return;
    currentPreferences = next;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch { /* 存储不可用时仅本次会话生效 */ }
    applyPreferences(next);
    listeners.forEach((listener) => listener());
  }, []);

  return { ...preferences, setEffects } as const;
}
