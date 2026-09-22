import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { validateCreationBriefForm } from "@/components/project-creation/creation-brief-defaults";

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
    expect(validateCreationBriefForm({ inputMode: "topic", productName: "", images: [], topic: "在家泡一杯手冲咖啡" }).valid).toBe(true);
  });
});
