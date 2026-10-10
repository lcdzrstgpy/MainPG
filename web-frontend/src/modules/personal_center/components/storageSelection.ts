import type { PodCacheScanCategory } from "../api/personalCenterApi";

/**
 * 「存储空间」勾选状态计算（纯函数，不碰 DOM/React，方便单测）。
 *
 * 语义：
 *  - 首次扫描：所有「有内容可清理」的分类默认全部勾上（每一项都有后端安全
 *    护栏），用户按需取消；
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
    return new Set(cleanable.map((item) => item.key));
  }
  return new Set(cleanable.filter((item) => previous.has(item.key)).map((item) => item.key));
}
