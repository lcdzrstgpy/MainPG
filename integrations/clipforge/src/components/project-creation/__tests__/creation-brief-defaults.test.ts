import { describe, expect, it } from "vitest";
import { DEFAULT_CREATION_BRIEF, sanitizeCreationBrief } from "@/lib/creation-brief";
import {
  AUDIENCE_OPTIONS,
  AUDIO_STRATEGY_OPTIONS,
  DEFAULT_VIDEO_MODE,
  DURATION_OPTIONS,
  INPUT_MODE_OPTIONS,
  MAX_SOURCE_IMAGES,
  OUTPUT_STRATEGY_OPTIONS,
  PLATFORM_OPTIONS,
  PRICE_RANGE_OPTIONS,
  SCRIPT_STYLE_OPTIONS,
  STYLE_SOURCE_LABELS,
  VIDEO_MODE_OPTIONS,
  audioStrategyLabel,
  defaultAudioStrategyFor,
  durationLabel,
  inputModeLabel,
  outputStrategyLabel,
  resolveStyleSource,
  scriptStyleLabel,
  validateCreationBriefForm,
} from "@/components/project-creation/creation-brief-defaults";

const ids = (options: ReadonlyArray<{ id: string | number }>) => options.map((option) => option.id);

describe("creation brief option vocabularies", () => {
  it("reuses /project/new's exact video mode / duration ids", () => {
    expect(ids(DURATION_OPTIONS)).toEqual([15, 30, 60]);
    expect(ids(VIDEO_MODE_OPTIONS)).toEqual(["product_closeup", "graphic_montage", "scene_demo", "live_presenter"]);
    expect(DEFAULT_VIDEO_MODE).toBe("product_closeup");
    expect(VIDEO_MODE_OPTIONS.every((option) => option.label.length > 0 && (option.description ?? "").length > 0)).toBe(true);
  });

  it("carries the same 11 script styles as /project/new, pain-point hyphenated", () => {
    expect(ids(SCRIPT_STYLE_OPTIONS)).toEqual([
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
    ]);
    expect(SCRIPT_STYLE_OPTIONS.map((option) => option.id)).not.toContain("pain_point");
    expect(SCRIPT_STYLE_OPTIONS.every((option) => option.label.length > 0 && option.description.length > 0)).toBe(true);
    expect(scriptStyleLabel("pain-point")).toBe("痛点种草");
    expect(scriptStyleLabel("not-a-style")).toBe("not-a-style");
  });

  it("keeps the delivery vocabularies identical to the raw values /project/new sends", () => {
    expect(ids(PRICE_RANGE_OPTIONS)).toEqual(["0-50", "50-200", "200-500", "500+"]);
    // the API receives the Chinese audience tag verbatim, so the option id IS the payload value
    expect(ids(AUDIENCE_OPTIONS)).toEqual(["学生党", "上班族", "宝妈", "精致白领", "中年群体", "男性用户", "健身人群", "数码爱好者"]);
    expect(ids(PLATFORM_OPTIONS)).toEqual(["douyin", "kuaishou", "xiaohongshu", "tiktok"]);
    expect(PLATFORM_OPTIONS.every((option) => option.label.length > 0)).toBe(true);
  });

  it("uses stable, unique ids everywhere and exposes a label lookup for every vocabulary", () => {
    for (const options of [
      INPUT_MODE_OPTIONS,
      DURATION_OPTIONS,
      VIDEO_MODE_OPTIONS,
      SCRIPT_STYLE_OPTIONS,
      PRICE_RANGE_OPTIONS,
      AUDIENCE_OPTIONS,
      PLATFORM_OPTIONS,
      OUTPUT_STRATEGY_OPTIONS,
      AUDIO_STRATEGY_OPTIONS,
    ]) {
      const optionIds = ids(options);
      expect(new Set(optionIds).size).toBe(optionIds.length);
    }
    expect(inputModeLabel("clone")).toBe("爆款复刻");
    expect(durationLabel(15)).toBe("15s");
    expect(MAX_SOURCE_IMAGES).toBe(5);
  });
});

