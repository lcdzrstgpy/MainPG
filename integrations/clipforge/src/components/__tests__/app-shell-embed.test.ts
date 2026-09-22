import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const shell = readFileSync(resolve(process.cwd(), "src/components/app-shell.tsx"), "utf8");
const css = readFileSync(resolve(process.cwd(), "src/app/globals.css"), "utf8");
const startPage = readFileSync(resolve(process.cwd(), "src/app/start/page.tsx"), "utf8");

describe("MainPG embedded shell", () => {
  it("removes the independent ClipForge navigation shell in explicit embed mode", () => {
    expect(shell).toMatch(/searchParams\.get\("embed"\) === "mainpg"/);
    expect(shell).toMatch(/mainpg-embed-content/);
    expect(shell).toMatch(/classList\.add\("mainpg-embedded"\)/);
    expect(css).toMatch(/\.mainpg-embedded/);
    expect(css).toMatch(/--background: #fff7f8/);
  });

  it("overrides the start page's hard-coded dark canvas for MainPG embed mode", () => {
    expect(startPage).toMatch(/className="cf-root"/);
    expect(css).toMatch(/\.mainpg-embedded \.cf-root/);
    expect(css).toMatch(/background: #fff7f8/);
  });

  it("does not advertise Atlas Cloud beside the MainPG start action", () => {
    expect(startPage).not.toMatch(/reassureLead/);
    expect(startPage).not.toMatch(/<b>Atlas Cloud<\/b>/);
  });
});
