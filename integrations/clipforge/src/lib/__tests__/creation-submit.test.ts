import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { buildScriptRequest } from "@/components/project-creation/build-script-request";
import { DEFAULT_CREATION_BRIEF, type CreationBrief } from "@/lib/creation-brief";
import { buildTopicScriptRequest, topicNarrationStyleFor } from "@/lib/creation-submit";

/**
 * P1（页面层）：两个创建入口（/start、/project/new）的创建链路与脚本链路。
 *
 * 仓库没有组件渲染器（无 @testing-library/react），沿用 start-entry.test.ts /
 * script-page-strategy.test.ts 的「纯函数直测 + 源码契约」风格：能抽成纯函数的（主题请求体、
 * 旁白风格白名单）直接单测，落在页面里的 DTO 键集用源码契约钉住。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), file), "utf8");

const startPage = read("src/app/start/page.tsx");
const newProjectPage = read("src/app/project/new/page.tsx");

const PAGES: ReadonlyArray<[string, string]> = [
  ["/start", startPage],
  ["/project/new", newProjectPage],
];

/** 页面里 `from` 到 `to` 之间的片段，把源码契约限定在真正执行那段逻辑的代码里。 */
function sliceOf(source: string, from: string, to: string): string {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start);
  expect(start, `源码中找不到 ${from}`).toBeGreaterThan(-1);
  expect(end, `源码中找不到 ${from} … ${to}`).toBeGreaterThan(start);
  return source.slice(start, end);
}

const TOPIC_BRIEF: CreationBrief = {
  ...DEFAULT_CREATION_BRIEF,
  inputMode: "topic",
  styleType: "lifestyle",
  targetDuration: 60,
};

