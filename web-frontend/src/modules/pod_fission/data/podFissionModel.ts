import type { PodBatch } from "../../pod_customization/types";
import type { FissionBatch } from "../types";

/** Supply presentation defaults for hidden template metadata, preserving all delivery fields. */
export type FissionBatchView = PodBatch & Pick<FissionBatch, "source" | "target" | "variants" | "generation_strategy">;
export function normalizeFissionBatch(batch: FissionBatch): FissionBatchView {
  return {
    ...batch,
    id: batch.id || batch.batch_id,
    template_id: "",
    template_name: "爆款裂变",
    template: { id: "", name: "爆款裂变", source: "personal", preview_url: batch.target?.preview_url ?? "", original_url: "", width: 0, height: 0, created_at: batch.created_at ?? "", updated_at: batch.updated_at ?? "" },
    prompt_version: "v1",
    creative_prompt: "",
    items: batch.items ?? [],
    dianxiaomi_export: batch.dianxiaomi_export ?? { ready: false, exportable_style_count: 0, skipped_style_count: batch.count, block_reason: "no_completed_styles" },
  };
}

export function fissionBatchViewStatus(batch: PodBatch): "queued" | "running" | "partial" | "completed" | "failed" | "cancelled" {
  if (batch.status === "completed" || batch.status === "failed" || batch.status === "cancelled" || batch.status === "queued") return batch.status;
  if (batch.status === "partial_failure" || batch.completed_count > 0) return "partial";
  return "running";
}
