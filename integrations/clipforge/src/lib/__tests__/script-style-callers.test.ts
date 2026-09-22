import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveScriptStyle, SCRIPT_STYLE_VALUES } from "@/lib/script-style";

/**
 * `/api/llm/script` 改造为「必须显式选风格」后，调用方不得再默认发送 auto：
 * auto/空 + 历史样本不足会返回 409 `needs_explicit_style`。
 *
 * 仓库没有组件渲染器（无 @testing-library/react），非入口页的调用方沿用 start-entry.test.ts
 * 的源码契约风格，守住三件事：
 * 1) 非交互入口（CLI / MCP / Infinite Canvas 节点）与爆款复刻页的默认风格必须是白名单里的显式
 *    UI 值，不能是 auto，也不能是痛点种草；
 * 2) 收到 409 时必须把候选风格带进错误信息，不得当成普通「生成失败」吞掉；
 * 3) batch / clone 复用共享解析器 parseStyleRequirement（与 /start、/project/new 同一份）。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const cli = read("bin/clipforge.mjs");
const mcp = read("mcp/clipforge-mcp.mjs");
const canvasNode = read("integrations/infinite-canvas/clipforge-node/src/index.tsx");
const clonePage = read("src/app/project/clone/page.tsx");
const batchPage = read("src/app/batch/page.tsx");
const batchMessages = read("src/lib/i18n/messages/batch.ts");
const cloneMessages = read("src/lib/i18n/messages/clone.ts");

/** 从源码读出默认风格的字符串字面量；常量名由本测试固定，改名即失败——这是有意的契约。 */
const literalOf = (source: string, name: string): string => {
  const match = source.match(new RegExp(`const ${name} = "([^"]+)"`));
  expect(match, `${name} 必须在源码中定义为字符串常量`).not.toBeNull();
  return match![1];
};

describe("调用方默认风格必须是显式合法 UI 值", () => {
  it("CLI / MCP 的默认风格在白名单内，且不是 auto / pain-point", () => {
    for (const source of [cli, mcp]) {
      const style = literalOf(source, "DEFAULT_SCRIPT_STYLE");
      expect(SCRIPT_STYLE_VALUES).toContain(style);
      expect(style).not.toBe("auto");
      expect(style).not.toBe("pain-point");
      // 白名单常量必须真正参与取值判断，而不是摆设
      expect(source).toMatch(/SCRIPT_STYLE_INPUTS\.includes\(/);
    }
  });

  it("爆款复刻页与 Infinite Canvas 节点不再硬编码 auto", () => {
    expect(literalOf(clonePage, "CLONE_SCRIPT_STYLE")).toBe("scenario");
    expect(literalOf(canvasNode, "SCRIPT_STYLE")).toBe("scenario");
    expect(clonePage).not.toMatch(/styleType: "auto"/);
    expect(canvasNode).not.toMatch(/styleType: "auto"/);
    expect(clonePage).toMatch(/styleType: CLONE_SCRIPT_STYLE/);
    expect(canvasNode).toMatch(/styleType: SCRIPT_STYLE/);
  });

  it("这些默认值经服务端解析都不会命中 409", () => {
    const defaults = [
      literalOf(cli, "DEFAULT_SCRIPT_STYLE"),
      literalOf(mcp, "DEFAULT_SCRIPT_STYLE"),
      literalOf(clonePage, "CLONE_SCRIPT_STYLE"),
      literalOf(canvasNode, "SCRIPT_STYLE"),
    ];
    for (const style of defaults) {
      expect(resolveScriptStyle({ requestedStyle: style })).toEqual({
        kind: "resolved",
        styleType: style,
        styleSource: "explicit",
      });
    }
  });

  it("CLI 的 --style 白名单同时接受 UI 与引擎拼写，并保留 auto 作为显式选项", () => {
    const accepted = cli.match(/const SCRIPT_STYLE_INPUTS = \[([\s\S]*?)\];/)?.[1] ?? "";
    for (const style of [...SCRIPT_STYLE_VALUES, "pain_point", "scene", "auto"]) {
      expect(accepted).toContain(`"${style}"`);
    }
  });
});

describe("调用方都处理 409 needs_explicit_style", () => {
  it("CLI / MCP / Canvas 节点把候选风格带进错误信息", () => {
    for (const source of [cli, mcp, canvasNode]) {
      expect(source).toMatch(/needs_explicit_style/);
      expect(source).toMatch(/candidates/);
    }
  });

  it("batch / clone 复用共享解析器并渲染候选风格", () => {
    for (const page of [batchPage, clonePage]) {
      expect(page).toMatch(/parseStyleRequirement/);
      expect(page).toMatch(/errorNeedsExplicitStyle/);
    }
    // 文案 key 必须真实存在且带 {candidates} 占位符，否则页面上会渲染出原始 key
    for (const messages of [batchMessages, cloneMessages]) {
      expect(messages).toMatch(/errorNeedsExplicitStyle:/);
      expect(messages).toMatch(/\{candidates\}/);
    }
  });
});
