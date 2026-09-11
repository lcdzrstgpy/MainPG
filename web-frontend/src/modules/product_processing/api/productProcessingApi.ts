import { apiRequest } from "../../../shared/api/apiClient";
import { ppRequest, ppUpload, type ApiContext } from "./client";
import type {
  DraftMediaResponse,
  MediaAssetView,
  MiaoshouExportResponse,
  MiaoshouTemplateKind,
  PreviewCoreFields,
  PreviewExportFormat,
  PreviewFinalizeRun,
  PreviewImageAsset,
  PreviewImageManifest,
  PreviewResponse,
  ShippingPackageRecordOverride,
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

export type DraftCollectionBatch = {
  batch_id: string;
  source_type: string;
  collection_channel: string;
  platform: string;
  channel_name?: string;
  count: number;
  first_created_at: string;
  latest_updated_at: string;
};

export async function listDraftBatches(limit = 100, offset = 0): Promise<{ batches: DraftCollectionBatch[]; pagination: { has_more: boolean } }> {
  return apiRequest(`/product-processing/draft-batches?limit=${limit}&offset=${offset}`);
}

export async function deleteDraftBatch(batchId: string): Promise<{ deleted_count: number }> {
  return apiRequest(`/product-processing/draft-batches/${encodeURIComponent(batchId)}/delete`, {
    method: "POST",
  });
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
    shipping_package_records?: Record<string, ShippingPackageRecordOverride>;
    variant_image_mode?: "source" | "main";
    /** 被整行剔除的 SKU 变种键：导出时该变种不产生表格行。 */
    excluded_variant_keys?: string[];
    /** 逐个 SKU 指定的规格图：键=变种键，值=预览资产 ID 或 http(s) 图片地址。 */
    variant_image_overrides?: Record<string, string>;
  };
};

export type PreviewSaveResponse = {
  saved_count: number;
  items: Array<{ product_draft_id: number; preview_revision: number }>;
};

export type ListingAdvice = {
  level: string;
  action: string;
  recommended_category: string;
  reason: string;
  warning: string;
  required_documents: string[];
  matched_rule_number: number;
  matched_rule: string;
  source: "ai+rules" | "rules";
  notice: string;
};

export function getListingAdvice(
  ctx: ApiContext,
  taskId: number,
  draftId: number,
  input: { title: string; description: string; category_path: string },
): Promise<ListingAdvice> {
  return ppRequest(
    ctx,
    `/api/product-processing/tasks/${taskId}/preview/items/${draftId}/listing-advice`,
    {
      method: "POST",
      body: { ...input, request_id: crypto.randomUUID() },
    },
  );
}

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

/** 把外部图片经后端图床转存成本商品的预览资产，拿到同源地址后即可框选裁剪。 */
export async function importPreviewAssetFromUrl(
  ctx: ApiContext,
  taskId: number,
  draftId: number,
  url: string,
): Promise<{ asset: PreviewImageAsset }> {
  return ppRequest(ctx, `/api/product-processing/tasks/${taskId}/preview/assets/import-url`, {
    method: 'POST',
    body: { draft_id: draftId, url },
  });
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

export function regeneratePreviewDetail(
  ctx: ApiContext,
  taskId: number,
  draftId: number,
): Promise<PreviewResponse> {
  return ppRequest(
    ctx,
    `/api/product-processing/tasks/${taskId}/preview/items/${draftId}/regenerate-detail`,
    { method: "POST", body: {} },
  );
}

export function finalizeProductPreview(
  ctx: ApiContext,
  taskId: number,
  items: PreviewSavePayload[],
  idempotencyKey: string,
  exportFormat: PreviewExportFormat = "dxm",
): Promise<PreviewFinalizeRun> {
  return ppRequest(ctx, `/api/product-processing/tasks/${taskId}/preview/finalize`, {
    method: "POST", headers: { "Idempotency-Key": idempotencyKey },
    body: { items, export_format: exportFormat },
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

/** 基于已完成预审的最终快照再次生成妙手导入模板（服饰类/非服饰类）。 */
export function exportMiaoshouPreview(
  ctx: ApiContext,
  taskId: number,
  runId: string,
  kind: MiaoshouTemplateKind,
): Promise<MiaoshouExportResponse> {
  return ppRequest(
    ctx,
    `/api/product-processing/tasks/${taskId}/preview/finalize/${encodeURIComponent(runId)}/export-miaoshou`,
    { method: "POST", body: { kind } },
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
