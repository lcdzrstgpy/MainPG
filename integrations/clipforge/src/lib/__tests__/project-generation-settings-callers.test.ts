import { describe, expect, it } from "vitest";
import fs from "node:fs";
import path from "node:path";

const source = (relativePath: string) =>
  fs.readFileSync(path.resolve(process.cwd(), relativePath), "utf8");

describe("project generation setting callers", () => {
  it("projects the saved creation brief before script-side native-film generation", () => {
    const page = source("src/app/project/[id]/script/page.tsx");
    expect(page).toContain('import { projectGenerationSettings } from "@/lib/output-schemes"');
    expect(page).toMatch(/const generation = projectGenerationSettings\(creationBrief, \{/);
    expect(page).toMatch(/buildImageOptions\(generation\.imageParams/);
    expect(page).toMatch(/buildVideoOptions\(generation\.videoParams/);
  });

  it("projects the saved creation brief before assets-side keyframe and I2V generation", () => {
    const page = source("src/app/project/[id]/assets/page.tsx");
    expect(page).toContain('import { projectGenerationSettings } from "@/lib/output-schemes"');
    expect(page).toMatch(/const generation = useMemo\(\(\) => projectGenerationSettings\(creationBrief, \{/);
    expect(page).toMatch(/buildImageOptions\(generation\.imageParams/);
    expect(page).toMatch(/buildVideoOptions\(generation\.videoParams/);
    expect(page).toContain("generation.chainMode");
    expect(page).toContain("generation.motionIntensity");
  });
});
