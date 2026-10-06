import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const componentDir = resolve(process.cwd(), "src/components/project-creation");
const readOptional = (file: string) => {
  const path = resolve(componentDir, file);
  return existsSync(path) ? readFileSync(path, "utf8") : "";
};

const optionStateStyles = readOptional("option-state-styles.tsx");
const outputStrategyPanel = readOptional("output-strategy-panel.tsx");

describe("option selection feedback", () => {
  it("defines strong selected card and chip treatments with keyboard focus feedback", () => {
    expect(optionStateStyles).toMatch(/export function optionCardClass/);
    expect(optionStateStyles).toMatch(/export function optionChipClass/);
    expect(optionStateStyles).toMatch(/bg-primary/);
    expect(optionStateStyles).toMatch(/text-primary-foreground/);
    expect(optionStateStyles).toMatch(/focus-visible:ring-2/);
  });

  it("provides a decorative check marker", () => {
    expect(optionStateStyles).toMatch(/export function SelectionCheck/);
    expect(optionStateStyles).toMatch(/aria-hidden="true"/);
    expect(optionStateStyles).toMatch(/data-selection-check/);
  });

  it("wires both output strategy cards and audio chips to the shared selected state", () => {
    expect(outputStrategyPanel).toMatch(/optionCardClass\(active\)/);
    expect(outputStrategyPanel).toMatch(/optionChipClass\(active\)/);
    expect(outputStrategyPanel.match(/active && <SelectionCheck/g)).toHaveLength(2);
    expect(outputStrategyPanel).toMatch(/aria-checked=\{active\}/);
    expect(outputStrategyPanel).toMatch(/onOutputStrategyChange\(option\.id\)/);
    expect(outputStrategyPanel).toMatch(/onAudioStrategyChange\(option\.id\)/);
  });
});
