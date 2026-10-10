import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const hookSource = readFileSync(new URL("./usePerformanceMode.ts", import.meta.url), "utf8");
const effectsSource = readFileSync(new URL("./useEffectPreferences.ts", import.meta.url), "utf8");
const panelSource = readFileSync(new URL("../../modules/personal_center/components/PreferencesPanel.tsx", import.meta.url), "utf8");
const perfCss = readFileSync(new URL("../styles/perf-mode.css", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("../../main.tsx", import.meta.url), "utf8");

test("流畅模式：localStorage 持久化 + <html data-perf-mode> 属性", () => {
  assert.match(hookSource, /"mainpg\.performanceMode"/);
  assert.match(hookSource, /"mainpg\.performanceMode\.auto"/);
  assert.match(hookSource, /setAttribute\("data-perf-mode"/);
});

test("流畅模式：低配设备只自动评估一次（记录标记），自动开启有提示", () => {
  assert.match(hookSource, /maybeAutoEnable/);
  assert.match(hookSource, /readAutoChecked\(\)/);
  assert.match(hookSource, /markAutoChecked\(\)/);
  assert.match(hookSource, /showToast\(/);
});

test("特效偏好把流畅模式并入生效值（原始选择仍写 localStorage）", () => {
  assert.match(effectsSource, /subscribePerformanceMode/);
  assert.match(effectsSource, /isPerformanceModeOn\(\)/);
  assert.match(effectsSource, /deriveEffective/);
  assert.match(effectsSource, /applyPreferences\(effectivePreferences\)/);
  assert.match(effectsSource, /patch\.tap \?\? currentPreferences\.tap/);
});

test("偏好设置面板提供「流畅模式」开关，并联动置灰特效开关", () => {
  assert.ok(panelSource.includes("流畅模式"));
  assert.ok(panelSource.includes("usePerformanceMode"));
  assert.ok(panelSource.includes('!performanceMode && uiMode === "classic"'));
});

test("性能降级样式覆盖毛玻璃、仪表盘装饰与页签入场动画", () => {
  assert.match(perfCss, /html\[data-perf-mode="on"\]/);
  assert.match(perfCss, /backdrop-filter: none !important/);
  assert.match(perfCss, /\.dashboard-page::before/);
  assert.match(perfCss, /display: none !important/);
  assert.match(perfCss, /\.workspace-tab-panel\.is-active/);
});

test("main.tsx 在启动时引入流畅模式 hook 与降级样式", () => {
  assert.match(mainSource, /import "\.\/shared\/hooks\/usePerformanceMode"/);
  assert.match(mainSource, /import "\.\/shared\/styles\/perf-mode\.css"/);
});
