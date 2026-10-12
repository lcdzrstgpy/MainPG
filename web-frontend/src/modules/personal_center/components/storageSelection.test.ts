import assert from "node:assert/strict";
import test from "node:test";

import { computeScanSelection } from "./storageSelection.ts";

function category(key: string, files: number, bytes = files * 100) {
  return { key, files, bytes };
}

test("首次扫描：只默认勾「纯垃圾」分类，有保留价值的分类不替用户勾", () => {
  const selection = computeScanSelection(
    [
      category("published_release", 2),
      category("terminal_intermediates", 4),
      category("disk_orphans", 5),
      category("pp_review_images", 9),
    ],
    new Set(),
    true,
  );
  // 纯垃圾默认勾上；published_release（结果图本地副本）与 pp_review_images（复核留档）
  // 默认不勾——删除决定留给用户。
  assert.deepEqual([...selection].sort(), ["disk_orphans", "terminal_intermediates"]);
});

test("首次扫描：产品处理的纯垃圾分类默认勾选", () => {
  const selection = computeScanSelection(
    [category("pp_asset_orphans", 1), category("pp_disk_orphans", 2), category("pp_review_images", 3)],
    new Set(),
    true,
  );
  assert.deepEqual([...selection].sort(), ["pp_asset_orphans", "pp_disk_orphans"]);
});

test("后续扫描：保留用户既有勾选（含手动勾上的保留类），只丢弃已清空的分类", () => {
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
