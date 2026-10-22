import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// 背景（2026-10-16）：用户反馈「重新连接不好用」+「界面还开着却弹出掉线」。排查结论：
// ① 单次网络层失败就弹整屏遮罩，开发态 vite 代理抖动即误报；
// ② mainpg:// 协议注册的是「最后启动的程序」（本地开发时指向已安装的 MainPG.exe），
//    dev 点「重新连接」会静默拉起安装版并换掉数据源；
// ③ 有任务在跑时静默收起遮罩，用户以为按钮没反应。
const readClient = () => readFileSync(new URL("./http/client.ts", import.meta.url), "utf8");
const readNotice = () =>
  readFileSync(new URL("../shared/components/BackendOfflineNotice.tsx", import.meta.url), "utf8");
const readGlobalCss = () =>
  readFileSync(new URL("../shared/styles/global.css", import.meta.url), "utf8");

test("失联判定必须经过确认窗口，不允许单次失败就直接弹遮罩", () => {
  const src = readClient();
  const m = src.match(/BACKEND_OFFLINE_CONFIRM_MS = ([\d_]+)/);
  const confirmMs = m ? Number(m[1].replace(/_/g, "")) : 0;
  // 2026-10-22：门槛从 3s 抬到 10s。用户的办公机配置普遍不高，跑 AI 出图 / POD
  // 渲染时后端十几秒回不了一次请求是常态（是「忙」不是「死」），6s 窗口会被正常
  // 负载凑满，表现为「一处理任务就弹掉线」。
  assert.ok(confirmMs >= 10000, `失联确认窗口应 >= 10000ms（实际 ${confirmMs}）`);

  const start = src.indexOf("function notifyBackendOffline");
  assert.ok(start > -1, "client.ts 应有 notifyBackendOffline");
  const body = src.slice(start, src.indexOf("\n}", start));
  assert.ok(body.includes("setTimeout"), "notifyBackendOffline 必须延迟判定（setTimeout）");
  assert.ok(
    body.includes("dispatchEvent") && body.includes("BACKEND_OFFLINE_EVENT"),
    "定时器回调仍需广播离线事件",
  );
});

test("有任务在跑时不得弹整屏遮罩，只能降级成提示条", () => {
  const notice = readNotice();
  const start = notice.indexOf("const confirmOffline = async");
  const body = notice.slice(start, notice.indexOf("\n    };", start));
  assert.ok(body.includes("hasRunningTask()"), "confirmOffline 要判断有没有任务在跑");
  const degradedIdx = body.indexOf("setDegraded(true)");
  const showIdx = body.indexOf("showOffline()");
  assert.ok(degradedIdx > -1, "有任务时应走降级分支（setDegraded）");
  assert.ok(
    degradedIdx < showIdx,
    "降级分支必须在 showOffline 之前 return，否则遮罩照样糊住用户",
  );
  assert.ok(notice.includes("backend-resume-toast--warn"), "应渲染降级提示条");
});

test("有处理任务在跑时失联窗口必须再放宽，避免把满载误判成掉线", () => {
  const src = readClient();
  const idle = src.match(/BACKEND_OFFLINE_CONFIRM_MS = ([\d_]+)/);
  const busy = src.match(/BACKEND_OFFLINE_CONFIRM_BUSY_MS = ([\d_]+)/);
  assert.ok(idle && busy, "client.ts 应同时定义空闲/忙碌两档失联确认窗口");
  const idleMs = Number(idle![1].replace(/_/g, ""));
  const busyMs = Number(busy![1].replace(/_/g, ""));
  assert.ok(idleMs >= 10_000, `空闲窗口应 >= 10000ms（实际 ${idleMs}）`);
  assert.ok(busyMs > idleMs, "忙碌窗口必须大于空闲窗口");
  assert.ok(
    src.includes("offlineConfirmWindowMs()"),
    "定时器必须按运行中任务动态取窗口",
  );
});

test("开发态「重新连接」不得拉起 mainpg:// 协议", () => {
  const src = readNotice();
  const start = src.indexOf("const retry = async");
  const end = src.indexOf("const buttonLabel", start);
  assert.ok(start > -1 && end > start, "应能找到 retry 函数体");
  const body = src.slice(start, end);
  const devIdx = body.indexOf("import.meta.env.DEV");
  const hrefIdx = body.indexOf("MAINPG_RECOVER_URL");
  assert.ok(devIdx > -1, "retry 必须有 DEV 环境守卫");
  assert.ok(hrefIdx > devIdx, "DEV 守卫必须在拉起协议之前 return");
});

test("有运行中任务不整页刷新时，必须给出可见反馈", () => {
  const src = readNotice();
  const idx = src.indexOf("if (hasRunningTask())");
  assert.ok(idx > -1, "应保留「有任务不整页刷新」守门");
  const body = src.slice(idx, idx + 240);
  assert.ok(body.includes("setResumeNotice(true)"), "守门分支应点亮恢复提示条");
  assert.ok(src.includes(`className="backend-resume-toast"`), "应渲染恢复提示条");
  assert.ok(src.includes("RESUME_NOTICE_MS"), "提示条应自动消失");
});

test("恢复提示条压在页面之上，底色必须用实心变量", () => {
  const css = readGlobalCss();
  const idx = css.indexOf(".backend-resume-toast {");
  assert.ok(idx > -1, "global.css 应定义 .backend-resume-toast");
  const block = css.slice(idx, css.indexOf("}", idx));
  assert.ok(
    block.includes("var(--theme-card-bg-solid"),
    "提示条底色应走 --theme-card-bg-solid（peach/alpine 的半透明变量会被洗掉）",
  );
  assert.ok(block.includes("z-index"), "提示条需显式 z-index 才能压在内容之上");
});

