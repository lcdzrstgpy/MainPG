import assert from "node:assert/strict";
import test from "node:test";

import { getAvailableModes, modelsForMode, TEXT_MODELS } from "./modelCatalog.ts";

test("文本对话模型固定为运营指定的三个模型", () => {
  assert.deepEqual(TEXT_MODELS.map((model) => model.id), ["deepseek-v4-flash", "deepseek-v4-pro", "gpt-5.6-terra"]);
});

test("图片模式只提供指定的三个图片模型", () => {
  assert.deepEqual(modelsForMode("edit").map((model) => model.id), ["gpt-image-2-1k", "gpt-image-2-2k", "gpt-image-2-4k"]);
  assert.deepEqual(getAvailableModes(TEXT_MODELS[0]), ["chat"]);
});
