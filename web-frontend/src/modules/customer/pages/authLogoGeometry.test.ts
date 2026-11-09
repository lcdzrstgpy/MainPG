import assert from "node:assert/strict";
import test from "node:test";

import { invertRect, isNegligible } from "./authLogoGeometry.ts";

test("首屏大 logo 缩到表单小 logo：反演位移为负、缩放大于 1", () => {
  const hero = { left: 400, top: 250, width: 208 };
  const form = { left: 460, top: 96, width: 88 };
  const move = invertRect(hero, form);
  assert.equal(move.dx, -60);
  assert.equal(move.dy, 154);
  assert.ok(Math.abs(move.scale - 208 / 88) < 1e-9);
  assert.ok(move.scale > 1);
});

test("反向（表单回到首屏）与正向互为逆运算", () => {
  const hero = { left: 400, top: 250, width: 208 };
  const form = { left: 460, top: 96, width: 88 };
  const forward = invertRect(hero, form);
  const backward = invertRect(form, hero);
  assert.equal(forward.dx, -60);
  assert.equal(backward.dx, 60);
  assert.ok(Math.abs(forward.scale * backward.scale - 1) < 1e-9);
});

test("宽度为 0 时缩放兜底为 1，不产生 Infinity", () => {
  const move = invertRect({ left: 0, top: 0, width: 0 }, { left: 0, top: 0, width: 0 });
  assert.equal(move.scale, 1);
  assert.ok(isNegligible(move));
});

test("登录 / 注册互切时 logo 原地不动，判定为无需动画", () => {
  const rect = { left: 460, top: 96, width: 88 };
  assert.ok(isNegligible(invertRect(rect, rect)));
});

test("亚像素位移仍判为需要动画", () => {
  const move = invertRect({ left: 0, top: 0, width: 88 }, { left: 0, top: 0.8, width: 88 });
  assert.equal(isNegligible(move), false);
});
