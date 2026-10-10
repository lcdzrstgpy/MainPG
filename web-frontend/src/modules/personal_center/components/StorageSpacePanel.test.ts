import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const panel = readFileSync(new URL("./StorageSpacePanel.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/personalCenterApi.ts", import.meta.url), "utf8");

test("存储空间面板通过专门的清扫接口释放缓存并处理忙态", () => {
  assert.match(panel, /sweepPodCache/);
  assert.match(panel, /一键清理/);
  assert.match(panel, /busyRef\.current/);
  assert.match(panel, /已释放/);
  assert.doesNotMatch(panel, /localStorage\.clear/);
});

test("清扫接口为 POST 且指向 POD 缓存清扫端点", () => {
  assert.match(api, /\/api\/pod-customization\/cache\/sweep/);
  assert.match(api, /method: "POST"/);
  assert.match(api, /PodCacheSweepResult/);
});
