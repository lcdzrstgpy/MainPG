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
  assert.match(page, /精选版式（选 1–4 个，另自动产出 1 张纯白底主图）/);
  assert.match(page, /infographic_hero/);
  assert.match(page, /infographic_details/);
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

test("精选版式收敛到 6 个（伪独立/重复版式已下架）", () => {
  const page = readFileSync(new URL("./ProductMarketingPage.tsx", import.meta.url), "utf8");
  for (const key of ["action_proof", "side_rail", "use_cases", "feature_strip", "material_strip"]) {
    assert.ok(!page.includes(key), `${key} 应已下架`);
  }
  for (const key of [
    "infographic_hero",
    "infographic_details",
    "headline_hero",
    "feature_cards",
    "icon_grid",
    "callout_ring",
  ]) {
    assert.ok(page.includes(key), `${key} 应保留`);
  }
  // AI 处理页的精选版式与默认值必须同步，不能还能选到已下架版式
  const task = readFileSync(new URL("./ProductProcessingTaskPage.tsx", import.meta.url), "utf8");
  assert.ok(!task.includes("'action_proof'"));
  assert.ok(!task.includes("'material_strip'"));
  assert.ok(task.includes("['headline_hero', 'feature_cards', 'icon_grid']"));
});

test("精选选品支持多方案与批次筛选（一个方案一个任务）", () => {
  const page = readFileSync(new URL("./ProductMarketingPage.tsx", import.meta.url), "utf8");
  // 方案配置持久化（只存配置，不存商品选择）
  assert.match(page, /pp\.marketing\.schemes\.v1/);
  assert.match(page, /loadSchemes/);
  // 列表效率：批次筛选走服务端 selection_run_id，批次列表复用 draft-batches
  assert.match(page, /draft-batches\?limit=200/);
  assert.match(page, /selection_run_id=\$\{encodeURIComponent/);
  assert.match(page, /全选筛选结果/);
  // 一方案一任务：按方案逐个提交，各自带独立幂等键
  assert.match(page, /for \(const entry of entries\)/);
  assert.match(page, /marketing_templates: entry\.scheme\.templates/);
  assert.match(page, /'Idempotency-Key':/);
});

test("精选处理默认多角度出图（朝向不再让用户二选一）", () => {
  const page = readFileSync(new URL("./ProductMarketingPage.tsx", import.meta.url), "utf8");
  // 多角度是默认能力：界面上不再出现「统一正向」开关，也不随方案提交开关字段。
  assert.ok(!page.includes("统一正向"));
  assert.ok(!page.includes("poseVariety"));
  assert.ok(!page.includes("marketing_pose_variety"));
});

test("精选处理用共通模板替代注意点（每方案独立，默认自动）", () => {
  const page = readFileSync(new URL("./ProductMarketingPage.tsx", import.meta.url), "utf8");
  // 注意点输入框已下架，提交不再带 batch_image_prompt
  assert.ok(!page.includes("注意点"));
  assert.ok(!page.includes("batch_image_prompt"));
  // 共通模板：默认 auto，按方案提交；工具也可以选亮色
  assert.match(page, /styleTemplate: 'auto'/);
  assert.match(page, /marketing_style_template: entry\.scheme\.styleTemplate/);
  assert.match(page, /明亮影棚/);
  assert.match(page, /暗色质感/);
  assert.match(page, /纯白极简/);
  assert.match(page, /生活场景/);
});

test("精选处理用「重要信息」替代产出说明（每方案独立，提交给生成参考）", () => {
  const page = readFileSync(new URL("./ProductMarketingPage.tsx", import.meta.url), "utf8");
  // 旧的「方案 X 每商品产出 N 张图片」说明块已整个删除
  assert.ok(!page.includes("每商品产出"));
  assert.ok(!page.includes("pm-output"));
  // 新的选填输入框：每方案一份、限长 300、随提交下发
  assert.match(page, /重要信息（选填）/);
  assert.match(page, /keyInfo: string/);
  assert.match(page, /maxLength=\{300\}/);
  assert.match(page, /marketing_key_info: entry\.scheme\.keyInfo\.trim\(\)/);
  assert.match(page, /keyInfo: typeof value\.keyInfo === 'string'/);
});
