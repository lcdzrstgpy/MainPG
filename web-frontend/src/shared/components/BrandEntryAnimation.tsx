import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";

import { BRAND_LOGO_URL, BRAND_NAME, type BrandLogoRect } from "../brand";

type BrandEntryAnimationProps = {
  active: boolean;
  /** 登录页 logo 的屏幕矩形：有它就从那个位置起飞，没有就从屏幕中央淡入 */
  origin?: BrandLogoRect | null;
  onComplete: () => void;
};

/**
 * 几何全部换算成「相对视口中心的位移 + 相对起始宽度的缩放」：
 * logo 元素本身尺寸固定（起始宽度），整段飞行只用 transform，
 * 不再动画 top/left/width，避免每帧重排。
 */
type EntryGeometry = {
  width: number;
  endX: number;
  endY: number;
  endScale: number;
  fromX: number;
  fromY: number;
  fromScale: number;
  fromAuth: boolean;
};

type EntryStyle = CSSProperties & Record<`--brand-entry-${string}`, string>;

const ENTRY_DURATION_MS = 2300;
/** 从登录页 logo 接过来时多一段「长到屏幕中央」，整体拉长 */
const ENTRY_DURATION_FROM_AUTH_MS = 2600;

function elementIsVisible(element: HTMLElement | null) {
  if (!element) return false;
  const rect = element.getBoundingClientRect();
  const style = window.getComputedStyle(element);
  return rect.width > 1 && rect.height > 1 && style.display !== "none" && style.visibility !== "hidden";
}

/**
 * 目标 logo 是 <img> + object-fit: contain：图片缩进框里之后，**实际画出来的**
 * 矩形往往比框小，而且还不一定居中（侧栏 .brand-logo 是 144×56 的框装 1070:630 的图，
 * 实际只画 95×56，并且 object-position: left center 靠左）。
 * 入场动画的终点必须落在「实际画出来的矩形」上，否则最后一下会明显变大再跳回去。
 */
function drawnImageRect(image: HTMLImageElement) {
  const rect = image.getBoundingClientRect();
  const naturalWidth = image.naturalWidth;
  const naturalHeight = image.naturalHeight;
  if (!naturalWidth || !naturalHeight || !rect.width || !rect.height) return null;

  const scale = Math.min(rect.width / naturalWidth, rect.height / naturalHeight);
  const width = naturalWidth * scale;
  const height = naturalHeight * scale;

  // object-position 计算值形如 "0% 50%"；也兼容 left/center/right 关键字
  const position = window.getComputedStyle(image).objectPosition || "50% 50%";
  const parts = position.trim().split(/\s+/);
  const toRatio = (token: string | undefined, fallback: number) => {
    if (!token) return fallback;
    if (token === "left" || token === "top") return 0;
    if (token === "center") return 0.5;
    if (token === "right" || token === "bottom") return 1;
    const percent = /^(-?[\d.]+)%$/.exec(token);
    if (percent) return Number(percent[1]) / 100;
    const pixels = /^(-?[\d.]+)px$/.exec(token);
    if (pixels) return Number(pixels[1]) / 100; // 极少见：按框宽百分比近似
    return fallback;
  };
  const ratioX = toRatio(parts[0], 0.5);
  const ratioY = toRatio(parts[1], ratioX);

  return {
    left: rect.left + (rect.width - width) * ratioX,
    top: rect.top + (rect.height - height) * ratioY,
    width,
    height,
  };
}

