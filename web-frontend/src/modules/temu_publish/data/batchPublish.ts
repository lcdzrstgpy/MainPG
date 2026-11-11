import { temuPublishApi } from "../api/temuPublishApi";
import { httpBlob } from "../../../transport/http/client";
import type { TemuGoodsDraft, TemuPublishPrefill, TemuSkuDraft } from "../types";
import { matchPresetValue } from "../utils/specMatch";
import { yuanToCents } from "../utils/price";
import { mapWithConcurrency } from "./concurrency";
import { positive, splitList } from "./batchPrecheck";
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
 * 单件发布时实测：并发 3 张能把 10 张轮播的等待压到 1/3 左右，又不至于
 * 把图床打限流。批量里这条更关键 —— 30 条 × 十几张图，串行就是几百次往返。
 */
const IMAGE_UPLOAD_CONCURRENCY = 3;

/**
 * 自建规格值的跨条目缓存（只收 createSpec 建出来的）。
 *
 * `ensure()` 里那份缓存只活一次 buildDraftForPublish：批量 30 条里「黑色」会反复出现，
 * 每条都 createSpec 就会在店铺规格库里堆出几十个同名「黑色」。
 * 这份缓存活在页面会话里，同一批复用同一个 spec_id。
 */
const createdSpecCache = new Map<
  string,
  { parent_spec_id: number; parent_spec_name: string; spec_id: number; spec_name: string }
>();

/**
 * 货号清洗：只留 A-Za-z0-9-_，和发布页 `cleanExtCode` 同一口径。
 *
 * Temu 对货号的字符集很窄（「请使用字母、数字和标点符号维护货号」，1000002），
 * 中文规格名（黑茶色-性价比版）拼进去会被整条拒掉。以前这里只删符号不删中文，
 * 批量发中文颜色的草稿必然失败 —— 同一个草稿在发布页发却能过，就是因为那边洗得更严。
 * 清洗后变空的片段由调用方用序号兜底，否则多个颜色会撞成同一个货号（ext_code 重复）。
 */
function safeCode(value: string | undefined | null): string {
  const cleaned = String(value || "").replace(/[^A-Za-z0-9_-]+/g, "").trim();
  // 只剩标点残渣也算没拼出来：「黑茶色-性价比版」洗完只剩一个 "-"，
  // 当成有效片段会让四个颜色全撞成同一个「PP-000199--」（ext_code 重复），
  // 也会让中文货号变成一串减号。这里要求至少含一个字母/数字。
  return /[A-Za-z0-9]/.test(cleaned) ? cleaned : "";
}

/**
 * 货号片段：清洗后为空（中文规格名）就退化成 `C1` / `S2` 这样的序号，
 * 保证每个变种的货号不同 —— 直接留空会让四个颜色全撞成「PP-000199」。
 */
