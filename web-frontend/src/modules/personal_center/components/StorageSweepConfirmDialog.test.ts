import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const dialog = readFileSync(new URL("./StorageSweepConfirmDialog.tsx", import.meta.url), "utf8");
const panel = readFileSync(new URL("./StorageSpacePanel.tsx", import.meta.url), "utf8");
const styles = readFileSync(
  new URL("../styles/personalCenter.css", import.meta.url),
  "utf8",
);

test("清理前必须确认：面板点「清理选中项」只打开弹窗，确认后才执行 runSweep", () => {
  assert.match(panel, /setConfirmSelection\(\[\.\.\.selectedCleanable\]\)/);
  assert.match(panel, /StorageSweepConfirmDialog/);
  assert.match(panel, /void runSweep\(snapshot\)/);
  // 直接调用 runSweep 的入口已不存在（必须经过弹窗）。
  assert.doesNotMatch(panel, /onClick=\{\(\) => void runSweep\(/);
});

test("弹窗先列清单：分类名称 + 文件数 + 体积 + 总计", () => {
  assert.match(dialog, /将清理以下 \{items\.length\} 类本地缓存文件/);
  assert.match(dialog, /totalFiles/);
  assert.match(dialog, /formatBytes\(totalBytes\)/);
  assert.match(dialog, /storage-sweep-confirm-list/);
});

test("不可恢复分类（复核原图）给红色警示且确认按钮切换危险色", () => {
  assert.match(dialog, /UNRECOVERABLE_KEYS = new Set\(\["pp_review_images"\]\)/);
  assert.match(dialog, /「历史任务的复核原图」删除后无法找回/);
  assert.match(dialog, /hasUnrecoverable \? "storage-sweep-confirm-danger" : "storage-sweep-confirm-primary"/);
  // 「无法找回」警示必须是条件渲染：只勾纯垃圾分类时不得出现吓人措辞。
  assert.match(dialog, /\{hasUnrecoverable && \(/);
});

test("导出表格分类给琥珀色「可重新导出」提示，且不混入不可恢复危险态", () => {
  assert.match(dialog, /EXPORT_TABLE_KEYS = new Set\(\["pp_task_exports"\]\)/);
  assert.match(
    dialog,
    /const hasExportTables = items\.some\(\(item\) => EXPORT_TABLE_KEYS\.has\(item\.key\)\)/,
  );
  assert.match(dialog, /\{hasExportTables && \(/);
  assert.match(dialog, /「历史任务的导出表格」删除后无法直接从工作台下载，需要时可回任务页重新导出/);
  // 危险按钮只属于不可恢复分类：导出表格可重新导出，绝不能混进去。
  assert.doesNotMatch(dialog, /UNRECOVERABLE_KEYS = new Set\(\["pp_review_images", "pp_task_exports"\]\)/);
});

test("交互：alertdialog / 默认焦点在取消 / Esc 与遮罩取消 / portal 到 body", () => {
  assert.match(dialog, /role="alertdialog"/);
  assert.match(dialog, /cancelRef\.current\?\.focus\(\)/);
  assert.match(dialog, /Escape/);
  assert.match(dialog, /onMouseDown=\{onClose\}/);
  assert.match(dialog, /createPortal/);
  // 危险按钮不得写「确定」之类的模糊措辞。
  assert.doesNotMatch(dialog, />确定</);
});

test("样式：backdrop 固定层最高、危险按钮与主色按钮都有定义", () => {
  assert.match(styles, /\.storage-sweep-confirm-backdrop \{/);
  assert.match(styles, /\.storage-sweep-confirm-danger \{/);
  assert.match(styles, /\.storage-sweep-confirm-primary \{/);
  assert.match(styles, /\.storage-sweep-confirm-warning \{/);
  assert.match(styles, /\.storage-sweep-confirm-caution \{/);
});

test("该弹窗的警示文案覆盖「使用中/模板/任务记录不受影响」的安心说明", () => {
  assert.match(dialog, /使用中的图片、模板与任务记录不会被删除/);
  assert.match(dialog, /已发布到图床的图片仍可在线查看/);
});
