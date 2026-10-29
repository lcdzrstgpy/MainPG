import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./Sidebar.tsx", import.meta.url), "utf8");
const globalStyles = readFileSync(new URL("../../shared/styles/global.css", import.meta.url), "utf8");
const themeStyles = readFileSync(new URL("../../shared/styles/themes.css", import.meta.url), "utf8");

test("sidebar distinguishes group expansion from opening a concrete page", () => {
  assert.match(source, /onToggleGroup:/);
  assert.match(source, /onToggleSubgroup:/);
  assert.match(source, /onOpenModule:/);
  assert.match(source, /isWorkspaceNavigationGroup\(module\)/);
  assert.match(source, /onToggleGroup\(module\)/);
  assert.match(source, /onOpenModule\(item\.id\)/);
});

test("collapsed flyout closes after selecting a page or clicking outside", () => {
  assert.match(source, /onCloseGroups:/);
  assert.match(source, /flyout: boolean/);
  assert.match(source, /document\.addEventListener\("pointerdown"/);
  assert.match(source, /if \(flyout\) onCloseGroups\(\)/);
});

test("sidebar renders a third navigation level for nested subgroups", () => {
  assert.match(source, /isWorkspaceNavigationGroup\(child\)/);
  assert.match(source, /SubgroupSection/);
  assert.match(source, /expandedSubgroupId === child\.id/);
  assert.match(source, /sidebar-subgroup-items/);
  assert.match(globalStyles, /\.sidebar-subgroup-toggle/);
  assert.match(globalStyles, /\.sidebar-subitem\.is-nested/);
});

test("sidebar exposes the current accordion state to assistive technology", () => {
  assert.match(source, /expandedGroupId === module\.id/);
  assert.match(source, /aria-expanded=\{isGroup \? groupExpanded : undefined\}/);
});

test("sidebar keeps group affordances readable in expanded and collapsed states", () => {
  assert.match(globalStyles, /\.sidebar-group-caret\s*\{/);
  assert.match(globalStyles, /\.sidebar-group-caret\.is-expanded\s*\{/);
  assert.match(globalStyles, /\.sidebar-card\.is-collapsed \.sidebar-group-caret\s*\{/);
  assert.match(globalStyles, /\.sidebar-subitem \.sidebar-module-badge\s*\{/);
});

test("sidebar uses a light disclosure icon and keeps submenu content available for animation", () => {
  assert.match(source, /sidebar-group-caret iconfont icon-down/);
  assert.match(source, /sidebar-submenu \$\{groupExpanded \? "is-expanded" : ""\}/);
  assert.match(source, /aria-hidden=\{!groupExpanded\}/);
});

test("submenu animation is soft and the active child has no left accent border", () => {
  assert.match(globalStyles, /\.sidebar-submenu\s*\{/);
  assert.match(globalStyles, /\.sidebar-submenu\.is-expanded\s*\{/);
  assert.match(globalStyles, /\.sidebar-submenu-inner\s*\{/);
  assert.match(globalStyles, /translateY\(-6px\)/);
  const activeChildRule = globalStyles.match(/\.sidebar-subitem\.is-active\s*\{([^}]*)\}/)?.[1] ?? "";
  assert.doesNotMatch(activeChildRule, /inset\s+3px\s+0/);
  assert.doesNotMatch(themeStyles, /\.sidebar-subitem\.is-active\s*\{[^}]*inset\s+3px\s+0/);
});

test("narrow desktop keeps the collapsed sidebar narrow without shrinking the content grid", () => {
  assert.match(globalStyles, /@media \(min-width: 801px\) and \(max-width: 1100px\)/);
  assert.match(globalStyles, /\.sidebar-card\.is-collapsed\s*\{\s*width: var\(--workspace-sidebar-width\);/);
  assert.match(globalStyles, /\.workspace-shell:has\(\.sidebar-card:hover\)\s*\{\s*grid-template-columns: 72px minmax\(0, 1fr\);/);
});
