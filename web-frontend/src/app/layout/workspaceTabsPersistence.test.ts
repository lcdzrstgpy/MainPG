import assert from "node:assert/strict";
import { test } from "node:test";

import type { WorkspaceTab } from "./TopNavigation.tsx";
import type { WorkspaceTabsSnapshot, WorkspaceTabsStorage } from "./workspaceTabsPersistence.ts";
import {
  MAX_RESTORED_TABS,
  WORKSPACE_TABS_STORAGE_KEY,
  readWorkspaceTabsSnapshot,
  sanitizeWorkspaceTabs,
  trailingSequence,
  trimTabsKeepingActive,
  writeWorkspaceTabsSnapshot,
} from "./workspaceTabsPersistence.ts";

// 真实案例背景：任务页开着时前端轮询后端 = 任务心跳，页签一丢（刷新/自动重载）心跳中断超时后任务会被
// 自动暂停。这组断言锁定「恢复页签」的读写与清洗：能原样往返，坏数据一律退回默认而不是崩。

class MemoryStorage implements WorkspaceTabsStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}

const ALLOWED_MODULE_IDS = new Set(["dashboard", "product_processing_tasks", "dimension_canvas"]);

test("写入后能原样读回页签与当前页签", () => {
  const storage = new MemoryStorage();
  const snapshot: WorkspaceTabsSnapshot = {
    activeTabKey: "product-processing-task-12-1",
    tabs: [
      { key: "dashboard", moduleId: "dashboard", label: "工作台首页", icon: "⌂" },
      {
        key: "product-processing-task-12-1",
        moduleId: "product_processing_tasks",
        label: "处理·#12",
        icon: "⚙",
        taskRunId: 12,
        draftIds: [1, 2, 3],
        premiumDraftIds: [2],
      },
      // 开工中的页签只带 activeTaskId：恢复时由工作台映射成 taskRunId（继续轮询心跳）。
      {
        key: "product-processing-tasks-2",
        moduleId: "product_processing_tasks",
        label: "处理·5项",
        icon: "⚙",
        draftIds: [7, 8, 9, 10, 11],
        activeTaskId: 34,
      },
    ],
  };

  writeWorkspaceTabsSnapshot(storage, snapshot);

  assert.deepEqual(readWorkspaceTabsSnapshot(storage, ALLOWED_MODULE_IDS), snapshot);
});

test("存储损坏、结构不对、没有存储时都不抛异常，退回默认", () => {
  const broken = new MemoryStorage();
  broken.setItem(WORKSPACE_TABS_STORAGE_KEY, "{not json");
  assert.equal(readWorkspaceTabsSnapshot(broken, ALLOWED_MODULE_IDS), null);

  const wrongShape = new MemoryStorage();
  wrongShape.setItem(WORKSPACE_TABS_STORAGE_KEY, JSON.stringify({ tabs: "nope", activeTabKey: 5 }));
  assert.equal(readWorkspaceTabsSnapshot(wrongShape, ALLOWED_MODULE_IDS), null);

  const empty = new MemoryStorage();
  assert.equal(readWorkspaceTabsSnapshot(empty, ALLOWED_MODULE_IDS), null);
  assert.equal(readWorkspaceTabsSnapshot(null, ALLOWED_MODULE_IDS), null);
});

test("模块已下架或当前角色不可见的页签直接丢弃，并按 key 去重", () => {
  const tabs = sanitizeWorkspaceTabs([
    { key: "dashboard", moduleId: "dashboard", label: "工作台首页", icon: "" },
    { key: "system_admin", moduleId: "system_admin", label: "系统管理", icon: "" },
    { key: "product-processing-task-3-1", moduleId: "product_processing_tasks", label: "", icon: "", taskRunId: 3 },
    { key: "product-processing-task-3-1", moduleId: "product_processing_tasks", label: "", icon: "" },
    { key: "", moduleId: "dashboard", label: "", icon: "" },
    "not-an-object",
  ], ALLOWED_MODULE_IDS);

  assert.deepEqual(tabs.map((tab) => tab.key), ["dashboard", "product-processing-task-3-1"]);
  assert.equal(tabs[1].taskRunId, 3);
  // 空文案留给调用方按模块补默认值，这里不编造标题。
  assert.equal(tabs[1].label, "");
});

test("字段类型与预期不符时按缺失处理，不让坏值流进页面", () => {
  const [tab] = sanitizeWorkspaceTabs([{
    key: "product-processing-tasks-1",
    moduleId: "product_processing_tasks",
    label: "处理·2项",
    icon: "⚙",
    taskRunId: "12",
    activeTaskId: "34",
    draftIds: "1,2",
    premiumDraftIds: [2, 3],
    returnTaskId: 4,
    processingOptions: null,
  }], ALLOWED_MODULE_IDS);

  assert.deepEqual(tab, {
    key: "product-processing-tasks-1",
    moduleId: "product_processing_tasks",
    label: "处理·2项",
    icon: "⚙",
    premiumDraftIds: [2, 3],
    returnTaskId: 4,
  });
});

test("当前页签不在页签列表里时落到第一个页签", () => {
  const storage = new MemoryStorage();
  storage.setItem(WORKSPACE_TABS_STORAGE_KEY, JSON.stringify({
    tabs: [{ key: "dashboard", moduleId: "dashboard", label: "工作台首页", icon: "" }],
    activeTabKey: "product-processing-task-99-1",
  }));

  assert.equal(readWorkspaceTabsSnapshot(storage, ALLOWED_MODULE_IDS)?.activeTabKey, "dashboard");
});

test("截断到上限时当前页签必须留下", () => {
  const tabs: WorkspaceTab[] = Array.from({ length: MAX_RESTORED_TABS + 5 }, (_, index) => ({
    key: `tab-${index}`,
    moduleId: "dashboard",
    label: `页签 ${index}`,
    icon: "",
  }));
  const active = tabs[tabs.length - 1];

  const kept = trimTabsKeepingActive(tabs, active.key, MAX_RESTORED_TABS);

  assert.equal(kept.length, MAX_RESTORED_TABS);
  assert.equal(kept[kept.length - 1].key, active.key);
  // 上限之内不动数组，保持原样。
  assert.equal(trimTabsKeepingActive(tabs.slice(0, 3), "tab-0", MAX_RESTORED_TABS).length, 3);
});

test("按页签 key 续上各自模块的序号，避免恢复后新页签撞 key", () => {
  const keys = [
    "daily-selection-collection-2",
    "product-processing-tasks-5",
    "product-processing-task-17-3",
    "product-processing-precheck-4",
  ];

  assert.equal(trailingSequence(keys, "daily-selection-collection-"), 2);
  assert.equal(trailingSequence(keys, "product-processing-tasks-"), 5);
  assert.equal(trailingSequence(keys, "product-processing-task-"), 3);
  assert.equal(trailingSequence(keys, "product-processing-precheck-"), 4);
  assert.equal(trailingSequence(keys, "no-such-prefix-"), 0);
});
