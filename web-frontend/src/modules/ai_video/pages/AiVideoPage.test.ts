import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const page = readFileSync(new URL("./AiVideoPage.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/clipforgeApi.ts", import.meta.url), "utf8");

// AiVideoPage.tsx 是 .tsx（含 JSX），Node 无法直接导入运行。
// 这里从真实源码里切出常量与纯函数片段，用 TypeScript 去掉类型标注后求值，
// 保证单测跑的是页面里真正上线的那份实现，而不是测试里另抄一份。
function sliceSource(pattern: RegExp, label: string) {
  const matched = page.match(pattern);
  assert.ok(matched, `AiVideoPage.tsx 中找不到 ${label}`);
  return matched[0];
}

function loadAiVideoLogic() {
  const snippets = [
    sliceSource(/^const AI_VIDEO_NAV = \[[\s\S]*?^\] as const;$/m, "AI_VIDEO_NAV 定义"),
    sliceSource(/^export const AI_VIDEO_NAVIGATE_MESSAGE = .*$/m, "AI_VIDEO_NAVIGATE_MESSAGE 常量"),
    sliceSource(/^export const AI_VIDEO_LOCATION_MESSAGE = .*$/m, "AI_VIDEO_LOCATION_MESSAGE 常量"),
    sliceSource(/^export const AI_VIDEO_NAV_INITIAL_STATE[\s\S]*?^};$/m, "AI_VIDEO_NAV_INITIAL_STATE 状态"),
    ...[
      "aiVideoNavHrefForPath",
      "selectAiVideoNav",
      "applyAiVideoLocation",
      "clipForgeOrigin",
    ].map((name) => sliceSource(new RegExp(`^export function ${name}\\([\\s\\S]*?^\\}$`, "m"), `${name} 函数`)),
  ];
  const compiled = ts.transpileModule(snippets.join("\n\n").replace(/^export /gm, ""), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None },
  }).outputText;
  const exported = [
    "AI_VIDEO_NAV",
    "AI_VIDEO_NAVIGATE_MESSAGE",
    "AI_VIDEO_LOCATION_MESSAGE",
    "AI_VIDEO_NAV_INITIAL_STATE",
    "aiVideoNavHrefForPath",
    "selectAiVideoNav",
    "applyAiVideoLocation",
    "clipForgeOrigin",
  ];
  return new Function(`${compiled}\nreturn { ${exported.join(", ")} };`)();
}

const logic = loadAiVideoLogic();

