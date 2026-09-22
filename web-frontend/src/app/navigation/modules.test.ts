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
    "dimension_canvas",
  ]);
  assert.deepEqual(productWorkflow?.children?.map((child) => child.label), [
    "采集",
    "AI处理",
    "历史记录",
    "尺寸画布",
  ]);
  assert.equal(productWorkflow?.children?.find((child) => child.id === "product_processing_history")?.iconClass, "iconfont icon-time-circle");
});

test("POD customization is a grouped section with full and semi customization", () => {
  assert.deepEqual(workspaceModules.map((module) => module.id), [
    "dashboard",
    "ai_video",
    "product_workflow",
    "combo_workflow",
    "pod_workflow",
    "sourcing_workflow",
    "personal_center",
  ]);
  assert.equal(workspaceModules.find((module) => module.id === "ai_video")?.label, "AI 视频");
  assert.equal(workspacePageModules.find((module) => module.id === "ai_video")?.label, "AI 视频");

  const podWorkflow = workspaceModules.find((module) => module.id === "pod_workflow");
  assert.equal(podWorkflow?.defaultChildId, "pod_customization");
  assert.deepEqual(podWorkflow?.children?.map((child) => child.id), [
    "pod_customization",
    "pod_semi_customization",
  ]);
  assert.deepEqual(podWorkflow?.children?.map((child) => child.label), ["全定制", "半定制"]);
  assert.equal(podWorkflow?.iconClass, "iconfont icon-skin");
});

test("sidebar navigation prioritizes price and source matching in its default sourcing workflow", () => {
  const sourcingWorkflow = workspaceModules.find((module) => module.id === "sourcing_workflow");

  assert.equal(sourcingWorkflow?.defaultChildId, "price_verification");
  assert.deepEqual(sourcingWorkflow?.children?.map((child) => child.id), [
    "price_verification",
    "profit_activity",
    "profit_activity_products",
  ]);
  assert.deepEqual(sourcingWorkflow?.children?.map((child) => child.label), [
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
