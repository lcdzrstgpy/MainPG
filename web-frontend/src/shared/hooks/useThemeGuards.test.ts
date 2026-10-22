import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

// 背景（2026-10-22）：「水墨青黛采集页签有方框」修完发出去，用户刷新页面后回了句
// 「没呀」。排查发现主题 CSS 缓存在 localStorage，而版本比对只写在 downloadTheme
// 里（用户主动点主题才触发），启动时直接 applyTheme(缓存) 就完事了 —— 主题包改版后
// 老用户永远拿旧样式。这里锁住「启动时后台保鲜」这条路径。
const readUseTheme = () => readFileSync(new URL("./useTheme.ts", import.meta.url), "utf8");

test("已安装主题必须在启动时做版本保鲜，不能只靠用户主动点", () => {
  const src = readUseTheme();
  const fn = src.indexOf("async function refreshInstalledTheme");
  assert.ok(fn > -1, "应抽出「后台保鲜」函数（版本比对 + 换新 CSS）");

  const body = src.slice(fn, src.indexOf("\n}\n", fn));
  assert.ok(body.includes("remote.version === installed.version"), "必须比对远端版本");
  assert.ok(body.includes("fetchThemePackage(id)"), "版本变了要重新拉包");
  assert.ok(body.includes("saveDownloadedThemes"), "拉完要落盘，否则下次又拿旧的");

  // 启动路径：模块加载时（applyTheme 之后）就要跑一次
  const bootIdx = src.indexOf("applyTheme(currentTheme, false)");
  const refreshIdx = src.indexOf("void refreshInstalledTheme(currentTheme)");
  assert.ok(bootIdx > -1, "应保留「首帧先用缓存」的启动路径");
  assert.ok(
    refreshIdx > bootIdx,
    "启动后必须后台刷新一次，否则主题改版老用户永远看不到",
  );
});

test("downloadTheme 复用保鲜函数，不重复实现版本比对", () => {
  const src = readUseTheme();
  const start = src.indexOf("const downloadTheme = useCallback");
  assert.ok(start > -1, "应能找到 downloadTheme");
  const body = src.slice(start, src.indexOf("}, []);", start));
  assert.ok(body.includes("refreshInstalledTheme(id)"), "已安装分支应交给保鲜函数处理");
});
