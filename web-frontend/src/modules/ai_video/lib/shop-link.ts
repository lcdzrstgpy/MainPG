/**
 * 来源：integrations/clipforge/src/lib/shop-link.ts（只读同步；改动请先同步上游）
 * 店铺链接 UTM 工具：给商品链接追加平台来源与联盟参数，供发布文案/二维码使用。
 */

export interface ShopLinkOpts {
  /** 分发平台 → utm_source（douyin/kuaishou/xiaohongshu/shipinhao/tiktok/reels/shorts） */
  platform?: string;
  /** 可选的联盟/合作代码，作为通用 `aff` 参数追加 */
  affiliateCode?: string;
  /** utm_campaign，默认 "clipforge" */
  campaign?: string;
}

/** 给店铺链接追加 UTM 参数（保留原有 query/hash）；空值或非 http(s) 返回 ""。 */
export function buildShopLink(shopUrl: string | null | undefined, opts: ShopLinkOpts = {}): string {
  const raw = (shopUrl || "").trim();
  if (!raw) return "";
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return "";
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return "";

  const platform = (opts.platform || "").trim().toLowerCase();
  if (platform) u.searchParams.set("utm_source", platform);
  u.searchParams.set("utm_medium", "short_video");
  u.searchParams.set("utm_campaign", (opts.campaign || "").trim() || "clipforge");

  const aff = (opts.affiliateCode || "").trim();
  if (aff) u.searchParams.set("aff", aff);

  return u.toString();
}
