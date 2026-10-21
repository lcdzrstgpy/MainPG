/**
 * AI 视频的 7 个二级入口。
 *
 * 外层侧栏子导航与内嵌 ClipForge 的跳转协议共用这一份定义：
 * - href 既是 iframe 内的路径，也是侧栏子项的面板标识；
 * - label / icon 沿用原页内横条导航的文案与字符图标。
 */
export const AI_VIDEO_NAV = [
  { href: "/start", label: "视频工作台", icon: "✦" },
  { href: "/projects", label: "我的项目", icon: "▣" },
  { href: "/products", label: "商品素材", icon: "◇" },
  { href: "/presenters", label: "主播素材", icon: "♙" },
  { href: "/project/clone", label: "爆款复刻", icon: "↗" },
  { href: "/batch", label: "任务中心", icon: "◌" },
  { href: "/settings", label: "设置", icon: "⚙" },
] as const;

export type AiVideoNavHref = (typeof AI_VIDEO_NAV)[number]["href"];

/**
 * 把页面路径映射为外层二级导航的高亮项：
 * - 项目详情（/project/<id>/...）按归属映射为「我的项目」，不当作新的二级导航项；
 * - /project/clone 是独立入口，必须优先精确匹配，不能被 /project/ 前缀规则吃掉；
 * - 其余 7 个模块入口精确匹配；
 * - 不在允许列表内的路径返回 null，表示保持当前高亮不变。
 */
export function aiVideoNavHrefForPath(pathname: string): AiVideoNavHref | null {
  if (pathname === "/project/clone") return "/project/clone";
  if (pathname === "/projects" || pathname.startsWith("/project/")) return "/projects";
  const matched = AI_VIDEO_NAV.find((item) => item.href === pathname);
  return matched ? matched.href : null;
}
