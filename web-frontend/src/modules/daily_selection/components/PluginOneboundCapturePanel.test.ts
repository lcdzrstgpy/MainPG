import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(new URL("./PluginOneboundCapturePanel.tsx", import.meta.url), "utf8");
const styles = readFileSync(new URL("../styles/shop-collection.css", import.meta.url), "utf8");

test("plugin capture panel starts prepared OneBound batches and retries terminal failures", () => {
  assert.match(panel, /1688\/淘宝链接/);
  assert.match(panel, /请前往 1688 或淘宝页面使用浏览器插件发起整页采集/);
  assert.match(panel, /canRetryPluginCaptureFailures/);
  assert.match(panel, /重试失败项/);
  assert.match(panel, /selectedBatch\.status === "prepared"/);
  assert.match(panel, /pluginOneboundCaptureApi\.startBatch/);
  assert.match(panel, /启动采集/);
  assert.doesNotMatch(panel, /shopCollectionApi/);
  assert.doesNotMatch(panel, /pluginOneboundCaptureApi\.(createBatch|pause|resume|cancel)\b/);
  assert.doesNotMatch(panel, />\s*(创建批次|暂停|取消)\s*</);
});

test("plugin capture panel polls active batches every two seconds and exposes source and draft entries", () => {
  assert.match(panel, /isActivePluginCaptureStatus/);
  assert.match(panel, /setInterval[\s\S]{0,500}2000/);
  assert.match(panel, /查看来源/);
  assert.match(panel, /onOpenDraft/);
  assert.match(panel, /打开产品处理 · 草稿/);
  assert.doesNotMatch(panel, /href=\{`\/product-processing\/drafts/);
  for (const label of ["总数", "已入池", "跳过", "失败", "未处理"]) {
    assert.match(panel, new RegExp(label));
  }
});

test("plugin capture panel renders the complete persisted contract safely", () => {
  assert.match(panel, /source_title\.trim\(\)/);
  assert.match(panel, /selectedBatch\.page_url/);
  assert.match(panel, /查看采集页面/);
  assert.match(panel, /unprocessed:\s*"未处理"/);
  assert.match(panel, /formatShopCollectionError\(new Error\(selectedBatch\.error_message\)\)/);
  assert.match(panel, /formatShopCollectionError\(new Error\(item\.error_message\)\)/);
  assert.match(panel, /item\.attempts/);
});

test("plugin capture panel reviews candidates with SKU filter, selection and backfill", () => {
  assert.match(panel, /候选商品/);
  assert.match(panel, /SKU筛选/);
  assert.match(panel, /SKU补齐/);
  assert.match(panel, /确认入池/);
  assert.match(panel, /全选/);
  assert.match(panel, /pluginOneboundCaptureApi\.listCandidates/);
  assert.match(panel, /pluginOneboundCaptureApi\.getSkuRepullState/);
  assert.match(panel, /pluginOneboundCaptureApi\.startSkuRepull/);
  assert.match(panel, /pluginOneboundCaptureApi\.cancelSkuRepull/);
  assert.match(panel, /pluginOneboundCaptureApi\.confirmCandidates/);
  assert.match(panel, /review_status === "pending"/);
  assert.match(panel, /backfillRunning/);
});

test("plugin candidate title links to the source and keeps an independent source action", () => {
  // 当前（也是最终）设计：标题本身就是可点击的来源链接（单行省略），
  // 行内另外保留一个独立的「查看来源」入口。
  assert.match(panel, /<a className="plugin-title-link" href=\{candidate\.source_url\}[^>]*>\{candidate\.source_title\.trim\(\)/);
  assert.match(panel, /<a className="plugin-candidate-source" href=\{candidate\.source_url\} target="_blank" rel="noreferrer">查看来源<\/a>/);
  assert.match(panel, /className="plugin-candidate-links"/);
});

test("plugin candidate review area styles are scoped under .plugin-candidate-*", () => {
  for (const selector of [
    ".plugin-candidate-review",
    ".plugin-candidate-list",
    ".plugin-candidate-body",
    ".plugin-candidate-links",
    ".plugin-candidate-source",
  ]) {
    assert.match(styles, new RegExp(selector.replace(/\./g, "\\.")));
  }
});

test("plugin batch management uses the shared side-drawer workflow", () => {
  assert.match(panel, /forwardRef<PluginOneboundCapturePanelHandle/);
  assert.match(panel, /useImperativeHandle\(ref, \(\) => \(\{ openBatchManager/);
  assert.match(panel, /shop-batch-drawer-layer/);
  assert.match(panel, /createPortal\(/);
  assert.doesNotMatch(panel, /<aside className="shop-batch-list"/);
  assert.match(styles, /\.plugin-capture-stats\s*\{[^}]*grid-template-columns:\s*repeat\(5,\s*minmax\(0,\s*1fr\)\);/);
  assert.match(styles, /\.plugin-capture-panel\s+\.plugin-candidate-review\s+\.shop-items-heading\s*\{[^}]*margin:\s*0;/);
});
