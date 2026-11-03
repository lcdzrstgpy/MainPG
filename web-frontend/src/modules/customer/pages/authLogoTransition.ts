import { useEffect, useLayoutEffect, useRef } from "react";

import { invertRect, isNegligible, type LogoRect } from "./authLogoGeometry";

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

function readRect(element: Element): LogoRect {
  const rect = element.getBoundingClientRect();
  return { left: rect.left, top: rect.top, width: rect.width };
}

/**
 * 登录页 logo 的共享元素过渡（FLIP）。
 *
 * 用法：把 `slotRef` 挂到当前步骤的占位槽上（首屏大槽 / 表单小槽，二者都 in-flow），
 * 把 `logoRef` 挂到槽内的 <img>（绝对定位铺满槽）。步骤变化时：
 *   1. 布局提交后用新槽的 rect 算出反演变换，在下一帧绘制前写回旧位置；
 *   2. 再放行 transform，交给 CSS transition 插值到新位置。
 *
 * 首帧与「减少动态」下不播动画；窗口尺寸变化时只刷新基准，不触发动画。
 */
export function useLogoFlip(step: string) {
  const logoRef = useRef<HTMLImageElement | null>(null);
  const slotRef = useRef<HTMLDivElement | null>(null);
  const prevRectRef = useRef<LogoRect | null>(null);

  // 尺寸变化时重设基准，避免用过期坐标算反演。
  useEffect(() => {
    const sync = () => {
      const slot = slotRef.current;
      if (slot) prevRectRef.current = readRect(slot);
    };
    window.addEventListener("resize", sync);
    return () => window.removeEventListener("resize", sync);
  }, []);

  useLayoutEffect(() => {
    const slot = slotRef.current;
    if (!slot) return;
    const next = readRect(slot);
    const prev = prevRectRef.current;
    prevRectRef.current = next;

    const logo = logoRef.current;
    // 首帧（prev 为空）与减少动态：直接落到目标位置，不播过渡。
    if (!logo || !prev || prefersReducedMotion()) return;

    const move = invertRect(prev, next);
    if (isNegligible(move)) return;

    const release = () => {
      logo.style.willChange = "";
      logo.removeEventListener("transitionend", release);
    };
    logo.addEventListener("transitionend", release);

    logo.style.willChange = "transform";
    logo.style.transition = "none";
    logo.style.transform = `translate(${move.dx}px, ${move.dy}px) scale(${move.scale})`;
    // 强制读一次布局，确保浏览器把「反演后的位置」当作过渡起点。
    void logo.offsetWidth;
    window.requestAnimationFrame(() => {
      logo.style.transition = "";
      logo.style.transform = "";
    });
  }, [step]);

  return { logoRef, slotRef };
}