describe("buildTopicScriptRequest（一句话主题链路的唯一请求体）", () => {
  it("键集就是主题引擎的入参，主题文本两侧空白被裁掉", () => {
    const body = buildTopicScriptRequest({
      projectId: "project-1",
      topic: "  加班到深夜的上班族  ",
      brief: TOPIC_BRIEF,
      llmConfig: { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro" },
    });
    expect(body).toEqual({
      projectId: "project-1",
      topic: "加班到深夜的上班族",
      narrationStyle: "lifestyle",
      targetDuration: 60,
      llmConfig: { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro" },
    });
    expect(Object.values(body).some((value) => value === undefined)).toBe(false);
    // 带货字段不得混进主题请求体（空的 productName 就是「请填写商品名称」的来源）
    for (const key of ["productName", "category", "styleType", "productImages", "narrative"]) {
      expect(key in body, key).toBe(false);
    }
  });

  it("目标时长取简报里的显式选择，不写死一个常量", () => {
    for (const targetDuration of [15, 30, 60] as const) {
      const body = buildTopicScriptRequest({
        projectId: "p",
        topic: "t",
        brief: { ...TOPIC_BRIEF, targetDuration },
        llmConfig: { baseUrl: "b", apiKey: "k", model: "m" },
      });
      expect(body.targetDuration).toBe(targetDuration);
    }
  });
});

describe("topicNarrationStyleFor（旁白风格白名单透传）", () => {
  it("白名单内的值原样透传", () => {
    for (const style of ["knowledge", "story", "lifestyle", "inspiration", "travel"]) {
      expect(topicNarrationStyleFor(style)).toBe(style);
    }
  });

  it("带货风格词表与空值都落到引擎默认 knowledge，不从题材猜风格", () => {
    for (const style of ["", "auto", "scenario", "pain-point", "drama"]) {
      expect(topicNarrationStyleFor(style)).toBe("knowledge");
    }
  });
});

describe("两个入口的创建链路（源码契约）", () => {
  it("创建 body 带 creativeIntent（经 sanitize）、visualBible、characterId 与 topic 标记", () => {
    for (const [name, page] of PAGES) {
      const createCall = sliceOf(page, 'fetch("/api/project", {', "if (!projectRes.ok)");
      expect(createCall, name).toMatch(/creationBrief: brief/);
      // 画面约束必须经共享 sanitizer 再进请求体
      expect(createCall, name).toMatch(/creativeIntent: sanitizeCreativeIntent\(values\.creativeIntent\)/);
      // 表单没给 visualBible 时整键不带，绝不凭空造锚点
      expect(createCall, name).toMatch(
        /\.\.\.\(values\.visualBible \? \{ visualBible: values\.visualBible \} : \{\}\)/
      );
      // 角色绑定来自简报（表单里的选择写进 brief.characterId）
      expect(createCall, name).toMatch(/\.\.\.\(brief\.characterId \? \{ characterId: brief\.characterId \} : \{\}\)/);
      // 一句话主题：项目类型与主题文本都要落库
      expect(createCall, name).toMatch(
        /\.\.\.\(isTopic \? \{ contentType: "topic" as const, topic: topicText \} : \{\}\)/
      );
    }
  });

  it("提交前先跑共享校验，错误（含「请先选择一个出片策略」）经 setError 展示而不是静默", () => {
    for (const [name, page] of PAGES) {
      expect(page, name).toMatch(/validateCreationBriefForm\(\{/);
      expect(page, name).toMatch(/strategyChosen: values\.strategyChosen/);
      expect(page, name).toMatch(/if \(!validation\.valid\) \{/);
      expect(page, name).toMatch(/setError\(Object\.values\(validation\.errors\)\.filter\(Boolean\)\.join\("；"\)\)/);
    }
  });

  it("/project/new 的表单快照初值补齐扩展契约（creativeIntent / strategyChosen）", () => {
    expect(newProjectPage).toMatch(/creativeIntent: sanitizeCreativeIntent\(\{ subject: "" \}\)/);
    expect(newProjectPage).toMatch(/strategyChosen: false/);
    expect(newProjectPage.match(/EMPTY_FORM_VALUES: CreationBriefFormValues/g) ?? []).toHaveLength(1);
  });
});

describe("一句话主题走主题引擎，不再打带货脚本接口", () => {
  it("两个入口的 topic 分支调用 /api/topic/script，并带上主题请求体", () => {
    for (const [name, page] of PAGES) {
      expect(page, name).toMatch(/brief\.inputMode === "topic"/);
      expect(page, name).toMatch(/fetch\("\/api\/topic\/script"/);
      expect(page, name).toMatch(/buildTopicScriptRequest\(/);
      // 带货脚本接口只剩产品分支那一处：主题不会再落进它
      expect(page.match(/\/api\/llm\/script/g) ?? [], name).toHaveLength(1);
    }
  });

  it("主题项目不再以「商品名」为前提，主题文本代位商品名/描述", () => {
    for (const [name, page] of PAGES) {
      expect(page, name).toMatch(/isTopic \? topicText : /);
    }
  });

  it("待重试请求带主题字段，风格重试不会把主题请求打回带货接口", () => {
    for (const [name, page] of PAGES) {
      const pending = page.match(/interface PendingScript \{[\s\S]*?\n\}/)?.[0] ?? "";
      expect(pending, name).toMatch(/brief: CreationBrief/);
      expect(pending, name).toMatch(/topic: string/);
    }
  });
});

describe("叙事不丢（narrative 由 buildScriptRequest 从 brief 派生）", () => {
  it("两个入口都把 brief 原样交给唯一构造器，风格重试复用同一个 brief", () => {
    for (const [name, page] of PAGES) {
      expect(page, name).toMatch(/buildScriptRequest\(\{\s*brief: pending\.brief/);
      // 409 选完风格后的重试：pending 原样带上，brief（及其 narrative）不被换成别的来源
      expect(page, name).toMatch(/requestScript\(\{ \.\.\.pending, brief \}\)/);
    }
  });

  it("brief.narrative 确实进了请求体（引用构造器的真实产出）", () => {
    const request = buildScriptRequest({
      brief: {
        ...DEFAULT_CREATION_BRIEF,
        narrative: { situation: "加班到深夜的上班族", language: "英语", tone: "共情" },
      },
      projectId: "project-1",
      productName: "桂花乌龙茶",
      category: "food",
      videoMode: "product_closeup",
      llmConfig: { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro" },
    });
    expect(request.narrative).toEqual({ situation: "加班到深夜的上班族", language: "英语", tone: "共情" });
  });
});
