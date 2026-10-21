import assert from "node:assert/strict";
import test from "node:test";

import type { Asset, Shot } from "../../api/types.ts";
import { buildShotAssetRows, isVideoAsset } from "../asset-rows.ts";

function shot(shotId: number): Shot {
  return {
    shotId,
    type: "product_reveal",
    duration: 4,
    description: `第 ${shotId} 镜`,
    camera: "固定",
    visualSource: "ai_generate",
    transition: "direct_concat",
    voiceover: "",
  };
}

function asset(partial: Partial<Asset> & { id: string; shotId: number }): Asset {
  return {
    projectId: "p1",
    type: "ai_generated",
    filePath: `/api/files/p1/${partial.id}.png`,
    thumbnailPath: null,
    provider: null,
    model: null,
    prompt: null,
    sourceUrl: null,
    author: null,
    license: null,
    selected: false,
    status: "done",
    createdAt: "2026-10-20T00:00:00.000Z",
    ...partial,
  };
}

test("锁定的 take 优先展示，并排在候选第一位", () => {
  const rows = buildShotAssetRows(
    [shot(1)],
    [
      asset({ id: "a", shotId: 1, selected: false }),
      asset({ id: "b", shotId: 1, selected: true }),
    ],
  );
  assert.equal(rows[0].locked?.id, "b");
  assert.equal(rows[0].display?.id, "b");
  assert.equal(rows[0].candidates[0].id, "b");
  assert.equal(rows[0].status, "done");
});

test("没有锁定时回落到已完成项，但仍视为未锁定（批量会覆盖）", () => {
  const rows = buildShotAssetRows([shot(1)], [asset({ id: "a", shotId: 1 })]);
  assert.equal(rows[0].locked, null);
  assert.equal(rows[0].display?.id, "a");
  assert.equal(rows[0].status, "done");
});

test("生成中 / 失败 / 缺失三种状态", () => {
  const generating = buildShotAssetRows([shot(1)], [asset({ id: "a", shotId: 1, status: "generating" })]);
  assert.equal(generating[0].status, "generating");

  const failed = buildShotAssetRows([shot(1)], [asset({ id: "a", shotId: 1, status: "failed" })]);
  assert.equal(failed[0].status, "failed");

  const missing = buildShotAssetRows([shot(1)], []);
  assert.equal(missing[0].status, "missing");
  assert.equal(missing[0].thumbnail, null);
});

test("视频素材识别：有缩略图用缩略图，没有则为空", () => {
  const video = asset({ id: "v", shotId: 1, filePath: "/api/files/p1/clip.mp4" });
  assert.equal(isVideoAsset(video), true);

  const withCover = buildShotAssetRows([shot(1)], [{ ...video, thumbnailPath: "/api/files/p1/cover.png" }]);
  assert.equal(withCover[0].isVideo, true);
  assert.equal(withCover[0].thumbnail, "/api/files/p1/cover.png");

  const noCover = buildShotAssetRows([shot(1)], [video]);
  assert.equal(noCover[0].thumbnail, null);
});