function codePart(value: string | undefined | null, index: number, prefix: string): string {
  const raw = String(value ?? "");
  return safeCode(raw) || (raw.trim() ? `${prefix}${index + 1}` : "");
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
  // 模板是存在 localStorage 里的：老版本存下的模板可能缺字段（sizeTemplates / attrs），
  // 直接用会在 `Object.keys(undefined)` / `.replace` 上抛 TypeError，
  // 整批每一条都变成「Cannot read properties of undefined」，看不出真实原因。
  const templatePath = productTemplate.categoryPath ?? [];
  const templateAttrs = productTemplate.attrs ?? {};
  const categoryIds = templatePath.map((item) => item.catId);
  if (!categoryIds.length) throw new Error("产品模板里没有类目");
  const leaf = categoryIds[categoryIds.length - 1];

  const carouselSource = splitList(prefill.images.join("\n"));
  // Temu 硬性要求至少 3 张（网关实测回「轮播图最少 3 张」）。提前拦，
  // 不然图全传完才被拒，批量里还看不出是哪一步的问题
  if (carouselSource.length < 3) {
    throw new Error(`草稿轮播图不足 3 张（当前 ${carouselSource.length} 张）：Temu 硬性要求`);
  }
  /**
   * 主图比例按类目走：服饰（= 必须有尺码表）3:4，其它 1:1。
   *
   * 以前这里是「原图直接传」，靠草稿图恰好是方图蒙对；发服装类目必然撞
   * 「服饰类目skc轮播图校验失败，应符合 3:4」。
   */
  const apparel = await isApparelCategory(leaf);
  const carouselShape = apparel ? APPAREL_CAROUSEL_SHAPE : SQUARE_SHAPE;
  const carouselSources = carouselSource.slice(0, 10);
  // 第一张的原始像素留着：素材图要从它另裁一张方图（轮播传上去之后已经是裁好的图床图了）。
  // 先取先判 —— 服饰类目取不到原图就没法另裁方图，别等 10 张轮播全传完才整条失败。
  const firstRaw = apparel ? await localDataUrl(carouselSources[0] ?? "") : null;
  if (apparel && !firstRaw) {
    throw new Error(
      "服饰类目的素材图必须单独是 1:1 方图，而这张轮播图不是本地成品图（外链 / 已是图床地址），没法另裁。" +
        "请在草稿库换成本地成品图，或用「发布商品」页单件发。",
    );
  }
  // 限并发：批量里每条都有十几张图，串行的话 30 条就是几百次排队往返
  const carousel = await mapWithConcurrency(carouselSources, IMAGE_UPLOAD_CONCURRENCY, (path) =>
    toTemuImage(path, leaf, carouselShape),
  );
  /**
   * 素材图：Temu 对所有类目都单独校验「1:1、宽 ≥800」。
   *
   * 非服饰下轮播本来就是 1:1，直接复用首图没问题；服饰下轮播被裁成了 3:4 竖图，
   * 拿它当素材图会被拒（实测第 4 关），必须用同一张原图另裁一张方图。
   */
  let material = carousel[0];
  if (apparel && firstRaw) {
    material = pickUrl(await temuPublishApi.uploadImage(await toSquareDataUrl(firstRaw), leaf));
  }
  // 详情图必须走 uploadDetailImage：后端会统一缩放到 DETAIL_WIDTH 并回传真实尺寸。
  // 直接传原图链接（之前那样）会因为各图宽度不同被判
  // 「Detail graphic article N floor image width is incorrect」（2000306）。
  const detailImages = await mapWithConcurrency(
    splitList((prefill.detailImages ?? []).join("\n")),
    IMAGE_UPLOAD_CONCURRENCY,
    async (path) => {
      // 已经是图床地址也照样过一遍：要的是「统一宽度 + 真实尺寸」，而不是省一次调用
      const image = isPublicHttp(path) ? path : await blobToDataUrl(await httpBlob(path));
      const uploaded = await temuPublishApi.uploadDetailImage(image, DETAIL_WIDTH, leaf);
      return {
        url: pickUrl(uploaded),
        width: Number(uploaded.width) || DETAIL_WIDTH,
        height: Number(uploaded.height) || 0,
      };
    },
  );

  /**
   * 规格值口径：变种模板 > 草稿里的**全部**规格值 > 单个值。
   *
   * 草稿的 specValue1/2 只是「第一个值」（例如第一个颜色），整列表在
   * specValues1/2 里。以前只读第一个值，多颜色的草稿批量发出去就只剩一个颜色 ——
   * 商品建出来了但少了几个变种，很难发现。发布页铺变种表用的就是整列表。
   */
  const colors = splitList(
    variantTemplate?.colorsText ||
      (prefill.specValues1?.length
        ? prefill.specValues1.join("\n")
        : prefill.specValue1 || ""),
  );
  const sizes = splitList(
    variantTemplate?.sizesText ||
      (prefill.specValues2?.length
        ? prefill.specValues2.join("\n")
        : prefill.specValue2 || ""),
  );
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
    // 跨条目复用：同一批里「黑色」会出现在几十条草稿上，不复用就在规格库里堆一堆同名值
    const shared = createdSpecCache.get(cacheKey);
    if (shared) {
      specCache.set(cacheKey, shared);
      return shared;
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
    createdSpecCache.set(cacheKey, resolved);
    return resolved;
  };

  // 属性表拿不到就别硬发：属性是全类目必填项，没有它连 pid/refPid 都还原不了
  if (Object.keys(templateAttrs).length && !attrs) {
    throw new Error("类目属性表没取到，无法组装属性（批量发布要靠它确认 pid/refPid），请重试");
  }
  /**
   * 属性 payload。
   *
   * `productTemplate.attrs` 的键是**页面上的会话键**：普通类目就是 pid，但「同一个 pid
   * 挂多条属性」的类目（服饰：面料纹理1/2 = 2054、面料克重1/2/里料克重 = 2052）
   * 已经在 api 层被换成了 refPid。网关只认原始 pid，所以这里要按当前类目属性表映射回去，
   * 顺便把 refPid / templatePid / 数值录入一起补齐 —— 少一个都会被判「属性不合法」
   * 或「属性数值录入不能为空」。
   */
  const properties = Object.entries(templateAttrs).map(([key, pick]) => {
    const sessionPid = Number(key);
    const prop = attrs?.properties.find((item) => item.pid === sessionPid);
    // 有预设可选值 = 「下拉 + 数值」属性（成分 60%），要额外带 numberInputValue；
    // 纯数值属性（面料克重 200）只能走 propValue，带上反而被判「数值录入信息必须为空」
    const hasOptions = (prop?.values?.length ?? 0) > 0;
    return {
      pid: prop?.realPid ?? sessionPid,
      vid: pick.vid,
      prop_name: prop?.name || prop?.propertyName || "",
      ref_pid: prop?.refPid ?? undefined,
      template_pid: prop?.templatePid ?? undefined,
      prop_value: pick.text,
      number_input_value: hasOptions ? pick.text : "",
      value_unit: String(prop?.valueUnit?.[0] ?? "").trim(),
    };
  });

  const finishDraft = (): TemuGoodsDraft => ({
    category_ids: categoryIds,
    // 类目路径名：发布历史里显示「女装 > 连衣裙」而不是一串 cat id（纯展示字段）
    category_names: templatePath.map((item) => item.catName).filter(Boolean),
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
    // 站外链接：Temu 只收 https，http 会被判不合法。单件发布是直接报错拦下，
    // 批量里为这一个次要字段把整条打失败不值得 —— 直接不带（这个字段本来就是可选的）。
    outer_goods_url: /^https:\/\//i.test(prefill.sourceUrl ?? "") ? (prefill.sourceUrl ?? "") : "",
    properties,
    skcs,
    // 来源草稿 id：后端发布成功后靠它把这条从草稿库移出，发布历史里也能显示来源。
    // 批量发布以前没带，结果「发成功了但刷新草稿还在」，同一件商品很容易再发一次。
    draft_id: prefill.draftId ?? "",
    site_ids: [SITE_ID],
    size_template_ids: splitList((productTemplate.sizeTemplates ?? "").replace(/[\s,，]+/g, ",")).map(Number).filter((n) => n > 0),
    personalization: productTemplate.isCustom,
    validate_catalog: true,
  });

  const skcs: TemuGoodsDraft["skcs"] = [];
  /** 三个边长降序：Temu 只认「最长 ≥ 次长 ≥ 最短」，用户怎么填都得按这个顺序传 */
  const sortedDims = [lengthCm, widthCm, heightCm].sort((a, b) => b - a);
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
    // Temu 体积校验要求「最长边 ≥ 次长边 ≥ 最短边」，顺序不对直接拒；
    // 体积只看三个边长，跟哪条叫「长」无关，所以这里排好再发。
    length_mm: Math.max(1, Math.round(sortedDims[0] * 10)),
    width_mm: Math.max(1, Math.round(sortedDims[1] * 10)),
    height_mm: Math.max(1, Math.round(sortedDims[2] * 10)),
    thumb_url: carousel[0] ?? null,
    is_sensitive: productTemplate.isSensitive,
    specs,
  });

  /**
   * 规格轴：类目只给颜色（手机壳、毛巾这类）或只给尺码时，另一列不能硬造。
   *
   * 以前不分轴，一律 colors × sizes，缺的那一列还会 createSpec 出一个「均码」，
   * 结果 Temu 判「不合法的规格属性」—— 单列类目在批量发布里必然全失败。
   * 发布页支持 0/1/2 列，后端 payload 也支持，这里对齐。
   *
   * ⚠️ 「类目支不支持规格」不能只看 isSale（那是发布页 2026-11-04 修过的同一个坑）：
   * 化妆包 / 桌布 / 箱包的属性表里一个 isSale 都没有，但 inputMaxSpecNum=2，允许自定义规格。
   * 按 isSale 判会把整批压成单 SKU —— 11 个颜色的包只发出 1 个变种，商品是错的还不报错。
   * 没有预设值时由 ensure() 走 createSpec 自建，和发布页勾了「按规格发」是同一条路。
   */
  const specDimLimit = Number(attrs?.inputMaxSpecNum ?? 0);
  /** 没有预设销售属性、但类目允许自定义规格（值由 createSpec 建） */
  const canCustomSpecs = specDimLimit > 0;
  const colorAxis = colors.length > 0 && !!colorParent && (saleIndex.has(colorParent) || canCustomSpecs);
  const sizeAxis = sizes.length > 0 && !!sizeParent && (saleIndex.has(sizeParent) || canCustomSpecs);

  if (!colorAxis && !sizeAxis) {
    // 草稿有变种值、却发不出变种：要么模板缺父规格、要么类目不支持规格。
    // 两种都不能静默吞成单 SKU —— 商品会建出来但变种没了，比报错难发现得多。
    if (colors.length > 0 || sizes.length > 0) {
      throw new Error(
        canCustomSpecs
          ? "产品模板缺少颜色/尺码父规格：补上再发，否则这些变种会被吞成单个 SKU"
          : `这个类目不支持颜色/尺码规格（最多 ${specDimLimit} 个规格维度），草稿里的变种值发不出去：换个类目，或清掉变种值按单 SKU 发`,
      );
    }
    if (saleIndex.size > 0) {
      throw new Error(
        "草稿的规格值和类目预设值对不上（或产品模板缺少颜色/尺码父规格）：选一个变种模板再发",
      );
    }
    // 一个变种值都没有：本来就是单 SKU 商品（桌布/亚麻布这类）
    const singleCode =
      [safeCode(prefill.sku) || "SKU", codePart(colors[0], 0, "C"), codePart(sizes[0], 0, "S")]
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

  const colorList = colorAxis ? colors : [""];
  const sizeList = sizeAxis ? sizes : [""];
  // 货号片段先算好：中文规格名清洗后为空的用序号兜底，避免 SKC/SKU 货号撞号
  const colorCodes = colorList.map((value, index) => codePart(value, index, "C"));
  const sizeCodes = sizeList.map((value, index) => codePart(value, index, "S"));

  for (const [colorIndex, color] of colorList.entries()) {
    const colorSpec = colorAxis
      ? await ensure(colorParent, productTemplate.colorParentName || "颜色", color)
      : null;
    const skus: TemuSkuDraft[] = [];
    for (const [sizeIndex, size] of sizeList.entries()) {
      const sizeSpec = sizeAxis
        ? await ensure(sizeParent, productTemplate.sizeParentName || "尺码", size)
        : null;
      skus.push(
        skuFields(
          [safeCode(prefill.sku) || "SKU", colorCodes[colorIndex], sizeCodes[sizeIndex]]
            .filter(Boolean)
            .join("-")
            .slice(0, 120) || `SKU-${skus.length + 1}`,
          [colorSpec, sizeSpec].filter(Boolean) as TemuSkuDraft["specs"],
        ),
      );
    }
    skcs.push({
      ext_code:
        [safeCode(prefill.sku) || "SKC", colorCodes[colorIndex]].filter(Boolean).join("-").slice(0, 120) ||
        `SKC-${skcs.length + 1}`,
      preview_image_urls: [carousel[0]],
      color_image_url: carousel[0],
      main_specs: colorSpec ? [colorSpec] : [],
      skus,
    });
  }

  return finishDraft();
}
