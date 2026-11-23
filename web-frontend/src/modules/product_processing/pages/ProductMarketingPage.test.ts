/**
 * 精选处理守卫：入口复用 AI 处理全流程（VerifyPage + marketing 模式），
 * 取数必须带 workspace 头（否则后端落 "local" 空间查不到草稿——2026-11-23 实际踩过）。
 */
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";

test("listProductDrafts 带 X-Workspace-ID 头", () => {
  const source = readFileSync(new URL("../api/productProcessingApi.ts", import.meta.url), "utf8");
  assert.match(source, /X-Workspace-ID/);
  assert.match(source, /productProcessingApiContext\(\)/);
});

test("精选处理入口复用 AI 处理页的 marketing 模式", () => {
  const shell = readFileSync(new URL("../../../app/layout/WorkspaceShell.tsx", import.meta.url), "utf8");
  assert.match(shell, /case "product_processing_marketing":/);
  assert.match(shell, /<ProductProcessingVerifyPage[\s\S]{0,120}marketing/);
  const verify = readFileSync(new URL("./ProductProcessingVerifyPage.tsx", import.meta.url), "utf8");
  assert.match(verify, /marketing\?: boolean/);
  assert.match(verify, /marketingTemplates/);
  // 用户要求：流程相同但 UI 不能一模一样（自有标题/按钮/金色强调）
  assert.match(verify, /is-marketing/);
  assert.match(verify, /开始精选处理/);
  assert.match(verify, /4K 精品四宫格/);
  const task = readFileSync(new URL("./ProductProcessingTaskPage.tsx", import.meta.url), "utf8");
  assert.match(task, /marketing_templates/);
  assert.match(task, /精选版式（选 3 个）/);
});
