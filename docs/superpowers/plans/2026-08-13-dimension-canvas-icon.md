# Dimension Canvas Icon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the dimension-canvas module a valid, theme-aware measurement icon in the sidebar and reuse that icon language in the page header, empty state, and primary actions.

**Architecture:** Reuse the existing bundled iconfont as the only icon source. Keep module identity in `workspaceModules`, page markup in `DimensionCanvasPage`, and visual treatment in the existing dimension-canvas stylesheet; add a source-level contract test because this frontend does not currently include a React DOM test runtime.

**Tech Stack:** React 18, TypeScript, CSS custom properties, bundled iconfont, Node test runner, Vite.

---

## File map

- Create `web-frontend/src/modules/product_processing/data/dimensionCanvasIconContract.test.ts`: verifies the selected icon classes exist in the bundled font and remain wired into navigation/page markup.
- Modify `web-frontend/src/app/navigation/modules.ts`: replaces the nonexistent `icon-ruler` class with `icon-column-width`.
- Modify `web-frontend/src/modules/product_processing/pages/DimensionCanvasPage.tsx`: adds decorative icons to the title, empty state, and main actions without changing button labels or behavior.
- Modify `web-frontend/src/modules/product_processing/styles/dimension-canvas.css`: adds theme-aware sizing, alignment, and spacing for the new page icons.

### Task 1: Lock the icon contract with a failing test

**Files:**
- Create: `web-frontend/src/modules/product_processing/data/dimensionCanvasIconContract.test.ts`

- [ ] **Step 1: Add the icon contract test**

```ts
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { workspaceModules } from "../../../app/navigation/modules.ts";

const pageSource = readFileSync(new URL("../pages/DimensionCanvasPage.tsx", import.meta.url), "utf8");
const styleSource = readFileSync(new URL("../styles/dimension-canvas.css", import.meta.url), "utf8");
const iconfontSource = readFileSync(
  new URL("../../../../download/font_5219619_sag0ft83mnn/iconfont.css", import.meta.url),
  "utf8",
);

test("dimension canvas uses icon classes that exist in the bundled font", () => {
  const module = workspaceModules.find((candidate) => candidate.id === "dimension_canvas");
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
```

- [ ] **Step 2: Run the focused test and verify the current broken contract**

Run from `web-frontend`:

```powershell
node --test --experimental-strip-types src/modules/product_processing/data/dimensionCanvasIconContract.test.ts
```

Expected: FAIL because `workspaceModules` still uses nonexistent `icon-ruler`, and the page icon classes do not yet exist.

### Task 2: Wire the valid icon into navigation and page markup

**Files:**
- Modify: `web-frontend/src/app/navigation/modules.ts`
- Modify: `web-frontend/src/modules/product_processing/pages/DimensionCanvasPage.tsx`

- [ ] **Step 1: Replace the invalid sidebar icon class**

Change the `dimension_canvas` module entry to:

```ts
{ id: "dimension_canvas", label: "尺寸画布", icon: "", iconClass: "iconfont icon-column-width", description: "精确制作并审核商品尺寸图" },
```

- [ ] **Step 2: Add the title identity and action icons**

Replace the command-bar copy/actions and empty-state icon/button with:

```tsx
<div className="dimension-command-copy">
  <span className="dimension-eyebrow">PRODUCT PROCESSING · DETERMINISTIC CANVAS</span>
  <div className="dimension-title-row">
    <span className="dimension-title-icon iconfont icon-column-width" aria-hidden="true" />
    <h1>尺寸画布</h1>
  </div>
  <p>商品本体尺寸与箭头由结构化数据绘制；物流包裹尺寸不会进入画布。</p>
</div>
<div className="dimension-command-actions">
  <button onClick={() => setImportOpen(true)}><i className="iconfont icon-upload" aria-hidden="true" />导入已完成任务</button>
  <button onClick={() => void loadBatches()}><i className="iconfont icon-sync" aria-hidden="true" />刷新历史批次</button>
  {batch && <button className="primary" onClick={submitReview} disabled={busy !== "" || !batch.items.some((item) => item.state === "completed")}><i className="iconfont icon-check-circle" aria-hidden="true" />{busy === "review" ? "交回中…" : "交回审核"}</button>}
</div>
```

