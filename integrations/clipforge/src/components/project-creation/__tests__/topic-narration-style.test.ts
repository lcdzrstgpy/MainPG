import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  SCRIPT_STYLE_OPTIONS,
  TOPIC_NARRATION_STYLE_OPTIONS,
  coerceStyleTypeForInputMode,
  topicNarrationStyleLabel,
} from "@/components/project-creation/creation-brief-defaults";
import { DEFAULT_CREATION_BRIEF } from "@/lib/creation-brief";
import { buildTopicScriptRequest, isTopicNarrationStyle, topicNarrationStyleFor } from "@/lib/creation-submit";

/**
 * 风格选择不可达（复审结论）：主题链路过去渲染的是带货风格词表，用户点「情景短剧 / 痛点种草」
 * 会被 topicNarrationStyleFor 静默降级成 knowledge。这里钉住「按 inputMode 切换词表」的契约。
 *
 * 仓库没有组件渲染器（无 @testing-library/react），沿用 creation-submit.test.ts 的
 * 「纯函数直测 + 源码契约」风格。
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), "src/components/project-creation", file), "utf8");
const form = read("creation-brief-form.tsx");
const panel = read("narrative-panel.tsx");
const summary = read("creation-brief-summary.tsx");

const TOPIC_STYLE_IDS = ["knowledge", "story", "lifestyle", "inspiration", "travel"] as const;
const SCRIPT_STYLE_IDS = [
  "drama",
  "reversal",
  "interview",
  "story",
  "unboxing",
  "product_pov",
  "comparison",
  "talking_head",
  "pain-point",
  "scenario",
  "auto",
] as const;

/** 源码里 `from` 到 `to` 之间的片段，把源码契约限定在真正渲染面板的那段代码里。 */
function sliceOf(source: string, from: string, to: string): string {
  const start = source.indexOf(from);
  const end = source.indexOf(to, start + from.length);
  expect(start, `源码中找不到 ${from}`).toBeGreaterThan(-1);
  expect(end, `源码中找不到 ${from} … ${to}`).toBeGreaterThan(start);
  return source.slice(start, end);
}

describe("TOPIC_NARRATION_STYLE_OPTIONS（主题旁白风格词表）", () => {
  it("id 恰好是引擎白名单的五个值，且每个都能通过 isTopicNarrationStyle", () => {
    const ids = TOPIC_NARRATION_STYLE_OPTIONS.map((option) => option.id);
    expect(ids).toEqual([...TOPIC_STYLE_IDS]);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ids) {
      expect(isTopicNarrationStyle(id), id).toBe(true);
    }
  });

  it("每个选项都带中文可读 label 与一句话 description", () => {
    for (const option of TOPIC_NARRATION_STYLE_OPTIONS) {
      expect(option.label.length, option.id).toBeGreaterThan(0);
      expect(option.description.length, option.id).toBeGreaterThan(0);
    }
    expect(TOPIC_NARRATION_STYLE_OPTIONS.map((option) => option.label)).toEqual([
      "知识科普",
      "情感故事",
      "生活方式",
      "励志金句",
      "旅行风光",
    ]);
  });

  it("主题模式选这五种里任意一种都原样保留并得到同名 narrationStyle，不再降级成 knowledge", () => {
    for (const styleType of TOPIC_STYLE_IDS) {
      const brief = { ...DEFAULT_CREATION_BRIEF, inputMode: "topic" as const, styleType };
      expect(brief.styleType).toBe(styleType);
      expect(topicNarrationStyleFor(brief.styleType)).toBe(styleType);
      const body = buildTopicScriptRequest({
        projectId: "project-1",
        topic: "在家泡一杯手冲咖啡",
        brief,
        llmConfig: { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro" },
      });
      expect(body.narrationStyle).toBe(styleType);
    }
  });
});

