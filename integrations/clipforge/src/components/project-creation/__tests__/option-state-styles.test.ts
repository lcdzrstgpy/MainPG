import { describe, expect, it } from "vitest";
import { optionCardClass, optionChipClass } from "@/components/project-creation/option-state-styles";

describe("creation option state styles", () => {
  it("makes selected chips unmistakable", () => {
    const selected = optionChipClass(true);
    expect(selected).toContain("bg-primary");
    expect(selected).toContain("text-primary-foreground");
    expect(selected).toContain("shadow");
    expect(selected).toContain("focus-visible:ring");
  });

  it("keeps unselected cards neutral but keyboard-focusable", () => {
    const unselected = optionCardClass(false);
    expect(unselected).toContain("bg-muted/20");
    expect(unselected).toContain("focus-visible:ring");
    expect(unselected).toContain("hover:border-primary");
  });
});
