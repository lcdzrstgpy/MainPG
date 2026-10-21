import { cfJson, type CfInit } from "../clipforgeClient";
import type { IngestProductInput, IngestProductResult, UploadResult } from "../types";

/** POST /api/ingest/product —— 商品链接导入（默认同时建项目并下载前 3 张图）。 */
export function ingestProduct(input: IngestProductInput, init?: CfInit) {
  return cfJson<IngestProductResult>("/api/ingest/product", {
    ...init,
    method: "POST",
    json: input,
  });
}

/** POST /api/products/upload —— 商品库图片（不绑定项目），每张 ≤20MB。 */
export function uploadProductImages(productId: string, files: readonly File[], signal?: AbortSignal) {
  const form = new FormData();
  form.set("productId", productId);
  for (const file of files) form.append("files", file);
  return cfJson<UploadResult>("/api/products/upload", { method: "POST", body: form, signal });
}