```tsx
<span className="dimension-empty-icon iconfont icon-column-width" aria-hidden="true" />
<h2>{loading ? "正在加载尺寸画布…" : "从单商品或批量任务开始"}</h2>
<p>在预检商品卡点击“添加尺寸图”，或导入已完成任务。页面刷新后草稿仍可继续。</p>
<button className="primary" onClick={() => setImportOpen(true)}><i className="iconfont icon-upload" aria-hidden="true" />导入已完成任务</button>
```

- [ ] **Step 3: Run the focused test to confirm markup and font contracts**

Run from `web-frontend`:

```powershell
node --test --experimental-strip-types src/modules/product_processing/data/dimensionCanvasIconContract.test.ts
```

Expected: the font/navigation assertions pass, while the style assertions still fail until Task 3.

### Task 3: Add restrained, theme-aware icon styling

**Files:**
- Modify: `web-frontend/src/modules/product_processing/styles/dimension-canvas.css`

- [ ] **Step 1: Add title and button icon layout rules**

Add after `.dimension-commandbar`:

```css
.dimension-command-copy {
  min-width: 0;
}

.dimension-title-row {
  display: flex;
  align-items: center;
  gap: 11px;
  margin-top: 4px;
}

.dimension-title-icon {
  display: grid;
  place-items: center;
  flex: 0 0 36px;
  width: 36px;
  height: 36px;
  border: 1px solid var(--theme-module-border-strong);
  border-radius: 10px;
  background: var(--theme-module-surface-soft);
  color: var(--theme-primary);
  font-size: 20px;
}
```

Update the heading margin and add action alignment:

```css
.dimension-commandbar h1 {
  margin-top: 0;
  font-size: 28px;
}

.dimension-command-actions button,
.dimension-empty-card > button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
}

.dimension-command-actions button .iconfont,
.dimension-empty-card > button .iconfont {
  font-size: 15px;
  line-height: 1;
}
```

- [ ] **Step 2: Run the focused contract test**

```powershell
node --test --experimental-strip-types src/modules/product_processing/data/dimensionCanvasIconContract.test.ts
```

Expected: PASS, 2 tests.

- [ ] **Step 3: Run all frontend tests**

```powershell
$tests = @(Get-ChildItem -LiteralPath 'src' -Recurse -Filter '*.test.ts' | Select-Object -ExpandProperty FullName)
node --test --experimental-strip-types $tests
```

Expected: all tests pass.

- [ ] **Step 4: Run the production build**

```powershell
npm.cmd run build
```

Expected: `tsc --noEmit` and `vite build` both succeed.

- [ ] **Step 5: Verify the live local UI**

Open `http://127.0.0.1:5173/` in the existing local browser session and verify:

- expanded and collapsed sidebar show the measurement icon;
- title and empty state use the same glyph without a missing-character box;
- import, refresh, and review buttons retain their text and show aligned icons;
- default theme and one non-default theme preserve contrast and spacing.

- [ ] **Step 6: Check and commit only the scoped files**

```powershell
git diff --check
git add -- web-frontend/src/app/navigation/modules.ts web-frontend/src/modules/product_processing/data/dimensionCanvasIconContract.test.ts web-frontend/src/modules/product_processing/pages/DimensionCanvasPage.tsx web-frontend/src/modules/product_processing/styles/dimension-canvas.css
git diff --cached --check
git commit -m "feat(product-processing): add dimension canvas icon identity"
```

Expected: one commit containing exactly the four scoped files; unrelated product-image work remains untouched.
