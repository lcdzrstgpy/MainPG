import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { sanitizeCreativeIntent } from "@/lib/production-system";
import { validateCreationBriefForm } from "@/components/project-creation/creation-brief-defaults";
import { EMPTY_VISUAL_CONSTRAINTS, buildCreativeIntentFields } from "@/components/project-creation/visual-control-panel";

const read = (file: string) => readFileSync(resolve(process.cwd(), "src/components/project-creation", file), "utf8");
const form = read("creation-brief-form.tsx");

describe("CreationBriefForm contract", () => {
  it("exposes exactly the documented props and the brief-only submit contract", () => {
    expect(form).toMatch(/"use client"/);
    expect(form).toMatch(/export function CreationBriefForm\(/);
    expect(form).toMatch(/initial\?: Partial<CreationBrief>/);
    expect(form).toMatch(/submitLabel: string/);
    expect(form).toMatch(/showAdvanced\?: boolean/);
    expect(form).toMatch(/onSubmit: \(brief: CreationBrief\) => void/);
    expect(form).toMatch(/disabled\?: boolean/);
    expect(form).toMatch(/onSubmit\(/);
  });

  it("only collects and validates: no model, ffmpeg, or API calls", () => {
    expect(form).not.toMatch(/\bfetch\(/);
    expect(form).not.toMatch(/ffmpeg/i);
    expect(form).not.toMatch(/\/api\//);
    expect(form).not.toMatch(/await\s/);
  });

  it("normalizes every state change through the shared sanitizer", () => {
    expect(form).toMatch(/sanitizeCreationBrief/);
    expect(form).toMatch(/validateCreationBriefForm/);
    expect(form).toMatch(/defaultAudioStrategyFor/);
    expect(form).toMatch(/resolveStyleSource/);
  });

  it("composes the four panels and keeps the summary out of the create flow", () => {
    for (const panel of ["InputSourcePanel", "NarrativePanel", "VisualControlPanel", "OutputStrategyPanel"]) {
      expect(form).toMatch(new RegExp(`\\b${panel}\\b`));
    }
    expect(form).not.toMatch(/CreationBriefSummary/);
  });

  it("gates the optional sections behind showAdvanced", () => {
    const gates = form.match(/\{showAdvanced && \(/g) ?? [];
    expect(gates.length).toBeGreaterThanOrEqual(2);
  });
});

describe("submission gating", () => {
  const canSubmit = (productName: string, imageCount: number) =>
    validateCreationBriefForm({
      productName,
      images: Array.from({ length: imageCount }, (_, index) => ({ id: `image-${index}` })),
      strategyChosen: true,
    }).valid;

  it("blocks submission while the product name is empty", () => {
    expect(canSubmit("", 1)).toBe(false);
  });

  it("blocks submission while no product image has been provided", () => {
    expect(canSubmit("桂花乌龙茶", 0)).toBe(false);
  });

  it("allows submission once name and images are both present", () => {
    expect(canSubmit("桂花乌龙茶", 1)).toBe(true);
  });

  it("requires a topic instead of a product name in topic mode", () => {
    expect(validateCreationBriefForm({ inputMode: "topic", productName: "", images: [], topic: "" }).valid).toBe(false);
    expect(validateCreationBriefForm({ inputMode: "topic", productName: "", images: [], topic: "在家泡一杯手冲咖啡", strategyChosen: true }).valid).toBe(true);
  });
});

describe("outcome-strategy gate (P2 / C3)", () => {
  it("starts unselected and only becomes chosen after a strategy card click", () => {
    expect(form).toMatch(/useState\(false\)/);
    expect(form).toMatch(/setStrategyChosen\(true\)/);
    expect(form).toMatch(/strategyChosen=\{strategyChosen\}|strategyChosen,/);
    expect(form).toMatch(/validateCreationBriefForm\(\{[\s\S]*strategyChosen[\s\S]*\}\)/);
  });

  it("blocks a form whose strategy was never clicked and allows it afterwards", () => {
    const filled = { productName: "桂花乌龙茶", images: [{ id: "a" }] };
    expect(validateCreationBriefForm({ ...filled, strategyChosen: false }).valid).toBe(false);
    expect(validateCreationBriefForm({ ...filled, strategyChosen: false }).errors.outputStrategy).toBeTruthy();
    expect(validateCreationBriefForm({ ...filled, strategyChosen: true }).valid).toBe(true);
  });
});

describe("extended form-values contract (C1)", () => {
  it("declares creativeIntent / visualBible / strategyChosen on the frozen shape", () => {
    expect(form).toMatch(/creativeIntent: CreativeIntent/);
    expect(form).toMatch(/visualBible\?: VisualBible/);
    expect(form).toMatch(/strategyChosen: boolean/);
    expect(form).toMatch(/import \{ sanitizeCreativeIntent, type CreativeIntent, type VisualBible \} from "@\/lib\/production-system"/);
  });

  it("reports the extended shape through both onSubmitForm and onValuesChange", () => {
    expect(form).toMatch(/onSubmitForm\(\{[^}]*creativeIntent[^}]*visualBible[^}]*strategyChosen[^}]*\}\)/);
    expect(form).toMatch(/onValuesChange\(\{[^}]*creativeIntent[^}]*visualBible[^}]*strategyChosen[^}]*\}\)/);
  });
});

describe("visual constraints wiring (C4)", () => {
  it("passes constraints / onConstraintsChange into VisualControlPanel", () => {
    expect(form).toMatch(/constraints=\{constraints\}/);
    expect(form).toMatch(/onConstraintsChange=\{setConstraints\}/);
    expect(form).toMatch(/useState<VisualConstraintValues>\(EMPTY_VISUAL_CONSTRAINTS\)/);
  });

  it("derives creativeIntent from the panel's pure mapper through the shared sanitizer", () => {
    expect(form).toMatch(/sanitizeCreativeIntent\(buildCreativeIntentFields\(constraints\)\)/);
  });

  it("maps only the collected forbidden changes into visualBible, without inventing anchors", () => {
    expect(form).toMatch(/forbiddenChanges/);
    expect(form).toMatch(/forbiddenChanges\.length/);
    // 五个锚点数组只有在收集到禁忌时才以空数组出现，绝不写入编造的锚点值
    expect(form).toMatch(/characterAnchors: \[\]/);
    expect(form).not.toMatch(/characterAnchors: \[\s*"/);
    expect(form).not.toMatch(/productAnchors: \[\s*"/);
  });
});

describe("constraint → CreativeIntent pure mapping", () => {
  it("sanitizes mapped constraints into a complete CreativeIntent with an allowed empty subject", () => {
    expect(
      sanitizeCreativeIntent(buildCreativeIntentFields({ ...EMPTY_VISUAL_CONSTRAINTS, environment: " 深夜书桌 ", negative: "无人物入镜、禁用英文" }))
    ).toEqual({ subject: "", environment: "深夜书桌", negative: ["无人物入镜", "禁用英文"] });
  });

  it("produces an empty intent for untouched constraints", () => {
    expect(sanitizeCreativeIntent(buildCreativeIntentFields(EMPTY_VISUAL_CONSTRAINTS))).toEqual({ subject: "" });
  });
});
