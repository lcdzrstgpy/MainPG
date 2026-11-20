import { useCallback, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";

export type CollectionGateMode = "daily" | "shop" | "plugin";

type Slide = { src: string };

/**
 * 三个入口圆泡的背景素材：定向=合规商品图；整店/插件=脱敏后的平台页截图
 * （店名与供应商已模糊）。与原来入口卡的素材保持一致。
 */
const SLIDES: Record<CollectionGateMode, Slide[]> = {
  daily: [
    { src: "/assets/daily-selection/desk.jpg" },
    { src: "/assets/daily-selection/pet.jpg" },
    { src: "/assets/daily-selection/case.jpg" },
    { src: "/assets/daily-selection/garden.jpg" },
  ],
  shop: [
    { src: "/assets/daily-selection/store-1.jpg" },
    { src: "/assets/daily-selection/store-2.jpg" },
  ],
  plugin: [
    { src: "/assets/daily-selection/search-1.jpg" },
    { src: "/assets/daily-selection/search-2.jpg" },
  ],
};

type GateEntry = {
  mode: CollectionGateMode;
  label: string;
  iconClass: string;
  tagline: string;
  intro: string;
  bullets: string[];
  slides: Slide[];
};

const ENTRIES: GateEntry[] = [
  {
    mode: "daily",
    label: "定向采集",
    iconClass: "icon-search",
    tagline: "关键词 / 参考图",
    intro: "给一个明确方向，系统逐条拉取商品详情，适合已有目标款、想横向比价。",
    bullets: [
      "输入最多 5 个关键词，或提供一张参考图",
      "逐条抓取 SKU、阶梯价、属性与库存",
      "按综合分排序后确认入池",
    ],
    slides: SLIDES.daily,
  },
  {
    mode: "shop",
    label: "整店采集",
    iconClass: "icon-shop",
    tagline: "整店链接 · 批量筛款",
    intro: "把一个店铺整店扫一遍，按销量、价格、上新时间批量筛出可做的款。",
    bullets: [
      "粘贴 1688 店铺链接即可开跑",
      "先粗筛再逐款补详情，省调用量",
      "结果按批次归档，可回看与续采",
    ],
    slides: SLIDES.shop,
  },
  {
    mode: "plugin",
    label: "插件采集",
    iconClass: "icon-cloud",
    tagline: "浏览器插件 · 一键收录",
    intro: "在 1688 / 淘宝页面上用浏览器插件点一下，把当前商品直接收进工作台。",
    bullets: [
      "适合边逛边收，不离开原页面",
      "自动带回落地的价格与规格",
      "收录后可继续补图与核价",
    ],
    slides: SLIDES.plugin,
  },
];

/** 拖影粒子上限与生成间隔：低配机器上宁可少而稳。 */
const MAX_PARTICLES = 26;
const EMIT_INTERVAL_MS = 30;
const PARTICLE_LIFE_MS = 620;

type Particle = {
  x: number;
  y: number;
  vx: number;
  vy: number;
  bornAt: number;
  size: number;
};

type Props = {
  onEnter: (mode: CollectionGateMode) => void;
};

/**
 * 采集入口独立界面。
 *
 * 交互（2026-11-05 第二版）：
 *  1. 三个圆泡按韦恩图交错排布（上、左下、右下，尺寸与高度刻意不等），悬停放大、其余后退；
 *  2. 科技感：网格底纹、扫描线、角标、渐变描边环，但全部是「合成层」动画；
 *  3. 性能：光粒拖影改成单张 canvas（粒子有上限、空闲即停 rAF），
 *     柔光跟随改用 transform，弹层不做 backdrop-filter；
 *     系统「流畅模式」（html[data-perf-mode="on"]）下直接关掉 canvas、气泡与扫描线。
 */
export function CollectionEntryGate({ onEnter }: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const glowRef = useRef<HTMLSpanElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const particlesRef = useRef<Particle[]>([]);
  const rafRef = useRef(0);
  const lastEmitRef = useRef(0);
  const spriteRef = useRef<HTMLCanvasElement | null>(null);
  const [hovered, setHovered] = useState<CollectionGateMode | null>(null);
  const motionOkay = useMotionAllowed();

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const now = performance.now();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const sprite = spriteRef.current;
    const alive: Particle[] = [];
    for (const particle of particlesRef.current) {
      const age = now - particle.bornAt;
      if (age >= PARTICLE_LIFE_MS) continue;
      const progress = age / PARTICLE_LIFE_MS;
      const alpha = (1 - progress) * 0.85;
      const size = particle.size * (1 - progress * 0.55);
      const x = particle.x + particle.vx * progress;
      const y = particle.y + particle.vy * progress;
      if (sprite) {
        ctx.globalAlpha = alpha;
        ctx.drawImage(sprite, (x - size / 2) * dpr, (y - size / 2) * dpr, size * dpr, size * dpr);
      }
      alive.push(particle);
    }
    ctx.globalAlpha = 1;
    particlesRef.current = alive;
    if (alive.length > 0) {
      rafRef.current = window.requestAnimationFrame(draw);
    } else {
      rafRef.current = 0;
    }
  }, []);

  const startLoop = useCallback(() => {
    if (rafRef.current) return;
    rafRef.current = window.requestAnimationFrame(draw);
  }, [draw]);

  // 预渲染一枚光点精灵，避免每帧创建渐变对象（低配机器上开销明显）。
  useEffect(() => {
    const sprite = document.createElement("canvas");
    const size = 32;
    sprite.width = size;
    sprite.height = size;
    const ctx = sprite.getContext("2d");
    if (ctx) {
      const gradient = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
      gradient.addColorStop(0, "rgba(255,255,255,.95)");
      gradient.addColorStop(0.45, "rgba(120,206,236,.55)");
      gradient.addColorStop(1, "rgba(120,206,236,0)");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, size, size);
    }
    spriteRef.current = sprite;
  }, []);

  // 画布尺寸跟随容器；DPR 封顶 1.5，4K 屏上也不至于让填充率翻倍。
  useEffect(() => {
    const stage = stageRef.current;
    const canvas = canvasRef.current;
    if (!stage || !canvas) return;
    const resize = () => {
      const rect = stage.getBoundingClientRect();
      const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      canvas.width = Math.round(rect.width * dpr);
      canvas.height = Math.round(rect.height * dpr);
      canvas.style.width = `${rect.width}px`;
      canvas.style.height = `${rect.height}px`;
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(stage);
    return () => observer.disconnect();
  }, []);

  // 页面不可见时停掉 rAF 并清空粒子，切回来重新开始，避免后台空转。
  useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) {
        particlesRef.current = [];
        if (rafRef.current) {
          window.cancelAnimationFrame(rafRef.current);
          rafRef.current = 0;
        }
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      if (rafRef.current) window.cancelAnimationFrame(rafRef.current);
    };
  }, []);

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const stage = stageRef.current;
    if (!stage) return;
    const rect = stage.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    // 柔光只改 transform（合成层），不做 top/left 过渡，避免每帧重排。
    if (glowRef.current) {
      glowRef.current.style.transform = `translate3d(${x - 170}px, ${y - 170}px, 0)`;
    }
    if (!motionOkay) return;

    const now = performance.now();
    if (now - lastEmitRef.current < EMIT_INTERVAL_MS) return;
    lastEmitRef.current = now;
    if (particlesRef.current.length >= MAX_PARTICLES) return;
    particlesRef.current.push({
      x,
      y,
      vx: (Math.random() - 0.5) * 34,
      vy: -14 - Math.random() * 26,
      bornAt: now,
      size: 5 + Math.random() * 6,
    });
    startLoop();
  };

  return (
    <section
      className={`collection-gate${hovered ? " has-hover" : ""}${motionOkay ? "" : " is-calm"}`}
      aria-label="采集入口"
      ref={stageRef}
      onPointerMove={handlePointerMove}
    >
      <span className="collection-gate-grid" aria-hidden="true" />
      <span className="collection-gate-sky" aria-hidden="true" />
      <span className="collection-gate-scan" aria-hidden="true" />
      <span className="collection-gate-glow" aria-hidden="true" ref={glowRef} />
      <canvas className="collection-gate-canvas" aria-hidden="true" ref={canvasRef} />
      <span className="collection-gate-bubbles" aria-hidden="true">
        {Array.from({ length: 9 }, (_, index) => (
          <i key={index} style={{ "--bubble-index": index } as CSSProperties} />
        ))}
      </span>
      <span className="collection-gate-frame" aria-hidden="true">
        <i /><i /><i /><i />
      </span>

      {/* 装饰层：同心水波纹 + 底部波浪，把三球之外的留白填满，纯静态、零渲染成本 */}
      <svg className="collection-gate-ripple" viewBox="0 0 600 520" aria-hidden="true" focusable="false">
        <g fill="none" stroke="rgba(56, 169, 214, .075)" strokeWidth=".8">
          <circle cx="300" cy="248" r="236" strokeDasharray="1 12" />
          <circle cx="300" cy="248" r="214" strokeDasharray="2 18" opacity=".8" />
          <circle cx="300" cy="248" r="188" opacity=".5" />
          <circle cx="300" cy="248" r="150" strokeDasharray="1 9" opacity=".45" />
        </g>
        <g fill="none" stroke="rgba(25, 189, 163, .08)" strokeWidth=".8">
          <path d="M64 248a236 236 0 0 1 472 0" strokeDasharray="4 14" />
          <path d="M92 352a208 208 0 0 0 416 0" strokeDasharray="2 16" opacity=".7" />
        </g>
      </svg>

      <svg className="collection-gate-waves" viewBox="0 0 1200 150" preserveAspectRatio="none" aria-hidden="true" focusable="false">
        <path
          d="M0 86c86-34 172-34 258 0s172 34 258 0 172-34 258 0 172 34 258 0"
          fill="none"
          stroke="rgba(56, 169, 214, .13)"
          strokeWidth="1"
        />
        <path
          d="M0 108c96-26 192-26 288 0s192 26 288 0 192-26 288 0 192 26 288 0"
          fill="none"
          stroke="rgba(25, 189, 163, .11)"
          strokeWidth=".9"
        />
        <path
          d="M0 130c110-22 220-22 330 0s220 22 330 0 220-22 330 0"
          fill="none"
          stroke="rgba(244, 168, 186, .12)"
          strokeWidth=".8"
        />
      </svg>

      <span className="collection-gate-aside collection-gate-aside--left" aria-hidden="true">
        <b>STEP 01</b>
        <i />
        选择货源来源
      </span>
      <span className="collection-gate-aside collection-gate-aside--right" aria-hidden="true">
        <b>SAME RULES</b>
        <i />
        同一套筛选与评分
      </span>

      <header className="collection-gate-head">
        <span className="collection-gate-eyebrow">COLLECTION ENTRY · SYS.01</span>
        <h2>先选一个采集方式</h2>
        <p>三种入口共用同一套筛选、评分与入池规则，差别只在货从哪里来。</p>
        <span className="collection-gate-status">
          <i aria-hidden="true" />
          3 个模块在线
        </span>
      </header>

      <div className="collection-gate-orbits">
        {ENTRIES.map((entry, index) => (
          <button
            key={entry.mode}
            type="button"
            className={`collection-gate-orb collection-gate-orb--${index}`}
            data-mode={entry.mode}
            aria-label={`${entry.label}：${entry.tagline}，点击直接进入`}
            onPointerEnter={() => setHovered(entry.mode)}
            onPointerLeave={() => setHovered((current) => (current === entry.mode ? null : current))}
            onFocus={() => setHovered(entry.mode)}
            onBlur={() => setHovered((current) => (current === entry.mode ? null : current))}
            onClick={() => onEnter(entry.mode)}
          >
            <span className="collection-gate-orb-media" aria-hidden="true">
              {entry.slides.slice(0, 3).map((slide) => (
                <img key={slide.src} src={slide.src} alt="" loading="lazy" draggable={false} />
              ))}
            </span>
            <span className="collection-gate-orb-ring" aria-hidden="true" />
            <span className="collection-gate-orb-body">
              <span className={`iconfont ${entry.iconClass}`} aria-hidden="true" />
              <strong>{entry.label}</strong>
              <small className="collection-gate-orb-tagline">{entry.tagline}</small>
              {/* 原来弹层里的说明收到气泡里，点进来之前就能读完 */}
              <span className="collection-gate-orb-note">{entry.intro}</span>
              <span className="collection-gate-orb-enter" aria-hidden="true">
                点击进入 <i>→</i>
              </span>
            </span>
          </button>
        ))}
      </div>
    </section>
  );
}

/**
 * 只在「系统没要求减少动效」且「没开流畅模式」时跑装饰动画。
 * 流畅模式是弱机自动开启的（见 usePerformanceMode），这里直接读 <html> 属性，
 * 避免为了一个开关把整页订阅进去。
 */
function useMotionAllowed(): boolean {
  const read = () => {
    if (typeof document === "undefined") return false;
    if (document.documentElement.dataset.perfMode === "on") return false;
    return !window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  };
  const [allowed, setAllowed] = useState(read);
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-reduced-motion: reduce)");
    const update = () => setAllowed(read());
    media?.addEventListener("change", update);
    const observer = new MutationObserver(update);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-perf-mode"] });
    return () => {
      media?.removeEventListener("change", update);
      observer.disconnect();
    };
  }, []);
  return allowed;
}
