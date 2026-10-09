import { describe, expect, it } from "vitest";
import {
  MAINPG_EMBED_ALLOWED_HREFS,
  MAINPG_EMBED_LOCATION,
  MAINPG_EMBED_NAVIGATE,
  MAINPG_EMBED_UI_MODE_SET,
  MAINPG_EMBED_UI_MODE_STATE,
  isAllowedEmbedHref,
  isFromEmbedParent,
  isMainPgNavigateMessage,
  isMainPgUiModeSetMessage,
  isMainPgUiModeStateMessage,
  isUiModeValue,
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

describe("MainPG 内嵌桥：uiMode 双向同步协议常量", () => {
  it("消息类型字面量与 MainPG 侧约定一致", () => {
    expect(MAINPG_EMBED_UI_MODE_SET).toBe("mainpg:ai-video-ui-mode-set");
    expect(MAINPG_EMBED_UI_MODE_STATE).toBe("mainpg:ai-video-ui-mode-state");
  });

  it("isUiModeValue 只认 simple / pro（大小写与其它字符串一律拒绝）", () => {
    expect(isUiModeValue("simple")).toBe(true);
    expect(isUiModeValue("pro")).toBe(true);
    for (const v of ["easy", "", "PRO", "Simple", null, undefined, 1, true, {}, ["simple"]]) {
      expect(isUiModeValue(v), `不应放行 ${JSON.stringify(v)}`).toBe(false);
    }
  });
});

describe("MainPG 内嵌桥：uiMode 消息守卫", () => {
  it("ui-mode-set 只接受正确形状", () => {
    expect(isMainPgUiModeSetMessage({ type: MAINPG_EMBED_UI_MODE_SET, uiMode: "simple" })).toBe(true);
    expect(isMainPgUiModeSetMessage({ type: MAINPG_EMBED_UI_MODE_SET, uiMode: "pro" })).toBe(true);
  });

  it("ui-mode-set 拒绝错误 type / 越权 uiMode / 缺失字段 / 垃圾载荷", () => {
    expect(isMainPgUiModeSetMessage({ type: MAINPG_EMBED_UI_MODE_STATE, uiMode: "simple" })).toBe(false);
    expect(isMainPgUiModeSetMessage({ type: MAINPG_EMBED_NAVIGATE, href: "/start" })).toBe(false);
    expect(isMainPgUiModeSetMessage({ type: MAINPG_EMBED_UI_MODE_SET, uiMode: "easy" })).toBe(false);
    expect(isMainPgUiModeSetMessage({ type: MAINPG_EMBED_UI_MODE_SET, uiMode: "PRO" })).toBe(false);
    expect(isMainPgUiModeSetMessage({ type: MAINPG_EMBED_UI_MODE_SET, uiMode: 1 })).toBe(false);
    expect(isMainPgUiModeSetMessage({ type: MAINPG_EMBED_UI_MODE_SET })).toBe(false);
    expect(isMainPgUiModeSetMessage({ uiMode: "simple" })).toBe(false);
    expect(isMainPgUiModeSetMessage({ type: MAINPG_EMBED_UI_MODE_SET, uiMode: { simple: true } })).toBe(false);
    expect(isMainPgUiModeSetMessage("mainpg:ai-video-ui-mode-set")).toBe(false);
    expect(isMainPgUiModeSetMessage(null)).toBe(false);
    expect(isMainPgUiModeSetMessage(undefined)).toBe(false);
    expect(isMainPgUiModeSetMessage(42)).toBe(false);
    expect(isMainPgUiModeSetMessage([{ type: MAINPG_EMBED_UI_MODE_SET, uiMode: "pro" }])).toBe(false);
  });

  it("ui-mode-state 只接受正确形状", () => {
    expect(isMainPgUiModeStateMessage({ type: MAINPG_EMBED_UI_MODE_STATE, uiMode: "simple" })).toBe(true);
    expect(isMainPgUiModeStateMessage({ type: MAINPG_EMBED_UI_MODE_STATE, uiMode: "pro" })).toBe(true);
  });

  it("ui-mode-state 拒绝错误 type / 越权 uiMode / 缺失字段 / 垃圾载荷", () => {
    expect(isMainPgUiModeStateMessage({ type: MAINPG_EMBED_UI_MODE_SET, uiMode: "pro" })).toBe(false);
    expect(isMainPgUiModeStateMessage({ type: MAINPG_EMBED_NAVIGATE, href: "/start" })).toBe(false);
    expect(isMainPgUiModeStateMessage({ type: MAINPG_EMBED_UI_MODE_STATE, uiMode: "easy" })).toBe(false);
    expect(isMainPgUiModeStateMessage({ type: MAINPG_EMBED_UI_MODE_STATE, uiMode: "" })).toBe(false);
    expect(isMainPgUiModeStateMessage({ type: MAINPG_EMBED_UI_MODE_STATE })).toBe(false);
    expect(isMainPgUiModeStateMessage({ uiMode: "pro" })).toBe(false);
    expect(isMainPgUiModeStateMessage(null)).toBe(false);
    expect(isMainPgUiModeStateMessage(undefined)).toBe(false);
    expect(isMainPgUiModeStateMessage({ type: MAINPG_EMBED_UI_MODE_STATE, uiMode: ["pro"] })).toBe(false);
  });
});
