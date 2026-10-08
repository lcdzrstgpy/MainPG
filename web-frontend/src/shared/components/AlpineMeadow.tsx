import { memo, useEffect, useRef } from "react";
import { createPortal } from "react-dom";

import type { ThemeId } from "../hooks/useTheme";
import type { UiModeId } from "../hooks/useUiMode";

/**
 * 高山草原主题专属交互层。
 *
 * 背景是 AI 生成的高原草甸全景图(public/theme/alpine-meadow-bg.jpg,经 CSS 挂到
 * WorkspaceShell 内、z-index:0),前景叠加两团缓缓漂移的云影;蒲公英种子画布通过
 * createPortal 挂到 document.body、z-index:30 —— 位于顶栏(18)之上、各类弹窗
 * (60+)之下,pointer-events:none 不拦截任何点击。
 *
 * 交互:
 *  - 种子随风飘落 + 正弦摇摆 + 绕竖轴翻面,光标靠近形成"气流"推开(160px 内);
 *  - 单击:种子爆散 + 草绿涟漪;
 *  - 双击:更大规模的爆散(草甸扬絮);
 *  - 尊重 prefers-reduced-motion(定格一帧静态种子)与页面可见性(隐藏时暂停)。
 *
 * 结构与 PeachGarden 保持一致,便于两套主题一起维护。
 */

