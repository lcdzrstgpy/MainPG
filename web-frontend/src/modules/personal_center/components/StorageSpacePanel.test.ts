import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(new URL("./StorageSpacePanel.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/personalCenterApi.ts", import.meta.url), "utf8");

test("存储空间面板是「先扫描 → 勾选 → 清理选中项」：无勾选不执行、含忙态防重入", () => {
  assert.match(panel, /scanPodCache/);
  assert.match(panel, /sweepPodCache\(keys\)/);
  assert.match(panel, /type="checkbox"/);
  assert.match(panel, /全选/);
  assert.match(panel, /清理选中项/);
  assert.match(panel, /已释放/);
  assert.match(panel, /busyRef\.current/);
  assert.doesNotMatch(panel, /一键清理/);
  assert.doesNotMatch(panel, /localStorage\.clear/);
});

test("首次扫描的「全选」判定在 setState 之前同步求出（防 updater 延迟导致的空勾选回归）", () => {
  assert.match(panel, /computeScanSelection/);
  assert.match(panel, /const firstScan = !seenScanRef\.current/);
  assert.match(panel, /computeScanSelection\(data\.categories, previous, firstScan\)/);
});

test("四个可清理分类都有可读文案，未知分类有回退", () => {
  assert.match(panel, /已发布图床的旧结果图/);
  assert.match(panel, /已结束批次的中间图/);
  assert.match(panel, /历史无引用残留/);
  assert.match(panel, /磁盘孤儿文件/);
  assert.match(panel, /CATEGORY_META\[item\.key\] \?\?/);
});

test("扫描为只读 GET；清扫把勾选分类带在查询串上（POST）", () => {
  assert.match(api, /\/api\/pod-customization\/cache\/sweep\/scan/);
  assert.match(api, /function scanPodCache\(\)/);
  assert.match(api, /function sweepPodCache\(categories: string\[\]\)/);
  assert.match(api, /categories: categories\.join\(","\)/);
  assert.match(api, /method: "POST"/);
  assert.match(api, /PodCacheScanResult/);
  assert.match(api, /PodCacheSweepResult/);
});
