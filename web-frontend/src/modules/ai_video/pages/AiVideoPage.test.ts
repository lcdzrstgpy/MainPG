import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const page = readFileSync(new URL("./AiVideoPage.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/clipforgeApi.ts", import.meta.url), "utf8");

test("AI video workspace loads service state and embeds only a ready ClipForge URL", () => {
  assert.match(api, /"\/api\/clipforge\/status"/);
  assert.match(api, /"\/api\/clipforge\/start"/);
  assert.match(page, /status\?\.state === "ready" && status\.url/);
  assert.match(page, /title="ClipForge AI 视频"/);
  assert.match(page, /src=\{embeddedClipForgeUrl\(status\.url, selected\.href\)\}/);
  assert.match(page, /status\?\.state === "unavailable"/);
});

test("AI video is hosted as a MainPG module and requests ClipForge embedded mode", () => {
  assert.match(page, /className="ai-video-page"/);
  assert.match(page, /searchParams\.set\("embed", "mainpg"\)/);
  assert.match(page, /视频工作台/);
  assert.match(page, /我的项目/);
  assert.match(page, /商品素材/);
  assert.match(page, /主播素材/);
  assert.match(page, /爆款复刻/);
  assert.match(page, /任务中心/);
  assert.match(page, /设置/);
});
