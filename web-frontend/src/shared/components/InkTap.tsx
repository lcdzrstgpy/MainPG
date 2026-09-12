import { memo, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

import type { ThemeId } from "../hooks/useTheme";
import type { UiModeId } from "../hooks/useUiMode";

/**
 * 水墨青黛（chinese）主题专属交互层：点击时在光标处晕开一颗墨点。
 *
 * 墨点层通过 createPortal 挂到 document.body、z-index:30 —— 顶栏(18)之上、
 * 弹窗(60+)之下，pointer-events:none 不拦截任何交互。
 *
 * 视觉：主墨晕 + 两个错位小墨晕（::before/::after），不规则 border-radius
 * 随机化，mix-blend-mode:multiply 叠加晕染，扩散同时轻微漂移并渐隐；
 * animationend 即移除 DOM。尊重 prefers-reduced-motion（不渲染墨点），
 * 活跃墨点上限 24 颗防堆积。
 */

const MAX_SPOTS = 24;

type InkTapProps = {
  theme: ThemeId;
  uiMode: UiModeId;
};

export const InkTap = memo(function InkTap({ theme, uiMode }: InkTapProps) {
  const layerRef = useRef<HTMLDivElement | null>(null);

  const active = theme === "chinese" && uiMode === "classic";

  useEffect(() => {
    if (!active) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;

    const spawn = (x: number, y: number) => {
      const layer = layerRef.current;
      if (!layer) return;
      while (layer.children.length >= MAX_SPOTS) {
        layer.firstElementChild?.remove();
      }
      const size = 36 + Math.random() * 48;
      const spot = document.createElement("div");
      spot.className = "ink-tap-spot";
      spot.style.left = `${x}px`;
      spot.style.top = `${y}px`;
      spot.style.setProperty("--ink-size", `${size}px`);
      spot.style.setProperty("--ink-drift-x", `${(Math.random() - 0.5) * 30}px`);
      spot.style.setProperty("--ink-drift-y", `${(Math.random() - 0.5) * 20}px`);
      spot.style.borderRadius = [
        `${50 + Math.random() * 10}% ${42 + Math.random() * 16}% ${55 - Math.random() * 10}% ${45 + Math.random() * 12}%`,
        `${58 + Math.random() * 8}% ${40 + Math.random() * 16}% ${60 - Math.random() * 8}% ${40 + Math.random() * 14}%`,
      ].join(" / ");
      spot.addEventListener("animationend", () => spot.remove(), { once: true });
      layer.appendChild(spot);
    };

    const onPointerDown = (event: PointerEvent) => {
      spawn(event.clientX, event.clientY);
    };

    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      layerRef.current?.replaceChildren();
    };
  }, [active]);

  if (!active) return null;

  return createPortal(<div ref={layerRef} className="ink-tap-layer" aria-hidden="true" />, document.body);
});
