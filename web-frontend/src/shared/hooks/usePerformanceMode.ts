import { useCallback, useSyncExternalStore } from "react";

import { showToast } from "../components/toastStore";
import {
  computeFrameRate,
  deviceLooksStrong,
  deviceLooksWeak,
  FRAME_RATE_SAMPLE_MS,
  isFrameRateWeak,
} from "./performanceHeuristics";

/**
 * 流畅模式（个人中心 → 偏好设置）。
 *
 * 低性能设备上关掉费时的视觉效果，换取界面顺滑：
 *  - 全站去毛玻璃（backdrop-filter，弱机最重的一项渲染成本）；
 *  - 停掉常驻装饰动画（仪表盘光斑/流星、顶栏流光、主题环境动效）与页签入场动画；
 *  - 联动关闭「点击特效 / 全屏特效」（useEffectPreferences 统一并入生效值）。
 *
 * 降级样式见 shared/styles/perf-mode.css，取值写在 <html data-perf-mode="on|off">。
 * 只影响动效与模糊，配色、布局、静态背景不受影响；随时可在偏好设置关掉。
 *
 * 低配设备**只自动开启一次**（maybeAutoEnable）：
 *  - ≤2 核，或 ≤4 核且内存 ≤4G 的设备直接开；
 *  - 其余设备做一次帧率采样，明显掉帧才开（覆盖「配置不差但渲染慢」的机器）；
 *  - 无论结果如何都只评估一次；自动开启会弹提示说明，用户可随手关掉。
 */

const STORAGE_KEY = "mainpg.performanceMode";
const AUTO_CHECKED_KEY = "mainpg.performanceMode.auto";

/** 启动后等首屏渲染与首批请求稳定下来再评估，避免把「刚打开那几秒」误判成设备慢。 */
const EVALUATE_DELAY_MS = 4500;

function readEnabled(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "on";
  } catch {
    return false;
  }
}

function applyEnabled(enabled: boolean) {
  document.documentElement.setAttribute("data-perf-mode", enabled ? "on" : "off");
}

let currentEnabled = typeof window === "undefined" ? false : readEnabled();
const listeners = new Set<() => void>();

if (typeof window !== "undefined") {
  applyEnabled(currentEnabled);
}

function emit() {
  listeners.forEach((listener) => listener());
}

/** 供 useEffectPreferences 订阅：流畅模式开关会改变特效的生效值。 */
export function subscribePerformanceMode(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function isPerformanceModeOn(): boolean {
  return currentEnabled;
}

function commit(next: boolean) {
  if (next === currentEnabled) return;
  currentEnabled = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next ? "on" : "off");
  } catch { /* 存储不可用时仅本次会话生效 */ }
  applyEnabled(next);
  emit();
}

// ---- 低配设备自动开启（一次性，宁漏勿误） ----
function readAutoChecked(): boolean {
  try {
    return window.localStorage.getItem(AUTO_CHECKED_KEY) === "1";
  } catch {
    return true; // 存储不可用时不评估
  }
}

function markAutoChecked() {
  try {
    window.localStorage.setItem(AUTO_CHECKED_KEY, "1");
  } catch { /* 忽略 */ }
}

function deviceFacts() {
  const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory;
  return {
    cores: Number(navigator.hardwareConcurrency) || 0,
    memoryGb: typeof memory === "number" ? memory : undefined,
  };
}

/** 采样一段时间的 rAF 帧率；页面在采样期间被切走则视为不可信（null）。 */
function sampleFrameRate(done: (fps: number | null) => void) {
  if (document.hidden) {
    done(null);
    return;
  }
  let frames = 0;
  let startedAt = 0;
  let lastAt = 0;
  const tick = (now: number) => {
    if (!startedAt) {
      startedAt = now;
      lastAt = now;
      frames = 1;
    } else {
      lastAt = now;
      frames += 1;
    }
    if (now - startedAt >= FRAME_RATE_SAMPLE_MS) {
      done(document.hidden ? null : computeFrameRate(frames, lastAt - startedAt));
      return;
    }
    window.requestAnimationFrame(tick);
  };
  window.requestAnimationFrame(tick);
}

function enableByAuto(reason: string) {
  markAutoChecked();
  if (currentEnabled) return;
  commit(true);
  showToast(`${reason}，已自动开启「流畅模式」，可在个人中心 → 偏好设置中关闭`, "info", 6000);
}

function maybeAutoEnable() {
  if (readAutoChecked()) return;
  window.setTimeout(() => {
    if (readAutoChecked()) return;
    if (currentEnabled) {
      markAutoChecked(); // 用户自己先开了，无需再评估
      return;
    }
    const facts = deviceFacts();
    if (deviceLooksWeak(facts)) {
      enableByAuto("检测到设备性能较低");
      return;
    }
    sampleFrameRate((fps) => {
      if (readAutoChecked()) return;
      if (fps === null) return; // 采样不可信：下次启动再评估
      if (isFrameRateWeak(fps) && !deviceLooksStrong(facts)) {
        enableByAuto("检测到界面渲染较慢");
        return;
      }
      markAutoChecked();
    });
  }, EVALUATE_DELAY_MS);
}

if (typeof window !== "undefined") {
  maybeAutoEnable();
  // 多标签页/多窗口同步：一边改了，另一边立刻跟着变。
  window.addEventListener("storage", (event) => {
    if (event.key !== STORAGE_KEY) return;
    const next = readEnabled();
    if (next === currentEnabled) return;
    currentEnabled = next;
    applyEnabled(next);
    emit();
  });
}

export function usePerformanceMode() {
  const enabled = useSyncExternalStore(subscribePerformanceMode, isPerformanceModeOn, isPerformanceModeOn);
  const setEnabled = useCallback((next: boolean) => {
    markAutoChecked(); // 用户亲手改过，之后不再自动评估
    commit(next);
  }, []);
  const toggleEnabled = useCallback(() => {
    markAutoChecked();
    commit(!currentEnabled);
  }, []);

  return { enabled, setEnabled, toggleEnabled } as const;
}