test("探活成功必须复位失联状态机，否则后续掉线不再弹提示", () => {
  const src = readClient();
  assert.ok(
    /export function resetBackendOfflineState\(\)/.test(src),
    "client.ts 应导出 resetBackendOfflineState 供探活复位",
  );
  const notice = readNotice();
  const probeStart = notice.indexOf("async function probeHealth");
  const componentStart = notice.indexOf("export function BackendOfflineNotice");
  assert.ok(probeStart > -1 && componentStart > probeStart, "应能找到 probeHealth 函数体");
  const probe = notice.slice(probeStart, componentStart);
  assert.ok(
    /if \(healthy\) resetBackendOfflineState\(\)/.test(probe),
    "probeHealth 探测成功时必须复位失联状态机",
  );
});

// --------------------------------------------------------------------------- //
// 2026-10-19：「很容易就弹掉线」——判定过敏感的三处结构性原因
// --------------------------------------------------------------------------- //
test("弹整屏遮罩前必须主动探活，不能只凭「窗口内没成功」", () => {
  const notice = readNotice();
  const start = notice.indexOf("const confirmOffline = async");
  assert.ok(start > -1, "BackendOfflineNotice 应有 confirmOffline（弹前探活）");
  const body = notice.slice(start, notice.indexOf("\n    };", start));
  // 2026-10-22：探活改带超时参数（确认场景 6s / 重试 3s，比自动探活宽松），
  // 所以这里只匹配调用本身，不再锁死无参形式。
  const probeIdx = body.search(/await probeHealth\(/);
  const showIdx = body.indexOf("showOffline()");
  assert.ok(probeIdx > -1, "confirmOffline 必须先探活");
  assert.ok(showIdx > probeIdx, "showOffline 只能在探活失败之后调用");
  assert.ok(
    body.includes("onlineEpochRef.current !== epoch"),
    "探活是异步的，回来时要确认期间没有被恢复事件翻盘",
  );
});

test("所有传输层都必须能复位状态机（产品处理与二进制下载不能例外）", () => {
  const src = readClient();
  assert.ok(
    /export function noteBackendReachable\(\)/.test(src),
    "client.ts 应导出 noteBackendReachable 供其它传输层复位",
  );
  assert.ok(
    /export function noteBackendUnreachable\(\)/.test(src),
    "client.ts 应导出 noteBackendUnreachable 供其它传输层上报失联",
  );

  // httpBlob 成功路径必须复位
  const blobStart = src.indexOf("export async function httpBlob");
  assert.ok(blobStart > -1, "应能找到 httpBlob");
  const blobBody = src.slice(blobStart, src.indexOf("\n}", blobStart));
  assert.ok(
    blobBody.includes("noteBackendReachable()"),
    "httpBlob 下载成功也必须复位失联状态机",
  );

  // 产品处理自带的 fetch 封装同样要接进来
  const pp = readFileSync(
    new URL("../modules/product_processing/api/client.ts", import.meta.url),
    "utf8",
  );
  assert.ok(pp.includes("noteBackendReachable"), "产品处理通道成功时要复位状态机");
  assert.ok(pp.includes("noteBackendUnreachable"), "产品处理通道网络层失败时要上报失联");
  const catchIdx = pp.indexOf("if (error instanceof TypeError) noteBackendUnreachable()");
  assert.ok(catchIdx > -1, "只在 TypeError（连接被拒）时上报，超时不算掉线");
});

test("开发态只把「代理替答」当成失联，纯文本 500 不再误判", () => {
  const src = readClient();
  const start = src.indexOf("function isBackendUnreachable");
  assert.ok(start > -1, "应能找到 isBackendUnreachable");
  const body = src.slice(start, src.indexOf("\n}", start));
  assert.ok(
    body.includes("PROXY_ERROR_HINT"),
    "应只认 ECONNREFUSED / socket hang up 这类传输层文案",
  );
  assert.ok(
    /\[500, 502, 503, 504\]/.test(body),
    "状态码应收紧到网关类错误",
  );
});

test("被浏览器捡回来的旧页面必须能自愈（bfcache / 后台节流）", () => {
  const notice = readNotice();
  // 用户「叉掉浏览器再点启动，还是那个掉线弹窗」= 浏览器把冻结的旧 DOM 捡了回来，
  // 自动探活的定时器在 bfcache / 后台节流下可能根本没跑。
  assert.ok(notice.includes('"pageshow"'), "必须监听 pageshow（bfcache 恢复）");
  assert.ok(notice.includes("event.persisted"), "只对 bfcache 恢复的旧页面特殊处理");
  assert.ok(notice.includes('"visibilitychange"'), "切回前台必须立刻探活一次");
});

test("掉线时自动拉起一次，且绝不无限重试", () => {
  const notice = readNotice();
  const fnStart = notice.indexOf("const tryAutoRecover = useCallback");
  assert.ok(fnStart > -1, "应有自动恢复入口（后端崩掉时只有浏览器能把它拉回来）");
  const body = notice.slice(fnStart, notice.indexOf("}, []);", fnStart));
  assert.ok(body.includes("import.meta.env.DEV"), "开发态不得拉起 mainpg://（会拉起安装版换掉数据源）");
  assert.ok(
    body.includes("autoRecoverTriedRef.current) return"),
    "自动恢复必须只做一次，否则杀软反复终止主程序时会形成重启循环",
  );
  assert.ok(
    notice.includes("if (!offline) return;"),
    "只在确认掉线（无任务在跑）时自动拉起，有任务时后端多半是忙不是死",
  );
});
