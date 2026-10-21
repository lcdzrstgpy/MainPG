import assert from "node:assert/strict";
import test from "node:test";

import { applyRetakePatch, RETAKE_SYMPTOMS } from "../retake-patch.ts";

test("诊断重拍只追加一条对应症状的补丁", () => {
  const base = "商品放在木桌上，镜头缓慢推近";
  const result = applyRetakePatch(base, "product_warped");
  assert.equal(result.applied, true);
  assert.match(result.prompt, /^商品放在木桌上，镜头缓慢推近。/);
  assert.match(result.prompt, /商品外观、包装、logo 与文字保持完全不变/);
  assert.match(result.change.zh, /商品保真/);
});

test("重复套用同一症状不会再次追加，避免重复提交相同提示词", () => {
  const first = applyRetakePatch("人物在窗边说话", "blurry");
  const second = applyRetakePatch(first.prompt, "blurry");
  assert.equal(second.applied, false);
  assert.equal(second.prompt, first.prompt);
});

test("英文提示词使用英文补丁", () => {
  const result = applyRetakePatch("A woman talks by the window", "face_broken");
  assert.equal(result.applied, true);
  assert.match(result.prompt, /features stay well-proportioned/);
});

test("症状目录覆盖六类可诊断问题", () => {
  assert.deepEqual(
    RETAKE_SYMPTOMS.map((item) => item.id),
    ["face_broken", "skin_waxy", "background_dead", "light_wrong", "blurry", "product_warped"],
  );
});
