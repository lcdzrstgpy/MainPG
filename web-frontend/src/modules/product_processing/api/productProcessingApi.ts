import { apiRequest } from "../../../shared/api/apiClient";
import { ppRequest, ppUpload, type ApiContext } from "./client";
import type {
  DraftMediaResponse,
  MediaAssetView,
  PreviewCoreFields,
  PreviewFinalizeRun,
  PreviewImageAsset,
  PreviewImageManifest,
  PreviewResponse,
} from "../types";

export type DraftSourceType = "web_manual_capture" | "onebound_api";

export type PrimarySourceImage = {
  sync_status: "pending" | "syncing" | "ready" | "failed";
  sync_error: string;
};

export type ProductDraft = {
  id: number;
  source_type: DraftSourceType;
  source_ref: string;
  title: string;
  image_url: string;
  image_path: string;
  primary_source_image: PrimarySourceImage | null;
  raw_payload: {
    source_platform?: string;
    collection_mode?: string;
  };
};

export async function listProductDrafts(sourceType?: DraftSourceType): Promise<ProductDraft[]> {
  const query = sourceType ? `?source_type=${encodeURIComponent(sourceType)}` : "";
  return (await apiRequest<{ drafts: ProductDraft[] }>(`/product-processing/drafts${query}`)).drafts;
}

export function retryProductDraftSourceImages(draftId: number): Promise<{ sync: { status: string } }> {
  return apiRequest(`/product-processing/drafts/${encodeURIComponent(String(draftId))}/source-images/retry`, {
    method: "POST",
  });
}

export type PreviewSavePayload = {
  product_draft_id: number;
  expected_preview_revision: number;
  expected_result_version: string;
  overrides: {
    title: string;
    description: string;
    core_fields: PreviewCoreFields;
    image_manifest_v2: PreviewImageManifest;
  };
};

export type PreviewSaveResponse = {
  saved_count: number;
  items: Array<{ product_draft_id: number; preview_revision: number }>;
};

export async function uploadPreviewAssets(
  ctx: ApiContext,
  taskId: number,
  draftId: number,
  files: File[],
): Promise<{ assets: PreviewImageAsset[] }> {
  const form = new FormData();
  form.append("draft_id", String(draftId));
  files.forEach((file) => form.append("image_files", file));
  return ppUpload(ctx, `/api/product-processing/tasks/${taskId}/preview/assets`, form);
}

export function saveProductPreview(
  ctx: ApiContext,
  taskId: number,
  items: PreviewSavePayload[],
): Promise<PreviewSaveResponse> {
  return ppRequest(ctx, `/api/product-processing/tasks/${taskId}/preview`, {
    method: "PATCH",
    body: { items },
  });
}

export function excludePreviewItem(
  ctx: ApiContext,
  taskId: number,
  draftId: number,
): Promise<PreviewResponse> {
  return ppRequest(
    ctx,
    `/api/product-processing/tasks/${taskId}/preview/items/${draftId}/exclude`,
    { method: "POST", body: {} },
  );
}

export function restorePreviewItem(
  ctx: ApiContext,
  taskId: number,
  draftId: number,
): Promise<PreviewResponse> {
  return ppRequest(
    ctx,
    `/api/product-processing/tasks/${taskId}/preview/items/${draftId}/restore`,
    { method: "POST", body: {} },
  );
}

export function finalizeProductPreview(
  ctx: ApiContext,
  taskId: number,
  items: PreviewSavePayload[],
  idempotencyKey: string,
): Promise<PreviewFinalizeRun> {
  return ppRequest(ctx, `/api/product-processing/tasks/${taskId}/preview/finalize`, {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey },
    body: { items },
  });
}

export function getPreviewFinalizeRun(
  ctx: ApiContext,
  taskId: number,
  runId: string,
): Promise<PreviewFinalizeRun> {
  return ppRequest(
    ctx,
    `/api/product-processing/tasks/${taskId}/preview/finalize/${encodeURIComponent(runId)}`,
  );
}

export function retryPreviewFinalizeRun(
  ctx: ApiContext,
  taskId: number,
  runId: string,
): Promise<PreviewFinalizeRun> {
  return ppRequest(
    ctx,
    `/api/product-processing/tasks/${taskId}/preview/finalize/${encodeURIComponent(runId)}/retry`,
    { method: "POST", body: {} },
  );
}

export function getDraftMedia(
  ctx: ApiContext,
  draftId: number,
): Promise<DraftMediaResponse> {
  return ppRequest(ctx, `/api/product-processing/drafts/${draftId}/media`);
}

export function retryMediaAsset(
  ctx: ApiContext,
  assetId: string,
): Promise<MediaAssetView> {
  return ppRequest(
    ctx,
    `/api/product-processing/media-assets/${encodeURIComponent(assetId)}/retry`,
    { method: "POST", body: {} },
  );
}
