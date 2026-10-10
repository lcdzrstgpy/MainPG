/**
 * POD 结果图的展示来源：**优先图床公网链接，公网链接缺失或加载失败再回退本地资产地址**。
 *
 * 后端对同一张图会同时给出两个地址：
 * - ``public_url``：已发布到 COS 的公网链接（全定制 / 爆款复刻才有；半定制不接图床）；
 * - ``composite_preview_url`` / ``pattern_preview_url``：本地内容寻址资产的鉴权预览路径。
 *
 * 两边都可能不可用：本地资产会被 48 小时清扫回收，公网链接也可能失效/被删。
 * 因此展示时必须「公网优先、本地兜底」，而不是二选一。
 */
export type PodImageSource = {
  /** 首选地址（公网链接优先，其次本地）。 */
  path?: string;
  /** 首选为公网链接时，加载失败后回退的本地地址。 */
  fallbackPath?: string;
};

export function podImageSource(
  publicUrl?: string | null,
  localUrl?: string | null,
): PodImageSource {
  const remote = (publicUrl ?? "").trim();
  const local = (localUrl ?? "").trim();
  if (remote) return local ? { path: remote, fallbackPath: local } : { path: remote };
  return local ? { path: local } : {};
}
