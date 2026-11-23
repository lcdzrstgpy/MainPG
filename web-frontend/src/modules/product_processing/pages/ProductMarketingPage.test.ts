/**
 * 精选处理页守卫：取数必须带 workspace 头（否则后端落 "local" 空间查不到草稿，
 * 页面永远空白——2026-11-23 实际踩过的坑）。
 */
import { readFileSync } from "node:fs";
import { test } from "node:test";
import assert from "node:assert/strict";

test("listProductDrafts 带 X-Workspace-ID 头", () => {
  const source = readFileSync(new URL("../api/productProcessingApi.ts", import.meta.url), "utf8");
  assert.match(source, /X-Workspace-ID/);
  assert.match(source, /productProcessingApiContext\(\)/);
});

test("精选处理页走 listProductDrafts 并有空态提示", () => {
  const page = readFileSync(new URL("./ProductMarketingPage.tsx", import.meta.url), "utf8");
  assert.match(page, /listProductDrafts/);
  assert.match(page, /暂无草稿/);
  assert.match(page, /generateMarketingImage/);
});
