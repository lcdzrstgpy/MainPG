import assert from "node:assert/strict";
import test from "node:test";

import {
  mapChangeSet,
  mapDimensionNotifications,
  serializeSaveDimensionItemRequest,
} from "./dimensionCanvasWire.ts";

test("save payload explicitly serializes nested editor state to snake case", () => {
  const payload = serializeSaveDimensionItemRequest({
    expected_revision: 9,
    selected_source_asset_id: "asset-1",
    target_slot_id: "carousel.dimension_background",
    physical_dimensions: {
      length: { valueCm: 12, provenance: "manual_confirmed", evidenceRef: "manual" },
      width: { valueCm: 8, provenance: "source_confirmed", evidenceRef: "source.width" },
      height: { valueCm: null, provenance: "unconfirmed", evidenceRef: "" },
      conflict: false,
    },
    annotations: [{
      id: "annotation-1",
      key: "length",
      valueCm: 12,
      start: { x: .1, y: .8 },
      end: { x: .9, y: .8 },
      label: { x: .5, y: .75 },
      style: "auto",
    }],
    canvas_settings: { fit: "contain", style: "auto" },
  });

  const dimensions = payload.physical_dimensions as Record<string, Record<string, unknown>>;
  const annotations = payload.annotations as Array<Record<string, unknown>>;
  assert.deepEqual(dimensions.length, {
    value_cm: 12,
    provenance: "manual_confirmed",
    evidence_ref: "manual",
  });
  assert.equal("valueCm" in dimensions.length, false);
  assert.equal(annotations[0].value_cm, 12);
  assert.equal("valueCm" in annotations[0], false);
  assert.deepEqual(annotations[0].start, { x: .1, y: .8 });
});

test("change mapper reads nested safe URLs and never managed_path", () => {
  const changeSet = mapChangeSet({
    id: "change-1",
    items: [{
      id: "item-1",
      base_asset: { slot: { url: "/safe/old.jpg" }, managed_path: "C:/private/old.jpg" },
      replacement_asset: { preview_url: "/safe/new.jpg", managed_path: "C:/private/new.jpg" },
      physical_dimensions: {},
    }],
  });
  assert.equal(changeSet.items[0].oldImageUrl, "/safe/old.jpg");
  assert.equal(changeSet.items[0].newImageUrl, "/safe/new.jpg");
  assert.equal(changeSet.items[0].oldImageUrl.includes("private"), false);
  assert.equal(changeSet.items[0].newImageUrl.includes("private"), false);
});

test("notification mapper falls back to nested payload", () => {
  const notifications = mapDimensionNotifications({
    notifications: [{
      id: "notice-1",
      payload: {
        change_set_id: "change-1",
        source_task_id: 42,
        completed_count: 3,
        failed_count: 1,
        conflict_count: 2,
      },
    }],
  });
  assert.deepEqual(notifications[0], {
    id: "notice-1",
    changeSetId: "change-1",
    sourceTaskId: 42,
    completedCount: 3,
    failedCount: 1,
    conflictCount: 2,
    createdAt: "",
    read: false,
  });
});
