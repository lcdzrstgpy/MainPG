import { useEffect, useRef } from "react";

/**
 * 「被滑走的这一程」折算成 0..100 的侵蚀进度。
 *
 * 抽成纯函数是为了能钉住口径：restTop 是元素停在原位时的顶边位置，
 * top 是当前顶边位置，height 是元素高度。滑过 (height + 80) 就啃满 ——
 * 也就是卡片一动就开始、滑过约一张卡的高度就结束，整个过程都在视野里。
 */
export function wipePercent(restTop: number, top: number, height: number): number {
  const travel = Math.max(1, height + 80);
  const raw = ((restTop - top) / travel) * 100;
  return Math.max(0, Math.min(100, raw));
}

/**
 * 滚动侵蚀：把元素「被滑走的这一程」写成 CSS 变量 `--wipe`（0..100），
 * 让卡片右侧的颜色随滚动一路啃向左边（见 daily-selection.css 的双层渐变）。
 *
 * 为什么用「JS 只写一个变量、其余交给 CSS」：
 *  - 每帧只有一次 rect 读取 + 一次变量写入，不碰布局、不碰重绘属性；
 *  - 啃的快慢、羽化宽度、用什么颜色，全在 CSS 里调，换个组件也不用改 JS；
 *  - 想关掉（性能模式 / 减少动画）直接 CSS 覆写 `--wipe: 0` 即可，不用跨层开关。
 */
export function useScrollWipe<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const stateRef = useRef<{ element: HTMLElement | null; restTop: number | null; last: number }>({
    element: null,
    restTop: null,
    last: -1,
  });

  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)");

    const apply = () => {
      const element = ref.current;
      if (!element) return;
      const state = stateRef.current;
      // 元素换过（切视图时两张卡共用一个 ref）→ 重新记一次初始位置
      if (state.element !== element) {
        state.element = element;
        state.restTop = null;
        state.last = -1;
      }
      // 减少动画 / 流畅模式（弱机）：停在初始态，别给渲染加负担
      if (reduced.matches || document.documentElement.dataset.perfMode === "on") {
        if (state.last !== 0) {
          state.last = 0;
          element.style.setProperty("--wipe", "0");
        }
        return;
      }
      const rect = element.getBoundingClientRect();
      if (state.restTop === null) state.restTop = rect.top;
      const travel = Math.max(1, rect.height + 80);
      const raw = ((state.restTop - rect.top) / travel) * 100;
      const value = Math.max(0, Math.min(100, raw));
      // 差不到 0.5% 就别写，省掉无谓的样式重算
      if (Math.abs(value - state.last) < 0.5) return;
      state.last = value;
      element.style.setProperty("--wipe", value.toFixed(1));
    };

    let ticking = false;
    const onScroll = () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        apply();
      });
    };
    const onResize = () => {
      // 位置基准变了，重新记一次「原位」
      stateRef.current.restTop = null;
      onScroll();
    };

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onResize, { passive: true });
    apply();
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return ref;
}
