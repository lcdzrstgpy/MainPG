import { memo, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

import type { ThemeId } from "../hooks/useTheme";
import type { UiModeId } from "../hooks/useUiMode";

/**
 * 桃花源主题专属交互层。
 *
 * 背景是 AI 生成的桃林溪水全景图(public/theme/peach-garden-bg.jpg,经 CSS 挂到
 * WorkspaceShell 内、z-index:0),前景叠加一层缓缓漂移的薄雾;花瓣画布通过
 * createPortal 挂到 document.body、z-index:30 —— 位于顶栏(18)之上、各类弹窗
 * (60+)之下,pointer-events:none 不拦截任何点击。
 *
 * 交互:
 *  - 花瓣飘落 + 正弦摇摆 + 3D 翻滚,光标靠近形成"风力"推开(160px 内);
 *  - 单击:花瓣爆裂四散 + 水波纹涟漪;
 *  - 双击:桃花雨(更大规模的爆裂);
 *  - 尊重 prefers-reduced-motion(定格一帧静态花瓣)与页面可见性(隐藏时暂停)。
 */

type Petal = {
  x: number;
  y: number;
  size: number;
  rot: number;
  rotSpeed: number;
  vy: number;
  swayPhase: number;
  swayAmp: number;
  swaySpeed: number;
  color: string;
  alpha: number;
  flip: number;
  /** 景深:0.5(远,小而淡) ~ 1(近,大而实),影响大小/透明度/下落速度 */
  depth: number;
  /** 3D 翻滚相位/速度:让花瓣绕竖轴翻转,而不是永远平面正对着看 */
  tumblePhase: number;
  tumbleSpeed: number;
};

type Burst = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  rot: number;
  vr: number;
  life: number;
  decay: number;
  color: string;
  alpha: number;
};

type Ripple = { x: number; y: number; r: number; alpha: number };

const TAU = Math.PI * 2;
const PETAL_COLORS = ["#ffc2d1", "#f9a8bd", "#ffd9e2", "#f48fb0", "#fdeef2", "#b7d9c2"];

/**
 * 桃花花瓣:底部收窄成柄,顶部两个圆瓣中间带一处小缺刻(樱/桃花的标志性外形)。
 * scaleX 用于 3D 翻滚(绕竖轴压缩),传入负值可左右镜像。
 */
function drawPetalShape(ctx: CanvasRenderingContext2D, x: number, y: number, size: number, rot: number, scaleX: number, alpha: number, color: string) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(rot);
  ctx.scale(scaleX, 1);
  ctx.globalAlpha = alpha;
  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(0, size * 0.92);
  ctx.bezierCurveTo(size * 0.85, size * 0.45, size * 0.8, -size * 0.5, size * 0.2, -size * 0.7);
  ctx.quadraticCurveTo(0, -size * 0.52, -size * 0.2, -size * 0.7);
  ctx.bezierCurveTo(-size * 0.8, -size * 0.5, -size * 0.85, size * 0.45, 0, size * 0.92);
  ctx.fill();
  ctx.restore();
}

/** 贴图基准尺寸:花瓣按颜色离线预渲染一次,之后每帧只 drawImage。
 *  每帧重画贝塞尔路径是 CPU 开销大头,drawImage 可走 GPU,动画明显更顺。 */
const SPRITE_BASE = 48;
const petalSpriteCache = new Map<string, HTMLCanvasElement>();

function getPetalSprite(color: string): HTMLCanvasElement {
  const cached = petalSpriteCache.get(color);
  if (cached) return cached;
  const sprite = document.createElement("canvas");
  sprite.width = Math.ceil(SPRITE_BASE * 2);
  sprite.height = Math.ceil(SPRITE_BASE * 2);
  const sctx = sprite.getContext("2d");
  if (sctx) drawPetalShape(sctx, sprite.width / 2, sprite.height / 2, SPRITE_BASE, 0, 1, 1, color);
  petalSpriteCache.set(color, sprite);
  return sprite;
}

