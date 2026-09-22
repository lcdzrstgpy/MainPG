// @vitest-environment node
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { mkdtemp, rm, writeFile } from "fs/promises";
import { join } from "path";
import { tmpdir } from "os";
import { searchAllStock, stockCacheKey } from "@/lib/providers/stock-registry";
import { updateMaterialMetadata } from "@/lib/local-material-library";
vi.mock("@/lib/providers/stock-types", async (importOriginal) => {
  const actual =
    await importOriginal<typeof import("@/lib/providers/stock-types")>();
  return {
    ...actual,
    STOCK_SOURCES: actual.STOCK_SOURCES.filter(
      (source) => source.id === "local" || source.id === "openverse",
    ),
  };
});
vi.mock("@/lib/providers/openverse", () => ({
  searchOpenverseImages: vi.fn(async () => []),
  searchOpenverseAudio: vi.fn(async () => []),
}));
let first: string, second: string;
beforeEach(async () => {
  first = await mkdtemp(join(tmpdir(), "clipforge-pool-a-"));
  second = await mkdtemp(join(tmpdir(), "clipforge-pool-b-"));
  await writeFile(join(first, "coffee.png"), "a");
  await writeFile(join(second, "tea.png"), "b");
});
afterEach(async () => {
  await Promise.all([
    rm(first, { recursive: true, force: true }),
    rm(second, { recursive: true, force: true }),
  ]);
});
it("isolates identical searches across project-owned material directories", async () => {
  expect(stockCacheKey("drink", { localDir: first }, ["local"])).not.toBe(
    stockCacheKey("drink", { localDir: second }, ["local"]),
  );
  const a = await searchAllStock("drink", {
    localDir: first,
    mediaType: "image",
  });
  const b = await searchAllStock("drink", {
    localDir: second,
    mediaType: "image",
  });
  expect(a.candidates.map((c) => c.id)).toEqual(["coffee.png"]);
  expect(b.candidates.map((c) => c.id)).toEqual(["tea.png"]);
});
it("refreshes local metadata and removed files while remote results remain cached", async () => {
  await searchAllStock("coffee", { localDir: first, mediaType: "image" });
  await updateMaterialMetadata(first, "coffee.png", "Pour over", [
    "kitchen",
    "coffee",
  ]);
  const edited = await searchAllStock("coffee", {
    localDir: first,
    mediaType: "image",
  });
  expect(edited.candidates[0]).toMatchObject({
    title: "Pour over",
    tags: ["kitchen", "coffee"],
  });
  await rm(join(first, "coffee.png"));
  const removed = await searchAllStock("coffee", {
    localDir: first,
    mediaType: "image",
  });
  expect(removed.candidates).toEqual([]);
});
