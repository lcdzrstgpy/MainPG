import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

const page = readFileSync(new URL("./AiVideoPage.tsx", import.meta.url), "utf8");
const api = readFileSync(new URL("../api/clipforgeApi.ts", import.meta.url), "utf8");

// 组件/样式源码：接线证据。文件被删除或改名时应视为失败，而不是静默跳过断言。
function readSource(relativePath: string) {
  return readFileSync(new URL(relativePath, import.meta.url), "utf8");
}

let serviceState = "";
try {
  serviceState = readSource("../components/AiVideoServiceState.tsx");
} catch {
  // 组件尚未实现时保持空串，让下面的断言逐条失败并给出可读原因。
  serviceState = "";
}

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
    sliceSource(/^export const AI_VIDEO_UI_MODE_SET_MESSAGE = .*$/m, "AI_VIDEO_UI_MODE_SET_MESSAGE 常量"),
    sliceSource(/^export const AI_VIDEO_UI_MODE_STATE_MESSAGE = .*$/m, "AI_VIDEO_UI_MODE_STATE_MESSAGE 常量"),
    sliceSource(/^export const AI_VIDEO_UI_MODE_STORAGE_KEY = .*$/m, "AI_VIDEO_UI_MODE_STORAGE_KEY 常量"),
    sliceSource(/^export const AI_VIDEO_NAV_INITIAL_STATE[\s\S]*?^};$/m, "AI_VIDEO_NAV_INITIAL_STATE 状态"),
    ...[
      "aiVideoNavHrefForPath",
      "selectAiVideoNav",
      "applyAiVideoLocation",
      "resetAiVideoNavForInstance",
      "clipForgeOrigin",
      "isAiVideoUiMode",
      "aiVideoUiModeFromStored",
    ].map((name) => sliceSource(new RegExp(`^export function ${name}\\([\\s\\S]*?^\\}$`, "m"), `${name} 函数`)),
  ];
  const compiled = ts.transpileModule(snippets.join("\n\n").replace(/^export /gm, ""), {
    compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.None },
  }).outputText;
  const exported = [
    "AI_VIDEO_NAV",
    "AI_VIDEO_NAVIGATE_MESSAGE",
    "AI_VIDEO_LOCATION_MESSAGE",
    "AI_VIDEO_UI_MODE_SET_MESSAGE",
    "AI_VIDEO_UI_MODE_STATE_MESSAGE",
    "AI_VIDEO_UI_MODE_STORAGE_KEY",
    "AI_VIDEO_NAV_INITIAL_STATE",
    "aiVideoNavHrefForPath",
    "selectAiVideoNav",
    "applyAiVideoLocation",
    "resetAiVideoNavForInstance",
    "clipForgeOrigin",
    "isAiVideoUiMode",
    "aiVideoUiModeFromStored",
  ];
  return new Function(`${compiled}\nreturn { ${exported.join(", ")} };`)();
}

const logic = loadAiVideoLogic();

test("AI video workspace loads service state and embeds only a ready ClipForge URL", () => {
  assert.match(api, /"\/api\/clipforge\/status"/);
  assert.match(api, /"\/api\/clipforge\/start"/);
  // 后端异步生命周期：状态查询与启动都必须支持取消，避免旧响应覆盖新状态。
  assert.match(api, /getClipForgeStatus\(signal\?: AbortSignal\)/);
  assert.match(api, /\{ method: "POST", signal \}/);
  // 只有真正可内嵌的实例（ready + url + instanceId）才渲染 iframe，
  // 不再是「HTTP 200 / state === ready」即认为已连接。
  assert.match(page, /const serviceReady = canEmbedClipForge\(status\)/);
  assert.match(page, /title="ClipForge AI 视频"/);
  // iframe 的 src 只由服务地址与固定的初始入口算一次，导航不改 src。
  assert.match(page, /src=\{embeddedClipForgeUrl\(status\.url, AI_VIDEO_INITIAL_HREF\)\}/);
  assert.match(page, /const AI_VIDEO_INITIAL_HREF: AiVideoNavHref = "\/start"/);
  // 「未安装完整」这类不可内嵌状态的文案由状态组件负责。
  assert.match(serviceState, /AI 视频服务未安装完整/);
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
  // 外层导航不得按路由销毁或重建 iframe：没有按选中项生成的 key，iframe 由 ref 持有。
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
  // 同源不等于就是当前 iframe：拒绝同一 sidecar origin 的其他窗口伪造导航状态。
  assert.match(page, /event\.source !== frameRef\.current\?\.contentWindow/);
  assert.match(page, /data\.type !== AI_VIDEO_LOCATION_MESSAGE/);
  assert.match(page, /typeof data\.pathname !== "string"/);

  // 监听必须成对注册与清理。
  assert.match(page, /window\.addEventListener\("message", onMessage\)/);
  assert.match(page, /return \(\) => window\.removeEventListener\("message", onMessage\)/);
});