export function BrandEntryAnimation({ active, origin = null, onComplete }: BrandEntryAnimationProps) {
  const [geometry, setGeometry] = useState<EntryGeometry | null>(null);
  const completedRef = useRef(false);
  // 父组件常传内联箭头（每次 render 身份都变）：用 ref 保存最新引用，
  // effect 依赖只留 [active]，避免动画期间父级重渲染重置定时器/完成标记。
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  useLayoutEffect(() => {
    if (!active) return;

    completedRef.current = false;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const frame = window.requestAnimationFrame(() => onCompleteRef.current());
      return () => window.cancelAnimationFrame(frame);
    }

    const brandTarget = document.querySelector<HTMLElement>("[data-brand-entry-target]");
    const fallbackTarget = document.querySelector<HTMLElement>(".sidebar-pin-button");
    const target = elementIsVisible(brandTarget) ? brandTarget : fallbackTarget;
    const rect = target?.getBoundingClientRect();
    const targetIsBrand = target === brandTarget;

    const width = Math.min(480, Math.max(260, window.innerWidth * 0.46));
    const centerX = window.innerWidth / 2;
    const centerY = window.innerHeight / 2;
    // 落点用「图片真正画出来的矩形」而不是 <img> 的盒子
    const drawn = target instanceof HTMLImageElement ? drawnImageRect(target) : null;
    const endWidth = drawn ? drawn.width : rect ? (targetIsBrand ? rect.width : Math.min(rect.width, 42)) : 42;
    const endCenterX = drawn ? drawn.left + drawn.width / 2 : rect ? rect.left + rect.width / 2 : 42;
    const endCenterY = drawn ? drawn.top + drawn.height / 2 : rect ? rect.top + rect.height / 2 : 38;
    const fromAuth = Boolean(origin && origin.width > 4);

    setGeometry({
      width,
      endX: endCenterX - centerX,
      endY: endCenterY - centerY,
      endScale: endWidth / width,
      fromX: fromAuth ? origin!.x + origin!.width / 2 - centerX : 0,
      fromY: fromAuth ? origin!.y + origin!.height / 2 - centerY : 0,
      fromScale: fromAuth ? origin!.width / width : 0.72,
      fromAuth,
    });

    const duration = fromAuth ? ENTRY_DURATION_FROM_AUTH_MS : ENTRY_DURATION_MS;
    const timer = window.setTimeout(() => onCompleteRef.current(), duration + 240);
    return () => window.clearTimeout(timer);
  }, [active, origin]);

  if (!active || !geometry) return null;

  const style: EntryStyle = {
    "--brand-entry-w": `${geometry.width}px`,
    "--brand-entry-x0": `${geometry.fromX}px`,
    "--brand-entry-y0": `${geometry.fromY}px`,
    "--brand-entry-s0": `${geometry.fromScale}`,
    "--brand-entry-x1": `${geometry.endX}px`,
    "--brand-entry-y1": `${geometry.endY}px`,
    "--brand-entry-s1": `${geometry.endScale}`,
  };
  const overlayStyle: EntryStyle = {
    ...style,
    "--brand-entry-duration": `${(geometry.fromAuth ? ENTRY_DURATION_FROM_AUTH_MS : ENTRY_DURATION_MS) / 1000}s`,
  };

  const finish = () => {
    if (completedRef.current) return;
    completedRef.current = true;
    onComplete();
  };

  return (
    <div
      className="workspace-entry-overlay"
      data-from-auth={geometry.fromAuth ? "true" : "false"}
      style={overlayStyle}
      aria-hidden="true"
    >
      {/* 幕布单独一层：它要能淡入淡出，而 logo 不能跟着一起变淡 */}
      <div className="workspace-entry-veil" />
      <div
        className="workspace-entry-brand"
        data-from-auth={geometry.fromAuth ? "true" : "false"}
        style={style}
        onAnimationEnd={(event) => {
          if (event.target === event.currentTarget) finish();
        }}
      >
        <span className="workspace-entry-halo" />
        {/* 从登录页接过来时，logo 一开始是登录页那种「反白」形态：
            底色还压着登录页的深色渐变。等白色幕布铺开，再交叉淡入墨色版本。 */}
        {geometry.fromAuth && (
          <img className="workspace-entry-logo workspace-entry-logo-white" src={BRAND_LOGO_URL} alt="" />
        )}
        <img className="workspace-entry-logo workspace-entry-logo-ink" src={BRAND_LOGO_URL} alt={BRAND_NAME} />
      </div>
    </div>
  );
}
