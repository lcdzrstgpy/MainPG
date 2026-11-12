import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./ProfitActivityProductsPage.tsx", import.meta.url), "utf8");
const drawer = readFileSync(new URL("../components/ProductSourceDrawer.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/profitActivityProducts.css", import.meta.url), "utf8");

test("product library edits inline and keeps its compact column contract", () => {
  // 行内编辑是当前（也是最终）设计：可编辑列统一由 EditableCell 承载。
  // 「只读表格 + ProductEditDialog 弹窗编辑」那套从未落地（该组件全仓不存在）。
  assert.match(source, /function EditableCell\(/);
  assert.match(source, /<EditableCell field="selling_price"/);
  assert.match(source, /<EditableCell field="note"/);
  // 附件图列已下线：货源图统一在「货源」抽屉里看。
  assert.doesNotMatch(source, /AttachmentImageCell/);
  assert.doesNotMatch(source, /key: "attachmentImage"/);
  assert.doesNotMatch(source, /ProductEditDialog/);
  assert.doesNotMatch(source, /profit-col-resizer/);
  // 表头文案与默认列宽统一由 productTableColumns 派生，表头不是硬编码 <th>。
  assert.match(source, /\{ key: "createdAt", label: "入库日期"/);
  assert.match(source, /<col key=\{column\.key\} style=\{\{ width: widths\[column\.key\] \}\} \/>/);
  // 备注列紧凑展示：正文只取前 4 字，完整内容放 title。
  assert.match(source, /function shortNote/);
  assert.match(source, /note\.slice\(0, 4\)/);
  assert.match(source, /title=\{item\.note \|\| ""\}/);
  // 紧凑表格样式契约。
  assert.match(styles, /\.profit-table\s*\{[^}]*table-layout:\s*fixed/);
  assert.match(styles, /\.profit-table\s*\{[^}]*min-width:\s*752px/);
  assert.match(styles, /\.profit-table tbody tr\s*\{[^}]*height:\s*34px/);
  assert.match(styles, /\.profit-table th\s*\{[^}]*overflow:\s*visible/);
  assert.match(styles, /\.profit-source-open\s*\{[^}]*width:\s*48px/);
  assert.match(styles, /\.profit-source-open\s*\{[^}]*font-size:\s*\.56rem/);
});

test("product library opens the source drawer and previews ledger source images", () => {
  // 「货源」列只负责打开抽屉；多货源与货源图（主图 + 用户补充）都在抽屉里维护。
  assert.match(source, /className="profit-source-open"/);
  assert.match(source, /setActiveProduct\(/);
  assert.match(source, /<ProductSourceDrawer/);
  assert.match(source, /<ImagePreviewModal/);
  // 一个货源链接可以带多张货源图（后端 image_paths），抽屉里要能点开看大图。
  // 对方分支把只读货源卡片去掉了，这里改成挂在编辑行里（SourceThumbStrip）。
  // 注意：不能再按 link.images 取图 —— 后端从来没有这个字段（只有 image_paths），
  // 那一版写法的结果是缩略图区永远不渲染。
  assert.match(drawer, /profit-source-thumb/);
  assert.match(drawer, /function SourceThumbStrip\(/);
  assert.match(drawer, /imagePaths=\{original\?\.image_paths \?\? \[\]\}/);
  assert.match(drawer, /loadProductImage\(\{/);
  assert.doesNotMatch(drawer, /linkOfRow\(row\)\?\.images/);
  // 抽屉改成按行按需 loadProductImage 取图（不再一次性读 link.images），
  // 断言跟随实际实现，避免继续守着已下线的字段。
  assert.match(drawer, /loadProductImage\(/);
  assert.match(drawer, /setPreviewImage\(\{ url: row\.imagePreview/);
});
