import assert from "node:assert/strict";
import test from "node:test";

import { workspaceModules, workspacePageModules } from "./modules.ts";

test("sidebar navigation groups the product workflow around its AI history entry", () => {
  const productWorkflow = workspaceModules.find((module) => module.id === "product_workflow");

  assert.equal(productWorkflow?.defaultChildId, "daily_selection");
  assert.deepEqual(productWorkflow?.children?.map((child) => child.id), [
    "daily_selection",
    "product_processing",
    "product_processing_history",
    "product_processing_sourcing",
    "dimension_canvas",
  ]);
  assert.deepEqual(productWorkflow?.children?.map((child) => child.label), [
    "采集",
    "AI处理",
    "历史记录",
    "货源成本",
    "尺寸画布",
  ]);
  assert.equal(productWorkflow?.children?.find((child) => child.id === "product_processing_history")?.iconClass, "iconfont icon-reloadtime");
});

test("POD customization is a grouped section with full, semi and replica customization", () => {
  assert.deepEqual(workspaceModules.map((module) => module.id), [
    "dashboard",
    "product_workflow",
    "combo_workflow",
    "pod_workflow",
    "sourcing_workflow",
    "ai_video",
    "temu_publish_workflow",
    "personal_center",
  ]);
  // 发布商品分组：草稿库在前、发布页在后；默认进来停在草稿库
  // （空发布表单没什么可填的，先让用户看到有哪些成品可发）。
  const temuWorkflow = workspaceModules.find((module) => module.id === "temu_publish_workflow");
  assert.equal(temuWorkflow?.label, "发布商品");
  assert.equal(temuWorkflow?.defaultChildId, "temu_draft_library");
  assert.deepEqual(temuWorkflow?.children?.map((child) => child.id), [
    "temu_draft_library",
    "temu_publish",
    "temu_publish_history",
  ]);
  assert.deepEqual(temuWorkflow?.children?.map((child) => child.label), [
    "草稿库",
    "发布商品（实验）",
    "发布历史",
  ]);
  assert.equal(workspaceModules.find((module) => module.id === "ai_video")?.label, "AI 视频（实验）");
  assert.equal(workspacePageModules.find((module) => module.id === "ai_video")?.label, "AI 视频（实验）");

  const podWorkflow = workspaceModules.find((module) => module.id === "pod_workflow");
  assert.equal(podWorkflow?.defaultChildId, "pod_customization");
  assert.deepEqual(podWorkflow?.children?.map((child) => child.id), [
    "pod_customization",
    "pod_semi_customization",
    "pod_free_customization",
    "pod_replica",
    "pod_fission",
  ]);
  assert.deepEqual(podWorkflow?.children?.map((child) => child.label), [
    "全款直出",
    "印花创作",
    "自由创意",
    "爆款复刻",
    "爆款裂变",
  ]);
  assert.equal(podWorkflow?.iconClass, "iconfont icon-skin");
  // 新增爆款复刻不改变原两个入口的可用性。
  assert.ok(podWorkflow?.children?.some((child) => child.id === "pod_customization"));
  assert.ok(podWorkflow?.children?.some((child) => child.id === "pod_semi_customization"));
  assert.ok(workspacePageModules.some((module) => module.id === "pod_customization"));
  assert.ok(workspacePageModules.some((module) => module.id === "pod_semi_customization"));
  assert.ok(workspacePageModules.some((module) => module.id === "pod_free_customization"));
  assert.ok(workspacePageModules.some((module) => module.id === "pod_replica"));
});

test("sidebar navigation prioritizes sourcing & profit in its default sourcing workflow", () => {
  const sourcingWorkflow = workspaceModules.find((module) => module.id === "sourcing_workflow");

  // 「货源成本 / 核价匹配」已合并进「货源与利润」，旧入口只保留路由（hiddenFromSidebar）。
  assert.equal(sourcingWorkflow?.defaultChildId, "sourcing_ledger");
  assert.deepEqual(sourcingWorkflow?.children?.map((child) => child.id), [
    "sourcing_ledger",
    "price_verification",
    "profit_activity",
    "profit_activity_products",
  ]);
  assert.deepEqual(sourcingWorkflow?.children?.map((child) => child.label), [
    "货源与利润",
    "核价/货源匹配",
    "利润活动",
    "货源关联产品库",
  ]);
});

test("AI service is not registered in user-visible workspace navigation or pages", () => {
  assert.equal(workspaceModules.some((module) => module.id === "ai_service"), false);
  assert.equal(workspacePageModules.some((module) => module.id === "ai_service"), false);
});

test("system configuration is not a standalone workspace module", () => {
  assert.equal(workspaceModules.some((module) => module.id === "basic_settings"), false);
  assert.equal(workspacePageModules.some((module) => module.id === "basic_settings"), false);
});

test("system admin module is not registered in user-visible workspace navigation", () => {
  assert.equal(workspaceModules.some((item) => item.id === "system_admin"), false);
  assert.equal(workspacePageModules.some((item) => item.id === "system_admin"), false);
});

// 侧栏能点到的每个子模块都必须在 workspacePageModules 里有一份完整定义。
// 2026-11-04 发布历史（temu_publish_history）只加进了分组 children、漏了扁平表，
// 点一下就在 moduleTab 里解构 undefined 抛错 → 整个工作台白屏。这条断言把这类
// 「加了一半」的漏项挡在提交前。
test("every sidebar child module has a full page definition (no half-registered entries)", () => {
  const pageById = new Map(workspacePageModules.map((module) => [module.id, module]));
  const missing: string[] = [];
  for (const group of workspaceModules) {
    for (const child of group.children ?? []) {
      const page = pageById.get(child.id);
      if (!page) {
        missing.push(`${group.id} → ${child.id}`);
        continue;
      }
      assert.equal(page.label, child.label, `${child.id} 的 label 在分组和扁平表里不一致`);
    }
    if (group.defaultChildId) {
      assert.ok(
        pageById.has(group.defaultChildId),
        `默认子页面 ${group.id} → ${group.defaultChildId} 没有登记到 workspacePageModules`,
      );
    }
  }
  assert.deepEqual(missing, []);
});
