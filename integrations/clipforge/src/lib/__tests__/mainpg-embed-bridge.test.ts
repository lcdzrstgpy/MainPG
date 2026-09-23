import { describe, expect, it } from "vitest";
import {
  MAINPG_EMBED_ALLOWED_HREFS,
  MAINPG_EMBED_LOCATION,
  MAINPG_EMBED_NAVIGATE,
  isAllowedEmbedHref,
  isFromEmbedParent,
  isMainPgNavigateMessage,
} from "@/lib/mainpg-embed-bridge";

/**
 * postMessage 协议的安全边界：消息形状固定，href 必须精确命中七个模块入口，
 * 来源必须是父窗口。任何放宽都会让 iframe 变成可被任意页面遥控的导航器。
 */
describe("MainPG 内嵌桥：消息形状常量", () => {
  it("消息类型与 MainPG 侧约定的字面量一致", () => {
    expect(MAINPG_EMBED_NAVIGATE).toBe("mainpg:ai-video-navigate");
    expect(MAINPG_EMBED_LOCATION).toBe("mainpg:ai-video-location");
  });
});

describe("MainPG 内嵌桥：允许的模块入口", () => {
  it("七个模块入口全部放行", () => {
    expect([...MAINPG_EMBED_ALLOWED_HREFS]).toEqual([
      "/start",
      "/projects",
      "/products",
      "/presenters",
      "/project/clone",
      "/batch",
      "/settings",
    ]);
    for (const href of MAINPG_EMBED_ALLOWED_HREFS) {
      expect(isAllowedEmbedHref(href), `应放行 ${href}`).toBe(true);
    }
  });

  it("非白名单 href 一律拒绝", () => {
    for (const href of [
      "/project/abc",
      "/project/new",
      "/media-lab",
      "javascript:alert(1)",
      "https://evil.example.com/start",
      "/start?embed=standalone",
      "/",
      "",
    ]) {
      expect(isAllowedEmbedHref(href), `不应放行 ${JSON.stringify(href)}`).toBe(false);
    }
  });

  it("非字符串一律拒绝", () => {
    for (const value of [null, undefined, 1, true, {}, ["/start"]]) {
      expect(isAllowedEmbedHref(value), `不应放行 ${JSON.stringify(value)}`).toBe(false);
    }
  });
});

describe("MainPG 内嵌桥：消息守卫", () => {
  it("只接受 navigate 形状 + 白名单 href", () => {
    expect(isMainPgNavigateMessage({ type: MAINPG_EMBED_NAVIGATE, href: "/projects" })).toBe(true);
    expect(isMainPgNavigateMessage({ type: MAINPG_EMBED_NAVIGATE, href: "/project/clone" })).toBe(true);
  });

  it("形状不符或 href 越权一律拒绝", () => {
    expect(isMainPgNavigateMessage({ type: MAINPG_EMBED_LOCATION, pathname: "/projects" })).toBe(false);
    expect(isMainPgNavigateMessage({ type: MAINPG_EMBED_NAVIGATE, href: "/project/abc" })).toBe(false);
    expect(isMainPgNavigateMessage({ type: MAINPG_EMBED_NAVIGATE })).toBe(false);
    expect(isMainPgNavigateMessage({ href: "/projects" })).toBe(false);
    expect(isMainPgNavigateMessage({ type: MAINPG_EMBED_NAVIGATE, href: 1 })).toBe(false);
    expect(isMainPgNavigateMessage("mainpg:ai-video-navigate")).toBe(false);
    expect(isMainPgNavigateMessage(null)).toBe(false);
    expect(isMainPgNavigateMessage(undefined)).toBe(false);
    expect(isMainPgNavigateMessage(42)).toBe(false);
  });

  it("只信任 window.parent 来源", () => {
    const parent = { name: "parent" };
    expect(isFromEmbedParent(parent, parent)).toBe(true);
    expect(isFromEmbedParent({ name: "evil" }, parent)).toBe(false);
    expect(isFromEmbedParent(null, parent)).toBe(false);
    expect(isFromEmbedParent(undefined, undefined)).toBe(false);
  });
});