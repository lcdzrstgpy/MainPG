/**
 * 精选处理守卫：独立页面（不与 AI 处理共用 DOM/视觉），
 * 取数必须带 workspace 头（否则后端落 "local" 空间查不到草稿——2026-11-23 踩过）。
 */
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";

test("listProductDrafts 带 X-Workspace-ID 头", () => {
  const source = readFileSync(new URL("../api/productProcessingApi.ts", import.meta.url), "utf8");
  assert.match(source, /X-Workspace-ID/);
  assert.match(source, /productProcessingApiContext\(\)/);
});

test("精选处理是独立界面（画廊 + 粘性面板），不复用 AI 处理页", () => {
  const shell = readFileSync(new URL("../../../app/layout/WorkspaceShell.tsx", import.meta.url), "utf8");
  assert.match(shell, /case "product_processing_marketing":/);
  assert.match(shell, /<ProductMarketingPage[\s\S]{0,160}onOpenTask/);
  const page = readFileSync(new URL("./ProductMarketingPage.tsx", import.meta.url), "utf8");
  assert.match(page, /pm-page/);
  assert.match(page, /商品画廊/);
  assert.match(page, /marketing_templates/);
  assert.match(page, /精选版式（选 3 个）/);
  // 草稿池口径必须与 AI 处理一致：待处理池（view=summary + 排除 processed/processing/deleted）
  assert.match(page, /view=summary&limit=500/);
  assert.match(page, /draft.status !== 'processed'/);
});

test("AI 处理页不再带精选专属分支", () => {
  const verify = readFileSync(new URL("./ProductProcessingVerifyPage.tsx", import.meta.url), "utf8");
  assert.ok(!verify.includes("is-marketing"));
  assert.ok(!verify.includes("开始精选处理"));
  assert.match(verify, /产品处理草稿池/);
});
