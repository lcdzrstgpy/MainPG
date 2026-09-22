import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { OUTPUT_STRATEGY_OPTIONS } from "@/components/project-creation/creation-brief-defaults";
import { buildCreativeIntentFields } from "@/components/project-creation/visual-control-panel";

/**
 * The repository has no component renderer (no @testing-library/react), so component behaviour is
 * pinned with source-contract assertions — same convention as src/components/__tests__/app-shell-embed.test.ts.
 */
const read = (file: string) => readFileSync(resolve(process.cwd(), "src/components/project-creation", file), "utf8");

const inputSourcePanel = read("input-source-panel.tsx");
const narrativePanel = read("narrative-panel.tsx");
const visualControlPanel = read("visual-control-panel.tsx");
const outputStrategyPanel = read("output-strategy-panel.tsx");
const summary = read("creation-brief-summary.tsx");
const types = read("creation-brief-types.ts");

describe("creation-brief-types", () => {
  it("only re-exports the shared contract so the component layer cannot define its own", () => {
    expect(types).toMatch(/export type \{[\s\S]*CreationBrief[\s\S]*\} from "@\/lib\/creation-brief"/);
    expect(types).not.toMatch(/interface |const |function /);
  });
});

describe("output-strategy-panel", () => {
  it("renders all three strategies from the shared vocabulary", () => {
    expect(outputStrategyPanel).toMatch(/OUTPUT_STRATEGY_OPTIONS\.map/);
    expect(outputStrategyPanel).toMatch(/role="radiogroup"/);
    expect(outputStrategyPanel).toMatch(/data-strategy=\{option\.id\}/);
    expect(outputStrategyPanel).toMatch(/aria-checked=\{active\}/);
    expect(OUTPUT_STRATEGY_OPTIONS.map((option) => option.id)).toEqual(["draft", "controlled-motion", "native-film"]);
  });

  it("states allowed media, default audio source and main output for every strategy", () => {
    expect(outputStrategyPanel).toMatch(/option\.allowedMedia/);
    expect(outputStrategyPanel).toMatch(/option\.defaultAudioSource/);
    expect(outputStrategyPanel).toMatch(/option\.mainOutput/);
  });

  it("labels the draft strategy as a non-AI static draft and offers the audio strategies", () => {
    expect(outputStrategyPanel).toMatch(/AUDIO_STRATEGY_OPTIONS\.map/);
    expect(outputStrategyPanel).toMatch(/onAudioStrategyChange/);
    const draft = OUTPUT_STRATEGY_OPTIONS.find((option) => option.id === "draft");
    expect(draft?.label).toContain("非 AI 动态视频");
  });
});

describe("input-source-panel", () => {
  it("switches between the five input modes", () => {
    expect(inputSourcePanel).toMatch(/INPUT_MODE_OPTIONS\.map/);
    expect(inputSourcePanel).toMatch(/data-input-mode=\{option\.id\}/);
    expect(inputSourcePanel).toMatch(/onInputModeChange/);
  });

  it("covers product image upload, product link import and one-sentence topic", () => {
    expect(inputSourcePanel).toMatch(/type="file"/);
    expect(inputSourcePanel).toMatch(/accept="image\/\*"/);
    expect(inputSourcePanel).toMatch(/MAX_SOURCE_IMAGES/);
    expect(inputSourcePanel).toMatch(/onRemoveImage/);
    expect(inputSourcePanel).toMatch(/onImportLink/);
    expect(inputSourcePanel).toMatch(/onLinkUrlChange/);
    expect(inputSourcePanel).toMatch(/onTopicChange/);
  });

  it("never performs the link import itself", () => {
    expect(inputSourcePanel).not.toMatch(/\bfetch\(/);
    expect(inputSourcePanel).not.toMatch(/\/api\//);
  });
});

describe("narrative-panel", () => {
  it("collects style, situation, language and tone", () => {
    // 风格词表由 prop 驱动，缺省仍是带货的 SCRIPT_STYLE_OPTIONS（调用方零改动）
    expect(narrativePanel).toMatch(/styleOptions = SCRIPT_STYLE_OPTIONS/);
    expect(narrativePanel).toMatch(/\{styleOptions\.map\(/);
    expect(narrativePanel).toMatch(/LANGUAGE_OPTIONS\.map/);
    expect(narrativePanel).toMatch(/TONE_OPTIONS\.map/);
    expect(narrativePanel).toMatch(/narrative\??\.situation/);
    expect(narrativePanel).toMatch(/narrative\??\.language/);
    expect(narrativePanel).toMatch(/narrative\??\.tone/);
    expect(narrativePanel).toMatch(/STYLE_SOURCE_LABELS/);
  });
});

describe("visual-control-panel", () => {
  it("collects video mode, template and character", () => {
    expect(visualControlPanel).toMatch(/VIDEO_MODE_OPTIONS\.map/);
    expect(visualControlPanel).toMatch(/onVideoModeChange/);
    expect(visualControlPanel).toMatch(/onTemplateIdChange/);
    expect(visualControlPanel).toMatch(/onCharacterIdChange/);
    expect(visualControlPanel).toMatch(/templateId/);
    expect(visualControlPanel).toMatch(/characterId/);
  });

  it("maps scene / visual constraints onto CreativeIntent field names", () => {
    expect(visualControlPanel).toMatch(/VISUAL_CONSTRAINT_FIELDS\.map/);
    expect(visualControlPanel).toMatch(/buildCreativeIntentFields/);
    for (const field of ["environment", "action", "camera", "lighting", "palette", "productConstraints", "negative"]) {
      expect(visualControlPanel).toMatch(new RegExp(`intentField: "${field}"`));
    }
  });

  it("builds only the filled CreativeIntent fields and splits list values", () => {
    expect(buildCreativeIntentFields({
      environment: " 深夜书桌 ",
      action: "",
      camera: "手持特写",
      lighting: "   ",
      palette: "暖橙",
      productConstraints: "包装盒不得变形、Logo 必须清晰",
      negative: "无人物入镜\n禁用英文",
    })).toEqual({
      environment: "深夜书桌",
      camera: "手持特写",
      palette: "暖橙",
      productConstraints: ["包装盒不得变形", "Logo 必须清晰"],
      negative: ["无人物入镜", "禁用英文"],
    });
    expect(buildCreativeIntentFields({
      environment: "",
      action: "",
      camera: "",
      lighting: "",
      palette: "",
      productConstraints: "",
      negative: "",
    })).toEqual({});
  });
});

describe("creation-brief-summary", () => {
  it("is read-only and normalizes whatever it is given", () => {
    expect(summary).toMatch(/sanitizeCreationBrief/);
    expect(summary).not.toMatch(/useState/);
    expect(summary).not.toMatch(/onChange/);
  });

  it("shows style and source, audience, platforms, narrative, strategy and audio", () => {
    for (const token of [
      /scriptStyleLabel/,
      /STYLE_SOURCE_LABELS/,
      /inputModeLabel/,
      /targetAudience/,
      /platforms/,
      /narrative/,
      /outputStrategyLabel/,
      /audioStrategyLabel/,
      /durationLabel/,
    ]) {
      expect(summary).toMatch(token);
    }
  });
});
