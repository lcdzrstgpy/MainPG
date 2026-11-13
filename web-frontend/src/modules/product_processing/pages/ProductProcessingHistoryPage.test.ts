import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

const pagePath = new URL("./ProductProcessingHistoryPage.tsx", import.meta.url);
const source = existsSync(pagePath) ? readFileSync(pagePath, "utf8") : "";
const styles = readFileSync(new URL("../styles/ProductProcessingVerifyPage.css", import.meta.url), "utf8");

test("AI history page restores a task and sends completed work to precheck", () => {
  assert.ok(existsSync(pagePath), "expected the dedicated AI history page");
  assert.match(source, /tasks\/history/);
  assert.match(source, /onOpenTask\(task\.task_id\)/);
  assert.match(source, /onOpenPrecheck\(task\.task_id\)/);
  assert.match(source, /继续查看/);
  assert.match(source, /进入预检/);
});

test("history filters use one compact date-range control with secondary actions", () => {
  assert.match(source, /className="processing-history-date-range"/);
  assert.match(source, /className="processing-history-refresh"/);
  assert.match(styles, /\.processing-history-date-range\s*\{/);
  assert.match(styles, /\.processing-history-toolbar \.verify-actions button\.primary/);
});

test("history rows name the batch and its products instead of a generic title", () => {
  // 每行都叫「产品处理任务」时既认不出批次也看不出处理了什么。
  assert.match(source, /`#\$\{task\.task_id\} · \$\{names\[0\]\}/);
  assert.match(source, /batch_names/);
  assert.match(source, /sample_items/);
  assert.doesNotMatch(source, /<strong title=\{task\.title\}>/);
});

test("history date inputs omit visible start and end labels", () => {
  assert.doesNotMatch(source, /<span>开始<\/span>/);
  assert.doesNotMatch(source, /<span>结束<\/span>/);
  assert.match(source, /aria-label="起始日期"/);
  assert.match(source, /aria-label="结束日期"/);
  assert.match(styles, /\.processing-history-date-divider\s*\{[^}]*margin-top:\s*0/);
});
