import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const read = (path: string) => readFileSync(resolve(process.cwd(), path), "utf8");

const shell = read("src/components/app-shell.tsx");
const layout = read("src/app/layout.tsx");
const css = read("src/app/globals.css");
const startPage = read("src/app/start/page.tsx");

describe("MainPG embedded shell", () => {
  it("root layout 在服务端首屏读 x-mainpg-embed 并把 embedded 传给 AppShell", () => {
    expect(layout).toMatch(/from "next\/headers"/);
    expect(layout).toMatch(/headers\(\)/);
    // 冻结协议：middleware 写入的请求头 `x-mainpg-embed: 1`
    expect(layout).toMatch(/x-mainpg-embed/);
    expect(layout).toMatch(/EMBED_HEADER_NAME/);
    expect(layout).toMatch(/mainpg-embedded/);
    expect(layout).toMatch(/<AppShell embedded=\{embedded\}>/);
  });

  it("AppShell 用 props 决定内嵌，不再在 mount 后识别 location.search 或改 documentElement", () => {
    expect(shell).toMatch(/embedded: boolean/);
    expect(shell).not.toMatch(/searchParams\.get\("embed"\)/);
    expect(shell).not.toMatch(/document\.documentElement\.classList/);
    expect(shell).not.toMatch(/classList\.(add|remove|toggle)/);
  });

  it("内嵌时只输出 mainpg-embed-content，独立壳的侧栏 / Logo 不进入该分支", () => {
    expect(shell).toMatch(/if \(embedded\) \{\s*return <main className="mainpg-embed-content">\{children\}<\/main>;\s*\}/);
  });

  it("AppShell 承载 MainPG 双向 postMessage 通信桥", () => {
    expect(shell).toMatch(/mainpg:ai-video-navigate/);
    expect(shell).toMatch(/mainpg:ai-video-location/);
    // 只接受父窗口来源 + 白名单 href 的校验都来自桥接模块
    expect(shell).toMatch(/isFromEmbedParent\(event\.source, window\.parent\)/);
    expect(shell).toMatch(/isMainPgNavigateMessage\(event\.data\)/);
    expect(shell).toMatch(/window\.parent\.postMessage/);
  });

  it("globals.css 仍以内嵌命名空间承载 MainPG 主题", () => {
    expect(css).toMatch(/\.mainpg-embedded/);
    expect(css).toMatch(/\.mainpg-embedded \.cf-root/);
  });

  it("does not advertise Atlas Cloud beside the MainPG start action", () => {
    expect(startPage).not.toMatch(/reassureLead/);
    expect(startPage).not.toMatch(/<b>Atlas Cloud<\/b>/);
  });
});