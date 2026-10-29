import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dialog = readFileSync(new URL("./PodSemiDeleteConfirmDialog.tsx", import.meta.url), "utf8");
const page = readFileSync(
  new URL("../pages/PodSemiCustomizationPage.tsx", import.meta.url),
  "utf8",
);
const styles = readFileSync(
  new URL("../styles/podSemiCustomization.css", import.meta.url),
  "utf8",
);

test("§6.3 文案四要点齐备：只存本机 / 无图床备份 / 不可恢复 / 先导出（下载 ZIP）", () => {
  assert.match(dialog, /删除印花创作批次/);
  assert.match(dialog, /只保存在本机/);
  assert.match(dialog, /没有图床公网备份/);
  assert.match(dialog, /不可恢复/);
  assert.match(dialog, /下载 ZIP/);
  // 确认按钮必须表态，不能写「确定」
  assert.match(dialog, /我已导出，仍要删除/);
  assert.doesNotMatch(dialog, /<button[^>]*>确定</);
});

test("§6.3 交互要求：默认焦点在取消 / 危险色按钮 / alertdialog / Esc 与遮罩与 × 取消 / 忙态禁用", () => {
  assert.match(dialog, /role="alertdialog"/);
  assert.match(dialog, /cancelRef\.current\?\.focus\(\)/);
  assert.match(dialog, /pod-semi-delete-confirm-danger/);
  assert.match(dialog, /Escape/);
  assert.match(dialog, /onMouseDown=\{\(\) => \{\s*if \(!busy\) onClose\(\);/);
  assert.match(dialog, /disabled=\{busy\}/);
  assert.match(dialog, /正在删除/);
});

test("批量删除在弹窗里显示将要删除的批次数", () => {
  assert.match(dialog, /本次将删除 \{batchCount\} 个批次/);
  assert.match(dialog, /Math\.max\(1, Number\(count\)/);
});

test("两处删除入口都走强确认（单删 + 批量删），window.confirm 已移除", () => {
  assert.match(page, /requestDeleteActiveBatch/);
  assert.match(page, /requestDeleteSelectedBatches/);
  assert.match(page, /confirmDeleteActiveBatch/);
  assert.match(page, /confirmDeleteSelectedBatches/);
  assert.match(page, /PodSemiDeleteConfirmDialog/);
  assert.doesNotMatch(page, /window\.confirm/);
});

test("弹窗根类并入 --pod-* 变量声明（portal 出页面作用域后背景不透明）", () => {
  assert.match(styles, /\.pod-semi-customization-page,\s*\.pod-semi-delete-confirm-backdrop \{/);
  assert.match(styles, /--pod-surface:/);
  assert.match(styles, /\.pod-semi-delete-confirm-dialog/);
});
