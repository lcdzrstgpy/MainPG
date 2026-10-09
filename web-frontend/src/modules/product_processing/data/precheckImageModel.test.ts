import assert from "node:assert/strict";
import test from "node:test";
import {
  addAssets,
  moveAsset,
  promoteToLibrary,
  removeAsset,
  removeFromLibrary,
  restoreRemovedAsset,
  selectMainAsset,
} from "./precheckImageModel.ts";

const base = {
  main_asset_id: "a",
  carousel_asset_ids: ["a", "b", "c"],
  detail_asset_ids: ["d"],
  library_asset_ids: [],
  semantic_asset_ids: { "carousel.hero": "a" },
};

test("carousel add appends and never replaces main", () => {
  assert.deepEqual(addAssets(base, "carousel", ["x", "y"]), {
    ...base,
    carousel_asset_ids: ["a", "b", "c", "x", "y"],
  });
});

test("removing main selects the next carousel asset", () => {
  const result = removeAsset(base, "carousel", "a");
  assert.equal(result.manifest.main_asset_id, "b");
  assert.deepEqual(result.manifest.carousel_asset_ids, ["b", "c"]);
  assert.deepEqual(restoreRemovedAsset(result.manifest, result.undo), base);
});

test("removing all details preserves an explicit empty list", () => {
  const result = removeAsset(base, "detail", "d");
  assert.deepEqual(result.manifest.detail_asset_ids, []);
});

test("reorder keeps first carousel as main and select-main moves to index zero", () => {
  const moved = moveAsset(base, "carousel", "c", -1);
  assert.deepEqual(moved.carousel_asset_ids, ["a", "c", "b"]);
  assert.equal(moved.main_asset_id, "a");
  const selected = selectMainAsset(base, "c");
  assert.equal(selected.main_asset_id, "c");
  assert.deepEqual(selected.carousel_asset_ids, ["c", "a", "b"]);
});

test("selecting an asset outside carousel moves it to position one", () => {
  const selected = selectMainAsset(base, "x");
  assert.equal(selected.main_asset_id, "x");
  assert.deepEqual(selected.carousel_asset_ids, ["x", "a", "b", "c"]);
  assert.deepEqual(selectMainAsset(selected, "x").carousel_asset_ids, selected.carousel_asset_ids);
});

test("new main is prepended to carousel exactly once", () => {
  const added = addAssets(base, "main", ["x"]);
  assert.equal(added.main_asset_id, "x");
  assert.deepEqual(added.carousel_asset_ids, ["x", "a", "b", "c"]);
  assert.deepEqual(addAssets(added, "main", ["x"]).carousel_asset_ids, added.carousel_asset_ids);
});

test("multi-file additions deduplicate and preserve order", () => {
  const withLegacyDuplicate = { ...base, carousel_asset_ids: ["a", "b", "a"] };
  assert.deepEqual(
    addAssets(withLegacyDuplicate, "carousel", ["x", "x", "b", "y"]).carousel_asset_ids,
    ["a", "b", "x", "y"],
  );
});

test("deleting the final carousel image leaves a visible no-main state", () => {
  const result = removeAsset(
    { main_asset_id: "a", carousel_asset_ids: ["a"], detail_asset_ids: [], library_asset_ids: [], semantic_asset_ids: {} },
    "carousel",
    "a",
  );
  assert.equal(result.manifest.main_asset_id, "");
  assert.deepEqual(result.manifest.carousel_asset_ids, []);
});

test("setting main moves it to carousel position one", () => {
  const next = selectMainAsset(base, "b");
  assert.deepEqual(next.carousel_asset_ids, ["b", "a", "c"]);
  assert.equal(next.main_asset_id, "b");
});

test("moving carousel changes main with the first card", () => {
  const next = moveAsset(base, "carousel", "b", -1);
  assert.equal(next.main_asset_id, "b");
});

test("promote and remove library membership", () => {
  const promoted = promoteToLibrary(base, "source-1");
  assert.deepEqual(promoted.library_asset_ids, ["source-1"]);
  const removed = removeFromLibrary(promoted, "source-1");
  assert.deepEqual(removed.library_asset_ids, []);
});
