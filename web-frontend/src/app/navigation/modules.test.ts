import assert from "node:assert/strict";
import test from "node:test";

import {
  isWorkspaceNavigationGroup,
  personalCenterPanels,
  systemSettingPanels,
  workspaceModules,
  workspacePageModules,
  type WorkspaceNavigationGroup,
  type WorkspaceNavigationItem,
} from "./modules.ts";

function findGroup(id: string): WorkspaceNavigationGroup | undefined {
  const walk = (items: WorkspaceNavigationItem[]): WorkspaceNavigationGroup | undefined => {
    for (const item of items) {
      if (!isWorkspaceNavigationGroup(item)) continue;
      if (item.id === id) return item;
      const nested = walk(item.children);
      if (nested) return nested;
    }
    return undefined;
  };
  return walk(workspaceModules);
}

test("top level navigation groups image services, overseas listing, data and settings", () => {
  assert.deepEqual(workspaceModules.map((module) => module.id), [
    "dashboard",
    "ai_image_service",
    "overseas_listing",
    "data_insights",
    "personal_center",
    "system_settings",
  ]);
  assert.deepEqual(workspaceModules.map((module) => module.label), [
    "工作台",
    "图像服务",
    "海外上架",
    "数据信息",
    "个人中心",
    "系统设置",
  ]);
});

test("image service nests the existing workflows one level deeper", () => {
  const imageService = findGroup("ai_image_service");

  assert.equal(imageService?.label, "图像服务");
  assert.deepEqual(imageService?.children.map((child) => child.id), [
    "product_workflow",
    "pod_workflow",
    "combo_workflow",
    "ai_video",
  ]);
  assert.deepEqual(imageService?.children.map((child) => child.label), [
    "产品处理",
    "POD定制",
    "商品自定义",
    "AI视频（实验）",
  ]);

  const productWorkflow = findGroup("product_workflow");
  assert.equal(productWorkflow?.defaultChildId, "daily_selection");
  assert.deepEqual(productWorkflow?.children.map((child) => child.id), [
    "daily_selection",
    "product_processing",
    "product_processing_history",
    "product_processing_sourcing",
    "dimension_canvas",
  ]);

  const podWorkflow = findGroup("pod_workflow");
  assert.equal(podWorkflow?.defaultChildId, "pod_customization");
  assert.deepEqual(podWorkflow?.children.map((child) => child.label), ["全定制", "半定制", "爆款复刻", "爆款裂变"]);

  const comboWorkflow = findGroup("combo_workflow");
  assert.equal(comboWorkflow?.defaultChildId, "combo_generate");
  assert.deepEqual(comboWorkflow?.children.map((child) => child.label), [
    "组合生图",
    "提示词模板预设",
    "历史组合套装",
  ]);

  const aiVideo = findGroup("ai_video");
  assert.ok(aiVideo?.panels?.length);
  assert.equal(aiVideo?.defaultChildId, "ai_video");
});

test("overseas listing keeps the publish page and draft library side by side", () => {
  const overseas = findGroup("overseas_listing");

  assert.equal(overseas?.label, "海外上架");
  assert.equal(overseas?.defaultChildId, "temu_publish");
  assert.deepEqual(overseas?.children.map((child) => child.id), ["temu_publish", "temu_draft_library"]);
});

test("data insights groups sourcing profit and store data management", () => {
  const dataInsights = findGroup("data_insights");

  assert.equal(dataInsights?.label, "数据信息");
  assert.deepEqual(dataInsights?.children.map((child) => child.id), ["sourcing_workflow", "order_profit"]);
  assert.deepEqual(dataInsights?.children.map((child) => child.label), ["商品核价及利润", "店铺数据管理"]);

  const sourcingWorkflow = findGroup("sourcing_workflow");
  assert.equal(sourcingWorkflow?.defaultChildId, "sourcing_ledger");
  assert.deepEqual(sourcingWorkflow?.children.map((child) => child.id), [
    "sourcing_ledger",
    "price_verification",
    "profit_activity",
    "profit_activity_products",
  ]);

  const storeData = findGroup("order_profit");
  assert.equal(storeData?.defaultChildId, "order_profit_overview");
  assert.deepEqual(
    storeData?.children.filter((child) => !isWorkspaceNavigationGroup(child) && !child.hiddenFromSidebar).map((child) => child.id),
    [
      "order_profit_overview",
      "order_profit_orders",
      "order_profit_funds",
      "order_profit_accounts",
      "order_profit_capture",
      "order_profit_records",
    ],
  );
  // 合并前的单页入口仍保留在页面路由里，避免浏览器已保存的旧页签失效。
  for (const legacyId of [
    "order_profit_home",
    "order_profit_traffic",
    "order_profit_recon",
    "order_profit_settlement",
  ]) {
    assert.ok(workspacePageModules.some((module) => module.id === legacyId), legacyId);
  }
});

test("personal center and system settings split their panels", () => {
  assert.deepEqual(personalCenterPanels.map((panel) => panel.label), [
    "积分钱包",
    "消费流水",
    "计费规则",
    "意见反馈",
    "推广计划",
  ]);
  assert.deepEqual(systemSettingPanels.map((panel) => panel.label), [
    "系统版本",
    "偏好设置",
    "存储空间",
    "插件设置",
  ]);

  const personalCenter = findGroup("personal_center");
  assert.equal(personalCenter?.defaultPanelId, "wallet");
  const systemSettings = findGroup("system_settings");
  assert.equal(systemSettings?.label, "系统设置");
  assert.equal(systemSettings?.defaultPanelId, "version");
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
