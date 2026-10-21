import { memo, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

import type { ThemeId } from "../hooks/useTheme";
import type { UiModeId } from "../hooks/useUiMode";

/**
 * 焦糖（dessert）主题专属交互层：Canvas 绘制点击焦糖印。
 *
 * 与 PeachGarden / AlpineMeadow 同构：portal 挂 body、z-index:30、pointer-events:none。
 * 放弃 DOM+CSS 关键帧，改用 Canvas —— 每帧自由插值缩放/透明/色相，后续加
 * 粘稠拉丝、糖滴溅落、噪声纹理都只是往 step 里加绘制，不必再和 CSS 动画抢层。
 *
 * 视觉：
 *  - 一滴焦糖「砸」下去：短促加深 + 轻微过冲，约 2s 由深褐化开褪回；
 *  - 多层径向渐变（深可可核 / 焦糖中层 / 暖琥珀外晕）+ 少量小糖珠；
 *  - 无 filter:blur，半径与 alpha 在帧内插值，合成成本可控。
 */

type Stain = {
  x: number;
  y: number;
  born: number;
  /** 生命周期 ms，主印约 2s */
  life: number;
  /** 基准半径 */
  radius: number;
  rot: number;
  seed: number;
};

type Droplet = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  born: number;
  life: number;
  color: string;
};

const TAU = Math.PI * 2;
const MAX_STAINS = 12;
const MAX_DROPS = 64;

function easeOutCubic(t: number): number {
  return 1 - (1 - t) ** 3;
}

/** 0→1→0 的「盖印」包络：前 12% 快速弹入，中段稳住，后段化开。 */
function stainEnvelope(t: number): { alpha: number; scale: number } {
  if (t <= 0) return { alpha: 0, scale: 0.42 };
  if (t >= 1) return { alpha: 0, scale: 1.28 };
  if (t < 0.12) {
    const u = easeOutCubic(t / 0.12);
    return { alpha: 0.15 + u * 0.85, scale: 0.42 + u * 0.65 };
  }
  if (t < 0.2) {
    const u = (t - 0.12) / 0.08;
    return { alpha: 1 - u * 0.04, scale: 1.07 - u * 0.07 };
  }
  // 0.2 → 1：缓缓放大并淡出
  const u = (t - 0.2) / 0.8;
  const fade = 1 - u * u * 0.92 - u * 0.08;
  return { alpha: Math.max(0, fade), scale: 1 + u * 0.28 };
}

function drawStain(ctx: CanvasRenderingContext2D, stain: Stain, now: number) {
  const t = (now - stain.born) / stain.life;
  if (t >= 1) return false;
  const { alpha, scale } = stainEnvelope(t);
  if (alpha <= 0.01) return true;

  const r = stain.radius * scale;
  const squash = 0.92 + 0.08 * Math.sin(stain.seed);
  ctx.save();
  ctx.translate(stain.x, stain.y);
  ctx.rotate(stain.rot);
  ctx.scale(1, squash);

  // 外晕（先散）
  const haloT = Math.min(1, t / 0.68);
  const haloAlpha = alpha * (1 - haloT) * 0.22;
  if (haloAlpha > 0.01) {
    const hr = r * (1.15 + haloT * 0.55);
    const halo = ctx.createRadialGradient(0, 0, r * 0.15, 0, 0, hr);
    halo.addColorStop(0, `rgba(242, 210, 168, ${haloAlpha * 0.55})`);
    halo.addColorStop(0.45, `rgba(232, 196, 150, ${haloAlpha * 0.28})`);
    halo.addColorStop(1, "rgba(232, 196, 150, 0)");
    ctx.globalCompositeOperation = "source-over";
    ctx.fillStyle = halo;
    ctx.beginPath();
    ctx.arc(0, 0, hr, 0, TAU);
    ctx.fill();
  }

  // 主印：浅焦糖暖褐，整体压到「微微加深」，色相避开发黑的深棕
  const core = ctx.createRadialGradient(0, -r * 0.04, r * 0.05, 0, 0, r);
  const a = alpha * 0.38;
  core.addColorStop(0, `rgba(150, 108, 72, ${0.5 * a})`);
  core.addColorStop(0.25, `rgba(176, 132, 90, ${0.38 * a})`);
  core.addColorStop(0.48, `rgba(202, 160, 114, ${0.24 * a})`);
  core.addColorStop(0.7, `rgba(224, 188, 146, ${0.12 * a})`);
  core.addColorStop(1, "rgba(230, 198, 156, 0)");
  ctx.fillStyle = core;
  ctx.beginPath();
  ctx.arc(0, 0, r, 0, TAU);
  ctx.fill();

  // 高光：前 30% 存在
  const glossT = t / 0.28;
  if (glossT < 1) {
    const ga = alpha * (1 - glossT) * 0.34;
    const gx = -r * 0.18;
    const gy = -r * 0.22;
    const gloss = ctx.createRadialGradient(gx, gy, 0, gx, gy, r * 0.32);
    gloss.addColorStop(0, `rgba(255, 236, 200, ${ga})`);
    gloss.addColorStop(0.55, `rgba(255, 220, 170, ${ga * 0.35})`);
    gloss.addColorStop(1, "rgba(255, 220, 170, 0)");
    ctx.fillStyle = gloss;
    ctx.beginPath();
    ctx.arc(gx, gy, r * 0.32, 0, TAU);
    ctx.fill();
  }

  ctx.restore();
  return true;
}

