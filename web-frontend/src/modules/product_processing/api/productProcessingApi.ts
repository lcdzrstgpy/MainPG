import { apiRequest } from "../../../shared/api/apiClient";

export type DraftSourceType = "web_manual_capture" | "onebound_api";

export type ProductDraft = {
  id: number;
  source_type: DraftSourceType;
  source_ref: string;
  title: string;
  image_url: string;
  image_path: string;
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
