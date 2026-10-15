import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./WorkspaceShell.tsx", import.meta.url), "utf8");
const taskPageSource = readFileSync(new URL("../../modules/product_processing/pages/ProductProcessingTaskPage.tsx", import.meta.url), "utf8");
const verifyPageSource = readFileSync(new URL("../../modules/product_processing/pages/ProductProcessingVerifyPage.tsx", import.meta.url), "utf8");

test("workspace opens AI history and restores a processing task by its own tab field", () => {
  assert.match(source, /ProductProcessingHistoryPage/);
  assert.match(source, /const openProcessingTaskDetail = \(taskId: number\)/);
  assert.match(source, /taskRunId: taskId/);
  assert.match(source, /case "product_processing_history":/);
  assert.match(taskPageSource, /initialTaskId\?: number/);
  assert.match(taskPageSource, /if \(initialTaskId != null\) void loadTask\(initialTaskId\)/);
  assert.match(taskPageSource, /继续处理中的历史任务/);
  assert.doesNotMatch(taskPageSource, /右侧：历史任务/);
  assert.doesNotMatch(verifyPageSource, /onOpenHistoryTasks/);
  assert.doesNotMatch(verifyPageSource, /历史采集任务/);
});

test("workspace keeps every open tab mounted and restores scroll by tab key", () => {
  assert.match(source, /WorkspaceTabScrollStore/);
  assert.match(source, /const saveActiveTabScroll = \(\) =>/);
  assert.match(source, /scrollPositions\.current\.save\(activeTabKey/);
  assert.match(source, /scrollPositions\.current\.restore\(activeTabKey\)/);
  assert.match(source, /restore\(activeTabKey\) \?\? \{ windowY: 0, contentY: 0 \}/);
  assert.match(source, /scrollPositions\.current\.remove\(key\)/);
  assert.match(source, /\{tabs\.map\(\(tab\) =>/);
  assert.match(source, /hidden=\{activeTabKey !== tab\.key\}/);
});

// 任务页开着时前端轮询后端 = 任务心跳；页签一丢（刷新/自动重载）心跳中断超时后任务会被自动暂停。
// 恢复页签是这条链路的兜底，别让它在后续重构里被顺手删掉。
test("workspace persists and restores open tabs across a refresh", () => {
  assert.match(source, /restoreWorkspaceTabs\(isAdmin\)/);
  assert.match(source, /readWorkspaceTabsSnapshot/);
  assert.match(source, /writeWorkspaceTabsSnapshot/);
  assert.match(source, /trailingSequence\(keys, "product-processing-task-"\)/);
  assert.match(source, /NON_RESTORABLE_MODULE_IDS/);
});

// 从核价页开出来的处理页签只带 draftIds、不带 task_id：开工后若不把任务号记回页签，
// 刷新恢复出来就是一张空的「处理设置」页，既不轮询（心跳断→超时暂停）又能再点一次「开始处理」。
test("processing tabs remember the task they started so a refresh resumes polling", () => {
  assert.match(source, /const markTabActiveTask = \(tabKey: string, taskId: number\) =>/);
  assert.match(source, /onTaskStarted=\{\(taskId\) => markTabActiveTask\(tab\.key, taskId\)\}/);
  assert.match(source, /restored\.taskRunId == null && restored\.activeTaskId != null/);
  assert.match(taskPageSource, /onTaskStarted\?: \(taskId: number\) => void/);
  assert.match(taskPageSource, /onTaskStarted\?\.\(data\.task_id\)/);
});

test("workspace does not import or render the AI service page", () => {
  assert.doesNotMatch(source, /AiServicePage/);
  assert.doesNotMatch(source, /case "ai_service":/);
});

test("workspace does not render the internal system admin page", () => {
  assert.doesNotMatch(source, /BasicSettingsPage/);
  assert.doesNotMatch(source, /case "basic_settings":/);
  assert.doesNotMatch(source, /SystemAdminPage/);
  assert.doesNotMatch(source, /case "system_admin":/);
});

test("product processing distinguishes remote service outages from API key failures", () => {
  assert.match(taskPageSource, /function isRemoteServiceError/);
  assert.match(taskPageSource, /服务器计费或 AI 服务暂时不可用/);
  assert.match(taskPageSource, /hasRemoteServiceIssue/);
  assert.doesNotMatch(
    taskPageSource,
    /const AI_CONFIG_ERROR_RE\s*=\s*\/[^/\n]*unreachable[^/\n]*\/i/,
  );
});
