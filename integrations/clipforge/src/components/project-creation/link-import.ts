import { MAX_SOURCE_IMAGES } from "./creation-brief-defaults";
import type { CreationBriefFormValues } from "./creation-brief-form";

/**
 * 商品链接导入的共享实现：两个创建入口都只把它当作「预填简报」的来源。
 *
 * 设计 §10 要求链接导入能预填统一表单、由用户在提交前查看与修改，所以这里绝不创建项目——
 * 项目一律由入口页带着 `creationBrief` 走 POST /api/project。
 */

/** 商品链接是否合法（http/https）。 */
export function isValidProductUrl(url: string): boolean {
  return /^https?:\/\/.+/i.test(url.trim());
}

/** 把远端/本地的商品图抓成 File，走正常的上传链路；抓不到就留给用户手工上传。 */
export async function fetchImagesAsFiles(urls: string[]): Promise<CreationBriefFormValues["images"]> {
  const files: CreationBriefFormValues["images"] = [];
  for (const [index, src] of urls.slice(0, MAX_SOURCE_IMAGES).entries()) {
    try {
      const res = await fetch(src);
      if (!res.ok) continue;
      const blob = await res.blob();
      files.push({
        id: crypto.randomUUID(),
        url: URL.createObjectURL(blob),
        file: new File([blob], `product-${index}.png`, { type: blob.type || "image/png" }),
      });
    } catch {
      /* 跨域或被拦截的图片：文本字段已填好，图片交给用户 */
    }
  }
  return files;
}

export interface ImportedProductSource {
  productName: string;
  sellingPoints: string;
  /** 服务端解析出的商品图地址（用于没有本地文件时的 productImages） */
  imageUrls: string[];
  /** 已抓成本地 File 的图片（跨域抓不到时为空数组） */
  files: CreationBriefFormValues["images"];
  linkUrl: string;
}

export type ProductLinkImportResult =
  | { ok: true; source: ImportedProductSource }
  | { ok: false; message?: string };

/** 抓取商品页并解析标题/描述/商品图；失败时把服务端文案原样带回，由入口页决定兜底文案。 */
export async function importProductSource(url: string): Promise<ProductLinkImportResult> {
  try {
    const res = await fetch("/api/ingest/product", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url.trim() }),
    });
    const data: { product?: { title?: string; description?: string; images?: string[] }; error?: string } =
      await res.json().catch(() => ({}));
    const product = data.product;
    if (!res.ok || !product) return { ok: false, message: data.error };
    const imageUrls = Array.isArray(product.images) ? product.images.filter((item) => typeof item === "string") : [];
    const files = await fetchImagesAsFiles(imageUrls);
    return {
      ok: true,
      source: {
        productName: product.title ?? "",
        sellingPoints: product.description ?? "",
        imageUrls,
        files,
        linkUrl: url.trim(),
      },
    };
  } catch (error) {
    return { ok: false, message: error instanceof Error ? error.message : undefined };
  }
}
