import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const stageSource = readFileSync(
  new URL("../components/DimensionCanvasStage.tsx", import.meta.url),
  "utf8",
);
const canvasStyles = readFileSync(
  new URL("../styles/dimension-canvas.css", import.meta.url),
  "utf8",
);
const toolbarSource = readFileSync(
  new URL("../components/DimensionCanvasToolbar.tsx", import.meta.url),
  "utf8",
);
const pageSource = readFileSync(
  new URL("../pages/DimensionCanvasPage.tsx", import.meta.url),
  "utf8",
);
const apiSource = readFileSync(
  new URL("../api/dimensionCanvasApi.ts", import.meta.url),
  "utf8",
);
const globalStyles = readFileSync(
  new URL("../../../shared/styles/global.css", import.meta.url),
  "utf8",
);

test("dimension canvas forwards vertical wheel input to the page scroll container", () => {
  assert.match(stageSource, /addEventListener\("wheel", handleWheel, \{ passive: false \}\)/);
  assert.match(stageSource, /removeEventListener\("wheel", handleWheel\)/);
  assert.match(stageSource, /verticalScrollTarget\(viewport, deltaY\)/);
  assert.match(stageSource, /requestAnimationFrame\(\(\) => \{/);
  assert.match(canvasStyles, /overscroll-behavior:\s*auto/);
});

test("dimension canvas uses Konva layers and keeps image rendering asynchronous", () => {
  assert.match(stageSource, /from "react-konva\/lib\/ReactKonvaCore"/);
  assert.match(stageSource, /<Stage/);
  assert.match(stageSource, /<Layer>/);
  assert.match(stageSource, /pointerAtBeginning/);
  assert.match(stageSource, /pointerAtEnding/);
  assert.match(stageSource, /decoding="async"/);
  assert.match(stageSource, /fetchPriority="high"/);
  assert.match(canvasStyles, /contain:\s*layout paint style/);
});

test("dimension canvas supports page scroll, modifier zoom and low-power rendering", () => {
  assert.match(stageSource, /event\.ctrlKey \|\| event\.metaKey/);
  assert.match(stageSource, /onZoomChangeRef\.current/);
  assert.match(stageSource, /hardwareConcurrency/);
  assert.match(stageSource, /Konva\.pixelRatio = preferredPixelRatio\(\)/);
  assert.match(stageSource, /event\.button === 1 \|\| \(spacePressed && event\.button === 0\)/);
  assert.match(stageSource, /viewport\.matches\(":hover"\) \|\| document\.activeElement === viewport/);
});

test("estimated height can be confirmed from the toolbar instead of staying disabled", () => {
  assert.doesNotMatch(toolbarSource, /provenance === "package_estimate"\) return/);
  assert.match(toolbarSource, /dimension\.valueCm == null \|\| dimension\.valueCm <= 0/);
  assert.match(pageSource, /selectDimensionTool\(activeTool\)/);
});

test("selected dimension lines expose persisted thin, normal and thick presets", () => {
  assert.match(toolbarSource, /onLineWidth/);
  assert.match(toolbarSource, /\["thin", "normal", "thick"\]/);
  assert.match(pageSource, /\{ \.\.\.annotation, lineWidth \}/);
  assert.match(stageSource, /function lineMetrics/);
  assert.match(toolbarSource, /dimension-width-swatch/);
  assert.match(toolbarSource, /selectedAnnotation\?\.style === style/);
  assert.match(canvasStyles, /white-space:\s*nowrap/);
});

test("custom dimension input lives beside the custom drawing tool", () => {
  assert.match(toolbarSource, /dimension-custom-tool-value/);
  assert.match(toolbarSource, /onCustomValueChange/);
  assert.doesNotMatch(pageSource, /className="dimension-custom-value"/);
  assert.match(canvasStyles, /\.dimension-custom-tool-value/);
});

test("annotation style includes a gray dashed option in preview and toolbar", () => {
  assert.match(toolbarSource, /gray_dashed/);
  assert.match(toolbarSource, /灰虚线/);
  assert.match(stageSource, /annotation\.style === "gray_dashed" \? \[9, 7\]/);
  assert.match(canvasStyles, /\.dimension-color-dot\.is-gray_dashed/);
});

test("dimension tools expose three persistent endpoint modes above length width and height", () => {
  const endpointModeIndex = toolbarSource.indexOf("dimension-endpoint-mode-row");
  const dimensionToolsIndex = toolbarSource.indexOf("DIMENSION_TOOLS.map");
  assert.ok(endpointModeIndex > 0 && endpointModeIndex < dimensionToolsIndex);
  assert.match(toolbarSource, /key: "arrow", label: "三角"/);
  assert.match(toolbarSource, /key: "bar", label: "横杠"/);
  assert.match(toolbarSource, /key: "none", label: "无"/);
  assert.match(toolbarSource, /activeEndpointStyle === key \? "is-active"/);
  assert.match(pageSource, /selectedAnnotationId: null/);
  assert.match(stageSource, /endpointStyle === "arrow"/);
  assert.match(stageSource, /endpointStyle === "bar"/);
  assert.match(canvasStyles, /\.dimension-endpoint-mode-row button\.is-active/);
  assert.match(apiSource, /raw\.endpoint_style \?\? raw\.endpointStyle \?\? "arrow"/);
  assert.match(apiSource, /rawSettings\.endpoint_style \?\? rawSettings\.endpointStyle \?\? "arrow"/);
});

test("selected annotations highlight endpoints without visible circular handles", () => {
  assert.match(stageSource, /function endpointAccentPoints/);
  assert.match(stageSource, /key={`endpoint-accent-\$\{index\}`}/);
  assert.match(stageSource, /fill="rgba\(0,0,0,0\.001\)"/);
  assert.doesNotMatch(stageSource, /radius=\{7\}[\s\S]*?fill="#ffffff"/);
});

test("desktop sidebar is fixed to the viewport", () => {
  assert.match(globalStyles, /@media \(min-width: 801px\)[\s\S]*?\.sidebar-card\s*\{[\s\S]*?position:\s*fixed/);
  assert.match(globalStyles, /\.workspace-main\s*\{\s*grid-column:\s*2/);
});
