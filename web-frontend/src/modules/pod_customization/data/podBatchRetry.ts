import type { PodStyleRow } from "./podCustomizationModel";

export type PodBatchRetryCandidate = {
  styleIndex: number;
  title: string;
  reason: string;
};

export type PodColumnRetryGroupCandidate = {
  groupIndex: number;
  styleIndices: number[];
  title: string;
  reuseSummary: string;
  reason: string;
};

export type PodBatchRetryRequest = {
  image_style_indices: number[];
  title_style_indices: number[];
};

const COLUMN_ROLE_LABELS = ["主图", "细节图 A", "细节图 B", "素材图"] as const;

function firstFailureReason(results: PodStyleRow["results"]): string | undefined {
  return results.find((result) => result?.error_message)?.error_message;
}

function padStyleIndex(index: number): string {
  return `#${String(index).padStart(3, "0")}`;
}

export function columnRetryGroupCandidates(styles: readonly PodStyleRow[]): PodColumnRetryGroupCandidate[] {
  const byGroup = new Map<number, PodStyleRow[]>();
  for (const style of styles) {
    const groupIndex = Math.floor((style.index - 1) / 4) + 1;
    const list = byGroup.get(groupIndex) ?? [];
    list.push(style);
    byGroup.set(groupIndex, list);
  }
  const groups: PodColumnRetryGroupCandidate[] = [];
  for (const [groupIndex, groupStyles] of [...byGroup.entries()].sort((a, b) => a[0] - b[0])) {
    const hasUnfinished = groupStyles.some(
      (style) => style.results.length === 4 && style.results.some((result) => result?.status !== "completed"),
    );
    if (!hasUnfinished) continue;
    const sortedStyles = [...groupStyles].sort((a, b) => a.index - b.index);
    const styleIndices = sortedStyles.map((style) => style.index);
    const primaryReady = sortedStyles.every((style) => style.results[0]?.status === "completed");
    const missingDerived = primaryReady
      ? ([1, 2, 3] as const)
        .filter((offset) => sortedStyles.some((style) => style.results[offset]?.status !== "completed"))
        .map((offset) => COLUMN_ROLE_LABELS[offset])
      : [];
    const reuseSummary = primaryReady && missingDerived.length
      ? `复用已完成轮次，仅补 ${missingDerived.join(" / ")}（不重复扣费）`
      : "主图未完成，将整组 4 轮重新生成";
    const reason = sortedStyles.map((style) => firstFailureReason(style.results)).find(Boolean) || "存在未完成的图片";
    const first = styleIndices[0];
    const last = styleIndices[styleIndices.length - 1];
    groups.push({
      groupIndex,
      styleIndices,
      title: `第 ${groupIndex} 组（款式 ${padStyleIndex(first)}–${padStyleIndex(last)}）`,
      reuseSummary,
      reason,
    });
  }
  return groups;
}

export function batchRetryCandidates(styles: readonly PodStyleRow[]): {
  image: PodBatchRetryCandidate[];
  title: PodBatchRetryCandidate[];
} {
  const image: PodBatchRetryCandidate[] = [];
  const title: PodBatchRetryCandidate[] = [];
  for (const style of styles) {
    // 一款的四张图来自同一次生图调用：只要还有槽位未完成（含断电中断留下的
    // 部分完成），该款就应当可整款重试；仅四张全完成时不再重生成。
    if (style.results.length === 4 && style.results.some((result) => result?.status !== "completed")) {
      image.push({
        styleIndex: style.index,
        title: style.title,
        reason: firstFailureReason(style.results) || "存在未完成的图片",
      });
      continue;
    }
    if (style.title_status === "failed"
      && style.results.length === 4
      && style.results.every((result) => result?.status === "completed" && Boolean(result.public_url))) {
      title.push({
        styleIndex: style.index,
        title: style.title,
        reason: style.title_error_message || "标题生成失败",
      });
    }
  }
  return { image, title };
}

function normalizeStyleIndices(indices: readonly number[]): number[] {
  return [...new Set(indices.filter((index) => Number.isInteger(index) && index > 0))].sort((left, right) => left - right);
}

export function buildFailedRetryRequest(
  imageStyleIndices: readonly number[],
  titleStyleIndices: readonly number[],
): PodBatchRetryRequest {
  const image_style_indices = normalizeStyleIndices(imageStyleIndices);
  const title_style_indices = normalizeStyleIndices(titleStyleIndices);
  if (!image_style_indices.length && !title_style_indices.length) throw new Error("请至少选择一个失败款式。");
  if (image_style_indices.some((index) => title_style_indices.includes(index))) throw new Error("同一款式不能同时选择图片重试和标题重试。");
  return { image_style_indices, title_style_indices };
}
