import assert from "node:assert/strict";
import test from "node:test";

import {
  flattenDraftMediaGroups,
  mediaStatusLabel,
  supportsMediaRetry,
} from "./draftMediaModel.ts";

const groups = {
  main: [{
    binding_id: "main-binding",
    asset_id: "main-asset",
    role: "main",
    slot_id: "",
    sku_id: "",
    variant_label: "",
    sort_order: 0,
    status: "ready" as const,
    preview_url: "/media/main",
    width: 800,
    height: 800,
    content_type: "image/jpeg",
    error_code: "",
    error_message: "",
  }],
  gallery: [],
  detail: [],
  sku: [{
    binding_id: "sku-binding",
    asset_id: "sku-asset",
    role: "sku",
    slot_id: "",
    sku_id: "SKU-RED",
    variant_label: "红色",
    sort_order: 1,
    status: "retryable" as const,
    preview_url: "",
    width: 0,
    height: 0,
    content_type: "",
    error_code: "download_failed",
    error_message: "temporary upstream failure",
  }],
  carousel: [],
  dimension: [],
};

test("V2 media panel preserves role grouping and SKU identity", () => {
  const flattened = flattenDraftMediaGroups(groups);
  assert.deepEqual(flattened.map(({ group, media }) => [group, media.asset_id, media.sku_id]), [
    ["main", "main-asset", ""],
    ["sku", "sku-asset", "SKU-RED"],
  ]);
});

test("only retryable and failed V2 media expose retry actions", () => {
  assert.equal(supportsMediaRetry("ready"), false);
  assert.equal(supportsMediaRetry("retryable"), true);
  assert.equal(supportsMediaRetry("failed"), true);
  assert.equal(mediaStatusLabel("materializing"), "同步中");
});