test("AI video polls the sidecar service instead of issuing one mount-only request", () => {
  assert.match(page, /const \{ status, error, start \} = useClipForgeService\(\)/);
  // 页面不再自己直接打旧 API：状态全部来自轮询 hook。
  assert.doesNotMatch(page, /getClipForgeStatus/);
  assert.doesNotMatch(page, /startClipForge/);
  assert.match(page, /const instanceKey = clipForgeInstanceKey\(status\)/);
  assert.match(page, /const bridgeReady = navState\.ready/);
});

test("a new sidecar instance destroys the old iframe and clears navigation state", () => {
  // iframe 以实例身份键为 key：换实例（instanceId 或 url 变化）必然重建文档。
  assert.match(page, /key=\{instanceKey \?\? "clipforge-none"\}/);
  assert.match(page, /resetAiVideoNavForInstance\(navStateRef\.current\)/);
  // 重置必须在实例变化时触发，且同时清掉桥接超时标记。
  assert.match(page, /\}, \[instanceKey\]\)/);
  assert.match(page, /setBridgeTimedOut\(false\)/);
});

test("separates service readiness from iframe bridge readiness", () => {
  // 服务就绪 ≠ 页面已连接：bridge 就绪只认 iframe 回报的可信 location 消息。
  assert.match(page, /视频服务已就绪/);
  assert.match(page, /bridgeReady[\s\S]{0,80}AI 视频已连接/);
  assert.match(page, /bridgeTimedOut[\s\S]{0,80}页面桥接超时/);
  // 桥接超时只标记超时，绝不把后端服务改写成 failed。
  assert.doesNotMatch(page, /bridgeTimedOut[\s\S]{0,120}state: "failed"/);
});

test("times out the iframe bridge after 10s and cleans the timer up", () => {
  assert.match(page, /AI_VIDEO_BRIDGE_TIMEOUT_MS = 10000/);
  assert.match(page, /setTimeout\(\(\) => setBridgeTimedOut\(true\), AI_VIDEO_BRIDGE_TIMEOUT_MS\)/);
  // 只在「服务就绪但桥接未就绪」期间计时，bridge 就绪/实例变化/卸载都必须清掉。
  assert.match(page, /if \(!serviceReady \|\| bridgeReady\) return;/);
  assert.match(page, /return \(\) => window\.clearTimeout\(timer\)/);
});

