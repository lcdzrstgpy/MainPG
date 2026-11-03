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
