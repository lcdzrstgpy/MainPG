import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./WorkspaceShell.tsx", import.meta.url), "utf8");
const taskPageSource = readFileSync(new URL("../../modules/product_processing/pages/ProductProcessingTaskPage.tsx", import.meta.url), "utf8");
const verifyPageSource = readFileSync(new URL("../../modules/product_processing/pages/ProductProcessingVerifyPage.tsx", import.meta.url), "utf8");
const personalCenterSource = readFileSync(new URL("../../modules/personal_center/pages/PersonalCenterPage.tsx", import.meta.url), "utf8");

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

test("workspace does not import or render the AI service page", () => {
  assert.doesNotMatch(source, /AiServicePage/);
  assert.doesNotMatch(source, /case "ai_service":/);
});

test("personal center owns the system configuration section", () => {
  assert.doesNotMatch(source, /BasicSettingsPage/);
  assert.doesNotMatch(source, /case "basic_settings":/);
  assert.match(personalCenterSource, /BasicSettingsPage/);
  assert.match(personalCenterSource, /<BasicSettingsPage \/>/);
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
