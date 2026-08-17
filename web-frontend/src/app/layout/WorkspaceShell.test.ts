import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./WorkspaceShell.tsx", import.meta.url), "utf8");

test("workspace shell keeps one expanded navigation group and opens a group's default page", () => {
  assert.match(source, /useState<WorkspaceNavigationGroupId \| null>\(null\)/);
  assert.match(source, /const openNavigationGroup = \(group: WorkspaceNavigationGroup\)/);
  assert.match(source, /setExpandedGroupId\(group\.id\)/);
  assert.match(source, /openModule\(group\.defaultChildId\)/);
});

test("workspace shell wires group and module actions separately into the sidebar", () => {
  assert.match(source, /expandedGroupId=\{expandedGroupId\}/);
  assert.match(source, /onOpenModule=\{openModule\}/);
  assert.match(source, /onToggleGroup=\{openNavigationGroup\}/);
});

test("narrow desktop keeps workspace content wide while the sidebar becomes a hover overlay", () => {
  assert.match(source, /NARROW_DESKTOP_QUERY = "\(min-width: 801px\) and \(max-width: 1100px\)"/);
  assert.match(source, /window\.matchMedia\(NARROW_DESKTOP_QUERY\)/);
  assert.match(source, /const sidebarIsCollapsed = sidebarCollapsed \|\| isNarrowDesktop/);
  assert.match(source, /collapsed=\{sidebarIsCollapsed && !sidebarTemporarilyExpanded\}/);
  assert.match(source, /sidebarPinned=\{!sidebarIsCollapsed\}/);
});
