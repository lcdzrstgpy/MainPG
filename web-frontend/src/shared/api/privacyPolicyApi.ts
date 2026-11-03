import { httpJson } from "../../transport/http/client";

/**
 * 隐私政策文档：服务端托管（Markdown 单一事实源），客户端只做拉取与消费。
 *
 * 数据来源是本地后端的转发路由 `/api/customer/privacy-policy`（对应平台服务器的
 * `/privacy.json`），走同源请求，避免浏览器直连远端时的 CORS 与混合内容问题。
 *
 * 失败语义（与设计文档第 6.3 节一致）：
 * - 拉取失败 → 用本地缓存 → 再失败就用内置常量；
 * - **任何失败都不改变「本次是否要求勾选」的既有行为**，断网时不能把用户卡在登录页。
 */

export type PrivacyPolicyDocument = {
  schema?: number;
  doc_key?: string;
  title?: string;
  version?: string;
  effective_date?: string;
  updated_at?: string;
  published?: boolean;
  public_url?: string;
  etag?: string;
  body_markdown?: string;
  body_html?: string;
};

type CachedPolicy = { fetchedAt: number; document: PrivacyPolicyDocument };

/** 服务端还没上线时，正文链接回落到公网阅读页。 */
export const PRIVACY_POLICY_URL_FALLBACK = "https://workbench.haocoming.top/privacy";

const CACHE_KEY = "mainpg.privacyPolicy.remote";
/** 6 小时：政策不会一天改好几次，但也不能让用户长期看不到新版。 */
const CACHE_TTL_MS = 6 * 60 * 60 * 1000;
/** 拉取超时：首屏不因为它变慢，超过就按失败处理。 */
const FETCH_TIMEOUT_MS = 3000;

function storage(): Storage | null {
  try {
    if (typeof window === "undefined") return null;
    return window.localStorage;
  } catch {
    return null;
  }
}

/** 读本地缓存（不判断新鲜度，交给调用方）。 */
export function readCachedPrivacyPolicy(): PrivacyPolicyDocument | null {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CachedPolicy;
    if (!parsed || typeof parsed !== "object" || !parsed.document) return null;
    return parsed.document;
  } catch {
    return null;
  }
}

function readCacheEntry(): CachedPolicy | null {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as CachedPolicy;
    return parsed && typeof parsed === "object" && parsed.document ? parsed : null;
  } catch {
    return null;
  }
}

function writeCache(document: PrivacyPolicyDocument): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(CACHE_KEY, JSON.stringify({ fetchedAt: Date.now(), document } satisfies CachedPolicy));
  } catch {
    // 静默：缓存不可用只是每次多拉一次远端。
  }
}

function isFresh(entry: CachedPolicy): boolean {
  return Date.now() - entry.fetchedAt < CACHE_TTL_MS;
}

/** 一次拉取（含 3s 超时）；失败返回 null，不抛异常。 */
async function fetchOnce(): Promise<PrivacyPolicyDocument | null> {
  try {
    const data = await httpJson<{ ok?: boolean; document?: PrivacyPolicyDocument }>(
      "/api/customer/privacy-policy",
      { timeoutMs: FETCH_TIMEOUT_MS },
    );
    if (data?.ok === false) return null;
    const doc = data?.document;
    if (!doc || typeof doc !== "object") return null;
    // 服务端未发布时约定 404；这里再兜一次 published=false。
    if (doc.published === false) return null;
    return doc;
  } catch {
    return null;
  }
}

/** 拉取 + 重试一次（退避 1s）。 */
async function fetchWithRetry(): Promise<PrivacyPolicyDocument | null> {
  const first = await fetchOnce();
  if (first) return first;
  await new Promise((resolve) => setTimeout(resolve, 1000));
  return fetchOnce();
}

/**
 * 取隐私政策文档：优先新鲜缓存（同时后台刷新），否则拉远端；失败回退旧缓存。
 * 永远不抛异常，返回 null 表示「没有可用远端信息，按内置常量走」。
 */
export async function loadPrivacyPolicy(options?: { force?: boolean }): Promise<PrivacyPolicyDocument | null> {
  const cached = readCacheEntry();
  if (!options?.force && cached && isFresh(cached)) {
    // stale-while-revalidate：先用缓存，后台静默刷新，不阻塞调用方。
    void fetchWithRetry().then((doc) => {
      if (doc) writeCache(doc);
    });
    return cached.document;
  }
  const fetched = await fetchWithRetry();
  if (fetched) {
    writeCache(fetched);
    return fetched;
  }
  return cached?.document ?? null;
}

export type PrivacyPolicySyncResult = {
  /** 当前可用的最全文档：优先远端，其次本地缓存。 */
  document: PrivacyPolicyDocument | null;
  /** 打开前本地缓存的版本（没有缓存则为 null）。 */
  previousVersion: string | null;
  /** 远端版本与本地缓存不一致（含首次拉取）：调用方据此刷新版本号与同意状态。 */
  changed: boolean;
  /** 远端不可用、结果来自本地缓存。 */
  fromCache: boolean;
};

/**
 * 每次「打开隐私政策」时调用：强制拉一次远端，并与本地缓存比对版本。
 *
 * - 远端成功：写缓存（最新版留存在本地，作为下次离线兜底），版本不同则 `changed=true`；
 * - 远端失败：退回本地缓存，`fromCache=true`，不打断阅读。
 */
export async function syncPrivacyPolicy(): Promise<PrivacyPolicySyncResult> {
  const cached = readCachedPrivacyPolicy();
  const previousVersion = typeof cached?.version === "string" ? cached.version.trim() : null;
  const fetched = await fetchWithRetry();
  if (fetched) {
    writeCache(fetched);
    const version = typeof fetched.version === "string" ? fetched.version.trim() : null;
    return {
      document: fetched,
      previousVersion,
      changed: Boolean(version) && version !== previousVersion,
      fromCache: false,
    };
  }
  return { document: cached, previousVersion, changed: false, fromCache: cached !== null };
}

/** 正文阅读地址：优先用服务端给的 public_url，缺失回落公网阅读页。 */
export function resolvePrivacyPolicyUrl(document?: PrivacyPolicyDocument | null): string {
  const url = typeof document?.public_url === "string" ? document.public_url.trim() : "";
  return url || PRIVACY_POLICY_URL_FALLBACK;
}
