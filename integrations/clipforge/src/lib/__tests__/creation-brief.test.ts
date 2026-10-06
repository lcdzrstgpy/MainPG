import { describe, expect, it } from "vitest";
import {
  DEFAULT_CREATION_BRIEF,
  buildWorkflowPlanForStrategy,
  isOutputStrategy,
  sanitizeCreationBrief,
  type OutputStrategy,
} from "@/lib/creation-brief";

const stage = (strategy: OutputStrategy, id: ReturnType<typeof buildWorkflowPlanForStrategy>[number]["id"]) =>
  buildWorkflowPlanForStrategy(strategy).find((row) => row.id === id);

describe("creation brief contract", () => {
  it("fills every missing field with the documented default", () => {
    expect(sanitizeCreationBrief(undefined)).toEqual(DEFAULT_CREATION_BRIEF);
    expect(sanitizeCreationBrief(null)).toEqual(DEFAULT_CREATION_BRIEF);
    expect(sanitizeCreationBrief({})).toEqual(DEFAULT_CREATION_BRIEF);
    expect(DEFAULT_CREATION_BRIEF).toEqual({
      version: 1,
      inputMode: "upload",
      targetDuration: 30,
      styleType: "",
      styleSource: "explicit",
      targetAudience: [],
      platforms: ["douyin"],
      outputStrategy: "draft",
      audioStrategy: "volcengine-tts",
    });
  });

  it("falls back to safe enum values instead of throwing on out-of-contract input", () => {
    const brief = sanitizeCreationBrief({
      version: 99,
      inputMode: "telepathy",
      targetDuration: 42,
      styleSource: "guesswork",
      outputStrategy: "hologram",
      audioStrategy: "scream",
      platforms: "douyin",
      narrative: "not-an-object",
    });
    expect(brief).toEqual(DEFAULT_CREATION_BRIEF);
  });

  it("keeps narrative optional and only stores the keys that carry content", () => {
    expect(sanitizeCreationBrief({ styleType: "scene" }).narrative).toBeUndefined();
    expect(sanitizeCreationBrief({ narrative: { tone: "  " } }).narrative).toBeUndefined();
    expect(sanitizeCreationBrief({ narrative: { situation: "  加班到深夜  ", tone: "共情" } }).narrative).toEqual({
      situation: "加班到深夜",
      tone: "共情",
    });
  });

  it("cleans and de-duplicates list fields while keeping a usable default platform", () => {
    const brief = sanitizeCreationBrief({
      targetAudience: ["宝妈", " 宝妈 ", 42, "", "学生党", null],
      platforms: ["douyin", "douyin", "  ", "xiaohongshu"],
      priceRange: "  ¥39.9 ",
      usageAdvantage: "3 秒出泡",
    });
    expect(brief.targetAudience).toEqual(["宝妈", "学生党"]);
    expect(brief.platforms).toEqual(["douyin", "xiaohongshu"]);
    expect(brief.priceRange).toBe("¥39.9");
    expect(brief.usageAdvantage).toBe("3 秒出泡");
    expect(sanitizeCreationBrief({ platforms: [] }).platforms).toEqual(["douyin"]);
  });

  it("round-trips a fully specified brief without mutating the shared default", () => {
    const brief = sanitizeCreationBrief({
      version: 1,
      inputMode: "clone",
      targetDuration: 15,
      styleType: "comparison",
      styleSource: "template",
      targetAudience: ["宝妈"],
      platforms: ["douyin", "tiktok"],
      priceRange: "£63.00",
      usageAdvantage: "一杯顶三杯",
      narrative: { situation: "通勤路上", language: "英语", tone: "轻快" },
      outputStrategy: "native-film",
      audioStrategy: "native-audio",
      templateId: "template-1",
      characterId: "character-1",
    });
    expect(brief).toEqual({
      version: 1,
      inputMode: "clone",
      targetDuration: 15,
      styleType: "comparison",
      styleSource: "template",
      targetAudience: ["宝妈"],
      platforms: ["douyin", "tiktok"],
      priceRange: "£63.00",
      usageAdvantage: "一杯顶三杯",
      narrative: { situation: "通勤路上", language: "英语", tone: "轻快" },
      outputStrategy: "native-film",
      audioStrategy: "native-audio",
      templateId: "template-1",
      characterId: "character-1",
    });
    expect(DEFAULT_CREATION_BRIEF.platforms).toEqual(["douyin"]);
    expect(DEFAULT_CREATION_BRIEF.targetAudience).toEqual([]);
  });

  it("recognises only the three output strategies", () => {
    expect(isOutputStrategy("draft")).toBe(true);
    expect(isOutputStrategy("controlled-motion")).toBe(true);
    expect(isOutputStrategy("native-film")).toBe(true);
    expect(isOutputStrategy("auto")).toBe(false);
    expect(isOutputStrategy(undefined)).toBe(false);
  });
});

describe("output strategy → workflow mapping", () => {
  it("keeps draft free of AI motion while still voicing the video", () => {
    expect(stage("draft", "motion")?.enabled).toBe(false);
    expect(stage("draft", "keyframes")?.billing).toBe("free");
    expect(stage("draft", "voice")?.enabled).toBe(true);
    expect(stage("draft", "compose")?.enabled).toBe(true);
  });

  it("enables paid keyframes and motion for controlled-motion", () => {
    expect(stage("controlled-motion", "keyframes")).toMatchObject({ enabled: true, execution: "client", billing: "paid" });
    expect(stage("controlled-motion", "motion")).toMatchObject({ enabled: true, execution: "client", billing: "paid" });
    expect(stage("controlled-motion", "voice")?.enabled).toBe(true);
    expect(stage("controlled-motion", "compose")?.enabled).toBe(true);
  });

  it("drops the separate voice stage for native-film and keeps motion + compose", () => {
    expect(stage("native-film", "motion")?.enabled).toBe(true);
    expect(stage("native-film", "voice")).toMatchObject({ enabled: false, reason: "native-audio" });
    expect(stage("native-film", "compose")?.enabled).toBe(true);
  });
});
