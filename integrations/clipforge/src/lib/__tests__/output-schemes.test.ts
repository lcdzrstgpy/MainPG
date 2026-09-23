import { describe, expect, it } from "vitest";
import { DEFAULT_IMAGE_PARAMS, DEFAULT_VIDEO_PARAMS } from "@/lib/gen-params";
import {
  OUTPUT_SCHEMES,
  projectGenerationSettings,
  sanitizeOutputSchemeSnapshot,
  type OutputSchemeId,
} from "@/lib/output-schemes";
import { sanitizeCreationBrief, type CreationBrief } from "@/lib/creation-brief";

const IDS: OutputSchemeId[] = [
  "draft", "controlled-rapid", "controlled-balanced", "controlled-cinematic", "native-film",
];

describe("output scheme snapshots", () => {
  it.each(IDS)("has a valid snapshot for %s", (id) => {
    expect(sanitizeOutputSchemeSnapshot({ id })).toEqual(OUTPUT_SCHEMES[id]);
    expect(Object.isFrozen(OUTPUT_SCHEMES[id])).toBe(true);
  });

  it("keeps the controlled presets' generation controls together", () => {
    expect(OUTPUT_SCHEMES["controlled-rapid"]).toMatchObject({
      outputStrategy: "controlled-motion", audioStrategy: "volcengine-tts", resolution: "720p",
      shotDuration: 4, motionStrength: 0.35, motionIntensity: "subtle",
      motionRealism: "constraints", chainMode: "off", visualLook: "none",
    });
    expect(OUTPUT_SCHEMES["controlled-balanced"]).toMatchObject({
      resolution: "720p", shotDuration: 5, motionStrength: 0.55, motionIntensity: "normal",
      motionRealism: "auto", chainMode: "pin", visualLook: "daylight_clean",
    });
    expect(OUTPUT_SCHEMES["controlled-cinematic"]).toMatchObject({
      resolution: "1080p", shotDuration: 8, motionStrength: 0.72, motionIntensity: "strong",
      motionRealism: "auto", chainMode: "tail", visualLook: "studio_product",
    });
  });

  it("keeps draft free of AI motion and native film on native audio", () => {
    expect(OUTPUT_SCHEMES.draft).toMatchObject({
      outputStrategy: "draft", audioStrategy: "volcengine-tts", resolution: "720p",
      shotDuration: 5, motionStrength: 0, chainMode: "off",
    });
    expect(OUTPUT_SCHEMES["native-film"]).toMatchObject({
      outputStrategy: "native-film", audioStrategy: "native-audio", resolution: "720p",
      shotDuration: 5, motionStrength: 0.55, motionIntensity: "normal",
      motionRealism: "auto", chainMode: "off",
    });
  });

  it("sanitizes native film to native audio and removes unknown fields", () => {
    const snapshot = sanitizeOutputSchemeSnapshot({
      id: "native-film", audioStrategy: "volcengine-tts", apiKey: "x",
    });
    expect(snapshot).toMatchObject({ id: "native-film", audioStrategy: "native-audio" });
    expect(snapshot).not.toHaveProperty("apiKey");
  });

  it("preserves mute for draft and controlled video without changing preset controls", () => {
    expect(sanitizeOutputSchemeSnapshot({ id: "draft", audioStrategy: "mute" }))
      .toEqual({ ...OUTPUT_SCHEMES.draft, audioStrategy: "mute" });
    expect(sanitizeOutputSchemeSnapshot({ id: "controlled-balanced", audioStrategy: "mute" }))
      .toEqual({ ...OUTPUT_SCHEMES["controlled-balanced"], audioStrategy: "mute" });
  });

  it("rejects stale controls, unknown IDs, and provider secrets", () => {
    expect(sanitizeOutputSchemeSnapshot({ id: "controlled-rapid", resolution: "1080p", motionStrength: 1, model: "secret" }))
      .toEqual(OUTPUT_SCHEMES["controlled-rapid"]);
    expect(sanitizeOutputSchemeSnapshot({ id: "unknown" })).toEqual(OUTPUT_SCHEMES["native-film"]);
  });
});

describe("project generation projection", () => {
  const globals = {
    imageParams: { ...DEFAULT_IMAGE_PARAMS, aspectRatio: "16:9" as const, seed: 42 },
    videoParams: { ...DEFAULT_VIDEO_PARAMS, aspectRatio: "16:9" as const, seed: 7, fps: 30 },
    motionIntensity: "strong" as const,
    motionRealism: "off" as const,
    chainMode: "tail" as const,
    visualLook: "studio_product",
  };

  it("applies the saved scheme while retaining unrelated global generation params", () => {
    const settings = projectGenerationSettings(sanitizeCreationBrief({ outputScheme: { id: "controlled-rapid" } }), globals);
    expect(settings).toMatchObject({
      outputStrategy: "controlled-motion", audioStrategy: "volcengine-tts",
      imageParams: { aspectRatio: "16:9", seed: 42 },
      videoParams: { aspectRatio: "16:9", seed: 7, fps: 30, resolution: "720p", duration: 4, motionStrength: 0.35 },
      motionIntensity: "subtle", motionRealism: "constraints", chainMode: "off", visualLook: "none",
    });
    expect(globals.videoParams.duration).toBe(5);
  });

  it("uses globals when the project has no creation brief", () => {
    expect(projectGenerationSettings(null, globals)).toEqual(globals);
  });

  it.each([
    ["draft", "mute", "draft", 0],
    ["controlled-motion", "volcengine-tts", "controlled-motion", 0.55],
  ] as const)("projects a legacy %s brief without a snapshot", (outputStrategy, audioStrategy, expectedStrategy, motionStrength) => {
    const legacy = { outputStrategy, audioStrategy } as CreationBrief;
    expect(projectGenerationSettings(legacy, globals)).toMatchObject({
      outputStrategy: expectedStrategy,
      audioStrategy,
      videoParams: { resolution: "720p", duration: 5, motionStrength },
    });
  });
});
