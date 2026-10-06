import { expect, it } from "vitest";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  digestTree,
  publishMainpgArtifact,
  readCurrentArtifact,
  validateMainpgArtifact,
} from "./lib/mainpg-sidecar-artifact.mjs";

function fixture(root) {
  const suffix = process.platform === "win32" ? ".exe" : "";
  for (const dir of [
    ".next/server",
    ".next/static",
    "node_modules/next",
    "node_modules/ffmpeg-static",
    `node_modules/@ffprobe-installer/${process.platform}-${process.arch}`,
    "public",
    "drizzle",
  ]) mkdirSync(join(root, dir), { recursive: true });
  writeFileSync(join(root, "server.js"), "export {};");
  writeFileSync(join(root, "package.json"), "{}");
  writeFileSync(join(root, ".next/BUILD_ID"), "build-a\n");
  writeFileSync(join(root, ".next/server/middleware-manifest.json"), "{}");
  writeFileSync(join(root, ".next/required-server-files.json"), JSON.stringify({ files: [".next/server/middleware-manifest.json"] }));
  writeFileSync(join(root, "node_modules/next/package.json"), "{}");
  writeFileSync(join(root, "node_modules/next/index.js"), "export {};");
  writeFileSync(join(root, `node_modules/ffmpeg-static/ffmpeg${suffix}`), "fixture");
  writeFileSync(join(root, `node_modules/@ffprobe-installer/${process.platform}-${process.arch}/ffprobe${suffix}`), "fixture");
}

// fixture 里没有真实依赖树：把依赖解析固定到 artifact 内的假 next 上，
// 让「依赖必须从 artifact 内解析」这条规则可以在无需真实安装的情况下被测试。
const fixtureResolver = (_id, appRoot) => join(appRoot, "node_modules", "next", "index.js");

function publishFixture() {
  const root = mkdtempSync(join(tmpdir(), "clipforge-publish-"));
  const sourceRoot = join(root, "source");
  const standalone = join(sourceRoot, ".next", "standalone");
  const outputRoot = join(root, "output");
  fixture(standalone);
  mkdirSync(join(sourceRoot, ".next", "static"), { recursive: true });
  mkdirSync(join(sourceRoot, "public"), { recursive: true });
  mkdirSync(join(sourceRoot, "drizzle"), { recursive: true });
  mkdirSync(outputRoot, { recursive: true });
  return { root, sourceRoot, standalone, outputRoot };
}

it("rejects server.js-only partial builds", () => {
  const root = mkdtempSync(join(tmpdir(), "clipforge-partial-"));
  writeFileSync(join(root, "server.js"), "export {};");
  expect(() => validateMainpgArtifact(root, { resolveModule: fixtureResolver })).toThrow(/BUILD_ID/);
});

it("rejects a missing required-server-file", () => {
  const root = mkdtempSync(join(tmpdir(), "clipforge-required-"));
  fixture(root);
  rmSync(join(root, ".next/server/middleware-manifest.json"));
  expect(() => validateMainpgArtifact(root, { resolveModule: fixtureResolver })).toThrow(/middleware-manifest/);
});

it("rejects module resolution outside the artifact", () => {
  const root = mkdtempSync(join(tmpdir(), "clipforge-escape-"));
  fixture(root);
  expect(() => validateMainpgArtifact(root, { resolveModule: () => "/source/node_modules/next/index.js" })).toThrow(/outside artifact/);
});

it("rejects an Electron-ABI artifact that is marked as a non-node runtime", () => {
  const root = mkdtempSync(join(tmpdir(), "clipforge-runtime-"));
  fixture(root);
  writeFileSync(join(root, "mainpg-sidecar.json"), JSON.stringify({ schemaVersion: 1, artifactId: "x", runtime: "electron" }));
  expect(() => validateMainpgArtifact(root, { resolveModule: fixtureResolver, requireMetadata: true })).toThrow(/runtime/);
});

it("does not replace current.json when smoke testing fails", async () => {
  const { sourceRoot, standalone, outputRoot } = publishFixture();
  const pointer = join(outputRoot, "current.json");
  const previous = '{"schemaVersion":1,"artifactId":"old","relativePath":"artifacts/old"}\n';
  writeFileSync(pointer, previous);

  await expect(publishMainpgArtifact({
    sourceRoot,
    outputRoot,
    resolveModule: (_id, root) => join(root, "node_modules", "next", "index.js"),
    smokeTest: async () => { throw new Error("smoke failed"); },
  })).rejects.toThrow("smoke failed");

  expect(readFileSync(pointer, "utf8")).toBe(previous);
  expect(existsSync(join(standalone, "server.js"))).toBe(true);
});

it("does not replace current.json when the static contract fails", async () => {
  const { sourceRoot, outputRoot } = publishFixture();
  rmSync(join(sourceRoot, ".next", "standalone", ".next", "BUILD_ID"));
  const pointer = join(outputRoot, "current.json");
  const previous = '{"schemaVersion":1,"artifactId":"old","relativePath":"artifacts/old"}\n';
  writeFileSync(pointer, previous);

  await expect(publishMainpgArtifact({
    sourceRoot,
    outputRoot,
    resolveModule: (_id, root) => join(root, "node_modules", "next", "index.js"),
    smokeTest: async () => { throw new Error("must not run"); },
  })).rejects.toThrow(/BUILD_ID/);

  expect(readFileSync(pointer, "utf8")).toBe(previous);
});

it("publishes a versioned artifact and points current.json at it", async () => {
  const { sourceRoot, outputRoot } = publishFixture();
  const published = await publishMainpgArtifact({
    sourceRoot,
    outputRoot,
    resolveModule: (_id, root) => join(root, "node_modules", "next", "index.js"),
    smokeTest: async () => {},
  });

  expect(published.artifactId).toMatch(/^build-a-[0-9a-f]{12}$/);
  expect(existsSync(join(outputRoot, "artifacts", published.artifactId, "server.js"))).toBe(true);

  const current = readCurrentArtifact(outputRoot, { resolveModule: (_id, root) => join(root, "node_modules", "next", "index.js") });
  expect(current.artifactId).toBe(published.artifactId);
  expect(current.runtime).toBe("node");
  expect(current.appRoot).toBe(join(outputRoot, "artifacts", published.artifactId));
});

it("digestTree is stable and ignores the publication metadata", () => {
  const root = mkdtempSync(join(tmpdir(), "clipforge-digest-"));
  writeFileSync(join(root, "server.js"), "a");
  const before = digestTree(root);
  expect(digestTree(root)).toBe(before);

  writeFileSync(join(root, "mainpg-sidecar.json"), "{}");
  expect(digestTree(root)).toBe(before);

  writeFileSync(join(root, "server.js"), "b");
  expect(digestTree(root)).not.toBe(before);
});
