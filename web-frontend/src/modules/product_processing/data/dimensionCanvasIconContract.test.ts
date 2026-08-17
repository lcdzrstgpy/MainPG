import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { workspacePageModules } from "../../../app/navigation/modules.ts";

const pageSource = readFileSync(new URL("../pages/DimensionCanvasPage.tsx", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../styles/dimension-canvas.css", import.meta.url), "utf8");
const iconfontSource = readFileSync(
  new URL("../../../../download/font_5219619_sag0ft83mnn/iconfont.css", import.meta.url),
  "utf8",
);

test("dimension canvas uses icon classes that exist in the bundled font", () => {
  const module = workspacePageModules.find((candidate) => candidate.id === "dimension_canvas");
  assert.equal(module?.iconClass, "iconfont icon-column-width");
  for (const name of ["column-width", "upload", "sync", "check-circle"]) {
    assert.match(iconfontSource, new RegExp(`\\.icon-${name}:before\\s*\\{`));
  }
});

test("dimension canvas page applies the measurement identity consistently", () => {
  assert.match(pageSource, /dimension-title-icon iconfont icon-column-width/);
  assert.match(pageSource, /dimension-empty-icon iconfont icon-column-width/);
  assert.match(pageSource, /iconfont icon-upload/);
  assert.match(pageSource, /iconfont icon-sync/);
  assert.match(pageSource, /iconfont icon-check-circle/);
  assert.match(styleSource, /\.dimension-title-row\s*\{/);
  assert.match(styleSource, /\.dimension-title-icon\s*\{/);
});
