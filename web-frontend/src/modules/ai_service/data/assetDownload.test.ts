import assert from "node:assert/strict";
import test from "node:test";

import { generatedImageDownloadName } from "./assetDownload.ts";

test("生成图使用连续且可识别的本地下载文件名", () => {
  assert.equal(generatedImageDownloadName(0), "ai-product-creation-1.png");
  assert.equal(generatedImageDownloadName(3), "ai-product-creation-4.png");
});
