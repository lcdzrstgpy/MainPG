import type { PodCacheScanCategory } from "../api/personalCenterApi";

/**
 * 首次扫描默认勾选的分类 = 「纯垃圾」：删了不会损失任何用户价值的项。
 *
 * 有保留价值的分类**默认不勾**（由用户自己决定）：
 *  - published_release（已发布图床的旧结果图）：是出图结果的本地副本，有人想留本地；
 *  - pp_review_images（历史任务的复核原图）：留着是为了人工复核，勾了才删。
 */
const DEFAULT_CHECKED_KEYS = new Set([
  "terminal_intermediates",
  "unreferenced_residue",
  "disk_orphans",
  "pp_asset_orphans",
  "pp_disk_orphans",
]);

/**
 * 「存储空间」勾选状态计算（纯函数，不碰 DOM/React，方便单测）。
 *
 * 语义：
 *  - 首次扫描：只默认勾「纯垃圾」分类（DEFAULT_CHECKED_KEYS），有保留价值的
 *    分类留给用户自己勾——默认不替用户做删除决定；
 *  - 之后每次扫描（手动重新扫描 / 清理完成后刷新）：保留用户此前的勾选，
 *    只丢弃已经清空（files=0）的分类，避免反复重置用户的选择。
 *
 * ⚠️ 调用方必须在 **发起扫描的同步代码里** 先算好 `firstScan` 再传进来：
 * React 的 setState updater 是延迟执行的，updater 里读「是否首次」的 ref
 * 会永远是 false（曾经踩过的 bug）。
 */
export function computeScanSelection(
  categories: PodCacheScanCategory[],
  previous: Set<string>,
  firstScan: boolean,
): Set<string> {
  const cleanable = categories.filter((item) => item.files > 0);
  if (firstScan) {
    return new Set(cleanable.filter((item) => DEFAULT_CHECKED_KEYS.has(item.key)).map((item) => item.key));
  }
  return new Set(cleanable.filter((item) => previous.has(item.key)).map((item) => item.key));
}
