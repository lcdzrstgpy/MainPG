import assert from "node:assert/strict";
import test from "node:test";
import { fissionBatchViewStatus, normalizeFissionBatch } from "./podFissionModel.ts";

test("shared fission DTO preserves ids, images and titles for the gallery", () => {
  const raw = { id: "batch-1", batch_id: "batch-1", count: 2, status: "partial_failure", items: [{ id: "image-1", style_index: 1, status: "completed", public_url: "https://example.com/1.png" }], style_titles: [{ style_index: 1, status: "completed", title: "Moon Bag", listing_ready: true }], dianxiaomi_export: { ready: true, exportable_style_count: 1, skipped_style_count: 1, block_reason: null } };
  const batch = normalizeFissionBatch(raw as never);
  assert.equal(batch.id, "batch-1");
  assert.equal(batch.count, 2);
  assert.equal(batch.template_name, "爆款裂变");
  assert.equal(batch.items[0].public_url, raw.items[0].public_url);
  assert.equal(batch.style_titles?.[0].title, "Moon Bag");
  assert.equal(batch.dianxiaomi_export.ready, true);
  assert.equal(fissionBatchViewStatus(batch), "partial");
});

test("a running batch exposes completed styles immediately", () => {
  assert.equal(fissionBatchViewStatus(normalizeFissionBatch({ id: "b", count: 2, status: "generating_titles", completed_count: 1 } as never)), "partial");
  assert.equal(fissionBatchViewStatus(normalizeFissionBatch({ id: "b", count: 2, status: "generating_titles", completed_count: 0 } as never)), "running");
});
