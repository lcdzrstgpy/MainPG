import assert from "node:assert/strict";
import test from "node:test";

import {
  addAnnotation,
  canComplete,
  changeDimensionValue,
  changeDisplayUnit,
  formatDimension,
  invalidateRenderOnEdit,
  nextQueueItem,
} from "./dimensionCanvasModel.ts";
import type {
  DimensionAnnotation,
  DimensionKey,
  EditorState,
} from "../types/dimensionCanvas.ts";

function annotation(id: string, key: DimensionKey, valueCm: number): DimensionAnnotation {
  return {
    id,
    key,
    valueCm,
    start: { x: 0.1, y: 0.8 },
    end: { x: 0.9, y: 0.8 },
    label: { x: 0.5, y: 0.75 },
    style: "auto",
    unit: "cm",
  };
}

function fixtureState(input: { length?: number; annotations?: DimensionAnnotation[] } = {}): EditorState {
  return {
    selectedAssetId: "asset-1",
    targetSlotId: "carousel.dimension_background",
    dimensions: {
      length: { valueCm: input.length ?? 10, provenance: "manual_confirmed", evidenceRef: "manual" },
      width: { valueCm: 8, provenance: "manual_confirmed", evidenceRef: "manual" },
      height: { valueCm: 4, provenance: "manual_confirmed", evidenceRef: "manual" },
      conflict: false,
    },
    annotations: input.annotations ?? [],
    activeTool: "select",
    selectedAnnotationId: null,
    displayUnit: "cm",
    customValueCm: null,
  };
}

test("dimension value change updates every semantic annotation", () => {
  const state = fixtureState({
    length: 10,
    annotations: [annotation("a", "length", 10), annotation("b", "length", 10)],
  });
  const next = changeDimensionValue(state, "length", 12);
  assert.deepEqual(next.annotations.map((item) => item.valueCm), [12, 12]);
});

test("annotation points are normalized and immutable", () => {
  const state = fixtureState();
  const next = addAnnotation(state, "length", { x: -0.1, y: 0.8 }, { x: 1.2, y: 0.8 });
  assert.equal(state.annotations.length, 0);
  assert.deepEqual(next.annotations[0].start, { x: 0, y: 0.8 });
  assert.deepEqual(next.annotations[0].end, { x: 1, y: 0.8 });
});

test("queue navigation keeps sparse stable ids", () => {
  assert.equal(nextQueueItem(["item-2", "item-5", "item-9"], "item-5", 1), "item-9");
});

test("package estimates never satisfy the completion gate", () => {
  const state = fixtureState({ annotations: [annotation("a", "length", 10)] });
  state.dimensions.length.provenance = "package_estimate";
  assert.deepEqual(canComplete(state), { ok: false, reason: "标注尺寸尚未确认" });
});

test("semantic edits invalidate an old render before another submit", () => {
  const editor = fixtureState();
  const item = {
    id: "item-1",
    batchId: "batch-1",
    taskId: 1,
    taskItemId: 2,
    productDraftId: 3,
    skc: "SKC-1",
    state: "completed" as const,
    itemRevision: 4,
    renderRevision: 2,
    renderAssetId: "render-old",
    sourcePreviewRevision: 7,
    assets: [],
    editor,
    errorCode: "",
    errorMessage: "",
  };
  const next = invalidateRenderOnEdit(item, changeDimensionValue(editor, "length", 12));
  assert.equal(next.state, "editing");
  assert.equal(next.renderRevision, 0);
  assert.equal(next.renderAssetId, "");
});

test("display-unit changes preserve canonical centimeters and relabel annotations", () => {
  const state = fixtureState({ annotations: [annotation("a", "length", 30.48)] });
  const next = changeDisplayUnit(state, "ft");
  assert.equal(next.dimensions.length.valueCm, 10);
  assert.equal(next.annotations[0].valueCm, 30.48);
  assert.equal(next.annotations[0].unit, "ft");
  assert.equal(formatDimension(next.annotations[0].valueCm, "ft"), "1 ft");
});

test("custom annotation requires a positive value before completion", () => {
  const state = fixtureState({ annotations: [annotation("a", "custom", 0)] });
  assert.deepEqual(canComplete(state), { ok: false, reason: "尺寸数值必须大于 0" });
});
