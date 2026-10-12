import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./ProfitActivityProductsPage.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/profitActivityApi.ts", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/profitActivityProducts.css", import.meta.url), "utf8");

test("product library edits inline and keeps its compact column contract", () => {
  // 行内编辑是当前（也是最终）设计：可编辑列统一由 EditableCell 承载。
  // 「只读表格 + ProductEditDialog 弹窗编辑」那套从未落地（该组件全仓不存在）。
  assert.match(source, /function EditableCell\(/);
  assert.match(source, /<EditableCell field="selling_price"/);
  assert.match(source, /<EditableCell field="note"/);
  assert.match(source, /<EditableCell field="attachment_image"/);
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

test("product edit saves inline and can clear an independent attachment image", () => {
  // 附件图列（表头「图片」）与商品图分开，走行内编辑上传/清除。
  assert.match(source, /\{ key: "attachmentImage", label: "图片"/);
  assert.match(source, /clearAttachmentImage/);
  assert.match(source, /clearAttachmentImage: inlineEdit\.field === "attachment_image" && inlineEdit\.clear/);
  assert.match(source, /saveProfitActivityProductEdit/);
  assert.match(api, /form\.set\("attachment_image", attachmentImage\)/);
  assert.match(api, /form\.set\("clear_attachment_image", "true"\)/);
});
