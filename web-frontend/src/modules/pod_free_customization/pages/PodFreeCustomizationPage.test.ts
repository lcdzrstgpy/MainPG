import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./PodFreeCustomizationPage.tsx", import.meta.url), "utf8");

test("主图固定在第一位：第 1 槽既不能长按拖动，也不接受拖入", () => {
  // 第 2 张起都拿第 1 张当过图参考，把主图拖走会把批次拖成没有主图。
  assert.match(source, /if \(variant < 2 \|\| !batch/);
  assert.match(source, /const resolved = next >= 2 \? next : null;/);
  assert.match(source, /if \(!batch \|\| from < 2 \|\| to < 2\) return;/);
});

test("短按松手即取消长按计时，不再在 2 秒后自己冒出拖动幽灵", () => {
  assert.match(source, /const endSlotPress = \(\) => \{/);
  assert.match(source, /pressRef\.current = null;\n {2}\};/);
  assert.match(source, /onPointerUp=\{endSlotPress\}/);
  assert.match(source, /onPointerCancel=\{endSlotPress\}/);
  assert.match(source, /onPointerLeave=\{endSlotPress\}/);
});

test("参考图排在提示词上方，且上传入口只属于主图", () => {
  // 先给参考图、再写这一张的提示词：参考图区在 JSX 里必须排在提示词 textarea 之前。
  assert.ok(source.indexOf("pod-free-ref-collapse") < source.indexOf("pod-free-prompt"));
  // 第 2 张起固定以主图为准，只折叠参考图区，不再给上传入口。
  assert.match(source, /activeVariant === 1 \? "is-open"/);
  assert.match(source, /!batch && draft\.reference_asset_ids\.length < 4/);
});