test("renders the service state component for every non-embeddable state", () => {
  assert.match(page, /<AiVideoServiceState status=\{status\} requestError=\{error\} onStart=\{start\} \/>/);
  assert.match(serviceState, /正在检查 AI 视频服务/);
  assert.match(serviceState, /AI 视频服务未启动/);
  assert.match(serviceState, /正在启动 AI 视频服务/);
  assert.match(serviceState, /AI 视频服务启动失败/);
  assert.match(serviceState, /正在关闭 AI 视频服务/);
  assert.match(serviceState, /启动 AI 视频服务/);
  assert.match(serviceState, /重新启动/);
  // 诊断编号可作为次要信息展示，但 exitCode 绝不能当成用户主文案。
  assert.match(serviceState, /诊断编号/);
  assert.doesNotMatch(serviceState, /exitCode/);
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

test("a new sidecar instance resets iframe navigation readiness", () => {
  const connected = { activeHref: "/batch", ready: true, pendingHref: null };
  assert.deepEqual(logic.resetAiVideoNavForInstance(connected), logic.AI_VIDEO_NAV_INITIAL_STATE);
  // 必须是新对象，不能把旧状态对象原地改坏。
  assert.notEqual(logic.resetAiVideoNavForInstance(connected), connected);
});

test("only trusts messages coming from the ClipForge sidecar origin", () => {
  assert.equal(logic.clipForgeOrigin("http://127.0.0.1:54321/start?embed=mainpg"), "http://127.0.0.1:54321");
  assert.equal(logic.clipForgeOrigin(null), null);
  assert.equal(logic.clipForgeOrigin(undefined), null);
  assert.equal(logic.clipForgeOrigin("not a url"), null);
});

test("freezes the ui-mode protocol constants and the set-message payload", () => {
  assert.equal(logic.AI_VIDEO_UI_MODE_SET_MESSAGE, "mainpg:ai-video-ui-mode-set");
  assert.equal(logic.AI_VIDEO_UI_MODE_STATE_MESSAGE, "mainpg:ai-video-ui-mode-state");
  // 两个消息类型字面量与「父 -> iframe」的 uiMode 字段一起上线。
  assert.match(page, /"mainpg:ai-video-ui-mode-set"/);
  assert.match(page, /"mainpg:ai-video-ui-mode-state"/);
  assert.match(page, /postMessage\(\{ type: AI_VIDEO_UI_MODE_SET_MESSAGE, uiMode \}, targetOrigin\)/);
});

test("whitelists ui-mode values and maps unknown stored values to simple", () => {
  assert.equal(logic.isAiVideoUiMode("simple"), true);
  assert.equal(logic.isAiVideoUiMode("pro"), true);
  assert.equal(logic.isAiVideoUiMode("easy"), false);
  assert.equal(logic.isAiVideoUiMode(""), false);
  assert.equal(logic.isAiVideoUiMode(null), false);
  assert.equal(logic.isAiVideoUiMode(123), false);
  assert.equal(logic.isAiVideoUiMode(undefined), false);

  assert.equal(logic.aiVideoUiModeFromStored("simple"), "simple");
  assert.equal(logic.aiVideoUiModeFromStored("pro"), "pro");
  assert.equal(logic.aiVideoUiModeFromStored("easy"), "simple");
  assert.equal(logic.aiVideoUiModeFromStored(""), "simple");
  assert.equal(logic.aiVideoUiModeFromStored(null), "simple");
  assert.equal(logic.aiVideoUiModeFromStored(undefined), "simple");
});

test("accepts ui-mode-state only from the same iframe after whitelist validation", () => {
  // 与 location 分支共享同一套三重校验：同源、当前 iframe、以及 uiMode 白名单。
  assert.match(page, /event\.origin !== iframeOrigin/);
  assert.match(page, /event\.source !== frameRef\.current\?\.contentWindow/);
  assert.match(page, /data\.type === AI_VIDEO_UI_MODE_STATE_MESSAGE/);
  assert.match(page, /isAiVideoUiMode\(data\.uiMode\)/);
  assert.match(page, /setUiMode\(data\.uiMode\)/);
});

test("re-syncs the saved ui-mode once a new iframe reports ready", () => {
  // 桥接就绪才发送；实例变化后新 iframe 的首次 location 会让 bridgeReady 重新变 true，从而再次下发。
  assert.match(page, /postAiVideoUiModeSet\(frameRef\.current, iframeOrigin, uiMode\)/);
  assert.match(page, /\[uiMode, bridgeReady, instanceKey, iframeOrigin\]/);
});

test("persists ui-mode under its own storage key, not the global layout mode", () => {
  assert.equal(logic.AI_VIDEO_UI_MODE_STORAGE_KEY, "mainpg.aiVideo.uiMode");
  assert.match(page, /AI_VIDEO_UI_MODE_STORAGE_KEY = "mainpg\.aiVideo\.uiMode"/);
  assert.match(page, /localStorage\.getItem\(AI_VIDEO_UI_MODE_STORAGE_KEY\)/);
  assert.match(page, /localStorage\.setItem\(AI_VIDEO_UI_MODE_STORAGE_KEY, uiMode\)/);
  // 不得占用全局布局模式的键盘 "mainpg.uiMode"。
  assert.doesNotMatch(page, /"mainpg\.uiMode"/);
});

test("renders the two-segment 小白/导演 mode switch in the header", () => {
  assert.match(page, /ai-video-ui-mode-toggle/);
  assert.match(page, /小白模式/);
  assert.match(page, /导演模式/);
  assert.match(page, /aria-pressed=\{uiMode === "simple"\}/);
  assert.match(page, /aria-pressed=\{uiMode === "pro"\}/);
});

test("uiMode 首次握手由父侧权威推进：先下发、后接受 iframe 回报", () => {
  // 下发标记：父侧成功下发过一次 set 之前，绝不接受 iframe 的本地模式回报
  assert.match(page, /const uiModeSetSentRef = useRef\(false\)/);
  assert.match(page, /postAiVideoUiModeSet\(frameRef\.current, iframeOrigin, uiMode\);\s*uiModeSetSentRef\.current = true;/);
  assert.match(page, /data\.type === AI_VIDEO_UI_MODE_STATE_MESSAGE &&\s*isAiVideoUiMode\(data\.uiMode\) &&\s*uiModeSetSentRef\.current/);
});

test("换实例归零下发标记，等新 iframe 完成第一次握手后再接受回报", () => {
  assert.match(page, /uiModeSetSentRef\.current = false;[\s\S]*?\}, \[instanceKey\]\)/);
});
