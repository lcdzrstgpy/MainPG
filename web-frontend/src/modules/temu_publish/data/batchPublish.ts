import { temuPublishApi } from "../api/temuPublishApi";
import { httpBlob } from "../../../transport/http/client";
import type { TemuGoodsDraft, TemuPublishPrefill, TemuSkuDraft } from "../types";
import { matchPresetValue } from "../utils/specMatch";
import { yuanToCents } from "../utils/price";
import {
  APPAREL_CAROUSEL_SHAPE,
  SQUARE_SHAPE,
  toRatioDataUrl,
  toSquareDataUrl,
  type ImageShape,
} from "./imageShape";
import type { ProductTemplate, VariantTemplate } from "./localStore";

const SITE_ID = 100;
/** 详情图统一缩放到的宽度（Temu 商详要求所有楼层等宽）。 */
const DETAIL_WIDTH = 800;

/**
 * 货号清洗：和发布页变种表同一套规则。
 *
 * Temu 对 extCode 的字符集很窄，「手机壳（透明）+12」这种会直接被拒，
 * 所以括号、加号、斜杠、空格一律去掉。
 */
function safeCode(value: string | undefined | null): string {
  return String(value || "")
    .replace(/[+()（）[\]{}<>《》「」,，.。;；:：/\\|*?"'`~!@#$%^&=\s]+/g, "")
    .trim();
}

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

/**
 * 图片转成 Temu 图床地址，并按类目裁好比例。
 *
 * 三种来源能做的事不一样：
 *   · 已经是图床地址（img.kwcdn.com）→ 直接复用；像素拿不到，比例改不了
 *   · 公网外链 → 后端代下载，浏览器同样拿不到像素
 *   · 本地媒体（AI/POD 成品）→ 先取 blob，再在 canvas 里裁，**只有这一种能裁**
 *
 * 裁不了的照原样上传，让 Temu 自己去拒（失败原因会写进批量结果），
 * 总比拿一张 16:9 的图硬塞进「1:1」校验里强。
 */
async function toTemuImage(
  path: string,
  cateId: number | undefined,
  shape: ImageShape,
): Promise<string> {
  if (/img\.kwcdn\.com/i.test(path)) return path;
  if (isPublicHttp(path)) {
    const uploaded = await temuPublishApi.uploadImageUrl(path, cateId);
    return pickUrl(uploaded);
  }
  const raw = await blobToDataUrl(await httpBlob(path));
  const shaped = await toRatioDataUrl(raw, shape.width, shape.height);
  return pickUrl(await temuPublishApi.uploadImage(shaped, cateId));
}

/**
 * 本地媒体图取原始像素，给「另裁一张方图」用。
 *
 * 外链 / 已经是图床的图取不到像素（会污染 canvas），返回 null 让调用方决定怎么说。
 */
async function localDataUrl(path: string): Promise<string | null> {
  if (/img\.kwcdn\.com/i.test(path) || isPublicHttp(path)) return null;
  try {
    return await blobToDataUrl(await httpBlob(path));
  } catch {
    return null;
  }
}

/**
 * 这个类目要不要尺码表 = 是不是服饰/量体类目（主图要 3:4 而不是 1:1）。
 *
 * 批量发布是「一批草稿共用一个产品模板类目」，所以按 catId 缓存一次即可，
 * 不然 30 件草稿要打 30 次同一个查询。查询失败按「不是服饰」处理：
 * 一次网关抖动不该让整批发布全挂。
 */
const apparelCache = new Map<number, Promise<boolean>>();

function isApparelCategory(catId: number): Promise<boolean> {
  if (!catId) return Promise.resolve(false);
  const cached = apparelCache.get(catId);
  if (cached) return cached;
  const pending = temuPublishApi
    .sizeCharts(catId)
    .then((info) => info?.required === true)
    .catch(() => false);
  apparelCache.set(catId, pending);
  return pending;
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
  /**
   * 主图比例按类目走：服饰（= 必须有尺码表）3:4，其它 1:1。
   *
   * 以前这里是「原图直接传」，靠草稿图恰好是方图蒙对；发服装类目必然撞
   * 「服饰类目skc轮播图校验失败，应符合 3:4」。
   */
  const apparel = await isApparelCategory(leaf);
  const carouselShape = apparel ? APPAREL_CAROUSEL_SHAPE : SQUARE_SHAPE;
  const carousel: string[] = [];
  // 第一张的原始像素留着：素材图要从它另裁一张方图（轮播传上去之后已经是裁好的图床图了）
  let firstRaw: string | null = null;
  for (const [index, path] of carouselSource.slice(0, 10).entries()) {
    if (index === 0) firstRaw = await localDataUrl(path);
    carousel.push(await toTemuImage(path, leaf, carouselShape));
  }
  /**
   * 素材图：Temu 对所有类目都单独校验「1:1、宽 ≥800」。
   *
   * 非服饰下轮播本来就是 1:1，直接复用首图没问题；服饰下轮播被裁成了 3:4 竖图，
   * 拿它当素材图会被拒（实测第 4 关），必须用同一张原图另裁一张方图。
   */
  let material = carousel[0];
  if (apparel) {
    if (!firstRaw) {
      throw new Error(
        "服饰类目的素材图必须单独是 1:1 方图，而这张轮播图不是本地成品图（外链 / 已是图床地址），没法另裁。" +
          "请在草稿库换成本地成品图，或用「发布商品」页单件发。",
      );
    }
    material = pickUrl(await temuPublishApi.uploadImage(await toSquareDataUrl(firstRaw), leaf));
  }
  // 详情图必须走 uploadDetailImage：后端会统一缩放到 DETAIL_WIDTH 并回传真实尺寸。
  // 直接传原图链接（之前那样）会因为各图宽度不同被判
  // 「Detail graphic article N floor image width is incorrect」（2000306）。
  const detailImages: Array<{ url: string; width: number; height: number }> = [];
  for (const path of splitList((prefill.detailImages ?? []).join("\n"))) {
    // 已经是图床地址也照样过一遍：要的是「统一宽度 + 真实尺寸」，而不是省一次调用
    const image = isPublicHttp(path) ? path : await blobToDataUrl(await httpBlob(path));
    const uploaded = await temuPublishApi.uploadDetailImage(image, DETAIL_WIDTH, leaf);
    detailImages.push({
      url: pickUrl(uploaded),
      width: Number(uploaded.width) || DETAIL_WIDTH,
      height: Number(uploaded.height) || 0,
    });
  }

  const colors = splitList(variantTemplate?.colorsText ?? prefill.specValue1 ?? "Default");
  const sizes = splitList(variantTemplate?.sizesText ?? prefill.specValue2 ?? "均码");
  const batch = variantTemplate?.batch;

  // 界面价格是「元」，提交前换成分
  const priceCents = yuanToCents(
    batch?.supplierPrice || prefill.supplierPrice || "",
    "申报价格",
  );
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

  // 颜色/尺码必须优先用「类目预设值」：自建规格值提交时会被判
  // 「不合法的规格属性 Color_Black」。预设值来自叶子类目的销售属性（isSale），
  // 和发布页下拉用的是同一份数据。
  const attrs = await temuPublishApi.attributes(leaf).catch(() => null);
  const saleIndex = new Map<number, Array<{ name: string; specId: number }>>();
  for (const prop of attrs?.properties ?? []) {
    if (!prop.isSale) continue;
    const parentId = Number(prop.parentSpecId ?? 0);
    if (!parentId) continue;
    const options = (prop.values ?? [])
      .map((item) => ({ name: String(item.value ?? "").trim(), specId: Number(item.specId ?? 0) }))
      .filter((item) => item.name && item.specId > 0);
    if (options.length) saleIndex.set(parentId, options);
  }

  // 同一个规格值在一次批量里只解析一次：颜色×尺码是笛卡尔积，尺码会被反复用到，
  // 没缓存的话「类目无预设值」分支会把同一条规格 create 好几遍。
  const specCache = new Map<string, { parent_spec_id: number; parent_spec_name: string; spec_id: number; spec_name: string }>();
  const ensure = async (parentId: number, parentName: string, name: string) => {
    const cacheKey = `${parentId}:${name}`;
    const cached = specCache.get(cacheKey);
    if (cached) return cached;
    if (!parentId) throw new Error(`产品模板缺少「${parentName}」父规格`);
    const options = saleIndex.get(parentId) ?? [];
    const matched = matchPresetValue(name, options.map((item) => item.name));
    const preset = matched ? options.find((item) => item.name === matched) : undefined;
    if (preset) {
      const resolved = {
        parent_spec_id: parentId,
        parent_spec_name: parentName,
        spec_id: preset.specId,
        spec_name: preset.name,
      };
      specCache.set(cacheKey, resolved);
      return resolved;
    }
    // 该类目压根没有预设值（或模板里填的是自定义值）时才自建，避免每条草稿都建一遍
    if (options.length) {
      throw new Error(
        `「${name}」不是「${parentName}」的合法值：Temu 只认类目预设值`,
      );
    }
    const created = await temuPublishApi.createSpec(parentId, name);
    if (!created.specId) throw new Error(`规格「${name}」创建失败`);
    const resolved = {
      parent_spec_id: parentId,
      parent_spec_name: parentName,
      spec_id: created.specId,
      spec_name: name,
    };
    specCache.set(cacheKey, resolved);
    return resolved;
  };

  const properties = Object.entries(productTemplate.attrs).map(([pid, pick]) => ({
    pid: Number(pid),
    vid: pick.vid,
    prop_value: pick.text,
  }));

  const finishDraft = (): TemuGoodsDraft => ({
    category_ids: categoryIds,
    title: prefill.title,
    title_en: "",
    carousel_image_urls: carousel,
    outer_package_image_urls: [],
    detail_image_urls: detailImages.map((item) => item.url),
    // 带真实宽高的详情图（后端优先用这个）：宽度统一到 800，尺寸来自实际上传结果
    detail_images: detailImages,
    material_image_url: material,
    freight_template_id: productTemplate.freightId,
    // 发货时效：1/2/7/9 个工作日与自定义都是合法档位（发布页同一套单选），
    // 不再把 1 天 / 7 天当坏值改写成 9 —— 那会让用户选的档位静默失效。
    // 空值才兜底到 Temu 预设 9 天（777600 秒）。
    shipment_limit_seconds: Math.max(1, Number(productTemplate.shipmentDays) || 9) * 86400,
    outer_goods_url: prefill.sourceUrl ?? "",
    properties,
    skcs,
    site_ids: [SITE_ID],
    size_template_ids: splitList(productTemplate.sizeTemplates.replace(/[\s,，]+/g, ",")).map(Number).filter((n) => n > 0),
    personalization: productTemplate.isCustom,
    validate_catalog: true,
  });

  const skcs: TemuGoodsDraft["skcs"] = [];
  /** 一个 SKU 的价格/库存/物流字段：单 SKU 与多规格两条路径共用一份。 */
  const skuFields = (extCode: string, specs: TemuSkuDraft["specs"]): TemuSkuDraft => ({
    ext_code: extCode,
    currency_type: "CNY",
    site_id: SITE_ID,
    supplier_price: priceCents,
    suggested_price: null,
    stock: Number.isFinite(stock) ? Math.max(0, Math.round(stock)) : 0,
    warehouse_id: warehouseId,
    weight_mg: Math.round(weightG * 1000) < 1 ? 1 : Math.round(weightG * 1000),
    length_mm: Math.max(1, Math.round(lengthCm * 10)),
    width_mm: Math.max(1, Math.round(widthCm * 10)),
    height_mm: Math.max(1, Math.round(heightCm * 10)),
    thumb_url: carousel[0] ?? null,
    is_sensitive: productTemplate.isSensitive,
    specs,
  });

  // 类目没有销售属性（桌布/亚麻布这类）：Temu 不给颜色/尺码变种，只能建单 SKU 商品。
  // 之前这里一律 createSpec 自建规格，这类类目批量发布必然全失败。
  if (saleIndex.size === 0) {
    const singleCode =
      [safeCode(prefill.sku) || "SKU", safeCode(colors[0]), safeCode(sizes[0])]
        .filter(Boolean)
        .join("-")
        .slice(0, 120) || "SKU-1";
    skcs.push({
      ext_code: (safeCode(prefill.sku) || "SKC").slice(0, 120),
      preview_image_urls: [carousel[0]],
      color_image_url: carousel[0],
      main_specs: [],
      skus: [skuFields(singleCode, [])],
    });
    return finishDraft();
  }

  for (const color of colors) {
    const colorSpec = await ensure(colorParent, productTemplate.colorParentName || "颜色", color);
    const skus: TemuSkuDraft[] = [];
    for (const size of sizes) {
      const sizeSpec = await ensure(sizeParent, productTemplate.sizeParentName || "尺码", size);
      skus.push(
        skuFields(
          [safeCode(prefill.sku) || "SKU", safeCode(color), safeCode(size)]
            .filter(Boolean)
            .join("-")
            .slice(0, 120) || `SKU-${skus.length + 1}`,
          [colorSpec, sizeSpec],
        ),
      );
    }
    skcs.push({
      ext_code:
        [safeCode(prefill.sku) || "SKC", safeCode(color)].filter(Boolean).join("-").slice(0, 120) ||
        `SKC-${skcs.length + 1}`,
      preview_image_urls: [carousel[0]],
      color_image_url: carousel[0],
      main_specs: [colorSpec],
      skus,
    });
  }

  return finishDraft();
}
