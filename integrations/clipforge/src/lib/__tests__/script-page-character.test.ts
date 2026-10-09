import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveScriptCharacter } from "@/app/project/[id]/script/page";

/**
 * P1（角色绑定）：创建时选中的主播必须在后续生图/生视频阶段仍然生效。
 *
 * 现状是脚本页只读 URL 参数 `?presenter=`，而创建后的跳转链接根本不带它，角色就丢了。
 * 仓库没有组件渲染器（无 @testing-library/react），所以把优先级决策抽成纯函数直接单测，
 * 页面接线用源码契约钉住（与 script-page-strategy.test.ts 同一风格）。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");
const scriptPage = read("src/app/project/[id]/script/page.tsx");

describe("resolveScriptCharacter（主播来源优先级）", () => {
  it("项目记录的 characterId 最优先", () => {
    expect(resolveScriptCharacter({ characterId: "project-char" }, { characterId: "brief-char" }, "url-char")).toBe(
      "project-char"
    );
  });

  it("项目没有绑定时落到项目创作简报", () => {
    expect(resolveScriptCharacter({ characterId: "" }, { characterId: "brief-char" }, "url-char")).toBe("brief-char");
    expect(resolveScriptCharacter(null, { characterId: "brief-char" }, "url-char")).toBe("brief-char");
  });

  it("前两者都为空时才用 URL ?presenter= 兜底旧链接", () => {
    expect(resolveScriptCharacter(null, null, "url-char")).toBe("url-char");
    expect(resolveScriptCharacter({ characterId: "   " }, { characterId: "" }, "url-char")).toBe("url-char");
  });

  it("全空返回 undefined 而不是空串（找不到就是没选主播）", () => {
    expect(resolveScriptCharacter(null, null, "")).toBeUndefined();
    expect(resolveScriptCharacter(null, null, null)).toBeUndefined();
    expect(resolveScriptCharacter({ characterId: " " }, {}, "  ")).toBeUndefined();
  });

  it("两侧空白被裁掉，避免带空格的 id 查不到角色", () => {
    expect(resolveScriptCharacter({ characterId: "  project-char  " }, null, null)).toBe("project-char");
    expect(resolveScriptCharacter(null, { characterId: "  brief-char  " }, null)).toBe("brief-char");
  });
});

describe("脚本页的角色接线（源码契约）", () => {
  it("projectMeta 带上创建时绑定的 characterId（两处读取都要带）", () => {
    expect(scriptPage.match(/characterId: proj\.characterId \?\? ""/g) ?? []).toHaveLength(2);
  });

  it("角色查找收敛为单一解析来源（只用 presenterId，不再只读 URL 参数）", () => {
    expect(scriptPage).toMatch(
      /const presenterId = resolveScriptCharacter\(projectMeta, creationBrief, presenterParam\)/
    );
    const lookups = scriptPage.match(/presenterLib\.find\(\(c\) => c\.id === (\w+)\)/g) ?? [];
    // 预览与提交共用组件顶部解析出的同一个 presenter，不再各自查一次（避免第二套角色解析来源）
    expect(lookups).toHaveLength(1);
    for (const lookup of lookups) {
      expect(lookup).toContain("presenterId");
      expect(lookup).not.toContain("presenterParam");
    }
    expect(scriptPage.match(/useCharacterStore\(\)/g) ?? []).toHaveLength(1);
  });

  it("?presenter= 仍被读取，作为旧链接的后备", () => {
    expect(scriptPage).toMatch(/qs\.get\("presenter"\)/);
  });

  it("topic 项目的脚本链路由项目的 contentType 决定，不靠别的东西猜", () => {
    expect(scriptPage).toMatch(/const isTopic = projectMeta\.contentType === "topic"/);
    expect(scriptPage).toMatch(/isTopic \? "\/api\/topic\/script" : "\/api\/llm\/script"/);
  });
});
