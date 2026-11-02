import type { AttrPickMap } from "./attributeModel";

/**
 * 产品属性选择的本机存储。
 *
 * 两套东西：
 * 1. **按叶子类目**记「上次怎么填的」（连发同款时下一个商品直接沿用）；
 * 2. **按草稿**记「入草稿库时自动估的那一份」，点「用于发布」时直接带进发布页。
 *
 * 都按账号隔离：同一台电脑换账号登录，不能把上一个人选的属性带出来。
 */

export const ATTR_PICKS_KEY = "temu_publish_attr_picks_v2";
/** 旧版单条存档 {catId, picks}：只留最后一次。升级时还要读它，否则用户刚填的会丢。 */
export const ATTR_PICKS_KEY_V1 = "temu_publish_attr_picks_v1";
/** 按类目留多少份历史：够覆盖日常几个类目，又不至于把本地存储撑大 */
export const ATTR_PICKS_HISTORY_MAX = 20;

/** 草稿 → 入草稿库时自动估出来的属性。 */
export const DRAFT_ATTR_PREFILL_KEY = "temu_publish_draft_attr_prefill_v1";
/** 草稿预填留多少条：草稿库一页 30 条，留 200 够翻好几页 */
export const DRAFT_ATTR_PREFILL_MAX = 200;

export type AttrPicksHistory = Record<string, { picks?: AttrPickMap; at?: number }>;

export type DraftAttrPrefill = {
  /** 估填时用的叶子类目：类目变了这份就作废 */
  catId: number;
  picks: AttrPickMap;
  at: number;
  /** 有几个值是「猜的」（没有历史、标题里也没有），界面要能区分 */
  guessed?: number;
};

/** 本机存储按账号分开，见各调用点。 */
export function storageScope(): string {
  try {
    const account = JSON.parse(window.localStorage.getItem("wh_demo_account") ?? "null") as
      | { user_id?: string }
      | null;
    return account?.user_id || "local";
  } catch {
    return "local";
  }
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 本机存储不可用（隐私模式/配额满）时不影响主流程
  }
}

/** 某个叶子类目上次填了什么（先读新版多份存档，没有再退到旧版单条）。 */
export function readAttrPicksHistory(scope: string, catId: number): AttrPickMap {
  if (!catId) return {};
  const all = readJson<AttrPicksHistory>(`${ATTR_PICKS_KEY}:${scope}`, {});
  const hit = all?.[String(catId)]?.picks;
  if (hit && typeof hit === "object") return hit;

  const legacy = readJson<{ catId?: number; picks?: AttrPickMap }>(
    `${ATTR_PICKS_KEY_V1}:${scope}`,
    {},
  );
  if (Number(legacy?.catId ?? 0) === catId && legacy?.picks && typeof legacy.picks === "object") {
    return legacy.picks;
  }
  return {};
}

/**
 * 存下某个类目的填写，只保留最近写入的 {@link ATTR_PICKS_HISTORY_MAX} 个类目。
 *
 * **按写入顺序裁，不按 `at` 排序**：同一毫秒内连写多个类目时 at 完全相同，
 * 按 at 排序会变成「保留最旧的、丢掉刚写的」。先 delete 再赋值，让 key 落到
 * 对象末尾，`slice(-N)` 拿到的就是最近写入的。
 */
export function saveAttrPicksHistory(scope: string, catId: number, picks: AttrPickMap): void {
  if (!catId) return;
  const key = `${ATTR_PICKS_KEY}:${scope}`;
  const all = readJson<AttrPicksHistory>(key, {});
  const id = String(catId);
  delete all[id];
  all[id] = { picks, at: Date.now() };
  writeJson(key, Object.fromEntries(Object.entries(all).slice(-ATTR_PICKS_HISTORY_MAX)));
}

/** 读出全部草稿的估填结果（草稿 id → 预填）。 */
export function readDraftAttrPrefills(scope: string): Record<string, DraftAttrPrefill> {
  const all = readJson<Record<string, DraftAttrPrefill>>(
    `${DRAFT_ATTR_PREFILL_KEY}:${scope}`,
    {},
  );
  return all && typeof all === "object" ? all : {};
}

/**
 * 写入一批草稿的估填结果，并裁剪总量。
 *
 * 按条写而不是整份覆盖：草稿库分页加载，只写本页看到的那几条，
 * 不能把别的页算好的结果冲掉。
 */
export function saveDraftAttrPrefills(
  scope: string,
  entries: Record<string, DraftAttrPrefill>,
): void {
  const key = `${DRAFT_ATTR_PREFILL_KEY}:${scope}`;
  const all = readJson<Record<string, DraftAttrPrefill>>(key, {});
  for (const [id, prefill] of Object.entries(entries)) {
    // 先删再写，让 key 落到末尾：裁剪按写入顺序来，不依赖同一毫秒里会撞的 at
    delete all[id];
    all[id] = prefill;
  }
  writeJson(key, Object.fromEntries(Object.entries(all).slice(-DRAFT_ATTR_PREFILL_MAX)));
}
