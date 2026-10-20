import { useLayoutEffect, useRef, useState } from "react";

export type SlidingIndicatorRect = { left: number; top: number; width: number; height: number };

/**
 * 测量容器内当前选中项的位置，交给唯一的胶囊滑块做平移动画。
 * selector 需用 :scope 明确限定「当前容器」的选中项，避免命中嵌套层级。
 */
export function useSlidingIndicator<T extends HTMLElement>(selector: string, deps: unknown[]) {
  const containerRef = useRef<T | null>(null);
  const [rect, setRect] = useState<SlidingIndicatorRect | null>(null);
  const [ready, setReady] = useState(false);

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) {
      setRect(null);
      return;
    }
    const measure = () => {
      const active = container.querySelector<HTMLElement>(selector);
      if (!active) {
        setRect(null);
        return;
      }
      const origin = container.getBoundingClientRect();
      const target = active.getBoundingClientRect();
      setRect({
        left: target.left - origin.left - container.clientLeft + container.scrollLeft,
        top: target.top - origin.top - container.clientTop + container.scrollTop,
        width: target.width,
        height: target.height,
      });
    };
    measure();
    // 首帧不播过渡，避免滑块从原点滑入。
    const frame = requestAnimationFrame(() => setReady(true));
    // 字体（尤其是 iconfont）晚于首帧加载时会改变项宽，补一次测量。
    let cancelled = false;
    document.fonts?.ready.then(() => {
      if (!cancelled) measure();
    });
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    const handleTransitionEnd = (event: TransitionEvent) => {
      if (event.target instanceof HTMLElement && container.contains(event.target)) measure();
    };
    container.addEventListener("transitionend", handleTransitionEnd);
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      container.removeEventListener("transitionend", handleTransitionEnd);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { containerRef, rect, ready };
}

export function slidingIndicatorStyle(rect: SlidingIndicatorRect) {
  return {
    transform: `translate3d(${rect.left}px, ${rect.top}px, 0)`,
    width: `${rect.width}px`,
    height: `${rect.height}px`,
  };
}
