import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(resolve(process.cwd(), path), "utf8");

const shell = read("src/components/app-shell.tsx");

/**
 * uiMode 双向同步桥的源契约断言：ui-mode-set 必须先在 onMessage 里验来源，
 * 再经过 isMainPgUiModeSetMessage 形状守卫才落到 setUiMode；任何非父窗口或
 * 越权 payload 都不能改 uiMode。ui-mode-state 回报只在非独立壳（iframed）时发出。
 */
describe("AppShell uiMode 桥接：来源与守卫", () => {
  it("ui-mode-set 严格排在 isFromEmbedParent 来源校验之后（非父来源被提前返回拦下）", () => {
    expect(
      shell,
    ).toMatch(/if \(!isFromEmbedParent\(event\.source, window\.parent\)\) return;[\s\S]*?isMainPgUiModeSetMessage\(event\.data\)/);
  });

  it("ui-mode-set 分支以 setUiMode 落地，且不干扰原 navigate 分支", () => {
    expect(shell).toMatch(/isMainPgUiModeSetMessage\(event\.data\)\) \{[\s\S]*?setUiMode\(event\.data\.uiMode\)/);
    // navigate 分支的原守卫与跳转仍然保留
    expect(shell).toMatch(/isMainPgNavigateMessage\(event\.data\)\) return;/);
    expect(shell).toMatch(/router\.push\(event\.data\.href\)/);
  });

  it("ui-mode-state 回报 effect 以 window.parent===window 作为独立壳闸门", () => {
    expect(
      shell,
    ).toMatch(/useEffect\(\(\) => \{\s*if \(window\.parent === window\) return;[\s\S]*?postMessage\(\{ type: MAINPG_EMBED_UI_MODE_STATE, uiMode \}, "\*"\)/);
  });

  it("挂载不抢先回报本地 uiMode：首条被跳过，只有真实变化才回报（避免覆盖父侧权威模式）", () => {
    expect(shell).toMatch(/const uiModeStateSent = useRef\(false\)/);
    expect(shell).toMatch(/if \(!uiModeStateSent\.current\) \{[\s\S]*?uiModeStateSent\.current = true;[\s\S]*?return;/);
  });
});