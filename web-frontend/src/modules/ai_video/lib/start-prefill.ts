/**
 * 原生入口之间的预填交接（T1.3）：商品素材「做视频」只把商品 id 放到本地暂存，
 * /start 挂载时消费一次——与爆款复刻使用 localStorage 交接的约定一致，避免把状态塞进 URL。
 */

export const AI_VIDEO_START_PREFILL_KEY = "mainpg.aiVideo.startPrefill";

export interface AiVideoStartPrefill {
  productId?: string;
}

export interface PrefillStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function writeStartPrefill(storage: PrefillStorage | null, prefill: AiVideoStartPrefill): void {
  if (!storage) return;
  try {
    storage.setItem(AI_VIDEO_START_PREFILL_KEY, JSON.stringify(prefill));
  } catch {
    // 存储不可用：本次跳转不预填，不影响进入工作台。
  }
}

/** 读取并清除：同一份交接只消费一次，刷新不会重放。 */
export function consumeStartPrefill(storage: PrefillStorage | null): AiVideoStartPrefill | null {
  if (!storage) return null;
  try {
    const raw = storage.getItem(AI_VIDEO_START_PREFILL_KEY);
    storage.removeItem(AI_VIDEO_START_PREFILL_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { productId?: unknown } | null;
    const productId = typeof parsed?.productId === "string" ? parsed.productId.trim() : "";
    return productId ? { productId } : null;
  } catch {
    return null;
  }
}
