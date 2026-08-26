import type { PodStyleRow } from "./podCustomizationModel";

export type PodBatchRetryCandidate = {
  styleIndex: number;
  title: string;
  reason: string;
};

export type PodBatchRetryRequest = {
  image_style_indices: number[];
  title_style_indices: number[];
};

function firstFailureReason(results: PodStyleRow["results"]): string | undefined {
  return results.find((result) => result?.error_message)?.error_message;
}

export function batchRetryCandidates(styles: readonly PodStyleRow[]): {
  image: PodBatchRetryCandidate[];
  title: PodBatchRetryCandidate[];
} {
  const image: PodBatchRetryCandidate[] = [];
  const title: PodBatchRetryCandidate[] = [];
  for (const style of styles) {
    if (style.results.length === 4 && style.results.every((result) => result?.status === "failed")) {
      image.push({
        styleIndex: style.index,
        title: style.title,
        reason: firstFailureReason(style.results) || "四张图片均生成失败",
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
