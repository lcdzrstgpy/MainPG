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
    "AI 视频（实验）",
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

  // 海外上架（原「发布商品」）：草稿库在前、发布页在后；默认进来停在草稿库
  // （空发布表单没什么可填的，先让用户看到有哪些成品可发）。
  const overseas = findGroup("overseas_listing");
  assert.equal(overseas?.label, "海外上架");
  assert.equal(overseas?.defaultChildId, "temu_draft_library");
  assert.deepEqual(overseas?.children?.map((child) => child.id), [
    "temu_draft_library",
    "temu_publish",
    "temu_publish_history",
  ]);
  assert.deepEqual(overseas?.children?.map((child) => child.label), [
    "成品库",
    "发布商品",
    "发布历史",
  ]);
  assert.equal(workspacePageModules.find((module) => module.id === "ai_video")?.label, "AI 视频（实验）");

  const podWorkflow = findGroup("pod_workflow");
  // 自由创意是 POD 首项，点开分组默认落到它。
  assert.equal(podWorkflow?.defaultChildId, "pod_free_customization");
  assert.deepEqual(podWorkflow?.children.map((child) => child.id), [
    "pod_free_customization",
    "pod_customization",
    "pod_semi_customization",
    "pod_replica",
    "pod_fission",
  ]);
  assert.deepEqual(podWorkflow?.children.map((child) => child.label), [
    "自由创意",
    "全款直出",
    "印花创作",
    "爆款复刻",
    "爆款裂变",
  ]);
  assert.equal(podWorkflow?.iconClass, "iconfont icon-skin");
  assert.ok(workspacePageModules.some((module) => module.id === "pod_free_customization"));

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
  // 默认停在草稿库而不是发布页：空发布表单没什么可填的，先让用户看到有哪些成品可发
  // （合并 release/1.5.8 时保留的 bigdogbujiao 决定，对方分支默认是 temu_publish）
  assert.equal(overseas?.defaultChildId, "temu_draft_library");
  assert.deepEqual(overseas?.children.map((child) => child.id), [
    "temu_draft_library",
    "temu_publish",
    "temu_publish_history",
  ]);
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
  assert.equal(storeData?.defaultChildId, "order_profit_binding");
  assert.deepEqual(
    storeData?.children.filter((child) => !isWorkspaceNavigationGroup(child) && !child.hiddenFromSidebar).map((child) => child.id),
    ["order_profit_binding", "order_profit_costs"],
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

// 侧栏能点到的每个「页面」都必须在 workspacePageModules 里有一份完整定义。
// 2026-11-04 发布历史（temu_publish_history）只加进了分组 children、漏了扁平表，
// 点一下就在 moduleTab 里解构 undefined 抛错 → 整个工作台白屏。这条断言把这类
// 「加了一半」的漏项挡在提交前。
//
// 侧栏改成三层（图像服务 → 产品处理 → 页面）之后必须递归：children 里可能还是分组，
// 只有「在 pageById 里查得到」的才算页面、才停；查不到的当分组继续往下走。
test("every sidebar leaf module has a full page definition (no half-registered entries)", () => {
  const pageById = new Map(workspacePageModules.map((module) => [module.id, module]));
  const missing: string[] = [];

  type NavNode = {
    id: string;
    label: string;
    children?: NavNode[];
    defaultChildId?: string;
  };

  const walk = (node: NavNode, path: string) => {
    const page = pageById.get(node.id);
    if (page) {
      assert.equal(page.label, node.label, `${node.id} 的 label 在分组和扁平表里不一致`);
      return;
    }
    const children = node.children ?? [];
    if (!children.length) {
      missing.push(`${path} → ${node.id}`);
      return;
    }
    // defaultChildId 允许指向**深层**的默认项（如 ai_image_service 默认 daily_selection，
    // 而 daily_selection 是 product_workflow 的子项），所以要在整个后代里找
    if (node.defaultChildId) {
      const descendants = new Set<string>();
      const collect = (child: NavNode) => {
        descendants.add(child.id);
        (child.children ?? []).forEach(collect);
      };
      children.forEach(collect);
      if (!descendants.has(node.defaultChildId)) {
        missing.push(`${node.id} 的默认子项 ${node.defaultChildId} 不在 children 里`);
      }
    }
    for (const child of children) walk(child, path ? `${path} → ${node.id}` : node.id);
  };

  for (const group of workspaceModules as NavNode[]) walk(group, "");
  assert.deepEqual(missing, []);
});