function drawPetalSprite(ctx: CanvasRenderingContext2D, x: number, y: number, size: number, rot: number, scaleX: number, alpha: number, color: string) {
  const sprite = getPetalSprite(color);
  const scale = size / SPRITE_BASE;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(rot);
  ctx.scale(scaleX * scale, scale);
  ctx.globalAlpha = alpha;
  ctx.drawImage(sprite, -sprite.width / 2, -sprite.height / 2);
  ctx.restore();
}

type PeachGardenProps = {
  theme: ThemeId;
  uiMode: UiModeId;
};

export const PeachGarden = memo(function PeachGarden({ theme, uiMode }: PeachGardenProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // apple 桌面模式下主题会被映射回 classic(见 applyTheme),此时不渲染桃花源层。
  const active = theme === "peach" && uiMode === "classic";

  useEffect(() => {
    if (!active) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let width = 0;
    let height = 0;
    let petals: Petal[] = [];
    const bursts: Burst[] = [];
    const ripples: Ripple[] = [];
    let rafId = 0;
    let last = performance.now();
    let running = true;
    const pointer = { x: -99999, y: -99999 };

    const seedPetals = () => {
      // 稀疏一点:1920x1080 约 32 片,小窗口最少 14 片
      const count = Math.max(14, Math.min(32, Math.round((width * height) / 64000)));
      petals = [];
      for (let i = 0; i < count; i += 1) {
        const depth = 0.5 + Math.random() * 0.5;
        petals.push({
          x: Math.random() * width,
          y: Math.random() * height,
          size: (5 + Math.random() * 9) * depth,
          rot: Math.random() * TAU,
          rotSpeed: (Math.random() - 0.5) * 0.018,
          vy: (0.35 + Math.random() * 0.85) * (0.55 + depth * 0.45),
          swayPhase: Math.random() * TAU,
          swayAmp: 8 + Math.random() * 26,
          swaySpeed: 0.006 + Math.random() * 0.014,
          color: PETAL_COLORS[(Math.random() * PETAL_COLORS.length) | 0],
          alpha: (0.4 + Math.random() * 0.35) * (0.6 + depth * 0.4),
          flip: Math.random() < 0.5 ? -1 : 1,
          depth,
          tumblePhase: Math.random() * TAU,
          tumbleSpeed: 0.0012 + Math.random() * 0.0028,
        });
      }
    };

    const resize = () => {
      // DPR 上限 1.5:2x 屏全屏画布是 4 倍像素填充,1.5x 视觉差别很小但省近一半像素
      const dpr = Math.min(1.5, window.devicePixelRatio || 1);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seedPetals();
    };

    const onPointerMove = (event: PointerEvent) => {
      pointer.x = event.clientX;
      pointer.y = event.clientY;
    };

    const burstAt = (x: number, y: number, power: number) => {
      ripples.push({ x, y, r: 6, alpha: 0.5 });
      ripples.push({ x, y, r: 2, alpha: 0.38 });

      // 近处花瓣被"震开"
      for (const petal of petals) {
        const dx = petal.x - x;
        const dy = petal.y - y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 130 * 130) {
          const d = Math.sqrt(d2) || 1;
          const f = (1 - d / 130) * 4.2 * power;
          petal.x += (dx / d) * f * 14;
          petal.y += (dy / d) * f * 14 - 12 * f;
          petal.rotSpeed += (Math.random() - 0.5) * 0.09;
        }
      }

      // 花瓣碎片
      const count = Math.round(10 * power);
      for (let i = 0; i < count; i += 1) {
        const angle = Math.random() * TAU;
        const speed = 1.2 + Math.random() * 3.4 * power;
        bursts.push({
          x,
          y,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed - 1.4,
          size: 2.5 + Math.random() * 4.5,
          rot: Math.random() * TAU,
          vr: (Math.random() - 0.5) * 0.3,
          life: 1,
          decay: 0.008 + Math.random() * 0.01,
          color: PETAL_COLORS[(Math.random() * PETAL_COLORS.length) | 0],
          alpha: 0.8,
        });
      }
      if (bursts.length > 420) bursts.splice(0, bursts.length - 420);
    };

    const onPointerDown = (event: PointerEvent) => burstAt(event.clientX, event.clientY, 1);
    const onDblClick = (event: MouseEvent) => burstAt(event.clientX, event.clientY, 2.6);

    const step = (now: number) => {
      const dt = Math.min(32, now - last);
      last = now;
      const k = dt / 16.7;

      ctx.clearRect(0, 0, width, height);

      // 花瓣:下落 + 摇摆 + 微风 + 3D 翻滚 + 光标风力
      for (const petal of petals) {
        petal.y += petal.vy * k;
        petal.x += Math.sin(petal.swayPhase) * petal.swayAmp * 0.014 * k + petal.swayAmp * 0.004;
        petal.swayPhase += petal.swaySpeed * dt;
        petal.rot += petal.rotSpeed * dt;
        petal.tumblePhase += petal.tumbleSpeed * dt;

        const dx = petal.x - pointer.x;
        const dy = petal.y - pointer.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 160 * 160) {
          const d = Math.sqrt(d2) || 1;
          const f = 1 - d / 160;
          petal.x += (dx / d) * f * 3.4 * k;
          petal.y += (dy / d) * f * 3.4 * k - f * 1.1 * k;
        }

        if (petal.y > height + 46) {
          petal.y = -40;
          petal.x = Math.random() * width;
        }
        if (petal.x < -60) petal.x = width + 40;
        else if (petal.x > width + 60) petal.x = -40;

        // 绕竖轴翻滚:scaleX 在 0.3~1 之间呼吸,营造花瓣翻面的立体动感
        const tumble = 0.3 + 0.7 * Math.abs(Math.sin(petal.tumblePhase));
        drawPetalSprite(ctx, petal.x, petal.y, petal.size, petal.rot, petal.flip * tumble, petal.alpha, petal.color);
      }

      // 爆裂碎片
      for (let i = bursts.length - 1; i >= 0; i -= 1) {
        const b = bursts[i];
        b.life -= b.decay * dt;
        if (b.life <= 0) {
          bursts.splice(i, 1);
          continue;
        }
        b.vy += 0.035 * k;
        b.x += b.vx * k;
        b.y += b.vy * k;
        b.rot += b.vr * dt;
        drawPetalSprite(ctx, b.x, b.y, b.size, b.rot, 1, Math.max(0, b.life) * b.alpha, b.color);
      }

      // 水波纹涟漪(压扁的椭圆,模拟水面)
      for (let i = ripples.length - 1; i >= 0; i -= 1) {
        const ripple = ripples[i];
        ripple.r += 2.6 * k;
        ripple.alpha -= 0.016 * k;
        if (ripple.alpha <= 0) {
          ripples.splice(i, 1);
          continue;
        }
        ctx.strokeStyle = `rgba(217, 93, 120, ${ripple.alpha})`;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.ellipse(ripple.x, ripple.y, ripple.r, ripple.r * 0.42, 0, 0, TAU);
        ctx.stroke();
      }

      rafId = requestAnimationFrame(step);
    };

    const onVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(rafId);
        running = false;
      } else if (!running) {
        running = true;
        last = performance.now();
        rafId = requestAnimationFrame(step);
      }
    };

    resize();
    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("pointerdown", onPointerDown, { passive: true });
    window.addEventListener("dblclick", onDblClick, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);

    if (reduceMotion) {
      // 静态定格一帧:花瓣散落,不做动画
      for (const petal of petals) {
        drawPetalShape(ctx, petal.x, petal.y, petal.size, petal.rot, petal.flip, petal.alpha, petal.color);
      }
    } else {
      rafId = requestAnimationFrame(step);
    }

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("dblclick", onDblClick);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [active]);

  if (!active) return null;

  return (
    <>
      <div className="peach-garden-scenery" aria-hidden="true">
        <div className="peach-garden-mist" />
      </div>
      {createPortal(
        <canvas ref={canvasRef} className="peach-garden-petals" aria-hidden="true" />,
        document.body,
      )}
    </>
  );
});
