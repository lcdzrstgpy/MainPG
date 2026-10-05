import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { OUTPUT_STRATEGY_OPTIONS } from "@/components/project-creation/creation-brief-defaults";
import { DEFAULT_CREATION_BRIEF } from "@/lib/creation-brief";

/**
 * 仓库没有组件渲染器（无 @testing-library/react），入口页的行为用源码契约 + 纯逻辑断言，
 * 与 src/components/__tests__/app-shell-embed.test.ts 的既有风格一致。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const startPage = read("src/app/start/page.tsx");
const newProjectPage = read("src/app/project/new/page.tsx");
const form = read("src/components/project-creation/creation-brief-form.tsx");
const requirement = read("src/components/project-creation/script-style-requirement.ts");
const prompt = read("src/components/project-creation/style-choice-prompt.tsx");

const FORM_MODULE = "@/components/project-creation/creation-brief-form";

describe("/start 成为唯一主创建入口", () => {
  it("删除 FORM_PRESETS / genMode / Atlas 一键接入面板", () => {
    expect(startPage).not.toMatch(/FORM_PRESETS/);
    expect(startPage).not.toMatch(/genMode/);
    expect(startPage).not.toMatch(/applyAtlasOneKey/);
    expect(startPage).not.toMatch(/ATLAS_KEYS_URL/);
    expect(startPage).not.toMatch(/atlas-cloud/);
    expect(startPage).not.toMatch(/atlas-onekey/);
    // 页面不得再引用 atlas*/keybox* 文案 key（定义保留，供其他批次清理）
    expect(startPage).not.toMatch(/t\("(?:atlas|keybox)[A-Za-z_]*"/);
  });

  it("未配置 LLM 时改为跳转设置页，不再内联填 Key", () => {
    expect(startPage).toMatch(/\/settings\?tab=llm/);
    expect(startPage).not.toMatch(/type="password"/);
    expect(startPage).not.toMatch(/test-provider/);
  });

  it("保留 cf-root 容器与既有视觉基调，且不出现 Atlas 文案", () => {
    expect(startPage).toMatch(/className="cf-root"/);
    expect(startPage).toMatch(/cf-amb/);
    expect(startPage).toMatch(/cf-grid/);
    expect(startPage).not.toMatch(/reassureLead/);
    expect(startPage).not.toMatch(/<b>Atlas Cloud<\/b>/);
  });

  it("shows the five-card scheme only in the creation form, without a legacy strategy legend", () => {
    expect(startPage).not.toMatch(/cf-legend|STRATEGY_NOTES|OUTPUT_STRATEGY_OPTIONS/);
    expect(form).toMatch(/<OutputSchemePanel/);
    expect(OUTPUT_STRATEGY_OPTIONS.find((option) => option.id === "draft")?.label).toContain("非 AI 动态视频");
  });

  it("出片方案默认原生整片，并在创建时把方案写进 creationBrief", () => {
    expect(DEFAULT_CREATION_BRIEF.outputStrategy).toBe("native-film");
    expect(form).toMatch(/sanitizeCreationBrief/);
    expect(form).toMatch(/OutputSchemePanel/);
    expect(startPage).toMatch(/creationBrief: /);
    expect(startPage).toMatch(/outputScheme/);
  });

  it("创建后记录 project_created 之外的 strategy_selected，且失败不阻断", () => {
    expect(startPage).toMatch(/recordStrategySelected/);
    expect(startPage).toMatch(/strategy_selected|recordStrategySelected/);
    expect(startPage).toMatch(/catch/);
  });

  it("新项目跳转脚本页不携带 auto=1，避免免费草稿静默开跑", () => {
    expect(startPage).toMatch(/return `\/project\/\$\{projectId\}\/script`/);
    expect(startPage).not.toMatch(/strategy === "draft" \? "\?auto=1"/);
    expect(startPage).not.toMatch(/gen=ai/);
  });
});

describe("两个入口共用同一份 CreationBriefForm", () => {
  it("start 与 project/new 从同一模块路径导入同一个表单组件", () => {
    const importOf = (source: string) =>
      source.match(/import \{[\s\S]*?\bCreationBriefForm\b[\s\S]*?\} from "([^"]+)"/)?.[1] ?? null;
    expect(importOf(startPage)).toBe(FORM_MODULE);
    expect(importOf(newProjectPage)).toBe(FORM_MODULE);
    // 每页只渲染一份表单：不再有第二套创建状态
    expect(startPage.match(/<CreationBriefForm\b/g)).toHaveLength(1);
    expect(newProjectPage.match(/<CreationBriefForm\b/g)).toHaveLength(1);
  });

  it("start 页把三种来源交给共享表单，并注入链接导入回调", () => {
    expect(startPage).toMatch(/onImportLink/);
    expect(startPage).toMatch(/onSubmitForm/);
    expect(startPage).toMatch(/inputMode|prefill/);
    // 链接导入走共享实现（只预填简报，不建项目）
    expect(startPage).toMatch(/importProductSource/);
    expect(startPage).toMatch(/from "@\/components\/project-creation\/link-import"/);
  });
});

describe("409 needs_explicit_style 的显式风格分支", () => {
  it("共享解析器认识该 code 并消费 candidates", () => {
    expect(requirement).toMatch(/needs_explicit_style/);
    expect(requirement).toMatch(/candidates/);
  });

  it("共享提示组件把 candidates 渲染成可点选项", () => {
    expect(prompt).toMatch(/candidates\.map/);
    expect(prompt).toMatch(/暂无足够数据推荐，请选择一个风格/);
    expect(prompt).toMatch(/onPick/);
  });

  it("两个入口都接上该分支，且提交前先要求显式风格", () => {
    for (const page of [startPage, newProjectPage]) {
      // 该 code 就是分支本身：页面认识它、读出 candidates、交给共享提示组件
      expect(page).toMatch(/needs_explicit_style/);
      expect(page).toMatch(/candidates/);
      expect(page).toMatch(/parseStyleRequirement/);
      expect(page).toMatch(/missingStyleRequirement/);
      expect(page).toMatch(/StyleChoicePrompt/);
    }
  });
});
