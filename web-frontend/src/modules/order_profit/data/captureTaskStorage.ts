import { getAuthAccount } from "../../../transport/http/client";

import type { CaptureTaskKind } from "./captureTaskModel";

/** 采集任务的本地持久化：页面切走/刷新后仍能找回正在跑的任务并恢复进度。 */

export type PersistedCaptureTask = {
  commandId: number;
  kind: CaptureTaskKind;
  label: string;
  shopName?: string;
  startedAt: number;
};

export const CAPTURE_TASK_EVENT = "order-profit-capture-task-changed";

const TASK_KEY_PREFIX = "order-profit-active-capture-v1";
const LAST_FULL_KEY_PREFIX = "order-profit-last-full-sync-v1";

/** 按登录账号隔离，避免同一浏览器上多个账号互相覆盖任务。 */
function storageScope(): string {
  try {
    const account = getAuthAccount<{ username?: string }>();
    const username = String(account?.username || "").trim().toLowerCase();
    return username || "local";
  } catch {
    return "local";
  }
}

function taskKey(): string {
  return `${TASK_KEY_PREFIX}:${storageScope()}`;
}

function lastFullKey(shopName: string): string {
  const shop = String(shopName || "").trim().toLowerCase();
  return `${LAST_FULL_KEY_PREFIX}:${storageScope()}:${shop || "unknown-shop"}`;
}

function canUseStorage(): boolean {
  try {
    return typeof window !== "undefined" && Boolean(window.localStorage);
  } catch {
    return false;
  }
}

function readText(key: string): string {
  if (!canUseStorage()) return "";
  try {
    return window.localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

function writeText(key: string, value: string): void {
  if (!canUseStorage()) return;
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // 隐私模式或配额满时静默降级：任务仍会在当前页面内正常轮询。
  }
}

function removeKey(key: string): void {
  if (!canUseStorage()) return;
  try {
    window.localStorage.removeItem(key);
  } catch {
    // 同上，存储不可用时不阻断采集。
  }
}

function notify(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new Event(CAPTURE_TASK_EVENT));
}

export function loadActiveCaptureTask(): PersistedCaptureTask | null {
  const raw = readText(taskKey());
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<PersistedCaptureTask>;
    const commandId = Number(parsed.commandId);
    if (!Number.isFinite(commandId) || commandId <= 0) return null;
    const kind: CaptureTaskKind = parsed.kind === "full" || parsed.kind === "resource"
      ? parsed.kind
      : "incremental";
    return {
      commandId,
      kind,
      label: String(parsed.label || "采集任务"),
      shopName: String(parsed.shopName || ""),
      startedAt: Number(parsed.startedAt) || 0,
    };
  } catch {
    return null;
  }
}

export function saveActiveCaptureTask(task: PersistedCaptureTask): void {
  writeText(taskKey(), JSON.stringify(task));
  notify();
}

/** 传入 commandId 时只清理这条任务，避免旧任务的收尾覆盖新任务。 */
export function clearActiveCaptureTask(commandId?: number): void {
  if (commandId) {
    const current = loadActiveCaptureTask();
    if (current && current.commandId !== commandId) return;
  }
  removeKey(taskKey());
  notify();
}

export function loadLastFullSyncAt(shopName = ""): number {
  const value = Number(readText(lastFullKey(shopName)));
  return Number.isFinite(value) ? value : 0;
}

export function saveLastFullSyncAt(timestamp: number, shopName = ""): void {
  writeText(lastFullKey(shopName), String(timestamp));
}