function drawDrop(ctx: CanvasRenderingContext2D, drop: Droplet, now: number) {
  const t = (now - drop.born) / drop.life;
  if (t >= 1) return false;
  const ease = easeOutCubic(t);
  const x = drop.x + drop.vx * ease * 28;
  const y = drop.y + drop.vy * ease * 28 + 18 * t * t;
  const alpha = (1 - t) * 0.42;
  const r = drop.r * (1 + t * 0.25);
  const g = ctx.createRadialGradient(x, y, 0, x, y, r);
  g.addColorStop(0, drop.color.replace("ALPHA", String(alpha * 0.9)));
  g.addColorStop(0.7, drop.color.replace("ALPHA", String(alpha * 0.35)));
  g.addColorStop(1, drop.color.replace("ALPHA", "0"));
  ctx.fillStyle = g;
  ctx.beginPath();
  ctx.arc(x, y, r, 0, TAU);
  ctx.fill();
  return true;
}

const DROP_COLORS = [
  "rgba(176, 132, 90, ALPHA)",
  "rgba(202, 160, 114, ALPHA)",
  "rgba(224, 188, 146, ALPHA)",
  "rgba(242, 210, 168, ALPHA)",
];

type DessertTapProps = {
  theme: ThemeId;
  uiMode: UiModeId;
  /** 点击特效开关（个人中心 → 偏好设置）。 */
  enabled: boolean;
};

export const DessertTap = memo(function DessertTap({ theme, uiMode, enabled }: DessertTapProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const active = theme === "dessert" && uiMode === "classic" && enabled;

  useEffect(() => {
    if (!active) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;

    const stains: Stain[] = [];
    const drops: Droplet[] = [];
    let rafId = 0;
    let running = true;
    let width = 0;
    let height = 0;
    let last = performance.now();

    const resize = () => {
      const dpr = Math.min(1.5, window.devicePixelRatio || 1);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };

    const spawn = (x: number, y: number) => {
      while (stains.length >= MAX_STAINS) stains.shift();
      const now = performance.now();
      stains.push({
        x,
        y,
        born: now,
        life: 2000,
        radius: 52 + Math.random() * 22,
        rot: Math.random() * TAU,
        seed: Math.random() * TAU,
      });

      const dropCount = 3 + ((Math.random() * 3) | 0);
      for (let i = 0; i < dropCount; i += 1) {
        if (drops.length >= MAX_DROPS) drops.shift();
        const angle = Math.random() * TAU;
        const speed = 0.35 + Math.random() * 0.95;
        drops.push({
          x,
          y,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed - 0.25,
          r: 3 + Math.random() * 5,
          born: now,
          life: 700 + Math.random() * 500,
          color: DROP_COLORS[(Math.random() * DROP_COLORS.length) | 0],
        });
      }

      if (!running) {
        running = true;
        last = now;
        rafId = requestAnimationFrame(step);
      }
    };

    const step = (now: number) => {
      last = now;
      ctx.clearRect(0, 0, width, height);
      for (let i = stains.length - 1; i >= 0; i -= 1) {
        if (!drawStain(ctx, stains[i], now)) stains.splice(i, 1);
      }
      for (let i = drops.length - 1; i >= 0; i -= 1) {
        if (!drawDrop(ctx, drops[i], now)) drops.splice(i, 1);
      }
      if (stains.length === 0 && drops.length === 0) {
        running = false;
        return;
      }
      rafId = requestAnimationFrame(step);
    };

    const onPointerDown = (event: PointerEvent) => {
      if (event.button !== 0) return;
      spawn(event.clientX, event.clientY);
    };

    const onVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(rafId);
        running = false;
      } else if (!running && (stains.length || drops.length)) {
        running = true;
        last = performance.now();
        rafId = requestAnimationFrame(step);
      }
    };

    resize();
    window.addEventListener("resize", resize);
    document.addEventListener("pointerdown", onPointerDown, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);
    rafId = requestAnimationFrame(step);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", resize);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("visibilitychange", onVisibility);
      stains.length = 0;
      drops.length = 0;
    };
  }, [active]);

  if (!active) return null;

  return createPortal(
    <canvas ref={canvasRef} className="dessert-tap-layer" aria-hidden="true" />,
    document.body,
  );
});
