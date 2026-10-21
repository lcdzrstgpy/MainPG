/**
 * 爆款复刻 → 视频工作台的原生交接（T1.5）。
 *
 * 上游把商品名/卖点放在 URL query、把节奏骨架放在 localStorage；原生页面没有 URL 参数，
 * 因此统一放进一个版本化暂存，由 /start 消费一次。金额/文案这里的清洗规则与上游一致。
 */

import type { CreationBrief } from "./creation-brief";

export const AI_VIDEO_CLONE_PREFILL_KEY = "mainpg.aiVideo.clonePrefill";

export interface CloneHandoff {
  version: 1;
  productName: string;
  sellingPoints: string;
  brief: Partial<CreationBrief>;
  referenceStructure?: string;
  referenceVideoUrl?: string;
  productImages?: string[];
}

export interface CloneHandoffStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

function text(value: unknown, max: number): string {
  return typeof value === "string" ? value.trim().slice(0, max) : "";
}

export function sanitizeCloneHandoff(raw: unknown): CloneHandoff | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as Record<string, unknown>;
  const productName = text(source.productName, 200);
  const sellingPoints = text(source.sellingPoints, 400);
  const briefRaw = source.brief && typeof source.brief === "object" ? (source.brief as Record<string, unknown>) : {};
  const referenceStructure = text(source.referenceStructure, 8000);
  const referenceVideoUrl = text(source.referenceVideoUrl, 500);
  const productImages = Array.isArray(source.productImages)
    ? source.productImages.filter((item): item is string => typeof item === "string" && item.trim().length > 0).slice(0, 5)
    : [];
  return {
    version: 1,
    productName,
    sellingPoints,
    brief: {
      ...(typeof briefRaw.inputMode === "string" ? { inputMode: briefRaw.inputMode as CreationBrief["inputMode"] } : {}),
      ...(typeof briefRaw.styleType === "string" ? { styleType: briefRaw.styleType } : {}),
      ...(typeof briefRaw.styleSource === "string"
        ? { styleSource: briefRaw.styleSource as CreationBrief["styleSource"] }
        : {}),
      ...(briefRaw.targetDuration === 15 || briefRaw.targetDuration === 30 || briefRaw.targetDuration === 60
        ? { targetDuration: briefRaw.targetDuration }
        : {}),
    },
    ...(referenceStructure ? { referenceStructure } : {}),
    ...(referenceVideoUrl ? { referenceVideoUrl } : {}),
    ...(productImages.length ? { productImages } : {}),
  };
}

export function writeCloneHandoff(storage: CloneHandoffStorage | null, handoff: CloneHandoff): void {
  if (!storage) return;
  const clean = sanitizeCloneHandoff(handoff);
  if (!clean) return;
  storage.setItem(AI_VIDEO_CLONE_PREFILL_KEY, JSON.stringify(clean));
}

/** 读取并清除：同一份交接只消费一次。 */
export function consumeCloneHandoff(storage: CloneHandoffStorage | null): CloneHandoff | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(AI_VIDEO_CLONE_PREFILL_KEY);
    storage.removeItem(AI_VIDEO_CLONE_PREFILL_KEY);
    if (!raw) return null;
    return sanitizeCloneHandoff(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function clearCloneHandoff(storage: CloneHandoffStorage | null): void {
  if (!storage) return;
  try {
    storage.removeItem(AI_VIDEO_CLONE_PREFILL_KEY);
  } catch {
    // 存储不可用：忽略。
  }
}