test("AI video workspace loads service state and embeds only a ready ClipForge URL", () => {
  assert.match(api, /"\/api\/clipforge\/status"/);
  assert.match(api, /"\/api\/clipforge\/start"/);
  assert.match(page, /status\?\.state === "ready" && status\.url/);
  assert.match(page, /title="ClipForge AI 视频"/);
  // iframe 的 src 只由服务地址与固定的初始入口算一次，导航不改 src。
  assert.match(page, /src=\{embeddedClipForgeUrl\(status\.url, AI_VIDEO_INITIAL_HREF\)\}/);
  assert.match(page, /const AI_VIDEO_INITIAL_HREF: AiVideoNavHref = "\/start"/);
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

test("AI video keeps a persistent iframe and speaks the frozen parent/iframe protocol", () => {
  // 外层导航不得销毁或重建 iframe：没有按路由生成的 key，iframe 由 ref 持有。
  assert.doesNotMatch(page, /key=\{selected/);
  assert.match(page, /ref=\{frameRef\}/);

  // 冻结协议的两个消息类型字面量。
  assert.match(page, /"mainpg:ai-video-navigate"/);
  assert.match(page, /"mainpg:ai-video-location"/);
  assert.match(page, /postMessage\(\{ type: AI_VIDEO_NAVIGATE_MESSAGE, href \}, targetOrigin\)/);

  // ready 前暂存、ready 后补发的逻辑标记。
  assert.match(page, /pendingHref/);
  assert.match(page, /sendHref/);
  assert.match(page, /AI_VIDEO_NAV_INITIAL_STATE/);
  assert.match(page, /applyAiVideoLocation\(navStateRef\.current, data\.pathname\)/);

  // 只处理来自 clipforge 服务同源的 location 消息，且 pathname 必须是字符串。
  assert.match(page, /event\.origin !== iframeOrigin/);
  assert.match(page, /data\.type !== AI_VIDEO_LOCATION_MESSAGE/);
  assert.match(page, /typeof data\.pathname !== "string"/);

  // 监听必须成对注册与清理。
  assert.match(page, /window\.addEventListener\("message", onMessage\)/);
  assert.match(page, /return \(\) => window\.removeEventListener\("message", onMessage\)/);
});

test("maps ClipForge paths onto the seven outer navigation entries", () => {
  assert.equal(logic.aiVideoNavHrefForPath("/start"), "/start");
  assert.equal(logic.aiVideoNavHrefForPath("/projects"), "/projects");
  assert.equal(logic.aiVideoNavHrefForPath("/products"), "/products");
  assert.equal(logic.aiVideoNavHrefForPath("/presenters"), "/presenters");
  assert.equal(logic.aiVideoNavHrefForPath("/batch"), "/batch");
  assert.equal(logic.aiVideoNavHrefForPath("/settings"), "/settings");
  // 爆款复刻必须优先精确匹配，不能被 /project/ 前缀规则吃掉。
  assert.equal(logic.aiVideoNavHrefForPath("/project/clone"), "/project/clone");
  // 项目详情按归属映射为「我的项目」。
  assert.equal(logic.aiVideoNavHrefForPath("/project/abc"), "/projects");
  assert.equal(logic.aiVideoNavHrefForPath("/project/abc/script"), "/projects");
  // 允许列表之外的路径返回 null，表示保持当前高亮不变。
  assert.equal(logic.aiVideoNavHrefForPath("/unknown"), null);
});

test("stashes the last pre-ready navigation and flushes it after the first location message", () => {
  const initial = logic.AI_VIDEO_NAV_INITIAL_STATE;
  assert.equal(initial.activeHref, "/start");
  assert.equal(initial.ready, false);
  assert.equal(initial.pendingHref, null);

  // ready 之前的点击：立即乐观高亮并暂存，但不下发给 iframe。
  const clicked = logic.selectAiVideoNav(initial, "/batch");
  assert.equal(clicked.sendHref, null);
  assert.equal(clicked.state.activeHref, "/batch");
  assert.equal(clicked.state.pendingHref, "/batch");
  assert.equal(clicked.state.ready, false);

  // 第一条 location 消息同时充当 ready 信号，并补发暂存的最后一次点击。
  const ready = logic.applyAiVideoLocation(clicked.state, "/start");
  assert.equal(ready.state.ready, true);
  assert.equal(ready.sendHref, "/batch");
  assert.equal(ready.state.pendingHref, null);
  // 首条消息报的还是 iframe 旧路径，高亮必须留在刚点的项上，不能闪回旧项。
  assert.equal(ready.state.activeHref, "/batch");

  // 补发后 iframe 回报真实路径：高亮以映射为准，且不再重复补发。
  const located = logic.applyAiVideoLocation(ready.state, "/batch");
  assert.equal(located.state.activeHref, "/batch");
  assert.equal(located.sendHref, null);
});

test("navigates immediately when ready and keeps the highlight for unknown paths", () => {
  const readyState = { activeHref: "/start", ready: true, pendingHref: null };

  const clicked = logic.selectAiVideoNav(readyState, "/settings");
  assert.equal(clicked.sendHref, "/settings");
  assert.equal(clicked.state.activeHref, "/settings");
  assert.equal(clicked.state.pendingHref, null);

  const located = logic.applyAiVideoLocation(clicked.state, "/project/abc/script");
  assert.equal(located.state.activeHref, "/projects");
  assert.equal(located.state.ready, true);

  const unknown = logic.applyAiVideoLocation(located.state, "/somewhere/else");
  assert.equal(unknown.state.activeHref, "/projects");
  assert.equal(unknown.sendHref, null);
});

test("only trusts messages coming from the ClipForge sidecar origin", () => {
  assert.equal(logic.clipForgeOrigin("http://127.0.0.1:54321/start?embed=mainpg"), "http://127.0.0.1:54321");
  assert.equal(logic.clipForgeOrigin(null), null);
  assert.equal(logic.clipForgeOrigin(undefined), null);
  assert.equal(logic.clipForgeOrigin("not a url"), null);
});