import assert from "node:assert/strict";
import test from "node:test";

import { batchRetryCandidates, buildFailedRetryRequest } from "./podBatchRetry.ts";

const imageFailed = {
  index: 2,
  title: "收纳篮 · 款式 #002",
  title_status: "failed" as const,
  title_error_message: "标题服务暂时不可用",
  results: Array.from({ length: 4 }, (_, index) => ({ id: `failed-${index}`, status: "failed" as const, error_message: "图片生成失败" })),
  status: "failed" as const,
};

const titleFailed = {
  index: 3,
  title: "收纳篮 · 款式 #003",
  title_status: "failed" as const,
  title_error_message: "标题服务暂时不可用",
  results: Array.from({ length: 4 }, (_, index) => ({ id: `completed-${index}`, status: "completed" as const, public_url: `/images/${index}` })),
  status: "completed" as const,
};

test("batch retry candidates separate fully failed images from title-only failures", () => {
  const candidates = batchRetryCandidates([imageFailed, titleFailed, {
    ...imageFailed,
    index: 4,
    results: [{ id: "partial", status: "failed" as const }, { id: "completed", status: "completed" as const }, undefined, undefined],
  }]);

  assert.deepEqual(candidates.image.map((candidate) => candidate.styleIndex), [2]);
  assert.deepEqual(candidates.title.map((candidate) => candidate.styleIndex), [3]);
  assert.equal(candidates.image[0]?.reason, "图片生成失败");
  assert.equal(candidates.title[0]?.reason, "标题服务暂时不可用");
});

test("batch retry request rejects empty and overlapping selections", () => {
  assert.throws(() => buildFailedRetryRequest([], []), /至少选择一个/);
  assert.throws(() => buildFailedRetryRequest([2], [2]), /不能同时/);
});

test("batch retry request deduplicates each retry kind in ascending order", () => {
  assert.deepEqual(buildFailedRetryRequest([3, 2, 3], [7, 5, 7]), {
    image_style_indices: [2, 3],
    title_style_indices: [5, 7],
  });
});
