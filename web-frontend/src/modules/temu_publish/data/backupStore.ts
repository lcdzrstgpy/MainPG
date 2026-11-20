/**
 * 本机数据的「导出备份 / 导入恢复」。
 *
 * 背景（2026-11-20 用户确认需求）：产品模板、变种模板、属性估填、失败台账、
 * 尺码表模板等全部只存浏览器 localStorage —— 换电脑/重装/清缓存就全丢，
 * 也发不了给同事。这里把「当前登录账号」名下的所有 temu_* 存档打包成一个
 * JSON 文件带走；导入时按下面的合并规则放回当前账号名下。
 *
 * 合并规则（导入不会清掉已有数据）：
 * - 数组且元素带 name → 按 name 覆盖合并（产品/变种模板）
 * - 数组且元素带 id → 按 id 覆盖合并（尺码表模板）
 * - 其它数组 → 按内容去重并入（失败台账）
 * - 普通对象 → 按键合并，导入方优先（估填/类目记录）
 * - 其它类型 → 以导入为准
 */

import { storageScope } from "./attrPicksStore";

export const BACKUP_FORMAT = "temu-publish-backup";
export const BACKUP_VERSION = 1;

/** 备份文件里「基名 → 中文说明」，导出摘要/导入摘要都用它 */
export const BACKUP_KEY_LABELS: Record<string, string> = {
  temu_publish_product_templates: "产品模板",
  temu_publish_variant_templates: "变种模板",
  temu_publish_failures: "失败台账",
  temu_draft_category_v2: "成品类目记录",
  temu_publish_attr_picks_current_v1: "当前属性估填",
  temu_publish_attr_picks_v2: "属性估填历史",
  temu_publish_draft_attr_prefill_v1: "成品属性估填",
  temu_size_chart_templates_v1: "尺码表模板",
};

export type BackupCounts = Record<string, number>;

type BackupFile = {
  format: string;
  version: number;
  exportedAt: string;
  /** 导出时的账号 scope；导入只作参考，一律写到当前账号名下 */
  scope: string;
  /** 基名（去掉 :scope 后缀）→ 数据 */
  items: Record<string, unknown>;
};

function labelOf(base: string): string {
  return BACKUP_KEY_LABELS[base] ?? base;
}

function countOf(value: unknown): number {
  if (Array.isArray(value)) return value.length;
  if (value && typeof value === "object") return Object.keys(value as object).length;
  return 1;
}

function readRaw(key: string): unknown {
  const raw = window.localStorage.getItem(key);
  if (!raw) return undefined;
  try {
    return JSON.parse(raw) as unknown;
  } catch {
    return undefined;
  }
}

/**
 * 会话级/瞬态键：不进备份。批量心跳（batch_running_at）如果被带进备份文件，
 * 在新电脑上导入后会误判「上一批还在跑」，把批量发布锁住几分钟。
 */
const EPHEMERAL_KEYS = new Set([
  "temu_publish_batch_running_at",
  "temu_publish_batch_stop",
  "temu_library_focus_draft_v1",
  "temu_library_retry_v1",
  "temu_batch_edit_pending_v1",
]);

/** 当前账号名下所有 temu_* 存档（基名 → 原始键名） */
function scopedKeys(): Array<{ base: string; key: string }> {
  const suffix = `:${storageScope()}`;
  const found: Array<{ base: string; key: string }> = [];
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index) ?? "";
    if (!key.startsWith("temu_") || !key.endsWith(suffix)) continue;
    const base = key.slice(0, key.length - suffix.length);
    if (EPHEMERAL_KEYS.has(base)) continue;
    found.push({ base, key });
  }
  return found;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function mergeValues(current: unknown, incoming: unknown): unknown {
  if (incoming === undefined) return current;
  if (Array.isArray(current) && Array.isArray(incoming)) {
    const keyOf = (item: unknown): string => {
      const obj = (item ?? {}) as { name?: unknown; id?: unknown };
      if (typeof obj.name === "string") return `name:${obj.name}`;
      if (obj.id !== undefined) return `id:${String(obj.id)}`;
      return `json:${JSON.stringify(item)}`;
    };
    const merged = new Map<string, unknown>();
    for (const item of current) merged.set(keyOf(item), item);
    for (const item of incoming) merged.set(keyOf(item), item); // 同键以导入为准
    return Array.from(merged.values());
  }
  if (isPlainObject(current) && isPlainObject(incoming)) {
    return { ...current, ...incoming };
  }
  return incoming;
}

/** 导出：打包当前账号名下全部 temu_* 存档为一个 JSON 文件 */
export function exportTemuPublishBackup(): {
  blob: Blob;
  filename: string;
  counts: BackupCounts;
} {
  const items: Record<string, unknown> = {};
  const counts: BackupCounts = {};
  for (const { base, key } of scopedKeys()) {
    const value = readRaw(key);
    if (value === undefined) continue;
    items[base] = value;
    counts[base] = countOf(value);
  }
  const payload: BackupFile = {
    format: BACKUP_FORMAT,
    version: BACKUP_VERSION,
    exportedAt: new Date().toISOString(),
    scope: storageScope(),
    items,
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const stamp = new Date().toISOString().slice(0, 16).replace(/[:T]/g, "-");
  return { blob, filename: `temu-上架数据备份-${stamp}.json`, counts };
}

export type ImportSummary = {
  /** 合并后各存档的总条数 */
  merged: BackupCounts;
  /** 跳过的未知键（正常情况下应为空） */
  skipped: string[];
};

/** 导入：校验格式后按合并规则放回当前账号名下 */
export async function importTemuPublishBackup(file: File): Promise<ImportSummary> {
  let payload: BackupFile;
  try {
    payload = JSON.parse(await file.text()) as BackupFile;
  } catch {
    throw new Error("文件不是有效的 JSON：请选用「导出备份」生成的文件");
  }
  if (payload?.format !== BACKUP_FORMAT || !isPlainObject(payload.items)) {
    throw new Error("文件格式不正确：请选用「导出备份」生成的文件");
  }
  if (Number(payload.version) > BACKUP_VERSION) {
    throw new Error("备份文件来自更高版本，请先升级应用后再导入");
  }
  const merged: BackupCounts = {};
  const skipped: string[] = [];
  for (const [base, value] of Object.entries(payload.items)) {
    if (!base.startsWith("temu_") || EPHEMERAL_KEYS.has(base)) {
      skipped.push(base);
      continue;
    }
    const target = `${base}:${storageScope()}`;
    const next = mergeValues(readRaw(target), value);
    window.localStorage.setItem(target, JSON.stringify(next));
    merged[base] = countOf(next);
  }
  return { merged, skipped };
}

/** 摘要文案：「产品模板 3、变种模板 2…」 */
export function backupSummaryText(counts: BackupCounts): string {
  const parts = Object.entries(counts).map(([base, count]) => `${labelOf(base)} ${count}`);
  return parts.length ? parts.join("、") : "（空）";
}