describe("coerceStyleTypeForInputMode（切换来源时清掉非法残留）", () => {
  it("主题模式下非法或空的风格重置成词表第一个合法值 knowledge", () => {
    expect(coerceStyleTypeForInputMode("drama", "topic")).toBe("knowledge");
    expect(coerceStyleTypeForInputMode("drama", "topic")).toBe(TOPIC_NARRATION_STYLE_OPTIONS[0].id);
    for (const styleType of ["", "scenario", "pain-point", "auto", "not-a-style"]) {
      expect(coerceStyleTypeForInputMode(styleType, "topic"), styleType).toBe("knowledge");
    }
  });

  it("主题模式下已经合法的风格原样保留", () => {
    for (const styleType of TOPIC_STYLE_IDS) {
      expect(coerceStyleTypeForInputMode(styleType, "topic")).toBe(styleType);
    }
  });

  it("非主题来源（upload/link/product-library/clone）完全不动风格，商品模式行为不变", () => {
    for (const inputMode of ["upload", "link", "product-library", "clone"] as const) {
      for (const styleType of [...SCRIPT_STYLE_IDS, "", "knowledge", "pain_point"]) {
        expect(coerceStyleTypeForInputMode(styleType, inputMode), `${inputMode}/${styleType}`).toBe(styleType);
      }
    }
  });
});

describe("商品模式回归：11 个带货风格选项与行为完全不变", () => {
  it("id 顺序与文案仍是 /project/new 的那 11 个，没有被主题词表混入", () => {
    expect(SCRIPT_STYLE_OPTIONS.map((option) => option.id)).toEqual([...SCRIPT_STYLE_IDS]);
    expect(SCRIPT_STYLE_OPTIONS.every((option) => option.label.length > 0 && option.description.length > 0)).toBe(true);
    expect(SCRIPT_STYLE_OPTIONS.map((option) => option.id)).not.toContain("knowledge");
  });

  it("商品模式下 11 个风格都可被选中且原样透传（不被归一化改写）", () => {
    for (const styleType of SCRIPT_STYLE_IDS) {
      expect(coerceStyleTypeForInputMode(styleType, "upload")).toBe(styleType);
    }
  });
});

describe("表单按 inputMode 切换词表与标题（源码契约）", () => {
  const panelJsx = sliceOf(form, "<NarrativePanel", "/>");
  const compact = panelJsx.replace(/\s+/g, " ");

  it("topic 模式传主题旁白词表与「主题旁白风格」标题，商品模式回落到带货词表", () => {
    expect(compact).toContain("TOPIC_NARRATION_STYLE_OPTIONS : SCRIPT_STYLE_OPTIONS");
    expect(compact).toContain("主题旁白风格");
    expect(compact).toContain("脚本风格");
    expect(form).toMatch(/const topicMode = brief\.inputMode === "topic"/);
  });

  it("切换来源时经 coerceStyleTypeForInputMode 归一化 styleType", () => {
    expect(form).toMatch(/coerceStyleTypeForInputMode\(brief\.styleType, inputMode\)/);
  });
});

describe("NarrativePanel 向后兼容（缺省仍是带货词表）", () => {
  it("styleOptions / styleLabel 都是可选 prop，缺省值不变", () => {
    expect(panel).toMatch(/styleOptions\?:/);
    expect(panel).toMatch(/styleLabel\?:/);
    expect(panel).toMatch(/styleOptions = SCRIPT_STYLE_OPTIONS/);
    expect(panel).toMatch(/styleLabel = "脚本风格"/);
    expect(panel).toMatch(/\{styleOptions\.map\(/);
    expect(panel).toMatch(/\{styleLabel\}<\/Label>/);
  });
});

describe("只读简报的主题风格展示名", () => {
  it("主题风格显示中文名，认不出的值回退成原始 id", () => {
    expect(topicNarrationStyleLabel("knowledge")).toBe("知识科普");
    expect(topicNarrationStyleLabel("travel")).toBe("旅行风光");
    expect(topicNarrationStyleLabel("drama")).toBe("drama");
    expect(topicNarrationStyleLabel("")).toBe("");
  });

  it("主题来源用主题词表的展示名，商品来源仍用带货展示名", () => {
    expect(summary).toMatch(/safe\.inputMode === "topic" \? topicNarrationStyleLabel\(safe\.styleType\) : scriptStyleLabel\(safe\.styleType\)/);
  });
});
