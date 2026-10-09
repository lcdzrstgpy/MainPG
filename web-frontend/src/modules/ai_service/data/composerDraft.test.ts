import assert from "node:assert/strict";
import test from "node:test";

import { consumeComposerDraft } from "./composerDraft.ts";

test("提交创作后清空编辑器，但保留已提交的图片供消息气泡展示", () => {
  const result = consumeComposerDraft({
    prompt: "加点中国元素",
    imageUrl: "blob:product-image",
    imageName: "WechatIMG79.jpg",
    assetId: "local-asset-1",
  });

  assert.deepEqual(result.submitted, {
    prompt: "加点中国元素",
    imageUrl: "blob:product-image",
    imageName: "WechatIMG79.jpg",
    assetId: "local-asset-1",
  });
  assert.deepEqual(result.next, { prompt: "", imageUrl: undefined, imageName: "", assetId: undefined });
});
