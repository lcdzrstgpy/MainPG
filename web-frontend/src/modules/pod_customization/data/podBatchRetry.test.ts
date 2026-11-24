import assert from "node:assert/strict";
import test from "node:test";

import { batchRetryCandidates, buildFailedRetryRequest, columnRetryGroupCandidates } from "./podBatchRetry.ts";

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

test("batch retry candidates include styles with unfinished images and title-only failures", () => {
  const candidates = batchRetryCandidates([imageFailed, titleFailed, {
    ...imageFailed,
    index: 4,
    results: [{ id: "partial", status: "failed" as const }, { id: "completed", status: "completed" as const }, undefined, undefined],
  }]);

  assert.deepEqual(candidates.image.map((candidate) => candidate.styleIndex), [2, 4]);
  assert.deepEqual(candidates.title.map((candidate) => candidate.styleIndex), [3]);
  assert.equal(candidates.image[0]?.reason, "图片生成失败");
  assert.equal(candidates.image[1]?.reason, "存在未完成的图片");
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

test("column retry group candidates group styles by 4 and distinguish primary vs derived reuse", () => {
  const group1 = [1, 2, 3, 4].map((index) => ({
    index,
    title: `脏衣篓 · 款式 #00${index}`,
    title_status: "failed" as const,
    results: [
      { id: `g1-${index}-0`, status: "failed" as const, error_message: "主图超时" },
      { id: `g1-${index}-1`, status: "failed" as const },
      { id: `g1-${index}-2`, status: "failed" as const },
      { id: `g1-${index}-3`, status: "failed" as const },
    ],
    status: "failed" as const,
  }));
  const group2 = [5, 6, 7, 8].map((index) => ({
    index,
    title: `脏衣篓 · 款式 #00${index}`,
    title_status: "completed" as const,
    results: [
      { id: `g2-${index}-0`, status: "completed" as const, public_url: `/p/${index}` },
      { id: `g2-${index}-1`, status: "completed" as const, public_url: `/a/${index}` },
      { id: `g2-${index}-2`, status: "failed" as const, error_message: "细节图 B 失败" },
      { id: `g2-${index}-3`, status: "failed" as const },
    ],
    status: "partial_failure" as const,
  }));

  const groups = columnRetryGroupCandidates([...group1, ...group2]);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0]?.styleIndices, [1, 2, 3, 4]);
  assert.match(groups[0]?.title ?? "", /第 1 组（款式 #001–#004）/);
  assert.match(groups[0]?.reuseSummary ?? "", /整组 4 轮重新生成/);
  assert.deepEqual(groups[1]?.styleIndices, [5, 6, 7, 8]);
  assert.match(groups[1]?.title ?? "", /第 2 组（款式 #005–#008）/);
  assert.match(groups[1]?.reuseSummary ?? "", /仅补 细节图 B \/ 素材图/);
});
