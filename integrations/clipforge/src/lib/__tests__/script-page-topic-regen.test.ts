import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveTopicScriptBrief } from "@/app/project/[id]/script/page";
import { DEFAULT_CREATION_BRIEF, sanitizeCreationBrief, type CreationBrief } from "@/lib/creation-brief";
import { buildTopicScriptRequest, topicNarrationStyleFor } from "@/lib/creation-submit";
import type { TopicNarrationStyle } from "@/lib/script-engine/prompts";

/**
 * P1（脚本页）：主题项目「重新生成脚本」不能再自己拼请求体。
 *
 * 原先脚本页 topic 分支把 targetDuration 写死成 25、且完全不传 narrationStyle，用户选的 15/30/60
 * 与旁白风格都会被静默改掉。现在它必须复用唯一构造器 buildTopicScriptRequest。
 *
 * 仓库没有组件渲染器（无 @testing-library/react），沿用 script-page-strategy.test.ts /
 * creation-submit.test.ts 的「纯函数直测 + 源码契约」风格。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");
const scriptPage = read("src/app/project/[id]/script/page.tsx");

/** 页面里 `from` 到 `to` 之间的片段，把源码契约限定在真正执行那段逻辑的代码里。 */
function sliceOf(source: string, from: string, to: string): string {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start);
  expect(start, `源码中找不到 ${from}`).toBeGreaterThan(-1);
  expect(end, `源码中找不到 ${from} … ${to}`).toBeGreaterThan(start);
  return source.slice(start, end);
}

const LLM = { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro" };

const TOPIC_STYLES: readonly TopicNarrationStyle[] = ["knowledge", "story", "lifestyle", "inspiration", "travel"];

const TOPIC_BRIEF: CreationBrief = {
  ...DEFAULT_CREATION_BRIEF,
  inputMode: "topic",
  styleType: "lifestyle",
  targetDuration: 30,
};

/** 与 handleGenerate 的 topic 分支同一条路径：先解析简报（含旧项目兜底），再交给唯一构造器。 */
const topicPayload = (topic: string, brief: CreationBrief | null) =>
  buildTopicScriptRequest({
    projectId: "project-1",
    topic,
    brief: resolveTopicScriptBrief(brief),
    llmConfig: LLM,
  });

describe("脚本页 topic 分支（源码契约）", () => {
  it("导入并调用共享构造器，不再存在第二套构造逻辑", () => {
    expect(scriptPage).toMatch(/import \{ buildTopicScriptRequest \} from "@\/lib\/creation-submit"/);
    expect(scriptPage).toMatch(/buildTopicScriptRequest\(\{/);
  });

  it("topic 分支不再写死 targetDuration: 25，也不再自带 narrationStyle", () => {
    // 页面任何位置都不得再出现写死的 25 秒
    expect(scriptPage).not.toMatch(/targetDuration:\s*25\b/);

    const payload = sliceOf(scriptPage, "const payload = isTopic", "const res = await fetch(endpoint");
    const topicBranch = payload.slice(0, payload.indexOf("productName"));
    expect(topicBranch).toMatch(/buildTopicScriptRequest\(\{/);
    // 时长与旁白风格都交给构造器（brief.targetDuration / topicNarrationStyleFor(brief.styleType)），
    // 页面不许再自己放常量
    expect(topicBranch).not.toMatch(/targetDuration/);
    expect(topicBranch).not.toMatch(/narrationStyle/);
  });

  it("topic 文本与简报来源保持不变（项目 topic → 项目名兜底 + 项目简报 / 旧项目兜底）", () => {
    const payload = sliceOf(scriptPage, "const payload = isTopic", "const res = await fetch(endpoint");
    const topicBranch = payload.slice(0, payload.indexOf("productName"));
    expect(topicBranch).toMatch(/topic: projectMeta\.topic \|\| projectName/);
    expect(topicBranch).toMatch(/brief: resolveTopicScriptBrief\(creationBrief\)/);
    expect(topicBranch).toMatch(/projectId: id/);
    expect(topicBranch).toMatch(/llmConfig: \{ baseUrl: llm\.baseUrl, apiKey: llm\.apiKey, model: llm\.model \}/);
  });

  it("商品分支（/api/llm/script）一行未动", () => {
    const payload = sliceOf(scriptPage, "const payload = isTopic", "const res = await fetch(endpoint");
    const commerceBranch = payload.slice(payload.indexOf("productName"));
    expect(commerceBranch).toMatch(/targetDuration: 30/);
    expect(commerceBranch).toMatch(/styleType: requestedStyle/);
    expect(commerceBranch).toMatch(/productName: projectMeta\.productName/);
    expect(commerceBranch).toMatch(/visionModel: llm\.visionModel/);
  });
});

describe("resolveTopicScriptBrief（旧主题项目的兼容兜底）", () => {
  it("有简报时原样返回，不做二次加工", () => {
    expect(resolveTopicScriptBrief(TOPIC_BRIEF)).toBe(TOPIC_BRIEF);
  });

  it("creationBrief 为 null 时落到明确基线：topic 模式 + 默认 30s + 空风格 → knowledge", () => {
    const brief = resolveTopicScriptBrief(null);
    expect(brief).toEqual(sanitizeCreationBrief({ ...DEFAULT_CREATION_BRIEF, inputMode: "topic" }));
    expect(brief.inputMode).toBe("topic");
    expect(brief.targetDuration).toBe(DEFAULT_CREATION_BRIEF.targetDuration);
    expect(topicNarrationStyleFor(brief.styleType)).toBe("knowledge");
  });
});

describe("topic 重新生成请求体：时长与旁白风格随用户选择", () => {
  it("15/30/60 × 五种主题旁白风格，请求体与用户选择一致", () => {
    for (const targetDuration of [15, 30, 60] as const) {
      for (const styleType of TOPIC_STYLES) {
        const body = topicPayload("加班到深夜的上班族", { ...TOPIC_BRIEF, targetDuration, styleType });
        expect(body.targetDuration).toBe(targetDuration);
        expect(body.narrationStyle).toBe(styleType);
      }
    }
  });

  it("narrationStyle 永远存在（不会再像写死 25 那样把它整键漏掉）", () => {
    for (const styleType of ["", "auto", "drama", ...TOPIC_STYLES]) {
      const body = topicPayload("桂花乌龙茶", { ...TOPIC_BRIEF, styleType });
      expect(typeof body.narrationStyle).toBe("string");
      expect(TOPIC_STYLES).toContain(body.narrationStyle);
    }
  });

  it("旧项目（creationBrief=null）不抛错，请求体仍含 topic/targetDuration/narrationStyle/llmConfig", () => {
    const body = topicPayload("  深夜加班的上班族  ", null);
    expect(body).toEqual({
      projectId: "project-1",
      topic: "深夜加班的上班族",
      narrationStyle: "knowledge",
      targetDuration: 30,
      llmConfig: LLM,
    });
    expect(Object.values(body).some((value) => value === undefined)).toBe(false);
  });

  it("topic 文本原样保留（只裁掉两侧空白，内部空白不动）", () => {
    expect(topicPayload("  桂花乌龙茶  ", TOPIC_BRIEF).topic).toBe("桂花乌龙茶");
    expect(topicPayload("A  B", TOPIC_BRIEF).topic).toBe("A  B");
  });
});
