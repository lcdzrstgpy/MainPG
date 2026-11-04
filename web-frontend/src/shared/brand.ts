// 统一使用随前端发布的透明底品牌资源，避免依赖外部图片服务。
export const BRAND_LOGO_URL = "/brand/jieye-logo.png";
export const BRAND_MARK_URL = "/brand/jieye-mark.png";
export const BRAND_NAME = "界野";

// 使用手册（飞书在线文档），官网下载页与客户端工作台共用同一入口。
export const BRAND_MANUAL_URL =
  "https://my.feishu.cn/wiki/TmZNwzJMiiN1fCkMm4Pc5GIdntg?from=from_copylink";

// 登录页 logo 在屏幕上的矩形：登录成功时交给入场动画，
// 让「登录页的 logo」和「屏幕中央的大 logo」是同一个元素在两段动画里的两帧。
export type BrandLogoRect = { x: number; y: number; width: number; height: number };
