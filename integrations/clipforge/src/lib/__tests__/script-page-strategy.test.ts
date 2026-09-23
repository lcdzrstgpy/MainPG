import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { shouldAutoStartPipeline } from "@/app/project/[id]/script/page";
import {
  NEEDS_EXPLICIT_STYLE_CODE,
  parseStyleRequirement,
} from "@/components/project-creation/script-style-requirement";

/**
 * 仓库没有组件渲染器（无 @testing-library/react），脚本页的行为用「纯函数直测 + 源码契约」断言，
 * 与 src/components/project-creation/__tests__/start-entry.test.ts 的既有风格一致。
 *
 * 覆盖计划 Task 2.2 / Task 3.1 的脚本页部分与设计 §6.2 / §7.4。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const scriptPage = read("src/app/project/[id]/script/page.tsx");
const assetsPage = read("src/app/project/[id]/assets/page.tsx");
const requirementModule = read("src/components/project-creation/script-style-requirement.ts");
const promptModule = read("src/components/project-creation/style-choice-prompt.tsx");

/** ?auto=1 自动启动那一段 effect 的主体（到依赖数组为止）。 */
const autoStartEffectBody = () => {
  const fromComment = scriptPage.slice(scriptPage.indexOf("?auto=1 fresh start"));
  return fromComment.slice(0, fromComment.indexOf("}, [autoMode"));
};

describe("shouldAutoStartPipeline（出片策略门控，设计 §7.4）", () => {
  it("新项目即使选择免费草稿也不会因 URL 参数自动开跑", () => {
    expect(shouldAutoStartPipeline({ outputStrategy: "draft", autoParam: true })).toBe(false);
  });

  it("controlled-motion / native-film 一律不许自动启动免费流水线", () => {
    expect(shouldAutoStartPipeline({ outputStrategy: "controlled-motion", autoParam: true })).toBe(false);
    expect(shouldAutoStartPipeline({ outputStrategy: "native-film", autoParam: true })).toBe(false);
  });

  it("旧项目（没有 creationBrief → null）保留原 ?auto=1 断点恢复行为", () => {
    expect(shouldAutoStartPipeline({ outputStrategy: null, autoParam: true })).toBe(true);
  });

  it("没有 auto 参数时任何策略都不启动", () => {
    const strategies = [null, "draft", "controlled-motion", "native-film"] as const;
    for (const outputStrategy of strategies) {
      expect(shouldAutoStartPipeline({ outputStrategy, autoParam: false })).toBe(false);
    }
  });
});

