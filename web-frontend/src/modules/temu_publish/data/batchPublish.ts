import { temuPublishApi } from "../api/temuPublishApi";
import { httpBlob } from "../../../transport/http/client";
import type { TemuGoodsDraft, TemuPublishPrefill, TemuSkuDraft } from "../types";
import type { ProductTemplate, VariantTemplate } from "./localStore";

const SITE_ID = 100;

function splitList(text: string): string[] {
  return text
    .split(/[\n,，、;；]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("读取图片失败"));
    reader.readAsDataURL(blob);
  });
}

function isPublicHttp(url: string): boolean {
  return /^https?:\/\//i.test(url) && !/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/i.test(url);
}

/** 图片转成 Temu 图床地址：本地媒体先取 blob 再上传，公网外链交给后端代下载。 */
async function toTemuImage(path: string, cateId?: number): Promise<string> {
  if (/img\.kwcdn\.com/i.test(path)) return path;
  if (isPublicHttp(path)) {
    const uploaded = await temuPublishApi.uploadImageUrl(path, cateId);
    return pickUrl(uploaded);
  }
  const uploaded = await temuPublishApi.uploadImage(await blobToDataUrl(await httpBlob(path)), cateId);
  return pickUrl(uploaded);
}

function pickUrl(result: { imageUrl?: string; image_url?: string; url?: string }): string {
  const url = result.imageUrl || result.image_url || result.url || "";
  if (!url) throw new Error("图片上传成功但没有返回地址");
  return url;
}

function positive(value: string, label: string): number {
  const number = Number(String(value ?? "").trim());
  if (!Number.isFinite(number) || number <= 0) throw new Error(`${label}必须大于 0`);
  return number;
}

/**
 * 批量发布用的草稿组装：和发布页同一套规则，但不依赖页面状态。
 *
 * 需要「产品模板」提供分类/属性/运费/仓库/时效，因为草稿库里的 AI/POD 成品
 * 本身没有类目和属性；变种模板提供 颜色×尺码 与批量值，没套就用草稿自带的值。
 */
export async function buildDraftForPublish(
  prefill: TemuPublishPrefill,
  productTemplate: ProductTemplate,
  variantTemplate: VariantTemplate | null,
): Promise<TemuGoodsDraft> {
  const categoryIds = productTemplate.categoryPath.map((item) => item.catId);
  if (!categoryIds.length) throw new Error("产品模板里没有类目");
  const leaf = categoryIds[categoryIds.length - 1];

  const carouselSource = splitList(prefill.images.join("\n"));
  if (!carouselSource.length) throw new Error("草稿没有轮播图");
  const carousel: string[] = [];
  for (const path of carouselSource.slice(0, 10)) carousel.push(await toTemuImage(path, leaf));
  const detail: string[] = [];
  for (const path of splitList((prefill.detailImages ?? []).join("\n"))) {
    detail.push(await toTemuImage(path, leaf));
  }

  const colors = splitList(variantTemplate?.colorsText ?? prefill.specValue1 ?? "Default");
  const sizes = splitList(variantTemplate?.sizesText ?? prefill.specValue2 ?? "均码");
  const batch = variantTemplate?.batch;

  const price = positive(batch?.supplierPrice || prefill.supplierPrice || "", "申报价格");
  const stock = Number(batch?.stock || prefill.stock || "0");
  const weightG = positive(batch?.weightG || String(Number(prefill.weightKg || 0) * 1000), "重量");
  const lengthCm = positive(batch?.lengthCm || prefill.lengthCm || "", "长度");
  const widthCm = positive(batch?.widthCm || prefill.widthCm || "", "宽度");
  const heightCm = positive(batch?.heightCm || prefill.heightCm || "", "高度");
  const warehouseId = productTemplate.warehouseId;
  if (!warehouseId) throw new Error("产品模板里没有默认发货仓库");
  if (!productTemplate.freightId) throw new Error("产品模板里没有运费模板");

  const colorParent = productTemplate.colorParentId ?? 0;
  const sizeParent = productTemplate.sizeParentId ?? 0;

  const specs: Array<{ parentSpecId: number; parentName: string; name: string }> = [];
  const ensure = async (parentId: number, parentName: string, name: string) => {
    if (!parentId) throw new Error(`产品模板缺少「${parentName}」父规格`);
    const created = await temuPublishApi.createSpec(parentId, name);
    if (!created.specId) throw new Error(`规格「${name}」创建失败`);
    return {
      parent_spec_id: parentId,
      parent_spec_name: parentName,
      spec_id: created.specId,
      spec_name: name,
    };
  };

  const skcs: TemuGoodsDraft["skcs"] = [];
  for (const color of colors) {
    const colorSpec = await ensure(colorParent, productTemplate.colorParentName || "颜色", color);
    specs.push({ parentSpecId: colorParent, parentName: color, name: color });
    const skus: TemuSkuDraft[] = [];
    for (const size of sizes) {
      const sizeSpec = await ensure(sizeParent, productTemplate.sizeParentName || "尺码", size);
      skus.push({
        ext_code: [prefill.sku || "SKU", color, size].filter(Boolean).join("-").slice(0, 120),
        currency_type: "CNY",
        site_id: SITE_ID,
        supplier_price: Math.round(price),
        suggested_price: null,
        stock: Number.isFinite(stock) ? Math.max(0, Math.round(stock)) : 0,
        warehouse_id: warehouseId,
        weight_mg: Math.round(weightG * 1000) < 1 ? 1 : Math.round(weightG * 1000),
        length_mm: Math.max(1, Math.round(lengthCm * 10)),
        width_mm: Math.max(1, Math.round(widthCm * 10)),
        height_mm: Math.max(1, Math.round(heightCm * 10)),
        thumb_url: carousel[0] ?? null,
        is_sensitive: productTemplate.isSensitive,
        specs: [colorSpec, sizeSpec],
      });
    }
    skcs.push({
      ext_code: [prefill.sku || "SKC", color].filter(Boolean).join("-").slice(0, 120),
      preview_image_urls: [carousel[0]],
      color_image_url: carousel[0],
      main_specs: [colorSpec],
      skus,
    });
  }

  const properties = Object.entries(productTemplate.attrs).map(([pid, pick]) => ({
    pid: Number(pid),
    vid: pick.vid,
    prop_value: pick.text,
  }));

  return {
    category_ids: categoryIds,
    title: prefill.title,
    title_en: "",
    carousel_image_urls: carousel,
    outer_package_image_urls: [],
    detail_image_urls: detail,
    material_image_url: carousel[0],
    freight_template_id: productTemplate.freightId,
    // 模板里存的 1 天是旧默认值，按 7 天兜底（与发布页一致）
    shipment_limit_seconds:
      Math.max(1, Number(productTemplate.shipmentDays === "1" ? "7" : productTemplate.shipmentDays || "7") || 7) *
      86400,
    outer_goods_url: prefill.sourceUrl ?? "",
    properties,
    skcs,
    site_ids: [SITE_ID],
    size_template_ids: splitList(productTemplate.sizeTemplates.replace(/[\s,，]+/g, ",")).map(Number).filter((n) => n > 0),
    personalization: productTemplate.isCustom,
    validate_catalog: true,
  };
}
