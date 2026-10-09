import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./ProfitActivityProductsPage.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/profitActivityApi.ts", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/profitActivityProducts.css", import.meta.url), "utf8");

test("product library is compact and only edits through its dialog", () => {
  assert.match(source, /<th>入库日期/);
  assert.match(source, /<th>操作/);
  assert.match(source, /<ProductEditDialog/);
  assert.match(source, />编辑<\/button>/);
  assert.doesNotMatch(source, /function EditableCell/);
  assert.doesNotMatch(source, /profit-edit-input/);
  assert.doesNotMatch(source, /profit-col-resizer/);
  assert.match(source, /function shortNote/);
  assert.match(source, /note\.slice\(0, 4\)/);
  assert.match(source, /title=\{item\.note \|\| ""\}/);
  assert.match(styles, /\.profit-table\s*\{[^}]*table-layout:\s*fixed/);
  assert.match(source, /<col style=\{\{ width: 50 \}\} \/>/);
  assert.match(source, /<col style=\{\{ width: 64 \}\} \/>/);
  assert.match(styles, /\.profit-table\s*\{[^}]*min-width:\s*752px/);
  assert.match(styles, /\.profit-table tbody tr\s*\{[^}]*height:\s*34px/);
  assert.match(styles, /\.profit-table \.profit-note-cell\s*\{[^}]*max-width:\s*48px/);
  assert.match(styles, /\.profit-table th\s*\{[^}]*overflow:\s*visible/);
  assert.match(styles, /\.profit-source-open\s*\{[^}]*width:\s*48px/);
  assert.match(styles, /\.profit-source-open\s*\{[^}]*font-size:\s*\.56rem/);
  assert.match(styles, /\.profit-product-edit-button\s*\{[^}]*width:\s*42px/);
});

test("product edit dialog saves and can clear an independent note image", () => {
  assert.match(source, /备注图片/);
  assert.match(source, /clearAttachmentImage/);
  assert.match(source, /saveProfitActivityProductEdit/);
  assert.match(api, /form\.set\("attachment_image", attachmentImage\)/);
  assert.match(api, /form\.set\("clear_attachment_image", "true"\)/);
});
