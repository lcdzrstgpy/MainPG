import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

function readSource(relativePath: string): string {
  return readFileSync(new URL(relativePath, import.meta.url), "utf-8");
}

const managerSource = readSource("../components/PrecheckImageManager.tsx");
const sourcePoolSource = readSource("../components/PrecheckSourcePool.tsx");
const materialLibrarySource = readSource("../components/PrecheckMaterialLibrary.tsx");

test("precheck renders source pool before processed material library", () => {
  assert.match(sourcePoolSource, /处理前图片池/);
  assert.match(materialLibrarySource, /处理后素材库/);
  const sourceRender = managerSource.indexOf("<PrecheckSourcePool");
  const libraryRender = managerSource.indexOf("<PrecheckMaterialLibrary");
  assert.ok(sourceRender >= 0 && libraryRender >= 0);
  assert.ok(sourceRender < libraryRender, "处理前图片池应渲染在处理后素材库之前");
  assert.doesNotMatch(managerSource, /统一素材状态/);
});

test("source-pool cards only promote to library", () => {
  assert.match(sourcePoolSource, /加入素材库/);
  assert.doesNotMatch(sourcePoolSource, /设为主图/);
  assert.doesNotMatch(sourcePoolSource, /加入轮播/);
});

test("main is rendered as carousel item one instead of a separate panel", () => {
  assert.match(managerSource, /轮播图.*第 1 张即主图/s);
  assert.doesNotMatch(managerSource, /precheck-main-section/);
});
