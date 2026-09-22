import { describe, expect, it } from "vitest";
import { DEFAULT_CREATION_BRIEF, type CreationBrief } from "@/lib/creation-brief";
import { buildScriptRequest, type ScriptRequestInput } from "@/components/project-creation/build-script-request";

/**
 * The request body below is a frozen copy of what `/project/new` sends to `/api/llm/script`
 * (src/app/project/new/page.tsx:587-623). The builder must stay byte-for-byte equivalent at the
 * key level, otherwise the extraction in Task 1.1 is not behaviour preserving.
 */
const FULL_BRIEF: CreationBrief = {
  version: 1,
  inputMode: "product-library",
  targetDuration: 60,
  styleType: "pain-point",
  styleSource: "template",
  targetAudience: ["宝妈", "学生党"],
  platforms: ["douyin", "tiktok"],
  priceRange: "50-200",
  usageAdvantage: "3 秒出泡，一杯顶三杯",
  narrative: { situation: "加班到深夜", language: "中文", tone: "共情" },
  outputStrategy: "controlled-motion",
  audioStrategy: "volcengine-tts",
  templateId: "template-1",
  characterId: "character-1",
};

const FULL_INPUT: ScriptRequestInput = {
  brief: FULL_BRIEF,
  projectId: "project-1",
  productName: "桂花乌龙茶",
  category: "food",
  productDescription: "0 糖 0 卡",
  productImages: ["/uploads/a.png", "/uploads/b.png"],
  videoMode: "live_presenter",
  llmConfig: { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro", visionModel: "doubao-vision" },
  referenceStructure: "1. [hook] 3s 口播参考：「你也这样吗」",
  customRequirements: "look=clean-studio",
  character: { id: "character-1", name: "主播小美", appearance: "short black hair", voiceStyle: "warm" },
};

const MINIMAL_INPUT: ScriptRequestInput = {
  brief: DEFAULT_CREATION_BRIEF,
  projectId: "project-2",
  productName: "桂花乌龙茶",
  category: "food",
  videoMode: "product_closeup",
  llmConfig: { baseUrl: "https://llm.example.com/v1", apiKey: "sk-test", model: "doubao-pro" },
};

const SCRIPT_REQUEST_KEYS = [
  "projectId",
  "productName",
  "category",
  "productDescription",
  "targetDuration",
  "styleType",
  "videoMode",
  "productImages",
  "llmConfig",
  "priceRange",
  "targetAudience",
  "platforms",
  "usageAdvantage",
  "narrative",
  "templateId",
  "referenceStructure",
  "customRequirements",
  "character",
] as const;

/** 改造前的键集合与顺序（未收集叙事时请求体必须逐键等价）。 */
const PRE_EXTRACTION_KEYS = [
  "projectId",
  "productName",
  "category",
  "productDescription",
  "targetDuration",
  "styleType",
  "videoMode",
  "productImages",
  "llmConfig",
  "priceRange",
  "targetAudience",
  "platforms",
  "usageAdvantage",
] as const;

describe("buildScriptRequest", () => {
  it("reproduces /project/new's script request body key for key", () => {
    const request = buildScriptRequest(FULL_INPUT);
    expect(request).toEqual({
      projectId: "project-1",
      productName: "桂花乌龙茶",
      category: "food",
      productDescription: "0 糖 0 卡",
      targetDuration: 60,
      styleType: "pain-point",
      videoMode: "live_presenter",
      productImages: ["/uploads/a.png", "/uploads/b.png"],
      llmConfig: {
        baseUrl: "https://llm.example.com/v1",
        apiKey: "sk-test",
        model: "doubao-pro",
        visionModel: "doubao-vision",
      },
      priceRange: "50-200",
      targetAudience: "宝妈,学生党",
      platforms: "douyin,tiktok",
      usageAdvantage: "3 秒出泡，一杯顶三杯",
      narrative: { situation: "加班到深夜", language: "中文", tone: "共情" },
      templateId: "template-1",
      referenceStructure: "1. [hook] 3s 口播参考：「你也这样吗」",
      customRequirements: "look=clean-studio",
      character: {
        id: "character-1",
        name: "主播小美",
        appearance: "short black hair",
        voiceStyle: "warm",
      },
    });
  });

  it("keeps the exact key set of the pre-extraction request body", () => {
    const request = buildScriptRequest(FULL_INPUT);
    expect(Object.keys(request).sort()).toEqual([...SCRIPT_REQUEST_KEYS].sort());
  });

  it("never emits an undefined value for a minimal brief", () => {
    const request = buildScriptRequest(MINIMAL_INPUT);
    expect(Object.values(request).some((value) => value === undefined)).toBe(false);
    expect(JSON.stringify(request)).not.toContain("undefined");
    // optional branches stay absent instead of being sent as undefined placeholders
    expect("templateId" in request).toBe(false);
    expect("referenceStructure" in request).toBe(false);
    expect("customRequirements" in request).toBe(false);
    expect("character" in request).toBe(false);
    // 没填叙事时，连键都不能出现
    expect("narrative" in request).toBe(false);
    expect(request.targetAudience).toBe("");
    expect(request.platforms).toBe("douyin");
    expect(request.productImages).toEqual([]);
    expect(request.targetDuration).toBe(30);
    expect(request.styleType).toBe("");
  });

  it("passes brief values through without silently replacing an empty style", () => {
    const request = buildScriptRequest({ ...MINIMAL_INPUT, brief: { ...DEFAULT_CREATION_BRIEF, styleType: "auto" } });
    expect(request.styleType).toBe("auto");
    const explicit = buildScriptRequest({ ...MINIMAL_INPUT, brief: { ...DEFAULT_CREATION_BRIEF, styleType: "drama" } });
    expect(explicit.styleType).toBe("drama");
  });

  it("keeps referenceStructure as an opaque payload and omits empty optional strings", () => {
    const request = buildScriptRequest({ ...MINIMAL_INPUT, referenceStructure: { shots: 3 }, customRequirements: "" });
    expect(request.referenceStructure).toEqual({ shots: 3 });
    expect("customRequirements" in request).toBe(false);
  });
});

describe("buildScriptRequest narrative (C2)", () => {
  it("carries the brief narrative field for field when at least one field is filled", () => {
    const request = buildScriptRequest(FULL_INPUT);
    expect(request.narrative).toEqual({ situation: "加班到深夜", language: "中文", tone: "共情" });
    expect(request).toEqual(expect.objectContaining({ narrative: { situation: "加班到深夜", language: "中文", tone: "共情" } }));
  });

  it("emits only the filled narrative fields and drops the blank ones", () => {
    const request = buildScriptRequest({
      ...MINIMAL_INPUT,
      brief: { ...DEFAULT_CREATION_BRIEF, narrative: { situation: "", language: "日语", tone: "  " } },
    });
    expect(request.narrative).toEqual({ language: "日语" });
    expect(Object.keys(request.narrative as object)).toEqual(["language"]);
  });

  it("omits the narrative key entirely when every narrative field is empty", () => {
    const emptyInputs: Array<CreationBrief["narrative"]> = [undefined, {}, { situation: "", language: "", tone: "" }, { situation: "  ", tone: "\n" }];
    for (const narrative of emptyInputs) {
      const request = buildScriptRequest({ ...MINIMAL_INPUT, brief: { ...DEFAULT_CREATION_BRIEF, narrative } });
      expect("narrative" in request).toBe(false);
      expect(Object.values(request).some((value) => value === undefined)).toBe(false);
    }
  });

  it("keeps the pre-extraction key set and order when no narrative is collected", () => {
    const request = buildScriptRequest(MINIMAL_INPUT);
    expect(Object.keys(request)).toEqual([...PRE_EXTRACTION_KEYS]);
    expect(Object.keys(request).includes("narrative")).toBe(false);
  });
});
