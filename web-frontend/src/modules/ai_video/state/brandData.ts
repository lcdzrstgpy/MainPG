/**
 * 品牌设置本地数据层（T1.7）：纯函数，可脱离 React 单测。
 * 存储键沿用 ClipForge 的 `daihuo-jianshou-brand`，落在 MainPG origin；
 * 导出页（T2.4）会读取同一份 store 来渲染水印/片尾等品牌元素。
 */

export const BRAND_STORAGE_KEY = "daihuo-jianshou-brand";

export type WatermarkPosition = "top-left" | "top-right" | "bottom-left" | "bottom-right";

export interface BrandWatermarkConfig {
  enabled: boolean;
  position: WatermarkPosition;
  /** 0.1–1 */
  opacity: number;
  /** 0.1–0.5 */
  scale: number;
}

export interface BrandConfig {
  id: string;
  name: string;
  logoUrl?: string;
  primaryColor: string;
  secondaryColor: string;
  fontFamily: string;
  watermark: BrandWatermarkConfig;
  introEnabled: boolean;
  outroEnabled: boolean;
  outroText?: string;
}

export interface BrandStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const POSITIONS: readonly WatermarkPosition[] = ["top-left", "top-right", "bottom-left", "bottom-right"];
const HEX_COLOR = /^#[0-9a-f]{3,8}$/i;

export function defaultBrand(): BrandConfig {
  return {
    id: crypto.randomUUID(),
    name: "我的店铺",
    primaryColor: "#6366f1",
    secondaryColor: "#8b5cf6",
    fontFamily: "默认字体",
    watermark: { enabled: false, position: "bottom-right", opacity: 0.3, scale: 0.15 },
    introEnabled: false,
    outroEnabled: false,
  };
}

function objectOf(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function text(value: unknown, fallback: string): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

function clamp(value: unknown, min: number, max: number, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.min(max, Math.max(min, value));
}

export function sanitizeBrand(raw: unknown): BrandConfig {
  const base = defaultBrand();
  // 兼容 zustand persist 包装。
  const source = objectOf(objectOf(raw)?.state)?.brand ?? raw;
  const item = objectOf(source);
  if (!item) return base;

  const watermark = objectOf(item.watermark);
  const rawPosition = watermark?.position;
  const position: WatermarkPosition =
    typeof rawPosition === "string" && (POSITIONS as readonly string[]).includes(rawPosition)
      ? (rawPosition as WatermarkPosition)
      : base.watermark.position;
  const outroText = text(item.outroText, "");

  return {
    id: text(item.id, base.id),
    name: text(item.name, base.name),
    primaryColor: HEX_COLOR.test(text(item.primaryColor, "")) ? text(item.primaryColor, "") : base.primaryColor,
    secondaryColor: HEX_COLOR.test(text(item.secondaryColor, ""))
      ? text(item.secondaryColor, "")
      : base.secondaryColor,
    fontFamily: text(item.fontFamily, base.fontFamily),
    watermark: {
      enabled: watermark?.enabled === true,
      position,
      opacity: clamp(watermark?.opacity, 0.1, 1, base.watermark.opacity),
      scale: clamp(watermark?.scale, 0.1, 0.5, base.watermark.scale),
    },
    introEnabled: item.introEnabled === true,
    outroEnabled: item.outroEnabled === true,
    ...(text(item.logoUrl, "") ? { logoUrl: text(item.logoUrl, "") } : {}),
    ...(outroText ? { outroText } : {}),
  };
}

export function readBrandFrom(storage: BrandStorage | null): BrandConfig {
  if (!storage) return defaultBrand();
  try {
    const stored = storage.getItem(BRAND_STORAGE_KEY);
    return stored ? sanitizeBrand(JSON.parse(stored)) : defaultBrand();
  } catch {
    return defaultBrand();
  }
}

export function writeBrandTo(storage: BrandStorage | null, brand: BrandConfig): void {
  if (!storage) return;
  try {
    storage.setItem(BRAND_STORAGE_KEY, JSON.stringify({ state: { brand }, version: 0 }));
  } catch {
    // 隐私模式 / 配额写满：本次会话内仍可用。
  }
}
