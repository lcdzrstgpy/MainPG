/**
 * MainPG 内嵌模式的纯判定逻辑：不依赖任何 Next API，服务端（middleware / layout）
 * 与测试共用同一份实现。
 *
 * 判定优先级（冻结协议，不可自创变体）：
 *   1. `?embed=mainpg`     → 内嵌（iframe 内运行）
 *   2. `?embed=standalone` → 独立壳（显式退出，必须压过 cookie）
 *   3. 无 query 但 cookie 为 "1" → 内嵌（内部路由不含 query 时的续航）
 *   4. 其余 → null（既不是内嵌，也不去动已有的 cookie）
 */

export type EmbedMode = "mainpg" | "standalone";

/** `?embed=mainpg`：MainPG 以 iframe 方式打开 ClipForge */
export const EMBED_QUERY_MAINPG = "mainpg";
/** `?embed=standalone`：显式回到 ClipForge 独立壳（开发 / 上游维护） */
export const EMBED_QUERY_STANDALONE = "standalone";

/** 记住内嵌状态的会话 cookie（不设 Max-Age，即会话级） */
export const EMBED_COOKIE_NAME = "mainpg_embed";
export const EMBED_COOKIE_ON = "1";

/** middleware 写入请求头，root layout 用 headers() 读取，供首屏判定 */
export const EMBED_HEADER_NAME = "x-mainpg-embed";
export const EMBED_HEADER_ON = "1";

/**
 * 由 `?embed=` 的 query 值与 cookie 值解析本次请求的内嵌模式。
 * standalone 优先于 cookie，否则一旦内嵌过，显式退出将永远无法生效。
 */
export function resolveEmbedMode(
  searchParamValue: string | null,
  cookieValue: string | null
): EmbedMode | null {
  if (searchParamValue === EMBED_QUERY_MAINPG) return "mainpg";
  if (searchParamValue === EMBED_QUERY_STANDALONE) return "standalone";
  if (cookieValue === EMBED_COOKIE_ON) return "mainpg";
  return null;
}