type Seed = {
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
  /** 绕竖轴翻面的相位/速度:让种子在风里打转,而不是永远平面正对 */
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
/** 蒲公英白絮为主,掺入草绿与野花金,呼应主题配色。 */
const SEED_COLORS = ["#ffffff", "#f7fbf4", "#eaf5e0", "#c9e8b4", "#8fbf6a", "#e8cf8f"];

/**
 * 蒲公英种子:一根细柄拖着顶端的小伞与放射冠毛。
 * scaleX 用于绕竖轴翻面(负值可左右镜像)。
 */
function drawSeedShape(ctx: CanvasRenderingContext2D, x: number, y: number, size: number, rot: number, scaleX: number, alpha: number, color: string) {
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(rot);
  ctx.scale(scaleX, 1);
  ctx.globalAlpha = alpha;
  ctx.fillStyle = color;
  ctx.strokeStyle = color;

  const tipY = -size * 0.6;
  // 冠毛:顶端向两侧扇开的细线
  ctx.lineWidth = Math.max(0.6, size * 0.07);
  ctx.lineCap = "round";
  for (let i = -2; i <= 2; i += 1) {
    const angle = i * 0.4;
    ctx.beginPath();
    ctx.moveTo(0, tipY);
    ctx.lineTo(Math.sin(angle) * size * 0.6, tipY - Math.cos(angle) * size * 0.5);
    ctx.stroke();
  }
  // 小伞:半透明柔圆
  ctx.globalAlpha = alpha * 0.5;
  ctx.beginPath();
  ctx.ellipse(0, tipY - size * 0.16, size * 0.3, size * 0.22, 0, 0, TAU);
  ctx.fill();
  // 种身:细长椭圆
  ctx.globalAlpha = alpha;
  ctx.beginPath();
  ctx.ellipse(0, size * 0.1, size * 0.12, size * 0.46, 0, 0, TAU);
  ctx.fill();
  ctx.restore();
}

/** 贴图基准尺寸:种子按颜色离线预渲染一次,之后每帧只 drawImage。
 *  每帧重画路径是 CPU 开销大头,drawImage 可走 GPU,动画明显更顺。 */
const SPRITE_BASE = 40;
const seedSpriteCache = new Map<string, HTMLCanvasElement>();

function getSeedSprite(color: string): HTMLCanvasElement {
  const cached = seedSpriteCache.get(color);
  if (cached) return cached;
  const sprite = document.createElement("canvas");
  sprite.width = Math.ceil(SPRITE_BASE * 2);
  sprite.height = Math.ceil(SPRITE_BASE * 2);
  const sctx = sprite.getContext("2d");
  if (sctx) drawSeedShape(sctx, sprite.width / 2, sprite.height / 2, SPRITE_BASE, 0, 1, 1, color);
  seedSpriteCache.set(color, sprite);
  return sprite;
}

function drawSeedSprite(ctx: CanvasRenderingContext2D, x: number, y: number, size: number, rot: number, scaleX: number, alpha: number, color: string) {
  const sprite = getSeedSprite(color);
  const scale = size / SPRITE_BASE;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(rot);
  ctx.scale(scaleX * scale, scale);
  ctx.globalAlpha = alpha;
  ctx.drawImage(sprite, -sprite.width / 2, -sprite.height / 2);
  ctx.restore();
}

type AlpineMeadowProps = {
  theme: ThemeId;
  uiMode: UiModeId;
  /** 点击特效:单击种子爆散 + 双击草甸扬絮(个人中心 → 偏好设置)。 */
  tapEffects: boolean;
  /** 全屏特效:种子飘落 + 云影 + 光标气流。关掉后只剩点击反馈(若点击特效开着)。 */
  ambientEffects: boolean;
};

export const AlpineMeadow = memo(function AlpineMeadow({ theme, uiMode, tapEffects, ambientEffects }: AlpineMeadowProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  // apple 桌面模式下主题会被映射回 classic(见 applyTheme),此时不渲染草原层。
  const sceneryActive = theme === "alpine" && uiMode === "classic";
  // 两个特效都关掉时不创建种子画布(草甸静态背景仍保留,它属于主题外观)。
  const active = sceneryActive && (tapEffects || ambientEffects);

  // 点击特效只决定事件里要不要响应,不该重建种子(重跑 effect 会让种子重新随机分布),
  // 所以用 ref 读最新值;全屏特效决定种子/云影是否存在,变化时需要重建。
  const tapRef = useRef(tapEffects);
  tapRef.current = tapEffects;

  useEffect(() => {
    if (!active) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    let width = 0;
    let height = 0;
    let seeds: Seed[] = [];
    const bursts: Burst[] = [];
    const ripples: Ripple[] = [];
    let rafId = 0;
    let last = performance.now();
    let running = true;
    const pointer = { x: -99999, y: -99999 };

    const seedSeeds = () => {
      // 稀疏一点:1920x1080 约 26 片,小窗口最少 12 片
      const count = Math.max(12, Math.min(26, Math.round((width * height) / 78000)));
      seeds = [];
      for (let i = 0; i < count; i += 1) {
        const depth = 0.5 + Math.random() * 0.5;
        seeds.push({
          x: Math.random() * width,
          y: Math.random() * height,
          size: (3.4 + Math.random() * 5.2) * depth,
          rot: Math.random() * TAU,
          rotSpeed: (Math.random() - 0.5) * 0.014,
          vy: (0.22 + Math.random() * 0.5) * (0.55 + depth * 0.45),
          swayPhase: Math.random() * TAU,
          swayAmp: 10 + Math.random() * 30,
          swaySpeed: 0.005 + Math.random() * 0.012,
          color: SEED_COLORS[(Math.random() * SEED_COLORS.length) | 0],
          alpha: (0.38 + Math.random() * 0.34) * (0.6 + depth * 0.4),
          flip: Math.random() < 0.5 ? -1 : 1,
          depth,
          tumblePhase: Math.random() * TAU,
          tumbleSpeed: 0.001 + Math.random() * 0.0024,
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
      // 全屏特效关闭时不播种：画布只用来画点击反馈(碎片/涟漪)。
      if (ambientEffects) seedSeeds();
      else seeds = [];
    };

    const onPointerMove = (event: PointerEvent) => {
      pointer.x = event.clientX;
      pointer.y = event.clientY;
    };

    const burstAt = (x: number, y: number, power: number) => {
      ripples.push({ x, y, r: 6, alpha: 0.46 });
      ripples.push({ x, y, r: 2, alpha: 0.34 });

      // 近处种子被气流"震开"
      for (const seed of seeds) {
        const dx = seed.x - x;
        const dy = seed.y - y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 140 * 140) {
          const d = Math.sqrt(d2) || 1;
          const f = (1 - d / 140) * 4 * power;
          seed.x += (dx / d) * f * 15;
          seed.y += (dy / d) * f * 15 - 11 * f;
          seed.rotSpeed += (Math.random() - 0.5) * 0.08;
        }
      }

      // 种子碎片(白絮)
      const count = Math.round(9 * power);
      for (let i = 0; i < count; i += 1) {
        const angle = Math.random() * TAU;
        const speed = 1.1 + Math.random() * 3.2 * power;
        bursts.push({
          x,
          y,
          vx: Math.cos(angle) * speed,
          vy: Math.sin(angle) * speed - 1.2,
          size: 2.2 + Math.random() * 4,
          rot: Math.random() * TAU,
          vr: (Math.random() - 0.5) * 0.28,
          life: 1,
          decay: 0.008 + Math.random() * 0.01,
          color: SEED_COLORS[(Math.random() * SEED_COLORS.length) | 0],
          alpha: 0.82,
        });
      }
      if (bursts.length > 380) bursts.splice(0, bursts.length - 380);
      // 全屏特效关闭时没有常驻动画,碎片/涟漪画完循环会停;点击时重新拉起。
      if (!reduceMotion && !running) {
        running = true;
        last = performance.now();
        rafId = requestAnimationFrame(step);
      }
    };

    const onPointerDown = (event: PointerEvent) => {
      if (!tapRef.current) return;
      burstAt(event.clientX, event.clientY, 1);
    };
    const onDblClick = (event: MouseEvent) => {
      if (!tapRef.current) return;
      burstAt(event.clientX, event.clientY, 2.5);
    };

    const step = (now: number) => {
      const dt = Math.min(32, now - last);
      last = now;
      const k = dt / 16.7;

      ctx.clearRect(0, 0, width, height);

      // 种子:缓慢飘落 + 摇摆 + 左向微风 + 翻面 + 光标气流
      for (const seed of seeds) {
        seed.y += seed.vy * k;
        // 草原上气流偏水平:横向漂移比桃花更明显
        seed.x += Math.sin(seed.swayPhase) * seed.swayAmp * 0.012 * k + seed.swayAmp * 0.006;
        seed.swayPhase += seed.swaySpeed * dt;
        seed.rot += seed.rotSpeed * dt;
        seed.tumblePhase += seed.tumbleSpeed * dt;

        const dx = seed.x - pointer.x;
        const dy = seed.y - pointer.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < 160 * 160) {
          const d = Math.sqrt(d2) || 1;
          const f = 1 - d / 160;
          seed.x += (dx / d) * f * 3.2 * k;
          seed.y += (dy / d) * f * 3.2 * k - f * 1 * k;
        }

        if (seed.y > height + 40) {
          seed.y = -36;
          seed.x = Math.random() * width;
        }
        if (seed.x < -60) seed.x = width + 40;
        else if (seed.x > width + 60) seed.x = -40;

        // 绕竖轴翻面:scaleX 在 0.3~1 之间呼吸,种子在风里打转的立体感
        const tumble = 0.3 + 0.7 * Math.abs(Math.sin(seed.tumblePhase));
        drawSeedSprite(ctx, seed.x, seed.y, seed.size, seed.rot, seed.flip * tumble, seed.alpha, seed.color);
      }

      // 爆散碎片
      for (let i = bursts.length - 1; i >= 0; i -= 1) {
        const b = bursts[i];
        b.life -= b.decay * dt;
        if (b.life <= 0) {
          bursts.splice(i, 1);
          continue;
        }
        b.vy += 0.032 * k;
        b.x += b.vx * k;
        b.y += b.vy * k;
        b.rot += b.vr * dt;
        drawSeedSprite(ctx, b.x, b.y, b.size, b.rot, 1, Math.max(0, b.life) * b.alpha, b.color);
      }

      // 涟漪(压扁的椭圆,呼应草甸的圆形花丛)
      for (let i = ripples.length - 1; i >= 0; i -= 1) {
        const ripple = ripples[i];
        ripple.r += 2.5 * k;
        ripple.alpha -= 0.015 * k;
        if (ripple.alpha <= 0) {
          ripples.splice(i, 1);
          continue;
        }
        ctx.strokeStyle = `rgba(47, 125, 82, ${ripple.alpha})`;
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        ctx.ellipse(ripple.x, ripple.y, ripple.r, ripple.r * 0.42, 0, 0, TAU);
        ctx.stroke();
      }

      // 全屏特效关闭时没有常驻动画:碎片与涟漪都消散后停帧,避免画布空转耗电。
      if (!ambientEffects && bursts.length === 0 && ripples.length === 0) {
        running = false;
        return;
      }
      rafId = requestAnimationFrame(step);
    };

    const onVisibility = () => {
      if (document.hidden) {
        cancelAnimationFrame(rafId);
        running = false;
      } else if (!running && !reduceMotion) {
        running = true;
        last = performance.now();
        rafId = requestAnimationFrame(step);
      }
    };

    resize();
    window.addEventListener("resize", resize);
    // 光标"气流"推开种子属于全屏特效,关掉时无需监听指针移动。
    if (ambientEffects) window.addEventListener("pointermove", onPointerMove, { passive: true });
    window.addEventListener("pointerdown", onPointerDown, { passive: true });
    window.addEventListener("dblclick", onDblClick, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);

    if (reduceMotion) {
      // 静态定格一帧:种子散落,不做动画
      for (const seed of seeds) {
        drawSeedShape(ctx, seed.x, seed.y, seed.size, seed.rot, seed.flip, seed.alpha, seed.color);
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
  }, [active, ambientEffects]);

  if (!sceneryActive) return null;

  return (
    <>
      <div className="alpine-meadow-scenery" aria-hidden="true">
        {/* 云影是常驻漂移动画,归全屏特效;草甸背景图保留(属于主题外观)。 */}
        {ambientEffects && <div className="alpine-meadow-clouds" />}
      </div>
      {active && createPortal(
        <canvas ref={canvasRef} className="alpine-meadow-seeds" aria-hidden="true" />,
        document.body,
      )}
    </>
  );
});
