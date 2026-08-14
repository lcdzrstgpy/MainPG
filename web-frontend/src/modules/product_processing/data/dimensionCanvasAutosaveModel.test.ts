import assert from "node:assert/strict";
import test from "node:test";

import {
  adoptSafeServerRevision,
  autosaveBaselineFromItem,
  DimensionCanvasAutosaveConflict,
  saveWithOneSafeRevisionRebase,
} from "./dimensionCanvasAutosaveModel.ts";
import type {
  DimensionCanvasItem,
  EditorState,
} from "../types/dimensionCanvas.ts";

function editor(length = 10): EditorState {
  return {
    selectedAssetId: "asset-1",
    targetSlotId: "carousel.dimension_background",
    dimensions: {
      length: { valueCm: length, provenance: "manual_confirmed", evidenceRef: "manual" },
      width: { valueCm: 8, provenance: "manual_confirmed", evidenceRef: "manual" },
      height: { valueCm: 4, provenance: "manual_confirmed", evidenceRef: "manual" },
      conflict: false,
    },
    annotations: [],
    activeTool: "select",
    selectedAnnotationId: null,
    displayUnit: "cm",
    customValueCm: null,
  };
}

function item(input: Partial<DimensionCanvasItem> = {}): DimensionCanvasItem {
  return {
    id: "item-1",
    batchId: "batch-1",
    taskId: 1,
    taskItemId: 2,
    productDraftId: 3,
    skc: "SKC-1",
    state: "editing",
    itemRevision: 10,
    renderRevision: 0,
    renderAssetId: "",
    sourcePreviewRevision: 7,
    assets: [],
    editor: editor(),
    errorCode: "",
    errorMessage: "",
    ...input,
  };
}

function conflict(): Error & { status: number } {
  return Object.assign(new Error("revision conflict"), { status: 409 });
}

test("lifecycle-only server revision advances the same editor baseline", () => {
  const baseline = autosaveBaselineFromItem(item());
  const adopted = adoptSafeServerRevision(baseline, item({
    state: "completed",
    itemRevision: 12,
    renderRevision: 1,
    renderAssetId: "render-1",
  }));
  assert.equal(adopted?.itemRevision, 12);
});

test("changed remote editor, upstream preview, or item identity stays fail-closed", () => {
  const baseline = autosaveBaselineFromItem(item());
  assert.equal(adoptSafeServerRevision(baseline, item({ itemRevision: 11, editor: editor(12) })), null);
  assert.equal(adoptSafeServerRevision(baseline, item({ itemRevision: 11, sourcePreviewRevision: 8 })), null);
  assert.equal(adoptSafeServerRevision(baseline, item({ id: "item-2", itemRevision: 11 })), null);
});

test("a lifecycle 409 rebases once and saves the local editor on the latest revision", async () => {
  const revisions: number[] = [];
  const localEditor = editor(12);
  const result = await saveWithOneSafeRevisionRebase({
    baseline: autosaveBaselineFromItem(item()),
    editor: localEditor,
    saveAtRevision: async (snapshot, expectedRevision) => {
      revisions.push(expectedRevision);
      if (revisions.length === 1) throw conflict();
      return item({ itemRevision: 13, editor: snapshot });
    },
    loadRemote: async () => item({ state: "completed", itemRevision: 12, renderRevision: 1 }),
  });
  assert.deepEqual(revisions, [10, 12]);
  assert.equal(result.rebased, true);
  assert.equal(result.saved.editor.dimensions.length.valueCm, 12);
  assert.equal(result.baseline.itemRevision, 13);
});

test("a concurrent remote editor change never receives a second write", async () => {
  let saveCalls = 0;
  await assert.rejects(
    saveWithOneSafeRevisionRebase({
      baseline: autosaveBaselineFromItem(item()),
      editor: editor(12),
      saveAtRevision: async () => {
        saveCalls += 1;
        throw conflict();
      },
      loadRemote: async () => item({ itemRevision: 11, editor: editor(9) }),
    }),
    (cause) => cause instanceof DimensionCanvasAutosaveConflict
      && cause.remoteItem?.itemRevision === 11,
  );
  assert.equal(saveCalls, 1);
});

test("a second 409 stops after one rebase and exposes the newest remote snapshot", async () => {
  const revisions: number[] = [];
  let loads = 0;
  await assert.rejects(
    saveWithOneSafeRevisionRebase({
      baseline: autosaveBaselineFromItem(item()),
      editor: editor(12),
      saveAtRevision: async (_snapshot, expectedRevision) => {
        revisions.push(expectedRevision);
        throw conflict();
      },
      loadRemote: async () => {
        loads += 1;
        return item({ itemRevision: loads === 1 ? 12 : 13, editor: loads === 1 ? editor() : editor(9) });
      },
    }),
    (cause) => cause instanceof DimensionCanvasAutosaveConflict
      && cause.remoteItem?.itemRevision === 13,
  );
  assert.deepEqual(revisions, [10, 12]);
  assert.equal(loads, 2);
});
