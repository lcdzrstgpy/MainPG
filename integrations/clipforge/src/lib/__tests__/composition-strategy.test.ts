import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { DEFAULT_CREATION_BRIEF } from "@/lib/creation-brief";
import {
  compositionStrategyFromBrief,
  compositionStrategyOf,
  groupCompositionsByStrategy,
} from "@/lib/project-detail-view";

/**
 * 成片记录的显式出片策略（迁移 0021 + compose / storyboard-film 写入 + 导出页严格分组）。
 *
 * 仓库没有 @testing-library/react，沿用既有「纯函数直测 + 源码契约」风格
 * （见 src/lib/__tests__/project-detail-brief.test.ts）。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const composeRoute = read("src/app/api/project/[id]/compose/route.ts");
const filmRoute = read("src/app/api/project/[id]/storyboard-film/route.ts");

describe("compositionStrategyFromBrief（写入侧的策略来源）", () => {
  it("读出项目简报里的 outputStrategy", () => {
    expect(compositionStrategyFromBrief({ ...DEFAULT_CREATION_BRIEF, outputStrategy: "controlled-motion" }))
      .toBe("controlled-motion");
    expect(compositionStrategyFromBrief({ outputStrategy: "native-film" })).toBe("native-film");
    expect(compositionStrategyFromBrief({ outputStrategy: "draft" })).toBe("draft");
  });

  it("旧项目（简报为 null/undefined）不猜策略", () => {
    expect(compositionStrategyFromBrief(null)).toBeNull();
    expect(compositionStrategyFromBrief(undefined)).toBeNull();
  });

  it("结构异常或非法枚举值一律 null，不抛错", () => {
    expect(compositionStrategyFromBrief("controlled-motion")).toBeNull();
    expect(compositionStrategyFromBrief([])).toBeNull();
    expect(compositionStrategyFromBrief({})).toBeNull();
    expect(compositionStrategyFromBrief({ outputStrategy: null })).toBeNull();
    expect(compositionStrategyFromBrief({ outputStrategy: "ai-film" })).toBeNull();
  });
});

describe("groupCompositionsByStrategy：有 strategy 列时严格分组", () => {
  it("同一 label 也按 strategy 严格区分 draft 与 controlled-motion", () => {
    // 两条成片的标签互相说谎：只有 strategy 列能证明各自策略
    const draftRow = { id: "draft-1", strategy: "draft" as const, label: "逐镜动态合成 · Seedance" };
    const motionRow = { id: "motion-1", strategy: "controlled-motion" as const, label: "免费草稿 · 静态合成" };

    const forDraft = groupCompositionsByStrategy([draftRow, motionRow], "draft");
    expect(forDraft.primary.map((c) => c.id)).toEqual(["draft-1"]);
    expect(forDraft.others.map((c) => c.id)).toEqual(["motion-1"]);

    const forMotion = groupCompositionsByStrategy([draftRow, motionRow], "controlled-motion");
    expect(forMotion.primary.map((c) => c.id)).toEqual(["motion-1"]);
    expect(forMotion.others.map((c) => c.id)).toEqual(["draft-1"]);
  });

  it("strategy 列优先于 label：标签与列冲突时以列为准", () => {
    expect(compositionStrategyOf({ strategy: "controlled-motion", label: "九宫格整片 · Seedance" }))
      .toBe("controlled-motion");
    expect(compositionStrategyOf({ strategy: "draft", label: "原生整片" })).toBe("draft");
    expect(compositionStrategyOf({ strategy: "native-film", label: "免费草稿" })).toBe("native-film");
  });

  it("strategy 为 null 的历史成片仍按 label 粗判归类，不被丢弃", () => {
    const legacyDraft = { id: "legacy-draft", strategy: null, label: "免费草稿 · 静态合成" };
    const legacyFilm = { id: "legacy-film", label: "九宫格整片 · Seedance 2.5" };
    const legacyUnlabeled = { id: "legacy-none", strategy: null, label: null };

    const groups = groupCompositionsByStrategy([legacyUnlabeled, legacyFilm, legacyDraft], "native-film");
    expect(groups.primary.map((c) => c.id)).toEqual(["legacy-film"]);
    expect(groups.others.map((c) => c.id)).toEqual(["legacy-none", "legacy-draft"]);
    expect(compositionStrategyOf(legacyDraft)).toBe("draft");
    expect(compositionStrategyOf(legacyUnlabeled)).toBeNull();
  });

  it("项目 outputStrategy 为 null（旧项目）时不抛错：主版本为空、全部归入其他版本", () => {
    const rows = [{ id: "a", strategy: "draft" as const }, { id: "b", strategy: null }];
    expect(groupCompositionsByStrategy(rows, null)).toEqual({ primary: [], others: rows });
    expect(groupCompositionsByStrategy(rows, undefined).primary).toEqual([]);
  });
});

describe("合成路由写入 strategy（源码契约）", () => {
  const insertBlock = composeRoute.slice(
    composeRoute.indexOf(".insert(compositions)"),
    composeRoute.indexOf(".returning();")
  );

  it("策略来自项目创作简报，读不到时为 null（不瞎猜）", () => {
    expect(composeRoute).toMatch(
      /import \{ compositionStrategyFromBrief \} from "@\/lib\/project-detail-view"/
    );
    expect(composeRoute).toMatch(/const recordedStrategy = compositionStrategyFromBrief\(project\.creationBrief\)/);
  });

  it("插入成片记录时带上 strategy，且既有写入行为不变", () => {
    expect(insertBlock).toMatch(/strategy: recordedStrategy/);
    // resolve / aspectRatio / label / aigcBadge / status 保持原样
    expect(insertBlock).toMatch(/resolution: outputCfg\.resolution/);
    expect(insertBlock).toMatch(/aspectRatio: outputCfg\.aspectRatio/);
    expect(insertBlock).toMatch(/aigcBadge/);
    expect(insertBlock).toMatch(/\.\.\.\(label && \{ label \}\)/);
    expect(insertBlock).toMatch(/status: "composing"/);
  });
});

describe("storyboard-film 写入原生整片策略（源码契约）", () => {
  const persistBlock = filmRoute.slice(
    filmRoute.indexOf("async function persistFilm"),
    filmRoute.indexOf("return { url: `/api/output/${projectId}/${fileName}`")
  );

  it("原生整片记录写入 strategy: \"native-film\"", () => {
    expect(persistBlock).toMatch(/strategy: "native-film"/);
  });

  it("既有字段（projectId/outputPath/label/status）保持不变", () => {
    expect(persistBlock).toMatch(/projectId,/);
    expect(persistBlock).toMatch(/outputPath,/);
    expect(persistBlock).toMatch(/label: `九宫格整片/);
    expect(persistBlock).toMatch(/status: "done"/);
  });
});

describe("导出页接线（源码契约）", () => {
  const exportPage = read("src/app/project/[id]/export/page.tsx");

  it("成片行的 strategy 列透传给严格分组器，主版本仍按项目 outputStrategy 挑", () => {
    expect(exportPage).toMatch(/strategy\?: string \| null/);
    expect(exportPage).toMatch(/groupCompositionsByStrategy\(history, briefStrategy\)/);
    expect(exportPage).toMatch(/groups\.primary\[0\] \?\? list\[0\]/);
  });
});
