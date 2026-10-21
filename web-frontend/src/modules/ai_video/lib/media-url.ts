/**
 * AI 视频媒体地址：sidecar 返回的多是 `/api/files/...`、`/api/output/...` 相对路径，
 * 必须统一拼到 MainPG 同源代理；已是绝对地址/对象 URL 时原样返回。
 */

import { CF_PROXY_PREFIX } from "../api/clipforgeClient.ts";

export function aiVideoMediaUrl(src: string | null | undefined): string | null {
  if (!src) return null;
  if (/^(?:https?:|data:|blob:)/i.test(src)) return src;
  return `${CF_PROXY_PREFIX}${src.startsWith("/") ? src : `/${src}`}`;
}