describe("脚本页：自动出片只保留给旧项目断点恢复", () => {
  it("自动启动判据是 outputStrategy 与 URL auto 参数的组合", () => {
    const body = autoStartEffectBody();
    expect(body).toMatch(/shouldAutoStartPipeline\(\{ outputStrategy, autoParam: autoMode \}\)/);
    expect(body).toMatch(/if \(!shouldAutoStartPipeline/);
    // 门控在前、启动在后：任何带创作简报的新项目在到达 autoFinish 前都会返回。
    expect(body.indexOf("shouldAutoStartPipeline")).toBeLessThan(body.indexOf("autoFinish()"));
    expect(body).toMatch(/autoFinish\(\)/);
  });

  it("简报还没读到时门控不生效，避免把受控动态误当成旧项目自动跑", () => {
    const body = autoStartEffectBody();
    expect(body).toMatch(/!briefLoaded/);
    expect(scriptPage).toMatch(/\[autoMode, autoModeTriggered, loading, briefLoaded, outputStrategy/);
  });

  it("接管进度卡片只在免费链确实适用时出现", () => {
    expect(scriptPage).toMatch(/const freeChainApplies = shouldAutoStartPipeline\(\{ outputStrategy, autoParam: autoMode \}\)/);
    expect(scriptPage).toMatch(/if \(\(freeChainApplies && !autoFinishError/);
  });

  it("只呈现一个由项目方案驱动的一键出片入口", () => {
    expect(scriptPage).toMatch(/outputSchemeExecution\(creationBrief\)/);
    expect(scriptPage).toMatch(/const startOutputScheme = \(\) =>/);
    expect(scriptPage).toMatch(/execution === "controlled-assets"/);
    expect(scriptPage).toMatch(/router\.push\(`\/project\/\$\{id\}\/assets`\)/);
    expect(scriptPage).toMatch(/execution === "native-film"/);
    expect(scriptPage).toMatch(/void runAiFilm\(\)/);
    expect(scriptPage).toMatch(/data-output-action="one-click"/);
  });
});

describe("脚本页：旧项目（creationBrief 为 null）兼容", () => {
  it("仍读取 ?auto=1 并明确说明这是旧项目", () => {
    expect(scriptPage).toMatch(/qs\.get\("auto"\) === "1"/);
    expect(scriptPage).toMatch(/旧项目：没有创作简报/);
    expect(scriptPage).toMatch(/断点恢复/);
  });

  it("简报读取完成（含读取失败）后标记 briefLoaded，门控不会永久等待", () => {
    expect(scriptPage).toMatch(/setBriefLoaded\(true\)/);
    expect(scriptPage).toMatch(/proj\.creationBrief \? sanitizeCreationBrief\(proj\.creationBrief\) : null/);
  });
});

describe("素材页：仅导演动态方案进入逐镜工作区", () => {
  it("把原生整片和免费草稿导回脚本页的唯一出片入口", () => {
    expect(assetsPage).toMatch(/const directorFlow = !creationBrief \|\| creationBrief\.outputStrategy === "controlled-motion"/);
    expect(assetsPage).toMatch(/!directorFlow \? \(/);
    expect(assetsPage).toMatch(/此项目不使用逐镜导演流程/);
    expect(assetsPage).toMatch(/返回脚本页一键出片/);
  });
});

describe("脚本页：简报摘要与 409 显式风格", () => {
  it("页面持久展示 CreationBriefSummary", () => {
    expect(scriptPage).toMatch(
      /import \{ CreationBriefSummary \} from "@\/components\/project-creation\/creation-brief-summary"/
    );
    expect(scriptPage).toMatch(/<CreationBriefSummary/);
    expect(scriptPage).toMatch(/brief=\{creationBrief \?\? DEFAULT_CREATION_BRIEF\}/);
  });

  it("409 needs_explicit_style 交给 StyleChoicePrompt 呈现 candidates，不是失败", () => {
    expect(scriptPage).toMatch(
      /import \{ StyleChoicePrompt \} from "@\/components\/project-creation\/style-choice-prompt"/
    );
    expect(scriptPage).toMatch(/<StyleChoicePrompt/);
    // 页面消费该 code 与 candidates（经共享解析器）
    expect(scriptPage).toMatch(/needs_explicit_style/);
    expect(scriptPage).toMatch(/candidates/);
    expect(scriptPage).toMatch(/parseStyleRequirement\(res\.status, e\)/);
    // 409 分支在抛错之前返回
    const generateBlock = scriptPage.slice(scriptPage.indexOf("const handleGenerate"), scriptPage.indexOf("/** 用户在 409"));
    expect(generateBlock.indexOf("setStylePrompt(requirement)")).toBeLessThan(generateBlock.indexOf("throw new Error("));
  });

  it("用户选完风格后写回简报（styleSource: explicit）再重试同一个请求", () => {
    expect(scriptPage).toMatch(/styleSource: "explicit"/);
    expect(scriptPage).toMatch(/await handleGenerate\(styleType\)/);
    expect(scriptPage).toMatch(/body: JSON\.stringify\(\{ creationBrief: nextBrief \}\)/);
  });

  it("共享解析器与提示组件确实是 409 的那条通路", () => {
    expect(requirementModule).toMatch(/needs_explicit_style/);
    expect(requirementModule).toMatch(/candidates/);
    expect(promptModule).toMatch(/candidates\.map/);
    expect(parseStyleRequirement(409, { code: NEEDS_EXPLICIT_STYLE_CODE, candidates: ["drama", "story"] })?.candidates)
      .toEqual(["drama", "story"]);
    expect(parseStyleRequirement(500, { code: NEEDS_EXPLICIT_STYLE_CODE })).toBeNull();
  });

  it("重新生成保留旧脚本记录，不静默覆盖", () => {
    expect(scriptPage).toMatch(/snapshotCurrentScripts/);
    expect(scriptPage).toMatch(/previousScriptsPanel/);
    expect(scriptPage).toMatch(/setPreviousScripts/);
  });
});
