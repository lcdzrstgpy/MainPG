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
  assert.match(drawer, /link\.images/);
});