describe("output strategy vocabulary", () => {
  it("lists exactly the three strategies with explicit, non-upgradable labels", () => {
    expect(ids(OUTPUT_STRATEGY_OPTIONS)).toEqual(["draft", "controlled-motion", "native-film"]);
    const draft = OUTPUT_STRATEGY_OPTIONS[0];
    const controlled = OUTPUT_STRATEGY_OPTIONS[1];
    const native = OUTPUT_STRATEGY_OPTIONS[2];
    expect(draft.label).toContain("非 AI 动态视频");
    expect(draft.label).toContain("免费草稿");
    expect(controlled.label).toContain("逐镜生视频");
    expect(native.label).toContain("自带音频");
    // every strategy must explain allowed media / default audio source / main output (§5.2)
    for (const option of OUTPUT_STRATEGY_OPTIONS) {
      expect(option.allowedMedia.length).toBeGreaterThan(0);
      expect(option.defaultAudioSource.length).toBeGreaterThan(0);
      expect(option.mainOutput.length).toBeGreaterThan(0);
    }
    expect(outputStrategyLabel("draft")).toBe(draft.label);
    expect(outputStrategyLabel("controlled-motion")).toBe(controlled.label);
    expect(outputStrategyLabel("native-film")).toBe(native.label);
  });

  it("records native audio for native-film and keeps the documented default elsewhere", () => {
    expect(defaultAudioStrategyFor("native-film")).toBe("native-audio");
    expect(defaultAudioStrategyFor("draft")).toBe(DEFAULT_CREATION_BRIEF.audioStrategy);
    expect(defaultAudioStrategyFor("controlled-motion")).toBe(DEFAULT_CREATION_BRIEF.audioStrategy);
  });

  it("describes the three audio strategies without hiding the mute option", () => {
    expect(ids(AUDIO_STRATEGY_OPTIONS)).toEqual(["volcengine-tts", "native-audio", "mute"]);
    expect(AUDIO_STRATEGY_OPTIONS.every((option) => option.label.length > 0 && option.description.length > 0)).toBe(true);
    expect(audioStrategyLabel("mute")).toBe(AUDIO_STRATEGY_OPTIONS[2].label);
  });
});

describe("style source resolution", () => {
  it("never upgrades auto into a concrete style", () => {
    expect(resolveStyleSource({ styleType: "auto" })).toBe("performance-recommendation");
    expect(STYLE_SOURCE_LABELS["performance-recommendation"]).toBe("数据推荐");
    expect(STYLE_SOURCE_LABELS.template).toBe("来自模板");
    expect(STYLE_SOURCE_LABELS.explicit).toBe("手动选择");
  });

  it("attributes a concrete style to the template only when a template is actually applied", () => {
    expect(resolveStyleSource({ styleType: "drama" })).toBe("explicit");
    expect(resolveStyleSource({ styleType: "drama", templateId: "template-1" })).toBe("template");
  });
});

describe("form validation", () => {
  it("rejects an empty product name or an empty image list", () => {
    expect(validateCreationBriefForm({ productName: "", images: [{ id: "a" }] }).valid).toBe(false);
    expect(validateCreationBriefForm({ productName: "   ", images: [{ id: "a" }] }).errors.productName).toBeTruthy();
    expect(validateCreationBriefForm({ productName: "桂花乌龙茶", images: [] }).valid).toBe(false);
    expect(validateCreationBriefForm({ productName: "桂花乌龙茶", images: [] }).errors.images).toBeTruthy();
  });

  it("accepts a complete product form and a complete topic form", () => {
    expect(validateCreationBriefForm({ productName: "桂花乌龙茶", images: [{ id: "a" }], strategyChosen: true }).valid).toBe(true);
    expect(validateCreationBriefForm({ inputMode: "topic", productName: "", images: [], topic: "在家泡一杯手冲咖啡", strategyChosen: true }).valid).toBe(true);
    expect(validateCreationBriefForm({ inputMode: "topic", productName: "", images: [], topic: "  " }).errors.topic).toBeTruthy();
  });
});

describe("output strategy must be explicitly chosen (P2 / C3)", () => {
  const complete = { productName: "桂花乌龙茶", images: [{ id: "a" }] };

  it("blocks submission while no strategy card has been clicked", () => {
    const blocked = validateCreationBriefForm({ ...complete, strategyChosen: false });
    expect(blocked.valid).toBe(false);
    expect(blocked.errors.outputStrategy).toBe("请先选择一个出片策略");
    // 缺省（没有选择记录）同样视为未选择
    expect(validateCreationBriefForm(complete).valid).toBe(false);
    expect(validateCreationBriefForm(complete).errors.outputStrategy).toBeTruthy();
  });

  it("allows submission once a strategy has been clicked", () => {
    expect(validateCreationBriefForm({ ...complete, strategyChosen: true }).valid).toBe(true);
  });

  it("keeps draft as the default value without counting it as chosen", () => {
    expect(DEFAULT_CREATION_BRIEF.outputStrategy).toBe("draft");
    expect(validateCreationBriefForm({ ...complete, strategyChosen: false }).valid).toBe(false);
  });
});

describe("defaults stay free of implicit downgrades", () => {
  it("normalizes an empty brief into the shared default contract", () => {
    expect(sanitizeCreationBrief(undefined)).toEqual(DEFAULT_CREATION_BRIEF);
    expect(DEFAULT_CREATION_BRIEF.styleType).toBe("");
    expect(DEFAULT_CREATION_BRIEF.outputStrategy).toBe("draft");
    expect(DEFAULT_CREATION_BRIEF.platforms).toEqual(["douyin"]);
  });

  it("does not preselect a script style anywhere in the option list", () => {
    // "auto" is a user-visible choice, never a silent default
    expect(SCRIPT_STYLE_OPTIONS.some((option) => option.id === "auto")).toBe(true);
    expect(DEFAULT_CREATION_BRIEF.styleType).not.toBe("auto");
  });
});
