/**
 * MainPG ⇄ 内嵌 ClipForge iframe 的 postMessage 协议（纯逻辑，可直接在 vitest 里单测）。
 *
 * 消息形状不可改，必须与 MainPG 侧 AiVideoPage 完全一致：
 *   父 → iframe：{ type: "mainpg:ai-video-navigate", href: string }
 *   iframe → 父：{ type: "mainpg:ai-video-location", pathname: string }
 *
 * 安全边界：iframe 只接受 `event.source === window.parent` 且 href 在白名单内的
 * navigate 消息；任何 href 都不允许被当作地址直接执行。
 */

export const MAINPG_EMBED_NAVIGATE = "mainpg:ai-video-navigate";
export const MAINPG_EMBED_LOCATION = "mainpg:ai-video-location";

/** 允许 iframe 内部跳转的模块入口，必须与 MainPG 的二级导航一一对应 */
export const MAINPG_EMBED_ALLOWED_HREFS = [
  "/start",
  "/projects",
  "/products",
  "/presenters",
  "/project/clone",
  "/batch",
  "/settings",
] as const;

export interface MainPgNavigateMessage {
  type: typeof MAINPG_EMBED_NAVIGATE;
  href: string;
}

export interface MainPgLocationMessage {
  type: typeof MAINPG_EMBED_LOCATION;
  pathname: string;
}

/** href 是否属于允许的模块入口（精确匹配，不做前缀或 query 容错） */
export function isAllowedEmbedHref(value: unknown): value is string {
  return (
    typeof value === "string" &&
    (MAINPG_EMBED_ALLOWED_HREFS as readonly string[]).includes(value)
  );
}

/** 是否为可执行的「父 → iframe」导航消息：形状与白名单双重校验 */
export function isMainPgNavigateMessage(value: unknown): value is MainPgNavigateMessage {
  if (typeof value !== "object" || value === null) return false;
  const msg = value as { type?: unknown; href?: unknown };
  return msg.type === MAINPG_EMBED_NAVIGATE && isAllowedEmbedHref(msg.href);
}

/** 消息来源是否就是父窗口（iframe 绝不信任其他 source） */
export function isFromEmbedParent(source: unknown, parent: unknown): boolean {
  return source !== null && source !== undefined && source === parent;
}