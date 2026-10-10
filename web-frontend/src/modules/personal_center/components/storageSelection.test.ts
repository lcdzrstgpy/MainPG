import assert from "node:assert/strict";
import test from "node:test";

import { computeScanSelection } from "./storageSelection.ts";

function category(key: string, files: number, bytes = files * 100) {
  return { key, files, bytes };
}

test("首次扫描：默认勾选全部可清理项，跳过已干净的分类", () => {
  const selection = computeScanSelection(
    [
      category("published_release", 2),
      category("terminal_intermediates", 0),
      category("disk_orphans", 5),
    ],
    new Set(),
    true,
  );
  assert.deepEqual([...selection].sort(), ["disk_orphans", "published_release"]);
});

test("后续扫描：保留用户既有勾选，只丢弃已清空的分类", () => {
  const previous = new Set(["published_release", "disk_orphans"]);
  const next = computeScanSelection(
    [
      category("published_release", 3),
      category("terminal_intermediates", 4),
      category("disk_orphans", 0),
    ],
    previous,
    false,
  );
  assert.deepEqual([...next], ["published_release"]);
});

test("后续扫描不会重新勾上用户取消过的分类", () => {
  const previous = new Set(["unreferenced_residue"]);
  const next = computeScanSelection(
    [category("published_release", 3), category("unreferenced_residue", 2)],
    previous,
    false,
  );
  assert.deepEqual([...next], ["unreferenced_residue"]);
});

test("全部已干净时返回空集合", () => {
  const next = computeScanSelection([category("a", 0), category("b", 0)], new Set(["a"]), false);
  assert.equal(next.size, 0);
});
