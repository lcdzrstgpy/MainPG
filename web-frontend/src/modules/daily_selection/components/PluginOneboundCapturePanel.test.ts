import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(new URL("./PluginOneboundCapturePanel.tsx", import.meta.url), "utf8");

test("plugin capture panel starts prepared OneBound batches and retries terminal failures", () => {
  assert.match(panel, /1688 插件 · 万邦 API/);
  assert.match(panel, /请前往 1688 页面使用浏览器插件发起整页采集/);
  assert.match(panel, /canRetryPluginCaptureFailures/);
  assert.match(panel, /重试失败项/);
  assert.match(panel, /selectedBatch\.status === "prepared"/);
  assert.match(panel, /pluginOneboundCaptureApi\.startBatch/);
  assert.match(panel, /启动万邦采集/);
  assert.doesNotMatch(panel, /pluginOneboundCaptureApi\.(createBatch|pause|resume|cancel)/);
  assert.doesNotMatch(panel, />\s*(创建批次|暂停|取消)\s*</);
});

test("plugin capture panel polls active batches every two seconds and exposes source and draft entries", () => {
  assert.match(panel, /isActivePluginCaptureStatus/);
  assert.match(panel, /setInterval[\s\S]{0,500}2000/);
  assert.match(panel, /查看来源/);
  assert.match(panel, /onOpenDraft/);
  assert.match(panel, /打开产品处理 · 草稿/);
  assert.doesNotMatch(panel, /href=\{`\/product-processing\/drafts/);
  for (const label of ["总数", "新建", "刷新", "跳过", "失败", "未处理"]) {
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
