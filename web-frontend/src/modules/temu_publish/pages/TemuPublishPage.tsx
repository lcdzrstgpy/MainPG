import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";
import { createPortal } from "react-dom";

import { httpBlob } from "../../../transport/http/client";
import { temuPublishApi } from "../api/temuPublishApi";
import {
  EMPTY_BATCH,
  PublishSkuPanel,
  createVariantRow,
  withFreshRowKeys,
} from "../components/PublishSkuPanel";
import type { VariantRow } from "../components/PublishSkuPanel";
import { DraftImagePicker } from "../components/DraftImagePicker";
import { CheckGlyph } from "../components/CheckGlyph";
import { TemuPropertyForm } from "../components/TemuPropertyForm";
import { DEFAULT_SHIPMENT_DAYS, ShipmentDaysPicker } from "../components/ShipmentDaysPicker";
import {
  APPAREL_CAROUSEL_HEIGHT,
  APPAREL_CAROUSEL_SHAPE,
  APPAREL_CAROUSEL_WIDTH,
  SQUARE_SHAPE,
  toRatioDataUrl,
  toSquareDataUrl,
} from "../data/imageShape";
import {
  autoFillAttributePicks,
  autoFillSourceTexts,
  describeAutoFill,
  type AutoFillHit,
} from "../data/attrAutofill";
import { pickSpecPair } from "../data/specPair";
import {
  attributeDiffText,
  describeAttributeDiff,
  migrateLegacyPicks,
  missingRequiredNames,
  pruneStaleAttrPicks,
  remapAttrPicks,
  visibleProperties as visibleAttributeProperties,
  type AttrPick,
} from "../data/attributeModel";
import { TemuImageStrip, TemuThumb } from "../components/TemuImageStrip";
import { localStore, parsePublishError } from "../data/localStore";
import {
  ATTR_PICKS_KEY,
  readAttrPicksHistory,
  readCurrentAttrPicks,
  saveAttrPicksHistory,
  saveCurrentAttrPicks,
  storageScope,
} from "../data/attrPicksStore";
import { categorySearchTexts, dedupeCategoryPaths } from "../utils/categoryMatch";
import { categoryName, categoryPathFromDto } from "../utils/categoryPath";
import { matchPresetValue } from "../utils/specMatch";
import { centsToYuanText, formatYuan, yuanToCents } from "../utils/price";
import type { ProductTemplate, VariantTemplate } from "../data/localStore";
import { TEMU_PUBLISH_PREFILL_EVENT, TEMU_PUBLISH_PREFILL_KEY } from "../types";
import type {
  TemuAttributes,
  TemuBuildPayloadResult,
  TemuGoodsDraft,
  TemuPublishResult,
  TemuCategory,
  TemuCategoryPath,
  TemuFreightTemplate,
  TemuGoodsItem,
  TemuImageUploadResult,
  TemuParentSpec,
  TemuProperty,
  TemuPublishPrefill,
  TemuSettingsStatus,
  TemuShop,
  TemuSizeChartInfo,
  TemuSkuDraft,
  TemuSkuSpec,
  TemuSpecCreated,
  TemuTokenInfo,
  TemuWarehouse,
  TemuWarehouseGroup,
} from "../types";
import "../styles/temuPublish.css";

const SITE_ID = 100;

/**
 * 换类目后变种行里不合法的规格值（只提示，不自动改）。
 *
 * field 用 color/size 这两个**内部键**，不用中文名：不同类目这两列可能叫
 * 「款式 / 型号 / 容量 / 长度」，拿中文当键会同时把内部判断和界面文案都写死。
 */
type SpecMismatch = {
  rowKey: string;
  rowNo: number;
  field: "color" | "size";
  value: string;
};

/**
 * 类目匹配带重试。
 *
 * 网关的 category.match 偶发 SYSTEM_EXCEPTION（实测过好几次），一次失败就让推荐列表空着，
 * 界面上看起来就像「这条草稿不识别类目」。这里最多试 3 次、退避 800/1600ms，
 * 只有真的连续失败才返回 null（标题匹配那边本来就是「失败不打扰」的语义）。
 */
async function matchCategoryWithRetry(
  text: string,
  attempts = 3,
): Promise<{ categoryPathDTOS: TemuCategoryPath[] } | null> {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await temuPublishApi.matchCategory(text, SITE_ID);
    } catch {
      if (attempt === attempts - 1) return null;
      await new Promise((resolve) => setTimeout(resolve, 800 * (attempt + 1)));
    }
  }
  return null;
}

/**
 * 限并发地按 INPUT 顺序处理（传图用：并发 3 张，串行 6 张要等 6~18 秒）。
 * 结果顺序与入参一致，调用方照旧拼到 URL 行里。
 */
async function mapWithConcurrency<T, R>(
  items: T[],
  limit: number,
  worker: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let cursor = 0;
  const runners = Array.from({ length: Math.max(1, Math.min(limit, items.length)) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      results[index] = await worker(items[index], index);
    }
  });
  await Promise.all(runners);
  return results;
}

type ImageSlot = "carousel" | "outer" | "detail";

const IMAGE_SLOTS: Array<{ key: ImageSlot; label: string; required: boolean; hint: string }> = [
  {
    key: "carousel",
    label: "产品轮播图",
    required: true,
    // 尺寸不用用户自己裁：上传时会居中裁成 1:1 再压成 800×800 JPEG
    hint: "相当于主图，最多 10 张；上传自动裁成 1:1 / 800×800",
  },
  { key: "outer", label: "外包装图片", required: false, hint: "最多 6 张，建议含正拍/侧拍/俯拍" },
  {
    key: "detail",
    label: "详情图",
    required: false,
    hint: "最多 30 张，一个详情模块一张图（宽度提交时统一）",
  },
];

/** 各槽位最多几张：上传时就挡，别等提交才被 Temu 拒。 */
const IMAGE_LIMITS: Record<ImageSlot, number> = { carousel: 10, outer: 6, detail: 30 };


/**
 * 发布表单：只保留「真的会提交给 Temu」的字段（基本信息 / 产品信息 / 变种信息 / 包装信息）。
 * 接口没有对应参数的字段一律不放，唯一例外是收起的「备注」——纯留档，已标明不提交。
 */
type PublishForm = {
  title: string;
  titleEn: string;
  productCode: string;
  origin: string;
  externalUrl: string;
  isSensitive: boolean;
  isCustom: boolean;
  carousel: string;
  material: string;
  colorsText: string;
  sizesText: string;
  sizeTemplates: string;
  outerImages: string;
  detailImages: string;
  freightId: string;
  warehouseId: string;
  shipmentDays: string;
  /** 每件装数量：标题里的 4 Pack / 12Pcs，>1 才会发 productSkuMultiPackReq */
  piecesPerPack: string;
  description: string;
};

const FORM_KEY = "temu_publish_form_v2";
const PRESETS_KEY = "temu_publish_presets_v1";
/** 变种行（价格/库存/尺寸/重量都是手填的）也要落盘，刷新后不能只剩颜色尺码文本。 */
const ROWS_KEY = "temu_publish_rows_v1";
// 变种模板存在 data/localStore.ts（VARIANT_KEY），key 必须保持一致
const VARIANT_TEMPLATE_KEY = "temu_publish_variant_templates";
/** 价格单位迁移标记：写过一次就不再动，否则会把用户新填的「元」又除一遍 */
const PRICE_UNIT_FLAG = "temu_publish_price_unit_yuan_v1";

/**
 * 一次性把本机存着的价格从「分」换成「元」。
 *
 * 为什么直接改存储、而不是只改内存里的值：React 严格模式会把 useState 的初始化
 * 跑两遍，只改内存的话第二遍又读到原始的分值，1299 就变成了 ¥1299。
 * 改完立刻写标记，以后再也不碰，用户新填的元不会被二次除算。
 */
function migrateLegacyPriceUnit(suffix: string): void {
  try {
    const store = window.localStorage;
    // 标记必须按账号分：rows / 模板都是 <key>:<account>，
    // 用全局标记会让第二个账号的老数据漏迁（1299 变 ¥1299）
    const flagKey = `${PRICE_UNIT_FLAG}:${suffix}`;
    if (store.getItem(flagKey) === "yuan") return;

    const rowsKey = `${ROWS_KEY}:${suffix}`;
    const rowsRaw = store.getItem(rowsKey);
    if (rowsRaw) {
      const rows = JSON.parse(rowsRaw) as VariantRow[];
      if (Array.isArray(rows)) {
        store.setItem(
          rowsKey,
          JSON.stringify(
            rows.map((row) =>
              row && typeof row === "object"
                ? {
                    ...row,
                    supplierPrice: centsToYuanText(row.supplierPrice),
                    suggestedPrice: centsToYuanText(row.suggestedPrice),
                  }
                : row,
            ),
          ),
        );
      }
    }

    const templateKey = `${VARIANT_TEMPLATE_KEY}:${suffix}`;
    const templateRaw = store.getItem(templateKey);
    if (templateRaw) {
      const templates = JSON.parse(templateRaw) as Array<Record<string, unknown>>;
      if (Array.isArray(templates)) {
        store.setItem(
          templateKey,
          JSON.stringify(
            templates.map((item) => {
              const batch = item?.batch as Record<string, unknown> | undefined;
              if (!batch) return item;
              return {
                ...item,
                batch: {
                  ...batch,
                  supplierPrice: centsToYuanText(batch.supplierPrice),
                  suggestedPrice: centsToYuanText(batch.suggestedPrice),
                },
              };
            }),
          ),
        );
      }
    }

    store.setItem(flagKey, "yuan");
  } catch {
    // 本机存储不可用：跳过迁移，不影响主流程
  }
}
const RECENT_CATEGORY_KEY = "temu_publish_recent_category_v1";
const SHOP_INFO_KEY = "temu_shop_info_v1";

const EMPTY_FORM: PublishForm = {
  title: "",
  titleEn: "",
  productCode: "",
  origin: "CN",
  externalUrl: "",
  isSensitive: false,
  isCustom: false,
  carousel: "",
  material: "",
  colorsText: "",
  sizesText: "",
  sizeTemplates: "",
  outerImages: "",
  detailImages: "",
  freightId: "",
  warehouseId: "",
  shipmentDays: DEFAULT_SHIPMENT_DAYS,
  piecesPerPack: "1",
  description: "",
};

type PublishPreset = {
  name: string;
  categoryPath: TemuCategory[];
  freightId: string;
  warehouseId: string;
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/**
 * 侧栏只放一句摘要。
 *
 * 完整文案在页面顶部那条横幅里（带「关闭」），侧栏这么窄再重复整段，
 * 只会被挤成一片密密麻麻的红字，谁都不想读。悬停 title 看全文。
 */
function briefError(text: string, limit = 40): string {
  const first = (text.split(/[。；\n]/)[0] ?? "").trim() || text.trim();
  return first.length > limit ? `${first.slice(0, limit)}…` : first;
}

function splitLines(text: string): string[] {
  return text
    .split(/[\n,，]+/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/** 变种值可能是「30cm,40cm,50cm」这种列表，取第一个当这行的规格值。 */
function firstSpecValue(text?: string): string {
  return (text || "").split(/[,，、;；]/)[0]?.trim() ?? "";
}

/**
 * 进页面时的初始类目。
 *
 * 有草稿带入且这条草稿存过类目 → 直接用它；从草稿带入但没存过 → 留空，等标题自动匹配。
 * 不能无脑读「最近用过的类目」：那样打开 B 商品会先显示 A 商品的类目，看着就像没识别。
 */
function initialCategoryPath(): TemuCategory[] {
  try {
    const raw = window.sessionStorage.getItem(TEMU_PUBLISH_PREFILL_KEY);
    if (raw) {
      const prefill = JSON.parse(raw) as TemuPublishPrefill;
      return prefill?.categoryPath?.length ? prefill.categoryPath : [];
    }
  } catch {
    // 带入数据损坏：退回最近用过的类目
  }
  return loadJsonLocal<TemuCategory[]>(`${RECENT_CATEGORY_KEY}:${storageScope()}`, []);
}

/** 价格按「分」存，展示成 ¥。 */

function firstWarehouse(groups: TemuWarehouseGroup[]): TemuWarehouse | null {
  const all = groups
    .flatMap((group) => group.validWarehouseList ?? [])
    .filter((warehouse) => !warehouse.warehouseDisable);
  // 实测：managementType=3 的仓（如「煊烊美东」）发部分商品会被拒
  // 「该商品不支持绑定发货仓」，默认优先选 1 / 0 类型的仓。
  return all.find((warehouse) => warehouse.managementType && warehouse.managementType !== "3") ?? all[0] ?? null;
}

/** 仓库下拉文案：3 类型仓标注风险，避免选完才发现不支持。 */
function warehouseLabel(warehouse: TemuWarehouse): string {
  const risk = warehouse.managementType === "3" ? "（可能不支持该商品）" : "";
  return `${warehouse.warehouseName} · ${warehouse.warehouseId}${risk}`;
}

function parsePositive(value: string, label: string): number {
  const number = Number(String(value).trim());
  if (!Number.isFinite(number) || number <= 0) throw new Error(`${label}必须大于 0`);
  return number;
}

/**
 * 库存：允许 0（Temu 接受，建出来的商品是「售罄/不可售」状态，卖之前补库存即可）。
 * 空值或负数才算错 —— 不能像价格那样要求 > 0。
 */
function parseStock(value: string, label: string): number {
  const text = String(value ?? "").trim();
  const number = Number(text);
  if (!text || !Number.isFinite(number) || number < 0) throw new Error(`${label}必须填 0 或正整数`);
  return Math.round(number);
}

/**
 * Temu 的 extCode（货号）只接受字母、数字、下划线和连字符。
 *
 * 中文、括号、加号、空格一律会被拒（实测「货号不合法」），而界面提示写着
 * 「不能含中文」却没拦 —— 这里统一清洗，清洗后为空再报错。
 */
function cleanExtCode(value: string): string {
  return String(value || "").replace(/[^A-Za-z0-9_-]+/g, "").trim();
}

/**
 * Temu 的体积校验要求「最长边 ≥ 次长边 ≥ 最短边」，三个值必须降序传。
 *
 * 用户填的「长 33 × 宽 13 × 高 20」是**他对物品的描述**，不是排序结果；
 * 直接照发会被拒：「体积内容必须遵循最长边 ≥ 次长边 ≥ 最短边原则」。
 * 体积只看三个边长，跟哪条叫「长」无关，所以这里替他排好，物理含义不变。
 */
function sortDimensionsDesc(a: number, b: number, c: number): [number, number, number] {
  return [a, b, c].sort((x, y) => y - x) as [number, number, number];
}

/** 取出现次数最多的宽度（详情图统一到这个宽度）；没有就 800。 */
function mostCommonWidth(widths: number[]): number {
  const valid = widths.filter((value) => Number.isFinite(value) && value > 0);
  if (!valid.length) return 800;
  const counts = new Map<number, number>();
  valid.forEach((value) => counts.set(value, (counts.get(value) ?? 0) + 1));
  let best = valid[0];
  let bestCount = 0;
  counts.forEach((count, value) => {
    if (count > bestCount || (count === bestCount && value > best)) {
      best = value;
      bestCount = count;
    }
  });
  return best;
}

/** 读图片真实像素尺寸；读不到返回 0（后端会按 800 兜底并给提示）。 */
function measureImage(src: string): Promise<{ width: number; height: number }> {
  return new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => resolve({ width: 0, height: 0 });
    image.src = src;
  });
}

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.onerror = () => reject(new Error("读取图片失败"));
    reader.readAsDataURL(blob);
  });
}

function loadJsonLocal<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function saveJsonLocal(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 本机存储不可用时不影响主流程
  }
}

function isPublicHttpUrl(url: string): boolean {
  if (!/^https?:\/\//i.test(url)) return false;
  return !/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/i.test(url);
}

function pickUploadedUrl(uploaded: TemuImageUploadResult): string {
  return (uploaded.imageUrl as string) || (uploaded.image_url as string) || (uploaded.url as string) || "";
}

/**
 * 从 goods.add 的返回里挑出商品 id。
 *
 * 网关不同版本的字段名不一样（goodsId / productId / gid…），这里和后端台账
 * `goods_id_from_result` 用同一套顺序 —— 发布成功后必须让人一眼看到 id，
 * 否则只能去翻原始 JSON。
 */
function goodsIdOf(result: unknown): string {
  if (!result || typeof result !== "object") return "";
  const record = result as Record<string, unknown>;
  for (const key of ["goodsId", "goods_id", "productId", "product_id", "gid", "id"]) {
    const value = record[key];
    if (value !== undefined && value !== null && value !== "") return String(value);
  }
  for (const key of ["result", "data"]) {
    const nested = record[key];
    if (nested && typeof nested === "object") {
      const found = goodsIdOf(nested);
      if (found) return found;
    }
  }
  return "";
}

/** 分区标题：编号 + 名称（如「1 基本信息」）+ 右侧状态。 */
/**
 * 按钮里的小图标：统一 14px、描边跟随 currentColor（换主题自动变色）。
 * 只给侧栏那两颗主操作按钮用，顶部按钮保持纯文字（那里四个按钮已经够挤）。
 */
function BtnIcon({ path }: { path: string }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

const ICON_CHECK = "M3 8.4l3.1 3.1L13 4.9";
const ICON_SUBMIT = "M8 12.5V4M4.6 7.4L8 4l3.4 3.4";
const ICON_NEXT = "M3.5 8h9M9 4.5L12.5 8 9 11.5";

/**
 * 右栏三张指标卡的图标：层叠方块（变种）/ 图片（轮播）/ 价格（申报价）。
 * 和按钮图标同一套 16×16 线性风格，描边走 currentColor，
 * 颜色由卡片的状态色决定（缺项时整张卡连图标一起变红）。
 */
function StatIcon({ kind }: { kind: "variant" | "image" | "price" }) {
  const base = {
    width: 15,
    height: 15,
    viewBox: "0 0 16 16",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.4,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    focusable: "false" as const,
  };
  if (kind === "variant") {
    return (
      <svg {...base} aria-hidden="true">
        <rect x="2" y="5.6" width="6.6" height="6.6" rx="1.5" />
        <path d="M5.2 5.6V4a1.5 1.5 0 011.5-1.5h5.8A1.5 1.5 0 0114 4v5.8a1.5 1.5 0 01-1.5 1.5h-1.6" />
      </svg>
    );
  }
  if (kind === "image") {
    return (
      <svg {...base} aria-hidden="true">
        <rect x="2" y="3" width="12" height="10" rx="1.6" />
        <circle cx="5.6" cy="6.4" r="1" />
        <path d="M2.6 11.4l3-3 2.4 2.4 2.4-2.4 3 3" />
      </svg>
    );
  }
  return (
    <svg {...base} aria-hidden="true">
      <path d="M8 10.4v3.2M4.6 3.2L8 8l3.4-4.8M5.6 6.5h4.8M5.6 8.9h4.8" />
    </svg>
  );
}

function SectionHead({
  step,
  index,
  extra,
  active = false,
}: {
  step: { label: string; hint: string; done: boolean };
  index: number;
  extra?: ReactNode;
  /** 当前该填的这一块（第一个没填完的）：给个视觉锚点 */
  active?: boolean;
}) {
  return (
    <div className={`temu-sec-head${active ? " is-active" : ""}`}>
      <span className={`temu-sec-num${step.done ? " is-done" : ""}`}>
        {step.done ? <CheckGlyph /> : index + 1}
      </span>
      <div className="temu-sec-title">
        <h2>{step.label}</h2>
        <p>{step.hint}</p>
      </div>
      {extra ? <div className="temu-sec-extra">{extra}</div> : null}
    </div>
  );
}

/**
 * 后端在 detail.section 里给「该去哪个区块改」，这里只负责把 id 翻成人话。
 * 后端用的是页面区块 id（sec-*），加新区块时两边一起加。
 */
const SECTION_LABELS: Record<string, string> = {
  "sec-base": "基本信息",
  "sec-info": "产品信息",
  "sec-variant": "变种信息",
  "sec-pack": "包装信息",
};

/**
 * 本地校验失败落在哪一块。
 *
 * 后端错误自带 section；前端自己抛的校验错误只有一句中文，这里按关键词归类 ——
 * 归类对了就把那个区块描红并滚过去，用户在长表单里不用自己找「到底哪里错了」。
 * 顺序有讲究：「产品货号」要在「货号」之前判（分属产品信息，不是变种）。
 */
function sectionOfValidation(message: string): string {
  if (/店铺凭证|绑定店铺|类目|必填属性/.test(message)) return "sec-base";
  if (/运费模板|发货仓库|发货时效|外包装图片/.test(message)) return "sec-pack";
  if (/产品货号/.test(message)) return "sec-info";
  if (/第 \d+ 行|变种|尺码|颜色|申报价|建议售价|库存|尺寸|重量/.test(message)) return "sec-variant";
  if (/标题|轮播图|素材图|详情图|站外|链接/.test(message)) return "sec-info";
  return "";
}

export function TemuPublishPage({ onOpenDraftLibrary }: { onOpenDraftLibrary?: () => void } = {}) {
  const [status, setStatus] = useState<TemuSettingsStatus | null>(null);
  // 进页面时 status 还是 null：这时候不知道绑没绑，不能先按「未绑定」渲染，
  // 否则店铺信息一回来就闪一下红色横幅（首屏看着像出错了）。
  const [statusLoaded, setStatusLoaded] = useState(false);
  // 店铺名/有效期先读本机缓存：进页面立刻有内容，随后再用接口结果覆盖
  const [tokenInfo, setTokenInfo] = useState<TemuTokenInfo | null>(() =>
    loadJsonLocal<TemuTokenInfo | null>(`${SHOP_INFO_KEY}:${storageScope()}`, null),
  );
  const [error, setError] = useState("");
  /**
   * 报错对应的区块 id（后端在 detail.section 里给）。
   * 有值时错误条上多一个「去改产品信息」按钮 —— 光说「哪里错了」不够，
   * 长表单里用户还得自己滚到那一块找。
   */
  const [errorSection, setErrorSection] = useState("");
  const [busy, setBusy] = useState("");
  // 提交发布进行中：别的任务跑完不能把它「解除」，否则连点就是重复上架
  const [publishing, setPublishing] = useState(false);
  const publishingRef = useRef(false);
  // 只有「点过发布被拦下来」之后才把必填项标红：一进页面满屏红框反而让人焦虑
  const [showFieldErrors, setShowFieldErrors] = useState(false);

  const [settingsOpen, setSettingsOpen] = useState(false);
  // 类目没有预设销售属性、但允许自定义规格时（化妆包这类），由用户决定单 SKU 还是多规格。
  // 默认勾上：绝大多数商品都有颜色/尺码，默认单 SKU 会让人填完才发现规格没生效。
  const [variantsOn, setVariantsOn] = useState(true);
  // 顶部「绑定/切换店铺」的小菜单：不占地方，点开才展开已存店铺
  const [shopMenuOpen, setShopMenuOpen] = useState(false);
  const [accessToken, setAccessToken] = useState("");

  const storageSuffix = useMemo(() => storageScope(), []);
  const formKey = `${FORM_KEY}:${storageSuffix}`;
  const presetsKey = `${PRESETS_KEY}:${storageSuffix}`;
  const recentCategoryKey = `${RECENT_CATEGORY_KEY}:${storageSuffix}`;
  const rowsKey = `${ROWS_KEY}:${storageSuffix}`;

  const [form, setForm] = useState<PublishForm>(() => {
    const saved = loadJsonLocal<Partial<PublishForm>>(`${FORM_KEY}:${storageScope()}`, {});
    // 这里**不再**把存档里的 1 天 / 7 天拉回 9 天：
    // 发货时效改成 Temu 后台那四个档位单选（1/2/7/9 + 自定义）之后，1 和 7 是
    // 用户可能主动选的合法值，再清洗一遍会把用户刚选的档位每次刷新都改回去。
    // 旧默认值的影响只剩「一进来看到选中了 1 个工作日」，眼就能发现并改。
    const raw = saved.shipmentDays;
    if (typeof raw === "string" && raw && !/^\d+$/.test(raw.trim())) {
      // 历史存过 "9天" / " 9 " 这类脏值（换算出秒会被 parsePositive 拦下）
      saved.shipmentDays = raw.replace(/[^\d]/g, "") || DEFAULT_SHIPMENT_DAYS;
    }
    return { ...EMPTY_FORM, ...saved };
  });

  const [categoryOptions, setCategoryOptions] = useState<TemuCategory[]>([]);
  const [categoryPath, setCategoryPath] = useState<TemuCategory[]>(initialCategoryPath);
  const [categoryQuery, setCategoryQuery] = useState("");
  const [matchPaths, setMatchPaths] = useState<TemuCategoryPath[]>([]);
  // 按标题自动匹配到的类目候选
  const [titleMatches, setTitleMatches] = useState<TemuCategoryPath[]>([]);
  const [titleMatching, setTitleMatching] = useState(false);
  // 换草稿时自增：标题没变（同一个商品再点一次）也要重新跑一次匹配
  const [matchSeq, setMatchSeq] = useState(0);
  // 请求代次：换草稿/改标题后旧请求回来不能覆盖新状态
  const matchSeqRef = useRef(0);
  // 用户手动选过类目后，标题再变也不覆盖
  const categoryTouchedRef = useRef(false);
  const [attributes, setAttributes] = useState<TemuAttributes | null>(null);
  const [attrPicks, setAttrPicks] = useState<Record<number, AttrPick>>(() => {
    // 只在「存档里的叶子类目 == 当前恢复出来的叶子类目」时恢复：
    // 不同类目的属性 pid 完全不一样，盲恢复等于给新类目塞一堆不存在的 pid。
    const currentLeaf = Number(categoryPath[categoryPath.length - 1]?.catId ?? 0);
    const scope = storageScope();
    // 先读「当前这一条」（任何来源都写过），没有再退回同类目历史（老版本只有这个）。
    // 注意用 ?? 而不是判空对象：用户清空后存的就是空对象，那种情况必须保持空，
    // 否则一刷新又从历史里把值填回来了。
    const current = readCurrentAttrPicks(scope, currentLeaf);
    return current ?? readAttrPicksHistory(scope, currentLeaf);
  });
  /** 自动填需要读到「此刻已填了什么」；effect 里不能依赖 attrPicks（会自我触发） */
  const attrPicksRef = useRef(attrPicks);
  attrPicksRef.current = attrPicks;
  /** 已经对哪个叶子类目跑过自动填：同一类目只跑一次，用户清空后不会被偷偷填回来。 */
  const autoFilledCatRef = useRef(0);
  /**
   * 草稿库估好的属性：先存着，等类目属性表加载完再铺。
   * 不能直接 setAttrPicks —— 紧接着的 applyCategory 会 setAttrPicks({}) 把它清掉。
   */
  const carriedAttrPicksRef = useRef<{
    catId: number;
    picks: Record<number, AttrPick>;
    guessedPids: number[];
  } | null>(null);
  /** 铺进来的属性里哪些是「猜的」：表单上标「估」，让人先核对这几项 */
  const [guessedPids, setGuessedPids] = useState<number[]>([]);
  /** 从草稿库带进来的估填项数（提示「这些是按类目估的」） */
  const [carriedAttrCount, setCarriedAttrCount] = useState(0);
  /** 草稿属性里「Temu 那边已经查不到」的那些名字：清空了，必须让用户知道要重选 */
  const [staleAttrNames, setStaleAttrNames] = useState<string[]>([]);
  const [autoFill, setAutoFill] = useState<{ hits: AutoFillHit[]; ambiguous: string[] }>({
    hits: [],
    ambiguous: [],
  });

  const [freightTemplates, setFreightTemplates] = useState<TemuFreightTemplate[]>([]);
  const [warehouseGroups, setWarehouseGroups] = useState<TemuWarehouseGroup[]>([]);
  const [presets, setPresets] = useState<PublishPreset[]>(() =>
    loadJsonLocal<PublishPreset[]>(`${PRESETS_KEY}:${storageScope()}`, []),
  );
  const [presetName, setPresetName] = useState("");
  const [showCategoryBrowser, setShowCategoryBrowser] = useState(false);

  const [parentSpecs, setParentSpecs] = useState<TemuParentSpec[]>([]);
  const [colorParentId, setColorParentId] = useState(0);
  const [sizeParentId, setSizeParentId] = useState(0);
  // 当前类目要不要尺码表。`has` 只是「店里有没有现成的表」，
  // 真正决定字不显示的是 `info.required`（服装类目必须有，哪怕店里一张都没建）。
  const [sizeChart, setSizeChart] = useState<{
    loading: boolean;
    has: boolean | null;
    info: TemuSizeChartInfo | null;
  }>({ loading: false, has: null, info: null });
  /**
   * 是不是「服饰 / 量体」类目 —— 用「这个类目必须有尺码表」当判据。
   *
   * 来源是 `sizecharts.meta.get`：有测量项（胸围/裙长…）就 required=true。
   * 实测 2026-11-05：女装连衣裙 39107 = true；假发 31035 / 女包 29164 / 女鞋 29183 都
   * 回「没有找到当前类目对应的尺码组」= false。
   * 这类类目的主图要求竖图 3:4，非服饰要求 1:1，两者互斥，所以必须区分。
   */
  const isApparelCategory = sizeChart.info?.required === true;
  /**
   * 尺码表信息的最新值（给「上传图片」那种异步流程用）。
   *
   * 上传回调里读到的是**点击那一刻的快照**：用户刚选完类目、尺码表还在路上时点上传，
   * 快照里 `info` 还是 null，就会按非服饰裁成 1:1 —— 传完再发现是服装类目，
   * 图已经在图床上了，只能全部重传。所以用 ref 轮询等它落地。
   */
  const sizeChartRef = useRef(sizeChart);
  useEffect(() => {
    sizeChartRef.current = sizeChart;
  }, [sizeChart]);
  // 店铺切换版本号：运费模板/发货仓库/尺码表都是「每家店各自一套」，
  // 换店后必须重新取一遍，否则会拿着上一家店的 id 去发新店的商品。
  const [shopEpoch, setShopEpoch] = useState(0);

  const [rows, setRows] = useState<VariantRow[]>(() => {
    // 读之前先把本机老数据里的价格从「分」换成「元」（只做一次）
    migrateLegacyPriceUnit(storageScope());
    const saved = loadJsonLocal<VariantRow[]>(`${ROWS_KEY}:${storageScope()}`, []);
    return Array.isArray(saved) ? withFreshRowKeys(saved.filter((row) => row && row.key)) : [];
  });

  // 模板与发布失败记录（都存在本机，按账号隔离）
  const [productTemplates, setProductTemplates] = useState<ProductTemplate[]>(() =>
    localStore.productTemplates(),
  );
  // 换类目后行里残留的非法规格值：只提示不自动改（清行会丢价格/重量）
  const [specMismatch, setSpecMismatch] = useState<SpecMismatch[]>([]);
  const [variantTemplates, setVariantTemplates] = useState<VariantTemplate[]>(() =>
    localStore.variantTemplates(),
  );
  const [templateName, setTemplateName] = useState("");

  const [result, setResult] = useState<TemuBuildPayloadResult | null>(null);
  const [copied, setCopied] = useState(false);
  const [publishResult, setPublishResult] = useState<TemuPublishResult | null>(null);
  // 发布后在店铺里核对（按 SKC 货号查商品列表）
  const [shopCheck, setShopCheck] = useState<{ loading: boolean; error: string; items: TemuGoodsItem[] }>({
    loading: false,
    error: "",
    items: [],
  });
  const [mandatory, setMandatory] = useState<{
    source: string;
    result: Record<string, unknown>;
    warnings: string[];
    missing: Array<{ pid?: number; name?: string }>;
  } | null>(null);
  const [prefillInfo, setPrefillInfo] = useState<{
    source?: "ai" | "pod";
    needsReview?: string[];
    specLabel?: string;
    title?: string;
    imageCount?: number;
    declaredPrice?: string;
  } | null>(null);

  // compose 阶段产生的提醒（如建议售价单位可疑），build 后并到 warnings 里展示
  const composeWarnings = useRef<string[]>([]);
  const specCache = useRef(new Map<string, TemuSpecCreated>());
  const imageCache = useRef(new Map<string, string>());
  // 详情图统一宽度后的尺寸（key 同 imageCache）
  const detailSizeCache = useRef(new Map<string, { width: number; height: number }>());
  const prefillRef = useRef<TemuPublishPrefill | null>(null);
  // 当前表单来自哪条草稿：选完类目回写给草稿库，下次不用重选
  const prefillDraftId = useRef("");

  const warehouses = useMemo(
    () =>
      warehouseGroups.flatMap((group) =>
        (group.validWarehouseList ?? []).filter((item) => !item.warehouseDisable),
      ),
    [warehouseGroups],
  );

  const patch = (partial: Partial<PublishForm>) => setForm((current) => ({ ...current, ...partial }));

  /**
   * 已选的尺码表模板 ID（逗号/空格分隔的字符串 ↔ 数组）。
   * 店里已经有表的时候，让用户点一下直接填，别去 Temu 后台抄 ID。
   */
  const selectedSizeTemplateIds = form.sizeTemplates
    .split(/[,，\s]+/)
    .map((item) => Number(item))
    .filter((id) => Number.isFinite(id) && id > 0);

  const toggleSizeTemplate = (id: number) => {
    const next = selectedSizeTemplateIds.includes(id)
      ? selectedSizeTemplateIds.filter((item) => item !== id)
      : [...selectedSizeTemplateIds, id];
    patch({ sizeTemplates: next.join(",") });
  };

  // 表单内容落本机：切页签/刷新不丢，等同「保存到待发布」。
  // 防抖 300ms —— 标题每敲一个字都全量写一次（含长图片 URL 数组）会卡。
  useEffect(() => {
    const timer = setTimeout(() => saveJsonLocal(formKey, form), 300);
    return () => clearTimeout(timer);
  }, [form, formKey]);

  // 防抖窗口内切页签/卸载时，把最后一次内容补写一次（否则最后 300ms 的输入会丢）
  const latestSaveRef = useRef({ formKey, rowsKey, form, rows });
  latestSaveRef.current = { formKey, rowsKey, form, rows };
  useEffect(
    () => () => {
      const snapshot = latestSaveRef.current;
      saveJsonLocal(snapshot.formKey, snapshot.form);
      saveJsonLocal(snapshot.rowsKey, snapshot.rows);
    },
    [],
  );

  // 变种行同理落盘（防抖 300ms：价格是逐字符输入的）
  useEffect(() => {
    const timer = setTimeout(() => saveJsonLocal(rowsKey, rows), 300);
    return () => clearTimeout(timer);
  }, [rows, rowsKey]);

  const attrPicksKey = `${ATTR_PICKS_KEY}:${storageScope()}`;
  /**
   * 类目属性选择落盘（按叶子类目分开存）。
   *
   * 落盘的是「某个类目下选了什么」，所以必须连 catId 一起存；恢复时按 catId 取，
   * 对不上就不恢复。属性是下拉选的，不是逐字符输入，不需要防抖窗——但保持和其他
   * 存档一致的节奏。
   */
  /**
   * 用户有没有自己动过属性下拉。
   *
   * 只有动过才写「同类目历史」：自动填和草稿库估好的值都是程序铺的，
   * 写进历史等于教它「以后都这么填」—— 一条没改过的草稿就会把整个类目
   * 的复用值污染成猜出来的第一个选项，甚至清空后历史跟着变空。
   */
  const picksTouchedRef = useRef(false);
  useEffect(() => {
    const leafCatId = Number(categoryPath[categoryPath.length - 1]?.catId ?? 0);
    if (!leafCatId) return;
    const scope = storageScope();
    const timer = setTimeout(() => {
      // 刷新恢复用：什么来源都写
      saveCurrentAttrPicks(scope, leafCatId, attrPicks);
      // 同类目复用用：只认用户自己改过的
      if (picksTouchedRef.current) {
        saveAttrPicksHistory(scope, leafCatId, attrPicks);
        // 只把这批改动记一次；之后程序再铺（换类目重新估）不会跟着写进历史
        picksTouchedRef.current = false;
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [attrPicks, categoryPath, attrPicksKey]);

  /** 表单 onChange 走这里：标记「用户动过」，才允许写同类目历史。 */
  const handleAttributePicksChange = (next: Record<number, AttrPick>) => {
    picksTouchedRef.current = true;
    setAttrPicks(next);
  };

  /**
   * 刷新页面 / HMR 后类目是从本机存档恢复出来的（最近用过的类目、或草稿带入），
   * 但属性表还没拉过 —— 不补拉的话属性区一片空，连恢复出来的选择都没处显示。
   * 只跑一次；拉回来顺手按该类目的预设值检查变种行（带入的草稿可能是别的类目存的）。
   */
  const restoredAttrsLoadedRef = useRef(false);
  useEffect(() => {
    if (restoredAttrsLoadedRef.current) return;
    const currentLeaf = categoryPath[categoryPath.length - 1];
    if (!currentLeaf?.isLeaf) return;
    restoredAttrsLoadedRef.current = true;
    if (attributes) return;
    void temuPublishApi
      .attributes(currentLeaf.catId)
      .then((attrs) => {
        setAttributes(attrs);
        setSpecMismatch(findSpecMismatches(attrs, rows));
      })
      .catch(() => {
        // 拉不到（后端刚重启/网关抖动）不能把一次性标记留在 true：
        // 那样这次挂载内属性区永远空白。清掉后，下一次换类目/重套类目还会再试。
        restoredAttrsLoadedRef.current = false;
      });
  }, [categoryPath, attributes, rows]);

  /**
   * 产品属性自动填。
   *
   * 接口给的是「这个类目可以选哪些值」，不是「这批货是哪个值」，所以只能靠两条：
   * ① 同一个叶子类目上次怎么填的（连发同款时最准；vid 已失效就跳过）；
   * ② 标题/描述里点名了某个可选值（只认唯一命中，多个值同时出现说明判断不了）。
   * 纯数值属性（厚度 mm、平方克重 g/㎡）和销售属性一律不填 —— 编一个数比空着更糟。
   */
  const runAttributeAutofill = (basePicks: Record<number, AttrPick>, leafCatId: number) => {
    if (!attributes || !leafCatId) return;
    const result = autoFillAttributePicks({
      attributes,
      picks: basePicks,
      texts: autoFillSourceTexts({
        title: form.title,
        titleEn: form.titleEn,
        description: form.description,
        categoryNames: categoryPath.map((item) => categoryName(item)),
      }),
      history: readAttrPicksHistory(storageScope(), leafCatId),
    });
    if (result.hits.length) setAttrPicks(result.picks);
    setAutoFill({ hits: result.hits, ambiguous: result.ambiguous });
    autoFilledCatRef.current = leafCatId;
  };

  // 属性表一加载就填一次。依赖只跟 attributes：换类目必然换属性表；
  // 标题是后来才补的话，点「按标题重新识别」再跑 —— 否则用户清空的值会被偷偷填回来。
  useEffect(() => {
    const leafCatId = Number(categoryPath[categoryPath.length - 1]?.catId ?? 0);
    if (!attributes || !leafCatId) return;
    if (autoFilledCatRef.current === leafCatId) return;
    // 草稿库带进来的估填值先铺上（只铺空位，用户已填的不动），再补剩下没估到的
    const carried = carriedAttrPicksRef.current;
    carriedAttrPicksRef.current = null;
    let base = attrPicksRef.current;
    // 本机存档里的键是「原始 pid」。服装类目同一个 pid 挂多条属性时，会话里的键
    // 已经换成 refPid（见 normalizeDuplicatePids），不翻译的话刷新后已填的
    // 面料纹理 / 克重会全部对不上、变成空的。
    const legacy = migrateLegacyPicks(base, attributes);
    if (legacy.migrated) {
      base = legacy.picks;
      setAttrPicks(base);
    }
    // 类目对不上就丢掉：不同类目的属性 pid 完全不同，铺进去等于给 Temu 塞无效 pid
    if (carried && carried.catId && carried.catId === leafCatId) {
      // 存的是「估填那一刻」的 pid/vid，Temu 调过类目属性表之后这些值就查不到了。
      // 原样铺进去 ≡ 下拉框空白、却算「已填」→ 本地校验放行 → goods.add 被拒
      // 「请求的内容不存在或已被删除」。先按当前属性表过一遍，失效的清掉。
      const pruned = pruneStaleAttrPicks(carried.picks, attributes);
      base = { ...pruned.picks, ...attrPicksRef.current };
      setAttrPicks(base);
      setCarriedAttrCount(Object.keys(pruned.picks).length);
      setStaleAttrNames(pruned.dropped);
      setGuessedPids(carried.guessedPids);
    }
    runAttributeAutofill(base, leafCatId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [attributes]);

  /** 按当前标题重新识别：先撤掉上一轮自动填的值（用户手改过的保留），再跑一遍。 */
  const reRunAttributeAutofill = () => {
    const leafCatId = Number(categoryPath[categoryPath.length - 1]?.catId ?? 0);
    if (!leafCatId) return;
    const base = { ...attrPicksRef.current };
    for (const hit of autoFill.hits) {
      const pick = base[hit.pid];
      if (pick && Number(pick.vid) === hit.vid) delete base[hit.pid];
    }
    runAttributeAutofill(base, leafCatId);
  };

  /** 一键撤掉自动填的值：只撤仍然等于自动填的那个值，手改过的不动。 */
  const clearAttributeAutofill = () => {
    const hits = autoFill.hits;
    setAttrPicks((current) => {
      const next = { ...current };
      for (const hit of hits) {
        const pick = next[hit.pid];
        if (pick && Number(pick.vid) === hit.vid) delete next[hit.pid];
      }
      return next;
    });
    setAutoFill({ hits: [], ambiguous: [] });
    // 「草稿库估了 N 项」这条也没意义了：值已经撤掉
    setCarriedAttrCount(0);
    setStaleAttrNames([]);
    setGuessedPids([]);
  };

  useEffect(() => {
    let alive = true;
    // 草稿库「用于发布」带入：一次性读取后立即删除，避免下次进入重复覆盖。
    try {
      const raw = window.sessionStorage.getItem(TEMU_PUBLISH_PREFILL_KEY);
      if (raw) {
        window.sessionStorage.removeItem(TEMU_PUBLISH_PREFILL_KEY);
        const prefill = JSON.parse(raw) as TemuPublishPrefill;
        prefillRef.current = prefill;
        // 草稿存过类目：先标成「已选」，否则接口回来前标题匹配可能抢先覆盖它
        if (prefill.categoryPath?.length) categoryTouchedRef.current = true;
        applyPrefillMeta(prefill);
        setForm((current) => ({ ...current, ...prefillToForm(current, prefill) }));
      }
    } catch {
      // 带入数据损坏时按空表单继续，不阻塞页面。
    }
    (async () => {
      try {
        // token 校验和类目/规格/运费/仓库并行发起：总耗时 = 最慢的那个，而不是相加
        const tokenPromise = temuPublishApi.verifyToken().catch(() => null);
        // 五个接口各自降级：只有 settings 决定「绑没绑店铺」，其余失败只影响自己那块。
        // （原来一个 Promise.all，类目树抖一下整页就静默，界面看着像「还没绑定店铺」。）
        const settle = <T,>(promise: Promise<T>) =>
          promise.then(
            (value) => ({ ok: true as const, value }),
            () => ({ ok: false as const }),
          );
        const [settingsR, catsR, specsR, freightsR, whR] = await Promise.all([
          settle(temuPublishApi.settings()),
          settle(temuPublishApi.categories(0)),
          settle(temuPublishApi.parentSpecs()),
          settle(temuPublishApi.freightTemplates([SITE_ID])),
          settle(temuPublishApi.warehouses([SITE_ID])),
        ]);
        if (!alive) return;
        setStatusLoaded(true);
        const missed: string[] = [];
        if (settingsR.ok) {
          setStatus(settingsR.value);
        } else {
          missed.push("店铺凭证");
          // 店铺凭证没取到（后端刚重启/网络抖动）时 status 停在 null，而标题自动匹配
          // 被 status.configured.ready 门控 —— 没有这层重试，草稿库点「编辑」带入的
          // 没记忆过类目的草稿就永远不触发属性加载，界面看着是「点了没反应」还不报错。
          // 静默重试三次（2s/5s/10s），取到为止；仍失败就维持「点刷新重试」的原语义。
          void (async () => {
            for (const delay of [2000, 5000, 10000]) {
              await new Promise((resolve) => setTimeout(resolve, delay));
              if (!alive) return;
              try {
                const fresh = await temuPublishApi.settings();
                if (alive) setStatus(fresh);
                return;
              } catch {
                // 继续下一次重试
              }
            }
          })();
        }
        if (catsR.ok) {
          setCategoryOptions(catsR.value.categoryDTOList ?? []);
        } else {
          missed.push("类目树");
        }
        const specItems = specsR.ok ? specsR.value.parentSpecDTOS ?? [] : [];
        if (specsR.ok) {
          setParentSpecs(specItems);
          const pair = pickSpecPair(specItems);
          setColorParentId(pair.color);
          setSizeParentId(pair.size);
        } else {
          missed.push("颜色/尺码规格");
        }
        const templates = freightsR.ok ? freightsR.value.freightTemplates ?? [] : [];
        if (freightsR.ok) {
          setFreightTemplates(templates);
        } else {
          missed.push("运费模板");
        }
        const whList = whR.ok ? whR.value.warehouseDTOList ?? [] : [];
        if (whR.ok) {
          setWarehouseGroups(whList);
        } else {
          missed.push("发货仓库");
        }
        if (missed.length) {
          setError(`这些没取到（不影响已填内容，可点「刷新」重试）：${missed.join("、")}`);
        }
        const defaultWarehouse = firstWarehouse(whList);
        const whId = defaultWarehouse?.warehouseId ?? "";
        // 表单里已经有值就用表单的，否则用接口默认值
        setForm((current) => ({
          ...current,
          freightId: current.freightId || templates[0]?.freightTemplateId || "",
          warehouseId: current.warehouseId || whId,
        }));
        // 预填数据必须在 updater 外面取出并清空：
        // updater 在 StrictMode 下会被调用两次，把「读 ref + 置空」写在里面，
        // 第二次就拿到 null，草稿的尺码/价格全部落不到变种行上。
        const prefill = prefillRef.current;
        prefillRef.current = null;
        const seedRows = prefill ? buildRowsFromPrefill(prefill, whId) : null;
        setRows((current) => {
          // 带草稿进来：整表按这条草稿重建，别把上一条的颜色/价格/尺寸留在后面几行。
          // 一条草稿往往有多个 SKU（一个颜色一条），全部铺出来，不用用户再点生成。
          if (seedRows && seedRows.length) return seedRows;
          if (current.length) return current;
          return [createVariantRow(whId, "", "", EMPTY_BATCH, 0)];
        });
        // 这条草稿上次选过类目：直接套回去，省得每次重选
        if (prefill?.categoryPath?.length) {
          void applyCategory(prefill.categoryPath);
        }
        const info = await tokenPromise;
        if (alive && info) {
          setTokenInfo(info);
          saveJsonLocal(`${SHOP_INFO_KEY}:${storageScope()}`, info);
        }
      } catch (cause) {
        if (alive) setError(errorMessage(cause));
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  function applyPrefillMeta(prefill: TemuPublishPrefill) {
    prefillDraftId.current = prefill.draftId || "";
    // 草稿自带颜色/尺码 → 说明这条是多规格商品，直接按多规格发。
    // 不然「默认单 SKU」会把草稿里的颜色吞掉（用户看到填了，发出去却没有），
    // 那才是真正的卡点。占位值「默认」不算（那是单 SKU 的占位符）。
    if (hasRealSpecValue(prefill)) setVariantsOn(true);
    // 草稿库按类目估好的属性：留给自动填那一步铺（那时才对得上当前类目）
    carriedAttrPicksRef.current =
      prefill.attrPicks && Object.keys(prefill.attrPicks).length
        ? {
            catId: Number(prefill.attrPicksCatId ?? 0),
            picks: prefill.attrPicks,
            guessedPids: prefill.attrGuessedPids ?? [],
          }
        : null;
    setPrefillInfo({
      source: prefill.sourceKind,
      needsReview: prefill.needsReview,
      // 用「名称：值 · 名称：值」而不是把四个字段糊在一起，否则读不通
      specLabel: [
        prefill.specName1 && prefill.specValue1 ? `${prefill.specName1}：${prefill.specValue1}` : "",
        prefill.specName2 && prefill.specValue2 ? `${prefill.specName2}：${prefill.specValue2}` : "",
      ]
        .filter(Boolean)
        .join(" · "),
      title: prefill.title,
      imageCount: (prefill.images || []).length,
      declaredPrice: prefill.supplierPrice,
    });
  }

  /** 草稿里的规格值是不是「真的规格」（排除单 SKU 占位符「默认」）。 */
  function hasRealSpecValue(prefill: TemuPublishPrefill): boolean {
    return [prefill.specValue1, prefill.specValue2].some((raw) => {
      const text = String(raw ?? "").trim();
      return !!text && text !== "默认";
    });
  }

  /** 草稿值铺到表单（不动变种行）。 */
  function prefillToForm(current: PublishForm, prefill: TemuPublishPrefill): Partial<PublishForm> {
    return {
      title: prefill.title || current.title,
      // Temu 的变种属性值一/值二
      // 草稿里的「变种属性值一/二」= 颜色列表 / 尺码列表
      // 有全量取值就用全量（11 个颜色都要铺出来），否则退回单个值
      colorsText:
        (prefill.specValues1?.length ? prefill.specValues1.join(",") : prefill.specValue1) ||
        current.colorsText,
      sizesText:
        (prefill.specValues2?.length ? prefill.specValues2.join(",") : prefill.specValue2) ||
        current.sizesText,
      carousel: (prefill.images || []).join("\n") || current.carousel,
      detailImages: (prefill.detailImages || []).join("\n") || current.detailImages,
      description: prefill.description || current.description,
    };
  }

  /** 草稿值铺到某一变种行：kg→g，价格/库存/尺寸直接搬。 */
  function prefillToRow(row: VariantRow, prefill: TemuPublishPrefill): VariantRow {
    const weightKg = Number(prefill.weightKg);
    return {
      ...row,
      extCode: prefill.sku || row.extCode,
      colorName: firstSpecValue(prefill.specValue1) || row.colorName,
      sizeName: firstSpecValue(prefill.specValue2) || row.sizeName,
      supplierPrice: prefill.supplierPrice || row.supplierPrice,
      suggestedPrice: prefill.suggestedPrice || row.suggestedPrice,
      stock: prefill.stock || row.stock,
      weightG:
        Number.isFinite(weightKg) && weightKg > 0 ? String(Math.round(weightKg * 1000)) : row.weightG,
      lengthCm: prefill.lengthCm || row.lengthCm,
      widthCm: prefill.widthCm || row.widthCm,
      heightCm: prefill.heightCm || row.heightCm,
    };
  }

  /**
   * 草稿 → 变种行：一个 SKU 一行。
   *
   * AI 处理采到的草稿 `source_variant_records` 本来就是「一个颜色一条」，
   * 价格、库存、图都各不相同。以前只读第一条，进发布页只剩一行，剩下 10 个颜色
   * 要用户手填 —— 数据明明都在，没理由让人再录一遍。
   */
  function buildRowsFromPrefill(prefill: TemuPublishPrefill, whId: string): VariantRow[] {
    const variants = (prefill.variants ?? []).filter(
      (item) => item && (item.color || item.size || item.sku),
    );
    if (variants.length <= 1) {
      return [prefillToRow(createVariantRow(whId, "", "", EMPTY_BATCH, 0), prefill)];
    }
    const fallbackSize = prefill.specValue2 || "";
    return variants.map((variant, index) => {
      const base = createVariantRow(
        whId,
        variant.color || "",
        variant.size || fallbackSize,
        EMPTY_BATCH,
        index,
      );
      const merged = prefillToRow(base, prefill);
      return {
        ...merged,
        extCode: variant.sku || merged.extCode,
        colorName: variant.color || merged.colorName,
        sizeName: variant.size || merged.sizeName,
        // 每个变体自己的申报价（已按导出规则按成本算过）；没有才用草稿主价
        supplierPrice:
          typeof variant.price === "number" && variant.price > 0
            ? String(variant.price)
            : merged.supplierPrice,
        // 库存 0 是合法值（先建品后补货），不能用 || 兜掉
        stock: typeof variant.stock === "number" ? String(variant.stock) : merged.stock,
        thumbUrl: variant.image || merged.thumbUrl,
      };
    });
  }

  /** 把草稿库带入的数据直接铺到表单（用于「页签已打开」的情况）。 */
  const applyPrefillNow = (prefill: TemuPublishPrefill) => {
    // 同一条草稿再点一次（改了价格回来）→ 保留手改；换了一条 → 整表重建
    const sameDraft = !!prefill.draftId && prefill.draftId === prefillDraftId.current;
    applyPrefillMeta(prefill);
    setForm((current) => ({ ...current, ...prefillToForm(current, prefill) }));
    setRows((current) => {
      if (!current.length) {
        // 还没有变种行（仓库还没回来）：交给挂载逻辑消费 prefillRef
        return current;
      }
      if (sameDraft) return [prefillToRow(current[0], prefill), ...current.slice(1)];
      // 换了一条草稿：按这条草稿的全部变体重建整表
      return buildRowsFromPrefill(prefill, current[0].warehouseId);
    });
    // 同样不能写在 updater 里（StrictMode 会跑两次）
    prefillRef.current = prefill;
    // 换了草稿：上一条的推荐列表一律先清空（不管这条草稿存没存过类目），
    // 再按新标题重新匹配。存过类目的不覆盖，只把推荐换成这条商品的。
    setTitleMatches([]);
    setMatchSeq((value) => value + 1);
    if (prefill.categoryPath?.length) {
      categoryTouchedRef.current = true;
      void applyCategory(prefill.categoryPath);
    } else {
      // 上一条商品的类目/属性也必须清掉，否则新商品下面挂着别人的类目，看着就是「没识别」
      categoryTouchedRef.current = false;
      setCategoryPath([]);
      setAttributes(null);
      setAttrPicks({});
    }
  };

  // 发布页已经打开时 openModule 只会切页签、不会重新挂载，
  // 所以草稿库还要走事件通道把数据推进来，否则「用于发布」点了没反应。
  useEffect(() => {
    const onPrefill = (event: Event) => {
      const detail = (event as CustomEvent<TemuPublishPrefill>).detail;
      if (!detail) return;
      window.sessionStorage.removeItem(TEMU_PUBLISH_PREFILL_KEY);
      applyPrefillNow(detail);
    };
    window.addEventListener(TEMU_PUBLISH_PREFILL_EVENT, onPrefill);
    return () => window.removeEventListener(TEMU_PUBLISH_PREFILL_EVENT, onPrefill);
  }, []);

  const run = async (label: string, task: () => Promise<void>) => {
    setBusy(label);
    setError("");
    setErrorSection("");
    try {
      await task();
    } catch (cause) {
      const text = errorMessage(cause);
      setError(text);
      const section = (cause as { section?: unknown } | null)?.section;
      // 后端给了 section 就用它；本地校验只有一句话，按关键词归到哪个区块
      setErrorSection(typeof section === "string" ? section : sectionOfValidation(text));
    } finally {
      // 只清自己那次：发布这种长任务进行中，别的短任务结束不能把按钮重新解禁
      setBusy((current) => (current === label ? "" : current));
    }
  };

  const loadLevel = async (parentCatId: number) => {
    const response = await temuPublishApi.categories(parentCatId);
    setCategoryOptions(response.categoryDTOList ?? []);
  };

  /**
   * 清后端类目/属性缓存，再重载当前这一级 + 当前叶子属性。
   *
   * 抽成裸函数是为了给「发布被 6000002 拒」那条路复用：那时候不能用 run() 包，
   * 否则会把已经写好的错误提示盖掉。
   *
   * 返回「属性表到底变了什么」。Temu 说属性更新时，光让用户重选是句废话 ——
   * 他打开属性区一看和原来一样，不知道该动哪一项。有 diff 才能点名。
   */
  const reloadCategoryAttributes = async (previous?: TemuAttributes | null) => {
    await temuPublishApi.refreshCategories();
    const currentLeaf = categoryPath[categoryPath.length - 1];
    if (!currentLeaf?.isLeaf) {
      await loadLevel(currentLeaf?.catId ?? 0);
      return null;
    }
    const attrs = await temuPublishApi.attributes(currentLeaf.catId);
    const prev = previous ?? attributes;
    const diff = describeAttributeDiff(prev, attrs, attrPicksRef.current);
    // 只是 vid 被重建的能自动对上，真正对不上的才清掉 ——
    // 一律清掉的话，用户会被要求「从头重选一遍」，而大部分值根本没变。
    const remap = remapAttrPicks(prev, attrs, attrPicksRef.current);
    if (remap.dropped.length || remap.remapped.length) setAttrPicks(remap.picks);
    setAttributes(attrs);
    setSpecMismatch(findSpecMismatches(attrs, rows));
    await loadLevel(categoryPath[categoryPath.length - 2]?.catId ?? 0);
    // 类目树变了，标题匹配的结果也一并重算，别留着旧的推荐
    setMatchSeq((value) => value + 1);
    return diff;
  };

  /** 手动刷新类目树：清掉后端两层缓存，再重载当前这一级 + 当前叶子属性。 */
  const refreshCategoryTree = () =>
    run("category", async () => {
      await reloadCategoryAttributes();
    });

  /**
   * 换类目会清空已填的类目属性（不同类目的属性集不一样，留着会被判不合法），
   * 所以换之前问一句。同一叶子重套、自动推荐、产品模板套用都不问。
   */
  const confirmCategorySwitch = (nextLeaf?: TemuCategory): boolean => {
    const currentLeaf = categoryPath[categoryPath.length - 1];
    if (!nextLeaf?.isLeaf) return true;
    if (!currentLeaf?.isLeaf) return true;
    if (Number(currentLeaf.catId) === Number(nextLeaf.catId)) return true;
    const filled = Object.values(attrPicks).filter(
      (pick) => pick && (pick.vid > 0 || pick.text.trim()),
    ).length;
    if (!filled) return true;
    return window.confirm(
      `换成「${categoryName(nextLeaf)}」？\n` +
        `已经填好的 ${filled} 个类目属性会被清空 —— 不同类目要求的属性不一样。`,
    );
  };

  const chooseCategory = (item: TemuCategory) =>
    run("category", async () => {
      // 手动点分类下钻 = 用户自己选的类目。这里必须标记，否则之后改标题时
      // 「自动按标题匹配类目」会把用户选的类目静默换掉、连属性一起清空
      // （categoryTouchedRef 只拦自动推荐，不拦手动选择）。
      categoryTouchedRef.current = true;
      if (!confirmCategorySwitch(item)) return;
      const next = [...categoryPath, item];
      setCategoryPath(next);
      if (item.isLeaf) {
        const attrs = await temuPublishApi.attributes(item.catId);
        setAttributes(attrs);
        setAttrPicks({});
        setSpecMismatch(findSpecMismatches(attrs, rows));
        saveJsonLocal(recentCategoryKey, next);
      } else {
        setAttributes(null);
        setAttrPicks({});
        setSpecMismatch([]);
        await loadLevel(item.catId);
      }
    });

  const resetToLevel = (index: number) =>
    run("category", async () => {
      // 面包屑回退同样是用户操作：回退后类目还没定，但也不该被自动匹配抢走。
      categoryTouchedRef.current = true;
      if (index < 0) {
        setCategoryPath([]);
        setAttributes(null);
        setAttrPicks({});
        setSpecMismatch([]);
        await loadLevel(0);
        return;
      }
      const next = categoryPath.slice(0, index + 1);
      setCategoryPath(next);
      setAttributes(null);
      setAttrPicks({});
      setSpecMismatch([]);
      await loadLevel(next[next.length - 1].catId);
    });

  /** 直接套用一条完整类目路径（预设、最近使用都走这里）。 */
  const applyCategory = (
    path: TemuCategory[],
    options?: { auto?: boolean; skipConfirm?: boolean },
  ) => {
    // 换到别的叶子类目会清空已填属性：先问一句（自动推荐 / 套产品模板不问）
    if (!options?.auto && !options?.skipConfirm && !confirmCategorySwitch(path[path.length - 1])) {
      return Promise.resolve();
    }
    return run("category", async () => {
      // 自动推荐不算「用户选过」：标题一改还能继续跟着改
      if (!options?.auto) categoryTouchedRef.current = true;
      const leaf = path[path.length - 1];
      // 同一个叶子类目再套一次（标题反复改、自动推荐重复命中）不该把用户填好的属性清掉
      const sameLeaf = !!leaf?.catId && leaf.catId === categoryPath[categoryPath.length - 1]?.catId;
      setCategoryPath(path);
      setMatchPaths([]);
      if (!leaf?.isLeaf) {
        setAttributes(null);
        // 类目还没定到叶子：旧属性必须一起清，否则会挂着上一级类目的属性值
        setAttrPicks({});
        setSpecMismatch([]);
        await loadLevel(leaf?.catId ?? 0);
        return;
      }
      const attrs = await temuPublishApi.attributes(leaf.catId);
      setAttributes(attrs);
      if (!sameLeaf) {
        setAttrPicks({});
        // 换到别的类目了：行里的颜色/尺码可能不在新类目预设里，一次性列出来
        setSpecMismatch(findSpecMismatches(attrs, rows));
      }
      setCategoryOptions([]);
      saveJsonLocal(recentCategoryKey, path);
      // 回写给来源草稿：草稿库那行会显示类目路径，下次带入也直接选中
      if (prefillDraftId.current) {
        localStore.rememberDraftCategory(prefillDraftId.current, path);
      }
    });
  };

  // effect 里要用最新的 applyCategory，用 ref 兜住（它的闭包会随状态重建）
  const applyCategoryRef = useRef(applyCategory);
  applyCategoryRef.current = applyCategory;

  /**
   * 右侧「发布检查」要贴着视口固定，但它上面压着可 pinned 的顶栏 ——
   * 顶栏高度随 pin 状态变化，所以实测一次写进 CSS 变量，别在样式里写死数值。
   */
  useEffect(() => {
    const syncRailTop = () => {
      const bar = document.querySelector<HTMLElement>(".topbar-card");
      const pinned = Boolean(bar?.classList.contains("is-pinned"));
      const height = pinned && bar ? bar.getBoundingClientRect().height : 0;
      document.documentElement.style.setProperty(
        "--temu-rail-sticky-top",
        `${Math.round(height + 18)}px`,
      );
    };
    syncRailTop();
    window.addEventListener("resize", syncRailTop);
    const bar = document.querySelector(".topbar-card");
    const observer = new MutationObserver(syncRailTop);
    if (bar) observer.observe(bar, { attributes: true, attributeFilter: ["class"] });
    return () => {
      window.removeEventListener("resize", syncRailTop);
      observer.disconnect();
      document.documentElement.style.removeProperty("--temu-rail-sticky-top");
    };
  }, []);

  /**
   * 类目是否有尺码表：服装/鞋类才有，宠物/家居这类没有。
   * 没有就把「尺码表模板 ID」藏掉，避免让人以为必填。接口失败按「有」处理（fail-open）。
   */
  useEffect(() => {
    const leafId = categoryPath.length ? Number(categoryPath[categoryPath.length - 1].catId) : 0;
    if (!leafId) {
      setSizeChart({ loading: false, has: null, info: null });
      return;
    }
    let alive = true;
    setSizeChart((current) => ({ ...current, loading: true }));
    void temuPublishApi
      .sizeCharts(leafId)
      .then((info) => {
        if (alive) setSizeChart({ loading: false, has: !!info.has_size_chart, info });
      })
      .catch(() => {
        if (alive) setSizeChart({ loading: false, has: null, info: null });
      });
    return () => {
      alive = false;
    };
  }, [categoryPath, shopEpoch]);

  /**
   * 标题 → 自动匹配类目。
   *
   * 标题停下 1.2 秒后，用 categorySearchTexts 给出的查询词（逗号前 + 去掉 `for …` 的
   * 主体段）查一次，合并去重后取前 8 条当推荐。
   *
   * 推荐列表**每次换草稿/改标题都重新算**（哪怕这条草稿已经记住过类目），否则打开
   * 另一个商品还挂着上一条的推荐；只有「用户没手动选过、草稿也没记住过类目」时，
   * 才自动套用第一个叶子路径，其余情况只给推荐，点一下自己切。
   */
  useEffect(() => {
    const title = form.title.trim();
    if (title.length < 8 || !status?.configured.ready) return;
    const timer = setTimeout(() => {
      const queries = categorySearchTexts(title);
      if (!queries.length) return;
      // 这一轮匹配的代次：回来时如果已经不是最新的（换草稿/又改了标题）就整份丢掉，
      // 否则上一条商品的结果会把当前商品的类目改掉。
      const seq = (matchSeqRef.current += 1);
      setTitleMatching(true);
      setTitleMatches([]);
      void Promise.all(
        queries.map((text) => matchCategoryWithRetry(text)),
      )
        .then((responses) => {
          if (seq !== matchSeqRef.current) return;
          const merged = dedupeCategoryPaths(
            responses.flatMap((response) => response?.categoryPathDTOS ?? []),
          ).slice(0, 8);
          setTitleMatches(merged);
          if (categoryTouchedRef.current) return;
          const firstLeaf = merged
            .map((dto) => categoryPathFromDto(dto))
            .find((path) => path.length > 0 && path[path.length - 1].isLeaf);
          if (firstLeaf) {
            void applyCategoryRef.current(firstLeaf, { auto: true });
          }
        })
        .catch(() => {
          // 匹配失败不打扰：用户仍可手动搜关键词或点分类浏览
        })
        .finally(() => {
          if (seq === matchSeqRef.current) setTitleMatching(false);
        });
    }, 1200);
    return () => clearTimeout(timer);
  }, [form.title, status?.configured.ready, matchSeq]);

  const savePreset = () => {
    if (!categoryPath.length) {
      setError("先选好类目，再保存预设");
      return;
    }
    const fallbackName = categoryPath[categoryPath.length - 1].catName.slice(0, 20);
    const name = presetName.trim() || fallbackName;
    const next = [
      { name, categoryPath, freightId: form.freightId, warehouseId: form.warehouseId },
      ...presets.filter((item) => item.name !== name),
    ].slice(0, 6);
    setPresets(next);
    saveJsonLocal(presetsKey, next);
    setPresetName("");
  };

  const applyPreset = (preset: PublishPreset) => {
    patch({ freightId: preset.freightId || form.freightId, warehouseId: preset.warehouseId || form.warehouseId });
    if (preset.warehouseId) {
      setRows((current) => current.map((row) => ({ ...row, warehouseId: preset.warehouseId })));
    }
    void applyCategory(preset.categoryPath);
  };

  const removePreset = (name: string) => {
    const next = presets.filter((item) => item.name !== name);
    setPresets(next);
    saveJsonLocal(presetsKey, next);
  };

  /** 存成产品模板：分类 + 属性 + 运费/仓库/时效/产地/尺码表/开关，都是「每款一样」的东西。 */
  const saveProductTemplate = () => {
    if (!categoryPath.length) {
      setError("先选好类目，再存产品模板");
      return;
    }
    const attrs: Record<string, { vid: number; text: string }> = {};
    Object.entries(attrPicks).forEach(([pid, pick]) => {
      if (pick && (pick.vid > 0 || pick.text.trim())) attrs[pid] = { vid: pick.vid, text: pick.text };
    });
    localStore.saveProductTemplate({
      name: templateName.trim() || categoryName(categoryPath[categoryPath.length - 1]),
      categoryPath,
      attrs,
      freightId: form.freightId,
      warehouseId: form.warehouseId,
      shipmentDays: form.shipmentDays,
      origin: form.origin,
      sizeTemplates: form.sizeTemplates,
      isCustom: form.isCustom,
      isSensitive: form.isSensitive,
      colorParentId,
      colorParentName: parentSpecs.find((item) => item.parentSpecId === colorParentId)?.parentSpecName,
      sizeParentId,
      sizeParentName: parentSpecs.find((item) => item.parentSpecId === sizeParentId)?.parentSpecName,
      savedAt: new Date().toLocaleString("zh-CN"),
    });
    setProductTemplates(localStore.productTemplates());
    setTemplateName("");
  };

  /** 套用产品模板：只补空字段，已经填过的不动。 */
  const applyProductTemplate = async (name: string) => {
    const template = productTemplates.find((item) => item.name === name);
    if (!template) return;
    patch({
      freightId: form.freightId || template.freightId,
      warehouseId: form.warehouseId || template.warehouseId,
      // 发货时效只有「用户改过」才保留：停在默认 9 天说明没动过，这时套模板
      // 应该跟随模板里的档位（模板也可能是 2 天 / 自定义 12 天）。
      // 别再用 `!== "1"` 当「没填」的判据了 —— 1 天现在是合法档位。
      shipmentDays:
        form.shipmentDays && form.shipmentDays !== DEFAULT_SHIPMENT_DAYS
          ? form.shipmentDays
          : template.shipmentDays || DEFAULT_SHIPMENT_DAYS,
      origin: form.origin || template.origin,
      sizeTemplates: form.sizeTemplates || template.sizeTemplates,
      isCustom: template.isCustom,
      isSensitive: template.isSensitive,
    });
    // 必须先切类目、等属性表回来，再填模板里的属性。
    // applyCategory 切到不同叶子类目时会 setAttrPicks({})（旧类目的属性和新类目对不上），
    // 这一步发生在 await 之后 —— 如果先填属性再切类目，刚填的会被清空，
    // 表现就是「套了产品模板但必填属性还是空的」。
    if (template.categoryPath.length) {
      // 模板自带要填的属性，换类目时不用再问「属性会被清空吗」
      await applyCategory(template.categoryPath, { skipConfirm: true });
    }
    setAttrPicks((current) => {
      const next = { ...current };
      Object.entries(template.attrs).forEach(([pid, pick]) => {
        if (!next[Number(pid)]) next[Number(pid)] = pick;
      });
      return next;
    });
  };

  /** 存成变种模板：颜色×尺码 + 第一行的批量值。 */
  const saveVariantTemplate = () => {
    const base = rows[0];
    localStore.saveVariantTemplate({
      name: templateName.trim() || `${form.colorsText || "颜色"} × ${form.sizesText || "尺码"}`,
      colorsText: form.colorsText,
      sizesText: form.sizesText,
      batch: {
        supplierPrice: base?.supplierPrice ?? "",
        suggestedPrice: base?.suggestedPrice ?? "",
        stock: base?.stock ?? "",
        weightG: base?.weightG ?? "",
        lengthCm: base?.lengthCm ?? "",
        widthCm: base?.widthCm ?? "",
        heightCm: base?.heightCm ?? "",
      },
      savedAt: new Date().toLocaleString("zh-CN"),
    });
    setVariantTemplates(localStore.variantTemplates());
    setTemplateName("");
  };

  /** 套用变种模板：填颜色/尺码列表，并把批量值铺到现有行。 */
  const applyVariantTemplate = (name: string) => {
    const template = variantTemplates.find((item) => item.name === name);
    if (!template) return;
    patch({ colorsText: template.colorsText, sizesText: template.sizesText });
    setRows((current) =>
      current.map((row) => {
        const next = { ...row };
        (Object.keys(template.batch) as Array<keyof typeof template.batch>).forEach((key) => {
          const value = template.batch[key];
          if (value && !next[key]) next[key] = value;
        });
        return next;
      }),
    );
  };

  const searchCategory = () =>
    run("match", async () => {
      if (!categoryQuery.trim()) throw new Error("请输入类目关键词");
      const response = await matchCategoryWithRetry(categoryQuery.trim());
      if (!response) {
        throw new Error("类目匹配服务暂时不可用（已重试 3 次），稍后再试，或直接用「分类浏览」逐级选");
      }
      setMatchPaths(response.categoryPathDTOS ?? []);
    });

  const chooseMatchedPath = (dto: TemuCategoryPath) =>
    run("category", async () => {
      const path = categoryPathFromDto(dto);
      if (!path.length) throw new Error("这个匹配结果没有类目路径");
      setCategoryPath(path);
      setMatchPaths([]);
      const leaf = path[path.length - 1];
      if (leaf.isLeaf) {
        const attrs = await temuPublishApi.attributes(leaf.catId);
        setAttributes(attrs);
        setAttrPicks({});
        setCategoryOptions([]);
        saveJsonLocal(recentCategoryKey, path);
      } else {
        await loadLevel(leaf.catId);
      }
    });

  /**
   * 换店 / 新增店 / 停用店之后必须做的事：把「店铺级」数据全部重取一遍。
   *
   * 运费模板 ID、发货仓库 ID、尺码表、父规格（含本店自建的规格值）都是每家店
   * 各一套。切店后如果还留着上一家店的 freightTemplateId / warehouseId，
   * 提交时 Temu 只会回一句「仓库不存在」或者干脆建到别的仓去。
   */
  const reloadShopScopedData = async () => {
    setShopEpoch((value) => value + 1);
    const [freightsR, whR, specsR] = await Promise.all([
      temuPublishApi.freightTemplates([SITE_ID]).catch(() => null),
      temuPublishApi.warehouses([SITE_ID]).catch(() => null),
      temuPublishApi.parentSpecs().catch(() => null),
    ]);
    const templates = freightsR?.freightTemplates ?? [];
    const whList = whR?.warehouseDTOList ?? [];
    setFreightTemplates(templates);
    setWarehouseGroups(whList);
    if (specsR) {
      const specItems = specsR.parentSpecDTOS ?? [];
      setParentSpecs(specItems);
      const pair = pickSpecPair(specItems);
      setColorParentId(pair.color);
      setSizeParentId(pair.size);
    }
    // 旧店的值一律丢掉，换成新店的默认值（没有就让用户自己选，别带着旧 id 发出去）
    const nextFreight = templates[0]?.freightTemplateId ?? "";
    const nextWarehouse = firstWarehouse(whList)?.warehouseId ?? "";
    setForm((current) => ({ ...current, freightId: nextFreight, warehouseId: nextWarehouse }));
    setRows((current) => current.map((row) => ({ ...row, warehouseId: nextWarehouse })));
    if (!freightsR || !whR) {
      setError("店铺已切换，但运费模板/发货仓库没取到：点「刷新」重试后再发布。");
    }
  };

  /** 新增店铺：后端先拿这条 token 打一次网关，成功才保存并设为当前。 */
  const saveCredentials = () =>
    run("settings", async () => {
      const token = accessToken.trim();
      if (!token) {
        throw new Error("请先粘贴店铺 access_token（Temu 后台：系统管理 → 授权管理 → 获取授权）");
      }
      const saved = await temuPublishApi.addShop(token);
      setStatus(saved);
      setAccessToken("");
      const info = await temuPublishApi.verifyToken(true).catch(() => null);
      if (info) {
        setTokenInfo(info);
        saveJsonLocal(`${SHOP_INFO_KEY}:${storageScope()}`, info);
      }
      await reloadShopScopedData();
    });

  /** 重读一次凭证状态（打开凭证面板 / 店铺菜单时用，避免显示成旧值）。 */
  const refreshSettingsStatus = () => {
    void temuPublishApi
      .settings()
      .then((fresh) => {
        setStatus(fresh);
        // 凭证齐全就顺手校验一次，面板/菜单里才能显示店铺名与有效期（服务端有 10 分钟缓存）
        if (!fresh.configured.ready) return undefined;
        return temuPublishApi
          .verifyToken()
          .then(setTokenInfo)
          .catch(() => undefined);
      })
      .catch(() => undefined);
  };

  /** 换店：用已保存的那条 token，不用重新输入。 */
  const switchShop = (shopId: string) =>
    run("shop", async () => {
      const next = await temuPublishApi.activateShop(shopId);
      setStatus(next);
      const info = await temuPublishApi.verifyToken(true).catch(() => null);
      if (info) {
        setTokenInfo(info);
        saveJsonLocal(`${SHOP_INFO_KEY}:${storageScope()}`, info);
      }
      await reloadShopScopedData();
    });

  const deleteShop = (shop: TemuShop) =>
    run("shop", async () => {
      const label = shop.mall_name || shop.id;
      if (!window.confirm(`删除店铺「${label}」？\n删掉之后要重新贴 token 才能再用这一家。`)) return;
      const next = await temuPublishApi.removeShop(shop.id);
      setStatus(next);
      if (!next.configured.ready) {
        setTokenInfo(null);
        saveJsonLocal(`${SHOP_INFO_KEY}:${storageScope()}`, null);
      }
      await reloadShopScopedData();
    });

  /** 解绑当前账号的店铺：只删自己那份凭证。 */
  const clearShop = () =>
    run("clear-shop", async () => {
      const confirmed = window.confirm(
        "停用当前店铺：发布时会提示还没绑定。\n这家店会留在「已保存的店铺」列表里，想再用点一下「切换到这家」即可，不用重新贴 token。确认停用？",
      );
      if (!confirmed) return;
      const next = await temuPublishApi.clearSettings();
      setStatus(next);
      setTokenInfo(null);
      saveJsonLocal(`${SHOP_INFO_KEY}:${storageScope()}`, null);
      // 店铺级数据一起清掉：下次绑别家店时不能还带着这家的运费模板/仓库 id
      setFreightTemplates([]);
      setWarehouseGroups([]);
      setShopEpoch((value) => value + 1);
      setForm((current) => ({ ...current, freightId: "", warehouseId: "" }));
      setRows((current) => current.map((row) => ({ ...row, warehouseId: "" })));
    });

  const verify = () =>
    run("verify", async () => {
      const info = await temuPublishApi.verifyToken(true);
      setTokenInfo(info);
      saveJsonLocal(`${SHOP_INFO_KEY}:${storageScope()}`, info);
    });

  const slotText = (slot: ImageSlot): string =>
    slot === "carousel" ? form.carousel : slot === "outer" ? form.outerImages : form.detailImages;

  const setSlotText = (slot: ImageSlot, text: string) => {
    if (slot === "carousel") patch({ carousel: text });
    else if (slot === "outer") patch({ outerImages: text });
    else patch({ detailImages: text });
  };

  /** 「从成品库挑图」：当前在给哪个槽位挑 */
  const [pickerSlot, setPickerSlot] = useState<ImageSlot | null>(null);
  /** 图片放大预览（变种图 / 轮播图点开看大图） */
  const [imagePreview, setImagePreview] = useState<{ src: string; alt?: string } | null>(null);
  /** 「怎么拿 access_token」说明弹窗：查店铺授权状态时随手能点开，不用去翻文档。 */
  const [tokenHelpOpen, setTokenHelpOpen] = useState(false);
  /** 挑图超出上限时的一句话提示（几秒后自己消失） */
  const [pickerNotice, setPickerNotice] = useState("");

  /**
   * 从成品库挑回来的图追加到槽位。
   *
   * 只做「去重 + 截到上限」：图片地址本身不用管，提交时 `resolveImage` 会把
   * 本地媒体路径 / 公网 URL 统一转存到 Temu 图床。
   */
  const appendSlotImages = (slot: ImageSlot, urls: string[]) => {
    const current = splitLines(slotText(slot));
    const merged = [...current];
    for (const url of urls) {
      const text = url.trim();
      if (text && !merged.includes(text)) merged.push(text);
    }
    const limit = IMAGE_LIMITS[slot];
    if (merged.length > limit) {
      const label = IMAGE_SLOTS.find((item) => item.key === slot)?.label ?? "图片";
      setPickerNotice(`${label}最多 ${limit} 张，多出的 ${merged.length - limit} 张没有加进来`);
      window.setTimeout(() => setPickerNotice(""), 4000);
    }
    setSlotText(slot, merged.slice(0, limit).join("\n"));
  };

  /**
   * 上传图片：轮播/外包装裁 1:1 再传（Temu 主图要求方图），
   * 详情图**不能裁方** —— 商详楼层是长图，裁成 1:1 会把内容切掉；
   * 详情图交给服务端按原宽上传（target_width=0 表示不缩放），宽度在提交时统一。
   */
  const uploadFiles = (slot: ImageSlot, files: File[]) =>
    run("upload", async () => {
      if (!files.length) return;
      // 数量先挡：外包装图 Temu 只收 6 张，轮播 10 张。传上去再被拒会白等半天上传
      const exist = splitLines(slotText(slot)).length;
      const limit = IMAGE_LIMITS[slot];
      if (exist + files.length > limit) {
        throw new Error(
          `${IMAGE_SLOTS.find((item) => item.key === slot)?.label ?? "图片"}最多 ${limit} 张：` +
            `已有 ${exist} 张，这次选了 ${files.length} 张。`,
        );
      }
      const leaf = categoryPath[categoryPath.length - 1];
      // 主图比例是**按类目**决定的（服饰 3:4 / 其它 1:1），没选类目就上传等于赌一把：
      // 传成 1:1 之后再选服装类目，图已经上了图床、比例改不了，只能全部重传。
      if (!leaf?.catId) {
        throw new Error("请先选到叶子类目再上传图片：主图比例按类目决定（服饰 3:4、其它 1:1）。");
      }
      /**
       * 服饰类目（必须有尺码表 = 量体类目）主图要竖图 3:4，
       * 非服饰要方图 1:1 —— 实测报错分别是
       * 「服饰类目skc轮播图校验失败，应符合 3:4，宽>=1340，高>=1785」
       * 「非服饰类目货品轮播图……1:1、≥800px」。
       */
      // 尺码表还在读就等它一下（最多 4 秒）：读的是快照的话会把服装类目按 1:1 裁。
      let chart = sizeChartRef.current;
      const deadline = Date.now() + 4000;
      while (chart.loading && Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 150));
        chart = sizeChartRef.current;
      }
      const apparel = chart.info?.required === true;
      const shapeCarousel = (raw: string) =>
        apparel
          ? toRatioDataUrl(raw, APPAREL_CAROUSEL_WIDTH, APPAREL_CAROUSEL_HEIGHT)
          : toSquareDataUrl(raw);
      // 并发 3 张：串行传 6 张要等 6~18 秒，并发后基本等于最慢那张
      const uploadedUrls = await mapWithConcurrency(files, 3, async (file) => {
        const raw = await blobToDataUrl(file);
        const uploaded =
          slot === "detail"
            ? await temuPublishApi.uploadDetailImage(raw, 0, leaf?.catId)
            : await temuPublishApi.uploadImage(
                slot === "carousel" ? await shapeCarousel(raw) : await toSquareDataUrl(raw),
                leaf?.catId,
              );
        return pickUploadedUrl(uploaded);
      });
      const urls = uploadedUrls.filter(Boolean);
      if (!urls.length) throw new Error("图片上传成功但没有返回图片地址");
      setSlotText(slot, [...splitLines(slotText(slot)), ...urls].join("\n"));
      // 服饰类目的素材图单独校验「1:1、宽>=800」，而轮播图已经被裁成 3:4 竖图，
      // 素材图留空时默认回退到轮播首图 → 直接被拒（实测第 4 关）。
      // 这里用同一张原图另裁一张 800×800 方图传上去填进素材图，省掉用户再传一遍。
      if (slot === "carousel" && apparel && !form.material.trim() && files[0]) {
        const square = await toSquareDataUrl(await blobToDataUrl(files[0]));
        const materialUrl = pickUploadedUrl(await temuPublishApi.uploadImage(square, leaf?.catId));
        if (materialUrl) patch({ material: materialUrl });
      }
    });

  /**
   * 类目销售属性的合法规格值：parentSpecId → [{name, specId}]。
   *
   * Temu 的规格值必须来自类目预设（颜色是预设色系），自己 createSpec 造值
   * 提交时会报「不合法的规格属性 Color_Black」。
   */
  const saleValueIndex = useMemo(() => {
    const index = new Map<number, Array<{ name: string; specId: number }>>();
    for (const prop of attributes?.properties ?? []) {
      if (!prop.isSale) continue;
      const parentId = Number(prop.parentSpecId ?? 0);
      if (!parentId) continue;
      const options = (prop.values ?? [])
        .map((item) => ({ name: String(item.value ?? "").trim(), specId: Number(item.specId ?? 0) }))
        .filter((item) => item.name && item.specId > 0);
      if (options.length) index.set(parentId, options);
    }
    return index;
  }, [attributes]);

  /**
   * 类目允许几个自定义规格维度（Temu 的 inputMaxSpecNum）。
   *
   * 实测（2026-11-04，真实店铺）：
   *   化妆包 30988 = 2、桌布 10498 = 2、餐垫 10493 = 2 —— 这些类目 attrs 里
   *   **一个 isSale 属性都没有**，但都允许自定义规格；
   *   假发 31035 = 0，反而带两个预设销售属性（颜色 / 头发长度）。
   *
   * 所以「没有 isSale」≠「不能多变种」：以前只看 isSale 就判单 SKU，
   * 化妆包这种能卖多色的类目被硬压成单 SKU，是错的。
   */
  const specDimLimit = Number(attributes?.inputMaxSpecNum ?? 0);
  /** 类目自带预设销售属性（颜色/尺码带 specId，可直接选） */
  const hasPresetSaleProps = saleValueIndex.size > 0;
  /** 没有预设、但允许自定义规格（值由我们 createSpec 建） */
  const canCustomSpecs = specDimLimit > 0;

  /**
   * 走单 SKU 还是多规格：
   * - 类目有预设销售属性 → 必须多规格（不然 Temu 判「规格不能为空」）
   * - 没有预设但允许自定义 → 看用户勾选（默认单 SKU，保持原有习惯）
   * - 两样都没有 → 只能单 SKU
   */
  const singleSkuMode = hasPresetSaleProps ? false : !(canCustomSpecs && variantsOn);

  /** 取某个父规格下的预设值（给变种表下拉用）。 */
  const specOptions = (parentSpecId: number): string[] =>
    (saleValueIndex.get(parentSpecId) ?? []).map((item) => item.name);

  /**
   * 当前这两列在 Temu 那边叫什么（颜色 / 款式 / 型号 / 容量…）。
   *
   * 界面文案一律用它，不能再写「尺码」：手机壳是「型号」、收纳盒是「容量」，
   * 写死的话用户看着标签就填错了列。
   */
  const colorSpecName =
    parentSpecs.find((item) => item.parentSpecId === colorParentId)?.parentSpecName || "属性一";
  const sizeSpecName =
    parentSpecs.find((item) => item.parentSpecId === sizeParentId)?.parentSpecName || "属性二";

  /**
   * 换类目后，变种行里已有的颜色/尺码可能不在新类目的预设值里。
   *
   * Temu 的规格值必须来自类目预设（自造值提交报「不合法的规格属性 Color_Black」），
   * 而换类目时我们**不清空行**（价格/重量都在里面，清 = 丢数据）。所以这里只做检查，
   * 一次性把全部不合法的值列出来 —— 原来要等到点发布、ensureSpec 逐行抛错，
   * 而且一次只报第一行第一个值。
   *
   * 只在类目确实给了预设值时才判定，避免「类目没有销售属性」被误报。
   */
  const findSpecMismatches = (
    attrs: TemuAttributes | null,
    list: VariantRow[],
  ): SpecMismatch[] => {
    const presets = new Map<number, Set<string>>();
    for (const prop of attrs?.properties ?? []) {
      if (!prop.isSale) continue;
      const parentId = Number(prop.parentSpecId ?? 0);
      if (!parentId) continue;
      const names = new Set(
        (prop.values ?? []).map((item) => String(item.value ?? "").trim()).filter(Boolean),
      );
      if (names.size) presets.set(parentId, names);
    }
    const colorPresets = presets.get(colorParentId);
    const sizePresets = presets.get(sizeParentId);
    const out: SpecMismatch[] = [];
    list.forEach((row, index) => {
      const color = row.colorName.trim();
      if (color && colorPresets && !colorPresets.has(color)) {
        out.push({ rowKey: row.key, rowNo: index + 1, field: "color", value: color });
      }
      const size = row.sizeName.trim();
      if (size && sizePresets && !sizePresets.has(size)) {
        out.push({ rowKey: row.key, rowNo: index + 1, field: "size", value: size });
      }
    });
    return out;
  };

  /**
   * 真正要提示的不匹配项：某一维被关掉后，它名下的旧值不再算数
   * （提交时根本不带那个规格），提示条不能还挂着它。
   */
  const activeSpecMismatch = specMismatch.filter((item) =>
    item.field === "color" ? colorParentId > 0 : sizeParentId > 0,
  );

  /** 按最接近的预设值把不合法的颜色/尺码批量改掉；对不上的保持原样并继续留在清单里。 */
  const fixSpecMismatches = () => {
    if (!specMismatch.length) return;
    const colorNames = specOptions(colorParentId);
    const sizeNames = specOptions(sizeParentId);
    const next = rows.map((row) => {
      const badColor = specMismatch.some((item) => item.rowKey === row.key && item.field === "color");
      const badSize = specMismatch.some((item) => item.rowKey === row.key && item.field === "size");
      const colorFixed = badColor ? matchPresetValue(row.colorName, colorNames) : null;
      const sizeFixed = badSize ? matchPresetValue(row.sizeName, sizeNames) : null;
      return {
        ...row,
        colorName: colorFixed ?? row.colorName,
        sizeName: sizeFixed ?? row.sizeName,
      };
    });
    setRows(next);
    /**
     * 上方的「颜色 / 尺码 列表」也要一起改。
     *
     * 只改表格是治标：那两个输入框还留着非法值，用户再点一次「生成变种」就按旧文本
     * 重新铺一遍，刚修好的行又被替换成「浅绿色」这种提交必被拒的值。
     */
    const remapList = (text: string, options: string[]) => {
      const seen = new Set<string>();
      const out: string[] = [];
      for (const raw of text.split(/[,，]/)) {
        const name = raw.trim();
        if (!name) continue;
        const fixed = matchPresetValue(name, options) ?? name;
        if (seen.has(fixed)) continue;
        seen.add(fixed);
        out.push(fixed);
      }
      return out.join(",");
    };
    const nextColorsText = remapList(form.colorsText, colorNames);
    const nextSizesText = remapList(form.sizesText, sizeNames);
    if (nextColorsText !== form.colorsText || nextSizesText !== form.sizesText) {
      patch({ colorsText: nextColorsText, sizesText: nextSizesText });
    }
    setSpecMismatch(findSpecMismatches(attributes, next));
  };

  /**
   * 自建规格值的缓存：颜色 × 尺码是笛卡尔积，同一个值会被反复解析，
   * 不缓存就会对同一个值重复 createSpec（Temu 侧可能报「已存在」）。
   */
  const createdSpecsRef = useRef(new Map<string, TemuSkuSpec>());

  /**
   * 把「颜色 / 尺码 的文字值」解析成 Temu 认的规格（spec_id）。
   *
   * 两条路：
   * - 类目有预设值 → 必须命中预设，对不上就报错让用户从下拉选；
   * - 类目没有这个属性的预设值（化妆包 / 桌布这类自定义规格类目：attrs 里一个
   *   isSale 都没有，`inputMaxSpecNum` 却是 2）→ 调 createSpec 自己建。
   *
   * 以前这里缺第二条分支，一律抛「Temu 只认类目预设值」，而界面上明明写着
   * 「可以自己填写（Temu 按自定义规格收）」—— 于是这类类目永远发不出去。
   * 批量发布那边一直有这条自建分支，单个发布漏了。
   */
  const ensureSpec = async (
    parentSpec: TemuParentSpec | null,
    specName: string,
  ): Promise<TemuSkuSpec> => {
    if (!parentSpec) throw new Error("Temu 父规格列表为空，无法创建规格");
    const name = specName.trim();
    if (!name) throw new Error("规格名称不能为空");

    const cacheKey = `${parentSpec.parentSpecId}:${name}`;
    const cached = createdSpecsRef.current.get(cacheKey);
    if (cached) return cached;

    const options = saleValueIndex.get(parentSpec.parentSpecId) ?? [];
    // 草稿带来的是「安卓通用款-黑色」「迷雾蓝」这种描述，先自动对齐到预设值
    const matchedName = matchPresetValue(name, options.map((item) => item.name));
    const preset = matchedName ? options.find((item) => item.name === matchedName) : undefined;
    if (preset) {
      return {
        parent_spec_id: parentSpec.parentSpecId,
        parent_spec_name: parentSpec.parentSpecName,
        spec_id: preset.specId,
        spec_name: preset.name,
      };
    }

    // 这个属性压根没有预设值 → 自建（界面已经告诉用户「Temu 按自定义规格收」）
    if (!options.length) {
      const created = await temuPublishApi.createSpec(parentSpec.parentSpecId, name);
      const specId = Number(created.specId ?? 0);
      if (!specId) {
        throw new Error(
          `${parentSpec.parentSpecName}「${name}」建成自定义规格失败（Temu 没返回 spec_id），重试一次；` +
            "仍失败就去 Temu 后台确认这个类目还能不能加自定义规格",
        );
      }
      const resolved: TemuSkuSpec = {
        parent_spec_id: parentSpec.parentSpecId,
        parent_spec_name: parentSpec.parentSpecName,
        spec_id: specId,
        spec_name: created.specName?.trim() || name,
      };
      createdSpecsRef.current.set(cacheKey, resolved);
      return resolved;
    }

    // 有预设但对不上：这是真的填错了，明确告诉他能选什么
    const samples = specOptions(parentSpec.parentSpecId).slice(0, 6).join("、");
    throw new Error(
      `「${name}」不在「${parentSpec.parentSpecName}」的预设值里` +
        (samples ? `：点上方「浏览全部预设」选一个（如 ${samples}）` : ""),
    );
  };

  /**
   * 读本地/素材图；读不到时给出能照着做的提示。
   *
   * 直接让 404 冒上去，会被 http 层映射成「请求的内容不存在或已被删除」，
   * 用户完全猜不到是哪张图、更猜不到该去哪儿补。
   *
   * ⚠️ 地址必须**从头**截，不能只留尾巴：本地素材/输出图的路径末尾全是一串
   * sha256，只露尾巴等于什么都没说（2026-11-03 实测：报错里只剩
   * `…3d303380…f2f08a`，看不出是 media-assets 还是 pod-customization）。
   * 也不要断言「被清理了」——素材可能压根没下载成功，怪用户删文件是误判。
   */
  const readLocalImage = async (text: string, label: string): Promise<Blob> => {
    try {
      return await httpBlob(text);
    } catch (cause) {
      const brief = text.length > 120 ? `${text.slice(0, 120)}…` : text;
      const reason = cause instanceof Error ? cause.message : String(cause);
      throw new Error(`${label}读不到：${brief}（${reason}）。请换一张${label}再试。`);
    }
  };

  /**
   * 本地媒体路径/外链统一上传到 Temu 图床；已是 Temu 地址则复用。
   *
   * `shape` 给定时先按比例裁一遍再上传 —— **草稿库带入的图也走这条路径**，
   * 以前它不裁，只有「手动上传」才裁，于是从草稿带入的图在服饰类目下
   * （要 3:4）和普通类目下（要 1:1）都可能比例不对而被拒。
   *
   * 缓存 key 必须带上比例：同一张原图先按 1:1 传过、改类目后又按 3:4 传，
   * 只按 URL 缓存会直接把 1:1 的图床地址还回来。
   */
  const resolveImage = async (
    url: string,
    cateId?: number,
    shape?: { width: number; height: number },
  ): Promise<string> => {
    const text = url.trim();
    if (!text) throw new Error("存在空图片地址");
    if (/img\.kwcdn\.com/i.test(text)) return text;
    const cacheKey = shape ? `${text}|${shape.width}x${shape.height}` : text;
    const cached = imageCache.current.get(cacheKey);
    if (cached) return cached;
    const shapeIt = (dataUrl: string) =>
      shape ? toRatioDataUrl(dataUrl, shape.width, shape.height) : Promise.resolve(dataUrl);
    let uploaded: TemuImageUploadResult;
    if (text.startsWith("data:image/")) {
      // 变种行「本地图片」是 FileReader 转的 dataURL，直接交给图床上传
      uploaded = await temuPublishApi.uploadImage(await shapeIt(text), cateId);
    } else if (isPublicHttpUrl(text)) {
      // 公网外链（如 COS）交给后端代下载，避免浏览器跨域 —— 像素拿不到，裁不了
      uploaded = await temuPublishApi.uploadImageUrl(text, cateId);
    } else {
      // 本地草稿图 / 素材图
      const blob = await readLocalImage(text, "图片");
      uploaded = await temuPublishApi.uploadImage(await shapeIt(await blobToDataUrl(blob)), cateId);
    }
    const resolved = pickUploadedUrl(uploaded);
    if (!resolved) throw new Error(`图片上传成功但没有返回地址：${text}`);
    imageCache.current.set(cacheKey, resolved);
    return resolved;
  };

  /**
   * 详情图：先量出所有图片的宽度，统一到一个宽度再上传。
   *
   * Temu 商详要求所有楼层等宽，1688 抓的详情图经常 1392 和 800 混排，直接提交会报
   * 「Detail graphic article N floor image width is incorrect」（错误码 2000306）。
   * 缩放放在服务端做（alicdn 图片浏览器取不到 canvas 像素，会污染）。
   */
  const resolveDetailImages = async (
    urls: string[],
    cateId?: number,
  ): Promise<Array<{ url: string; width: number; height: number }>> => {
    const sources = urls.map((item) => item.trim()).filter(Boolean);
    if (!sources.length) return [];

    const widths: number[] = [];
    for (const text of sources) {
      let objectUrl = "";
      try {
        if (isPublicHttpUrl(text)) {
          widths.push((await measureImage(text)).width);
        } else {
          const blob = await readLocalImage(text, "详情图");
          objectUrl = URL.createObjectURL(blob);
          widths.push((await measureImage(objectUrl)).width);
        }
      } finally {
        if (objectUrl) URL.revokeObjectURL(objectUrl);
      }
    }
    const target = mostCommonWidth(widths);

    const out: Array<{ url: string; width: number; height: number }> = [];
    for (const text of sources) {
      const cacheKey = `detail:${target}:${text}`;
      const cached = imageCache.current.get(cacheKey);
      if (cached) {
        const size = detailSizeCache.current.get(cacheKey) ?? { width: target, height: 0 };
        out.push({ url: cached, ...size });
        continue;
      }
      const image = isPublicHttpUrl(text)
        ? text
        : await blobToDataUrl(await readLocalImage(text, "详情图"));
      const uploaded = await temuPublishApi.uploadDetailImage(image, target, cateId);
      const resolved = pickUploadedUrl(uploaded);
      if (!resolved) throw new Error(`详情图上传成功但没有返回地址：${text}`);
      const size = {
        width: Number(uploaded.width) || target,
        height: Number(uploaded.height) || 0,
      };
      imageCache.current.set(cacheKey, resolved);
      detailSizeCache.current.set(cacheKey, size);
      out.push({ url: resolved, ...size });
    }
    return out;
  };

  const resolveImages = async (
    urls: string[],
    cateId?: number,
    shape?: { width: number; height: number },
  ): Promise<string[]> => {
    const resolved: string[] = [];
    for (const url of urls) resolved.push(await resolveImage(url, cateId, shape));
    return resolved;
  };

  /** 组装发布草稿（校验 + 图片上传 + 创建规格）。build / 校验 / 发布共用同一份。 */
  const composeDraft = async (): Promise<TemuGoodsDraft> => {
    const warnings: string[] = [];
    composeWarnings.current = warnings;
    if (!status?.configured.ready) {
      throw new Error("请先在「店铺凭证」里配置 client、token 与店铺 access_token");
    }
    const leaf = categoryPath[categoryPath.length - 1];
    if (!leaf?.isLeaf) throw new Error("请选择到叶子类目");
    if (!form.title.trim()) throw new Error("请填写产品标题");
    const carouselSource = splitLines(form.carousel);
    // Temu 硬性要求至少 3 张（网关实测回「轮播图最少 3 张」），界面上也是这么写的。
    // 以前只拦空，1~2 张能一路传到网关才被拒 —— 白等一轮图片上传。
    if (carouselSource.length < 3) {
      throw new Error(`轮播图至少 3 张（当前 ${carouselSource.length} 张）：Temu 硬性要求`);
    }
    if (carouselSource.length > 10) throw new Error("轮播图最多 10 张");
    // 草稿带入 / 手填 URL 也要守上限（上传路径已经挡过，这里兜底）
    const outerSource = splitLines(form.outerImages);
    if (outerSource.length > 6) throw new Error("外包装图片最多 6 张");
    const detailSource = splitLines(form.detailImages);
    if (detailSource.length > IMAGE_LIMITS.detail) {
      throw new Error(`详情图最多 ${IMAGE_LIMITS.detail} 张`);
    }
    if (!form.freightId) throw new Error("请选择运费模板");
    if (!form.warehouseId) throw new Error("请选择默认发货仓库");
    // 发货时效是四档单选 + 自定义：自定义档点了但没填时值是空串，
    // 直接甩一句「发货时效必须大于 0」用户看不出是哪里没填
    const shipmentDays = form.shipmentDays.trim();
    if (!/^\d+$/.test(shipmentDays) || Number(shipmentDays) <= 0) {
      throw new Error("请选择「承诺发货时效」；选了自定义就填一个大于 0 的天数（如 12）");
    }
    // 产品货号会拼进 SKC / SKU 货号，含中文或符号 Temu 直接拒
    const productCode = cleanExtCode(form.productCode);
    if (form.productCode.trim() && !productCode) {
      throw new Error("产品货号只能包含字母、数字、- 和 _（不能含中文或其它符号）");
    }
    if (form.productCode.trim() && productCode !== form.productCode.trim()) {
      warnings.push(`产品货号里的中文/符号已自动去掉：${form.productCode.trim()} → ${productCode}`);
    }
    // 站外链接直接进 productWhExtAttrReq.outerGoodsUrl，http 会被 Temu 判不合法
    if (form.externalUrl.trim() && !/^https:\/\//i.test(form.externalUrl.trim())) {
      throw new Error("站外产品链接必须是 https:// 开头的地址");
    }

    // 必填属性先校验，再传图 —— 图片上传要打 Temu 图床（慢且占配额），
    // 属性没填完就传完图再报错，等于白等一遍。
    if (attributes) {
      const missing = missingRequiredNames(visibleProperties, attrPicks);
      if (missing.length) throw new Error(`缺少必填属性：${missing.join("、")}`);
    }

    // 本地草稿图/外链图在生成前统一上传到 Temu 图床；已上传过的复用缓存。
    //
    // ⚠️ 主图比例按类目定（服饰 3:4、其它 1:1）：草稿库带入的图也走这条路径，
    // 不在这里裁的话就只有「手动上传」的图是对的，草稿带入的全靠原图碰运气。
    const apparel = sizeChart.info?.required === true;
    const carouselShape = apparel ? APPAREL_CAROUSEL_SHAPE : SQUARE_SHAPE;
    const carousel = await resolveImages(carouselSource, leaf.catId, carouselShape);
    const outerImages = await resolveImages(splitLines(form.outerImages), leaf.catId, SQUARE_SHAPE);
    const detailImages = await resolveDetailImages(splitLines(form.detailImages), leaf.catId);
    // 服饰类目的素材图单独校验「1:1、宽>=800」，而轮播图是 3:4 竖图 ——
    // 留空回退到轮播首图会被 Temu 拒（实测报「素材图[...]校验失败，应符合 1:1，宽>=800」）。
    // 上传轮播时已经自动另裁一张方图填进 material，走到这里还空着说明是草稿带入的竖图，
    // 只能让用户补一张，别默默回退去撞必然失败的校验。
    const materialSource = form.material.trim();
    if (apparel && !materialSource) {
      throw new Error(
        "服饰类目的产品素材图要单独填一张 1:1（宽 ≥800px）的方图：" +
          "轮播图是 3:4 竖图，直接当素材图会被 Temu 拒。请上传一张或在「产品素材图」里填地址。",
      );
    }
    const material = materialSource
      ? await resolveImage(materialSource, leaf.catId, SQUARE_SHAPE)
      : carousel[0];

    const attrByPid = new Map((attributes?.properties ?? []).map((item) => [item.pid, item]));
    // 联动隐藏的属性不能带进 payload（留着上次填的值会被判「属性不合法」）
    const visiblePids = new Set(visibleProperties.map((item) => item.pid));
    const properties = Object.entries(attrPicks)
      .filter(([pid]) => visiblePids.has(Number(pid)))
      .filter(([, pick]) => pick && (pick.vid > 0 || pick.text.trim()))
      .map(([pid, pick]) => {
        const attr = attrByPid.get(Number(pid));
        const text = pick.text.trim();
        const unit = String(attr?.valueUnit?.[0] ?? "").trim();
        // 「下拉 + 数值」属性（成分 60%）要额外给数值；纯数值属性（面料克重 200）
        // 只能走 propValue，带上 numberInputValue 会被判「数值录入信息必须为空」。
        // 两者的区别是有没有预设可选值 —— 实测服装类目：成分有 values，
        // 面料克重 / 里料克重 values 是空数组。
        const hasOptions = (attr?.values?.length ?? 0) > 0;
        return {
          // 同一个 pid 挂多条属性时会话里用的是 refPid 当键，网关只认原始 pid
          pid: attr?.realPid ?? Number(pid),
          vid: pick.vid,
          // 类目属性表里的名称/模板 id 必须带上，否则 Temu 判「属性不合法」
          prop_name: attr?.name || attr?.propertyName || "",
          ref_pid: attr?.refPid ?? undefined,
          template_pid: attr?.templatePid ?? undefined,
          prop_value: text,
          number_input_value: hasOptions ? text : "",
          value_unit: unit,
        };
      });

    if (!rows.length) throw new Error("至少需要一个变种");
    if (rows.length > 20) throw new Error("Temu 多变种上限 20 个");
    const colorParent = parentSpecs.find((item) => item.parentSpecId === colorParentId) ?? null;
    const sizeParent = parentSpecs.find((item) => item.parentSpecId === sizeParentId) ?? null;

    /**
     * SKU 货号去重表。
     * `cleanExtCode` 只保留 A-Za-z0-9-_，所以自动生成的「SKU-黑」「SKU-浅绿」
     * 清洗后**全都是「SKU-」**，四个颜色撞成同一个货号 → 提交被判「ext_code 重复」。
     * 用户看到表格里明明各不相同，报错却说重复，就是这么来的。
     */
    const usedSkuCodes = new Set<string>();

    /** 一行变种 → 一个 SKU 草稿。带不带规格由调用方给，字段映射只写这一份。 */
    const buildSku = (
      row: VariantRow,
      at: string,
      specs: TemuSkuDraft["specs"],
      skcImage: string,
    ): TemuSkuDraft => {
      const price = Number(row.supplierPrice || 0);
      const suggested = Number(row.suggestedPrice || 0);
      if (suggested > 0 && price > 0 && suggested * 20 < price) {
        warnings.push(
          `${at}建议售价 ${row.suggestedPrice} 元远小于申报价 ${row.supplierPrice} 元：` +
            "确认一下是不是少填了一位（两个都是元，不用换算）。",
        );
      }
      const rawSkuCode = row.extCode.trim();
      if (!rawSkuCode) throw new Error(`${at}缺少 SKU 货号（只能用字母/数字/-/_）`);
      const cleaned = cleanExtCode(rawSkuCode);
      let skuCode = cleaned;
      if (!cleaned || /[-_]$/.test(cleaned)) {
        // 整条货号都是中文，或者中文在末尾（「SKU-黑」→ 洗完只剩「SKU-」）：
        // 这等同于没写，用序号兜底，别让用户卡在「我没乱填但发不出去」
        skuCode = `SKU-${String(usedSkuCodes.size + 1).padStart(3, "0")}`;
        warnings.push(`${at}SKU 货号「${rawSkuCode}」去掉中文后只剩空壳，自动改用 ${skuCode}`);
      } else if (cleaned !== rawSkuCode) {
        warnings.push(`${at}SKU 货号里的中文/符号已自动去掉：${rawSkuCode} → ${cleaned}`);
      }
      // 清洗后撞号（「SKU-黑」「SKU-浅绿」→ 都是 SKU-）→ 追加序号，仍然唯一
      if (usedSkuCodes.has(skuCode)) {
        const base = skuCode;
        let n = 2;
        while (usedSkuCodes.has(`${base}-${n}`)) n += 1;
        skuCode = `${base}-${n}`;
        warnings.push(`${at}SKU 货号去掉中文后与前面的重复，已改成 ${skuCode}`);
      }
      usedSkuCodes.add(skuCode);
      if (!row.warehouseId) throw new Error(`${at}缺少发货仓库`);

      // Temu 要求「最长边 ≥ 次长边 ≥ 最短边」，顺序不对直接拒：
      // 「体积内容必须遵循最长边 ≥ 次长边 ≥ 最短边原则」
      const lengthCm = parsePositive(row.lengthCm, `${at}长`);
      const widthCm = parsePositive(row.widthCm, `${at}宽`);
      const heightCm = parsePositive(row.heightCm, `${at}高`);
      const [longest, middle, shortest] = sortDimensionsDesc(lengthCm, widthCm, heightCm);
      if (longest !== lengthCm || middle !== widthCm) {
        warnings.push(
          `${at}长宽高 ${lengthCm}×${widthCm}×${heightCm} 不是「最长 ≥ 次长 ≥ 最短」，` +
            `已按 ${longest}×${middle}×${shortest} 提交（Temu 的体积校验要求降序，体积不变）`,
        );
      }

      return {
        ext_code: skuCode,
        // 半托管供货价按人民币结算：传其它币种会被拒「请选择正确的币种：CNY」
        currency_type: "CNY",
        site_id: SITE_ID,
        // 界面填元，接口收分（见 parseYuanToCents）
        supplier_price: yuanToCents(row.supplierPrice, `${at}申报价格`),
        suggested_price: row.suggestedPrice.trim()
          ? yuanToCents(row.suggestedPrice, `${at}建议售价`)
          : null,
        // 库存 0 是合法的（Temu 会建品但不可售），别拿价格的规则卡它
        stock: parseStock(row.stock, `${at}库存`),
        warehouse_id: row.warehouseId,
        // 页面填 g，接口要毫克；尺寸填 cm，接口要毫米。三维已按降序排好（见上面）
        weight_mg: Math.round(parsePositive(row.weightG, `${at}重量`) * 1000),
        length_mm: Math.round(longest * 10),
        width_mm: Math.round(middle * 10),
        height_mm: Math.round(shortest * 10),
        thumb_url: rowThumbs.get(row.key) || skcImage,
        // 多件装（4 Pack / 12Pcs）：1 就是单件装，接口里不发这个字段
        pieces_per_pack: Math.max(1, Math.round(Number(form.piecesPerPack || "1") || 1)),
        is_sensitive: form.isSensitive,
        specs,
      };
    };

    const skcs: TemuGoodsDraft["skcs"] = [];
    /**
     * 变种行单独设的预览图也要过一遍图床。
     *
     * 它们可能是本地文件（FileReader 转的 dataURL）或本机媒体路径：
     * dataURL 直接塞进 payload 会超长且 Temu 读不到，本地路径更是内网地址。
     * 以前这里是 `row.thumbUrl.trim() || skcImage` 直接发，等于「设了变种图就发不出去」。
     * 没单独设图的行留空，调用方回退到 SKC 图 / 轮播首图。
     */
    const rowThumbs = new Map<string, string>();
    for (const row of rows) {
      const raw = row.thumbUrl.trim();
      if (!raw) continue;
      rowThumbs.set(row.key, await resolveImage(raw, leaf.catId));
    }
    /**
     * 单 SKU 分支：规格声明与 SKU 规格都发空数组。
     *
     * 触发条件见上面的 singleSku：类目没有预设销售属性、且用户没开「多规格」。
     * 注意**不代表类目不支持变种** —— 化妆包/桌布这类没有 isSale，但
     * inputMaxSpecNum=2，用户打开开关后照样能按颜色发（已有成功发布的实例）。
     */
    const singleSku = singleSkuMode;
    if (singleSku) {
      if (rows.length > 1) {
        warnings.push(
          `该类目没有销售属性（不能建颜色/尺码变种）：只提交第 1 行，后面 ${rows.length - 1} 行已忽略。`
            + " 规格会由后端自动补「款式：默认」占位。",
        );
      }
      const row = rows[0];
      const skcImage = rowThumbs.get(row.key) || carousel[0];
      skcs.push({
        ext_code: (productCode || "SKC").slice(0, 120),
        preview_image_urls: [skcImage],
        color_image_url: skcImage,
        main_specs: [],
        skus: [buildSku(row, "变种第 1 行", [], skcImage)],
      });
    } else {
      // 按颜色分组：每个颜色一个 SKC（Temu 的 SKC = 颜色/款式，SKU = 颜色×尺码）
      const grouped = new Map<string, VariantRow[]>();
      rows.forEach((row, index) => {
        const color = row.colorName.trim();
        if (!color) throw new Error(`第 ${index + 1} 行缺少${colorSpecName}`);
        const list = grouped.get(color) ?? [];
        list.push(row);
        grouped.set(color, list);
      });

      let colorIndex = 0;
      for (const [color, colorRows] of grouped) {
        colorIndex += 1;
        const colorSpec = await ensureSpec(colorParent, color);
        // SKC 图：优先用该色第一行单独设的变种图，否则取轮播首图
        const skcImage = colorRows.map((row) => rowThumbs.get(row.key)).find(Boolean) || carousel[0];
        const seenSizes = new Set<string>();
        const skus: TemuSkuDraft[] = [];
        for (const [index, row] of colorRows.entries()) {
          const at = `${colorSpecName}「${color}」第 ${index + 1} 行`;
          const specs: TemuSkuDraft["specs"] = [colorSpec];
          // 只有一列销售属性的类目（手机壳只有「型号」、毛巾只有「颜色」）：
          // 第二列压根不存在，不能硬要求填尺码 —— 以前这里会抛「父规格列表为空」。
          if (sizeParent) {
            if (!row.sizeName.trim()) throw new Error(`${at}缺少${sizeSpecName}`);
            if (seenSizes.has(row.sizeName.trim())) {
              throw new Error(`${at}${sizeSpecName}「${row.sizeName}」重复`);
            }
            seenSizes.add(row.sizeName.trim());
            specs.push(await ensureSpec(sizeParent, row.sizeName));
          }
          skus.push(buildSku(row, at, specs, skcImage));
        }
        // 颜色名全是中文时（黑/浅绿/象牙）cleanExtCode 会把它清成空字符串，
        // 四个 SKC 就都变成同一个「SKC」→ 提交被判「SKC ext_code 重复」。
        // 用颜色序号兜底，并保证后缀不被 120 字截断（截断了一样会撞号）。
        const colorCode = cleanExtCode(color) || `C${colorIndex}`;
        const skcHead = (productCode || "SKC").slice(0, Math.max(1, 120 - colorCode.length - 1));
        skcs.push({
          ext_code: `${skcHead}-${colorCode}`,
          preview_image_urls: [skcImage],
          color_image_url: skcImage,
          main_specs: [colorSpec],
          skus,
        });
        if (colorIndex > 20) throw new Error("Temu 一次最多 20 个颜色（SKC）");
      }
    }

    const sizeTemplates = form.sizeTemplates
      .split(/[\s,，]+/)
      .map((item) => Number(item))
      .filter((item) => Number.isFinite(item) && item > 0);

    return {
      category_ids: categoryPath.map((item) => item.catId),
      // 类目路径名跟着一起发：发布历史里能直接显示「美妆个护 > 化妆包」，
      // 而不是一串 cat id（纯展示字段，服务端不参与任何校验）
      category_names: categoryPath.map((item) => item.catName).filter(Boolean),
      title: form.title.trim(),
      title_en: form.titleEn.trim(),
      carousel_image_urls: carousel,
      outer_package_image_urls: outerImages,
      detail_images: detailImages,
      detail_image_urls: detailImages.map((item) => item.url),
      material_image_url: material,
      freight_template_id: form.freightId,
      shipment_limit_seconds: Number(shipmentDays) * 86400,
      outer_goods_url: form.externalUrl.trim(),
      properties,
      skcs,
      // 带上来源草稿 id：发布成功后这条草稿会从草稿库移除（避免重复上架），
      // 同时发布历史里能看出这条是从哪条草稿来的
      draft_id: prefillDraftId.current || "",
      site_ids: [SITE_ID],
      // 类目明确不需要尺码表（sizeChart.has === false）时不要带旧的模板 id：
      // 字段被藏起来了，用户看不到也改不掉，带上去只会让 Temu 回一句莫名其妙的话
      // 类目不需要尺码表 → 清空（避免带旧 id 换来莫名报错）；
      // 需要时按用户填的走 —— 哪怕店里暂时还没有现成的表。
      size_template_ids: sizeChart.info?.required === false ? [] : sizeTemplates,
      // 定制/POD 商品要显式打开，否则前台看不到定制入口
      personalization: form.isCustom,
      validate_catalog: true,
    };
  };

  const buildPayload = () =>
    run("build", async () => {
      const draft = await composeDraft();
      const built = await temuPublishApi.buildPayload(draft);
      const extra = composeWarnings.current;
      setResult({ ...built, warnings: [...built.warnings, ...extra] });
      setCopied(false);
    });

  /** 发布前校验：问网关这个叶子类目还缺哪些必填属性，不会提交商品。 */
  const checkMandatory = () =>
    run("mandatory", async () => {
      const draft = await composeDraft().catch((cause: unknown) => {
        noteValidationBlocked(cause);
        throw cause;
      });
      setMandatory(null);
      const checked = await temuPublishApi.checkMandatory(draft);
      const missing = Array.isArray(checked.result?.requiredMissing)
        ? (checked.result.requiredMissing as Array<{ pid?: number; name?: string }>)
        : [];
      setMandatory({ source: checked.source, result: checked.result, warnings: checked.warnings, missing });
      // 结果面板在页面最下面：点完直接带过去，别让侧栏那个按钮看着像没反应
      jumpTo("sec-publish");
    });

  /**
   * 发布确认弹窗（替代 window.confirm）。
   *
   * WebView 宿主可能禁用原生 confirm（直接返回 false）—— 点了提交看着像没反应，
   * 原生框样式也跟工作台不搭。这里换成应用内弹窗，顺便把「要提交什么」列清楚。
   * kind=duplicate 是后端说「24 小时内提交过同一条」时的二次确认。
   */
  const [publishConfirm, setPublishConfirm] = useState<
    null | { kind: "submit" | "duplicate"; draft: TemuGoodsDraft; skuCount: number; note?: string }
  >(null);

  /** 点「提交发布」：先过校验，再弹应用内确认框（真正提交在 confirmPublish 里）。 */
  const publishGoods = () => {
    // 双击 / 发布期间又点了一次：直接忽略（接口非幂等，重复提交就重复上架）
    if (publishingRef.current || publishConfirm) return;
    publishingRef.current = true;
    setPublishing(true);
    return run("publish", async () => {
      // 校验没过：把必填项标红，并滚到第一个还没填完的区块 ——
      // 否则长表单里只看到顶部一句「缺少必填属性」，还得自己往下找在哪
      const draft = await composeDraft().catch((cause: unknown) => {
        noteValidationBlocked(cause);
        throw cause;
      });
      const skuCount = draft.skcs.reduce((total, skc) => total + skc.skus.length, 0);
      setPublishConfirm({ kind: "submit", draft, skuCount });
    }).finally(() => {
      publishingRef.current = false;
      setPublishing(false);
    });
  };

  /** 确认框里点「确认提交」：真正打 Temu 接口。 */
  const confirmPublish = async () => {
    const job = publishConfirm;
    if (!job || publishingRef.current) return;
    setPublishConfirm(null);
    publishingRef.current = true;
    setPublishing(true);
    try {
      await run("publish", async () => {
        const draft = job.draft;
        setPublishResult(null);
        try {
          const published = await temuPublishApi.publish(draft, job.kind === "duplicate");
          setPublishResult(published);
        } catch (cause) {
        const text = errorMessage(cause);
        // 后端台账发现 24 小时内提交过同一条：不是失败，是「可能已经在店铺里了」
        if (/重复提交/.test(text)) {
          // 换成应用内「仍然提交」确认框（原生 confirm 在 WebView 里可能被禁）
          setPublishConfirm({ kind: "duplicate", draft, skuCount: job.skuCount, note: text });
          return;
        }
        // 6000002 = Temu 说「货品类目属性更新」。后端已经把属性缓存清掉了，
        // 这里把前端这份也重新拉一遍，并把「到底变了什么」点名出来 ——
        // 只说「重新选一遍」等于让用户盲选，他看完属性区和原来一样，只能再被拒一次。
        if (/6000002|类目属性更新/.test(text)) {
          const diff = await reloadCategoryAttributes(attributes).catch(() => null);
          const detail = diff ? attributeDiffText(diff) : "";
          const todo = diff ? diff.staleChoices.length + diff.addedRequired.length : 0;
          if (detail && todo > 0) {
            throw new Error(
              `Temu 后台改过这个类目的属性定义：${detail}。`
                + "请在下方「类目属性」里把这几项重新选好再提交。",
            );
          }
          if (detail) {
            // 只有 vid 重建、选值文字没变 → 已经自动沿用，不用用户动手
            throw new Error(
              `Temu 后台改过这个类目的属性定义：${detail}。原来的选择已经自动沿用，直接再点一次提交即可。`,
            );
          }
          // 重拉后没差异：说明属性表本身没变，问题在别处。
          // 2026-11-03 实测到的真凶是「规格」而不是属性：没有销售属性的类目
          // （桌布/桌旗/餐垫）以前补的是 id 全 0 的「款式/默认」占位规格，
          // Temu 的规格校验能过，但 goods.add 永远回 6000002 —— 把 12 个必填属性
          // 逐个单发全都能复现，所以清缓存、重选属性怎么弄都没用。
          // 后端现在补的是真实规格（颜色 + 该类目合法的值），正常不会再撞这个错。
          throw new Error(
            "已清掉本地的类目/属性缓存，但重新加载后属性定义没有变化 —— 跟属性表新旧无关。"
              + "这个类目如果本来就没有销售属性（桌布、桌旗、餐垫、化妆包这类），"
              + "问题多半在自动补的那条规格上：把类目 id 发给开发排查，"
              + "或者先在变种区手动选一个规格值再提交。",
          );
        }
        // 失败也要留痕：草稿库/发布页的「发布失败」列表靠这条重试
        const parsed = parsePublishError(cause);
        localStore.addFailure({
          id: `${Date.now()}`,
          draftTitle: draft.title,
          image: draft.carousel_image_urls[0] || "",
          siteLabel: "美国站",
          // 记类目名路径（之前记的是 catId 数字，列表里只看到一串数字，不知道是哪个类目）
          categoryLabel:
            categoryPath.map((step) => step.catName).filter(Boolean).join(" > ") ||
            String(draft.category_ids[draft.category_ids.length - 1] ?? ""),
          skuLabel: draft.skcs[0]?.skus[0]?.ext_code || "",
          priceLabel: `CNY ${draft.skcs[0]?.skus[0]?.supplier_price ?? ""}`,
          at: new Date().toLocaleString("zh-CN"),
          reason: parsed.reason,
          traceId: parsed.traceId,
          // 带上重试所需的字段：草稿库「发布失败」里点重试可以直接回填
          prefill: {
            title: draft.title,
            images: draft.carousel_image_urls,
            detailImages: draft.detail_image_urls ?? [],
            sku: rows[0]?.extCode ?? "",
            supplierPrice: rows[0]?.supplierPrice ?? "",
            stock: rows[0]?.stock ?? "",
            weightKg: rows[0] ? String(Number(rows[0].weightG) / 1000) : "",
            lengthCm: rows[0]?.lengthCm ?? "",
            widthCm: rows[0]?.widthCm ?? "",
            heightCm: rows[0]?.heightCm ?? "",
            specValue1: rows[0]?.colorName ?? "",
            specValue2: rows[0]?.sizeName ?? "",
          },
        });
        throw cause;
      }
      });
    } finally {
      publishingRef.current = false;
      setPublishing(false);
    }
  };

  const copyPayload = async () => {
    if (!result) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(result.payload, null, 2));
      setCopied(true);
    } catch {
      setError("复制失败，请手动选中 JSON 复制");
    }
  };

  /** 发布后去店铺里核对：按 SKC 货号查商品列表，确认真的建成了。 */
  const checkShopGoods = async (extCode: string) => {
    setShopCheck({ loading: true, error: "", items: [] });
    try {
      const result = await temuPublishApi.searchGoods({ extCode: extCode || undefined, pageSize: 5 });
      setShopCheck({ loading: false, error: "", items: result.items });
    } catch (cause) {
      setShopCheck({ loading: false, error: errorMessage(cause), items: [] });
    }
  };

  const updateRow = (key: string, rowPatch: Partial<VariantRow>) => {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...rowPatch } : row)));
  };

  /**
   * 删一行变种。
   *
   * 只从表格里删是不够的：上面的「颜色 / 尺码 列表」还留着那个值，
   * 用户再点一次「生成变种」它就长回来了 —— 看起来像「删不掉」。
   * 所以删掉某颜色的最后一行时，顺手把它从颜色列表里去掉（尺码同理）。
   */
  const removeRow = (key: string) => {
    const target = rows.find((row) => row.key === key);
    if (!target || rows.length <= 1) return;
    const next = rows.filter((row) => row.key !== key);
    setRows(next);
    const dropValue = (text: string, value: string) => {
      const name = value.trim();
      if (!name) return null;
      const parts = text
        .split(/[,，]/)
        .map((item) => item.trim())
        .filter(Boolean);
      if (!parts.some((item) => item === name)) return null;
      return parts.filter((item) => item !== name).join(",");
    };
    // 这个颜色 / 尺码在剩下的行里已经没人用了，才从列表里移除
    const color = target.colorName.trim();
    if (color && !next.some((row) => row.colorName.trim() === color)) {
      const nextColors = dropValue(form.colorsText, color);
      if (nextColors !== null) patch({ colorsText: nextColors });
    }
    const size = target.sizeName.trim();
    if (size && !next.some((row) => row.sizeName.trim() === size)) {
      const nextSizes = dropValue(form.sizesText, size);
      if (nextSizes !== null) patch({ sizesText: nextSizes });
    }
  };

  const leaf = categoryPath[categoryPath.length - 1];
  // 产品货号（ParentSKU）：Temu 只认字母/数字/-/_，渲染层也用它拼变种货号前缀
  const productCode = cleanExtCode(form.productCode);
  /**
   * 发下一条：清掉「这件商品特有」的内容，保留一批商品共用的设置。
   *
   * 实际发一批货时常是同一类目、同一运费模板和仓库，只换个款（图/货号/变种不同）。
   * 以前发完一条要回草稿库重进一次，类目和属性都得重选。
   *
   * 保留：类目、属性选择、运费模板、发货仓库、时效、敏感/定制开关、多件装、产地。
   * 清掉：标题、英文标题、货号、轮播/外包装/详情图、描述、站外链接、变种行、提交结果。
   */
  const startNextItem = () => {
    setForm((current) => ({
      ...current,
      title: "",
      titleEn: "",
      productCode: "",
      externalUrl: "",
      carousel: "",
      outerImages: "",
      detailImages: "",
      description: "",
    }));
    setRows([]);
    setError("");
    setErrorSection("");
    setResult(null);
    setPublishResult(null);
    setMandatory(null);
    setShowFieldErrors(false);
    setCopied(false);
    // 上一条草稿的预填数据与来源条必须一起清：
    // 留着的话「rows 为空就消费 prefillRef」的路径会把它重新填回来，
    // 侧栏也还挂着「POD 草稿 · 申报价 ¥xx」。
    prefillRef.current = null;
    prefillDraftId.current = "";
    setPrefillInfo(null);
    // 类目是刻意留着的：标题清空后不该让自动匹配把已选类目冲掉
    categoryTouchedRef.current = true;
    jumpTo("sec-info");
    // 光标直接落到标题：清完这条就能接着打下一件的标题
    window.setTimeout(() => {
      document.getElementById("temu-title-input")?.focus({ preventScroll: true });
    }, 0);
  };

  // 发布成功后要显示的「商品 id」：网络中断反查到的用 matched_goods，正常提交用返回值
  const publishedGoodsId = publishResult
    ? publishResult.confirmed_by_lookup
      ? String(publishResult.matched_goods?.product_id ?? "")
      : goodsIdOf(publishResult.result)
    : "";
  /**
   * 当前选择下「适用」的属性（Temu 属性联动）。
   *
   * 子属性带 templatePropertyValueParentList，parentTemplatePid 指向父属性的 templatePid：
   * 父属性没选、或选的 vid 不在 parentVidList 里 → 子属性压根不该出现
   * （实测「电池属性=不带电池」时，「可充电电池/太阳能电池/不可充电电池」都是 required，
   *  但填了反而被判不合法）。选中的父值时，子属性只保留 vidList 里的值。
   */
  const visibleProperties = useMemo<TemuProperty[]>(
    () => visibleAttributeProperties(attributes, attrPicks),
    [attributes, attrPicks],
  );

  const requiredProps = visibleProperties.filter((item) => item.required);
  const optionalProps = visibleProperties.filter((item) => !item.required);

  const filledRequired = requiredProps.filter((item) => {
    const pick = attrPicks[item.pid];
    return !!pick && (pick.vid > 0 || !!pick.text.trim());
  }).length;
  const carouselCount = splitLines(form.carousel).length;

  // 分区顺序按 Temu 刊登习惯，且只保留「真会提交」的字段：基本信息 → 产品信息 → 变种信息 → 包装信息 → 发布
  /**
   * 一行变种缺哪些必填列。
   *
   * 尺码只在类目有第二销售属性时才算必填 —— 和提交校验（sizeParent 才查尺码）一致。
   * 以前无条件要求尺码：单维类目（只有颜色）的行永远「没填全」，
   * 检查清单一直红着，而提交其实能过。
   */
  const rowMissingLabels = (row: VariantRow): string[] => {
    const missing: string[] = [];
    if (sizeParentId > 0 && !row.sizeName.trim()) missing.push("尺码");
    if (!row.extCode.trim()) missing.push("货号");
    if (!row.warehouseId) missing.push("发货仓库");
    if (!row.supplierPrice.trim()) missing.push("申报价格");
    if (!row.weightG.trim()) missing.push("重量");
    if (!row.lengthCm.trim() || !row.widthCm.trim() || !row.heightCm.trim()) missing.push("长宽高");
    return missing;
  };
  const incompleteRows = rows.filter((row) => rowMissingLabels(row).length > 0).length;
  /** 没填全的行都缺什么（去重）：清单直接说缺哪列，不用用户逐格找 */
  const missingRowLabels = Array.from(new Set(rows.flatMap(rowMissingLabels)));
  const missingAttrs = requiredProps.length - filledRequired;

  /**
   * 检查清单：每一格都算出「还差什么」，而不是只写一句静态说明。
   * 这样填长表单时不用往下翻就知道卡在哪。
   */
  const flowBase: Array<{ id: string; label: string; hint: string; missing: string[] }> = [
    {
      id: "sec-base",
      label: "基本信息",
      hint: "店铺、站点、产品分类、产品属性",
      missing: [
        ...(statusLoaded && !status?.configured.ready ? ["绑定店铺"] : []),
        ...(!leaf?.isLeaf ? ["选到叶子类目"] : []),
        ...(leaf?.isLeaf && !attributes ? ["属性还在加载"] : []),
        ...(missingAttrs > 0 ? [`${missingAttrs} 个必填属性`] : []),
      ],
    },
    {
      id: "sec-info",
      label: "产品信息",
      hint: "标题、货号、轮播图、素材图",
      missing: [
        ...(!form.title.trim() ? ["产品标题"] : []),
        ...(!carouselCount ? ["轮播图"] : []),
      ],
    },
    {
      id: "sec-variant",
      label: "变种信息",
      hint: "颜色尺码、申报价格、尺寸重量",
      missing: [
        ...(!rows.length ? ["至少一个变种"] : []),
        ...(rows.length && incompleteRows ? [`${incompleteRows} 行没填全（缺：${missingRowLabels.join("、")}）`] : []),
      ],
    },
    {
      id: "sec-pack",
      label: "包装信息",
      hint: "外包装图、运费模板、发货仓库",
      missing: [
        ...(!form.freightId ? ["运费模板"] : []),
        ...(!form.warehouseId ? ["发货仓库"] : []),
      ],
    },
    {
      id: "sec-publish",
      label: "校验发布",
      hint: "检查必填 → 提交",
      missing: [],
    },
  ];
  const flowSteps = flowBase.map((step) =>
    step.id === "sec-publish"
      ? { ...step, done: !!publishResult }
      : { ...step, done: step.missing.length === 0 },
  );
  // 「校验发布」是动作不是内容，统计剩余项时不算它
  const remainingContentSteps = flowSteps.filter((step) => step.id !== "sec-publish" && !step.done);
  /** 第一个没填完的区块：给左侧状态条和区块头上色，让人一眼知道该填哪 */
  const activeSectionId = remainingContentSteps[0]?.id ?? "";
  const doneCount = flowSteps.filter((step) => step.done).length;
  const jumpTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  /**
   * 校验没通过时的统一反馈：标红必填项 + 把出错的那块描红并滚过去。
   *
   * 能定位到具体区块就跳它 —— 比「第一个没填完的」更准：轮播图不够时变种块
   * 可能早就填完了，跳第一个缺口只会让人盯着一块全绿的区块发愣。
   */
  const noteValidationBlocked = (cause?: unknown) => {
    setShowFieldErrors(true);
    const provided = (cause as { section?: unknown } | null)?.section;
    const section =
      typeof provided === "string" && provided
        ? provided
        : sectionOfValidation(cause ? errorMessage(cause) : "");
    if (section) setErrorSection(section);
    const target =
      section || flowSteps.find((step) => step.id !== "sec-publish" && !step.done)?.id || "";
    if (target) jumpTo(target);
  };
  const stepOf = (id: string) => flowSteps.find((item) => item.id === id) ?? flowSteps[0];
  const ready = !!status?.configured.ready;
  // 服务商凭据：跟具体店铺无关，解绑店铺也保留
  const gatewayReady = !!(status?.configured.yl_client && status?.configured.yl_token);
  // 发布中一律禁用：接口非幂等，重复点就重复上架
  const submitDisabled = busy !== "" || !ready || publishing;
  const firstPrice = rows[0]?.supplierPrice?.trim() ?? "";
  const effectiveMaterial = form.material.trim() || splitLines(form.carousel)[0] || "";

  // 快捷键拿最新的发布函数：不让每次 render 都重新绑一遍监听
  const publishRef = useRef(publishGoods);
  publishRef.current = publishGoods;

  /**
   * Ctrl / ⌘ + Enter = 点「提交发布」。
   *
   * 填完最后一行变种手还在键盘上，连发时也不想每次都摸鼠标找按钮。
   * 提交本身有 publishing 锁（接口非幂等），连按不会重复上架。
   */
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Enter" || !(event.ctrlKey || event.metaKey)) return;
      if (publishing || busy !== "" || !ready || publishConfirm) return;
      event.preventDefault();
      void publishRef.current();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [publishing, busy, ready, publishConfirm]);

  return (
    <div className="temu-publish-page is-console">
      <header className="temu-console-top">
        <div className="temu-console-title">
          <p className="eyebrow">TEMU · 半托管刊登</p>
          <div className="temu-console-heading">
            <h1>发布商品</h1>
            <span className={`temu-pill ${remainingContentSteps.length ? "warn" : "ok"}`}>
              {remainingContentSteps.length ? `还差 ${remainingContentSteps.length} 项` : "可提交"}
            </span>
          </div>
          {/* 进度改成下面的横向步骤条：可点击跳转、能显示「还差什么」，
              原来这排只读小圆点表达的是同一件事，留着是重复信息 */}
        </div>
        <div className="temu-console-actions">
          <button
            type="button"
            className={`temu-pill temu-store-chip ${ready ? "ok" : "warn"}`}
            onClick={() => {
              const next = !settingsOpen;
              setSettingsOpen(next);
              // 面板可能是在别处改过凭证后打开的：重读一次，避免显示成「未配置」
              if (next) refreshSettingsStatus();
            }}
            title="点击查看/修改凭证"
          >
            {ready ? tokenInfo?.mall_name || "我的店铺已连接" : "绑定我的店铺"}
            {tokenInfo?.remaining_days != null ? ` · 剩 ${tokenInfo.remaining_days} 天` : ""}
          </button>

          {/* 切换店铺：只在已绑定时出现（没绑时左边那个 pill 本身就是「绑定我的店铺」入口，
              再加一个按钮就是两个入口做同一件事）。点开列已存店铺，点一下换店。 */}
          {ready && (
            <div className="temu-shop-switch">
              <button
                type="button"
                className="temu-btn"
                onClick={() => {
                  const next = !shopMenuOpen;
                  setShopMenuOpen(next);
                  if (next) refreshSettingsStatus();
                }}
                title="换一家已绑定的店，或绑定新店铺"
              >
                切换店铺
              </button>
              {shopMenuOpen && (
                <>
                  <button
                    type="button"
                    className="temu-shop-switch-backdrop"
                    aria-label="关闭店铺菜单"
                    onClick={() => setShopMenuOpen(false)}
                  />
                  <div className="temu-shop-switch-panel" role="menu">
                    <p className="temu-shop-switch-title">
                      已保存的店铺（{status?.shops?.length ?? 0}）
                    </p>
                    {(status?.shops ?? []).map((shop) => {
                      const current = shop.id === status?.active_shop_id;
                      return (
                        <button
                          key={shop.id}
                          type="button"
                          role="menuitem"
                          className={`temu-shop-switch-item${current ? " is-current" : ""}`}
                          onClick={() => {
                            setShopMenuOpen(false);
                            if (!current) void switchShop(shop.id);
                          }}
                        >
                          <span>{shop.mall_name || shop.id}</span>
                          <em>{current ? "当前" : "切换"}</em>
                        </button>
                      );
                    })}
                    <button
                      type="button"
                      role="menuitem"
                      className="temu-shop-switch-add"
                      onClick={() => {
                        setShopMenuOpen(false);
                        setSettingsOpen(true);
                        refreshSettingsStatus();
                      }}
                    >
                      ＋ 绑定新店铺（粘贴 access_token）
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
          <button type="button" className="temu-btn" onClick={verify} disabled={busy !== "" || !ready}>
            验证店铺
          </button>
          {/* 「检查必填」不在顶栏放第二份：右侧检查栏已经有一个，
              两个按钮做同一件事只会让人多点一次（提交前的校验也是同一个接口） */}
          <button
            type="button"
            className="temu-btn temu-btn-primary"
            disabled={submitDisabled}
            onClick={() => void publishGoods()}
          >
            {publishing ? "提交中…" : "提交发布"}
          </button>
        </div>
      </header>

      {settingsOpen && (
        <section className="temu-card temu-settings-card">
          <div className="temu-card-head">
            <h2>我的店铺</h2>
            <span className="temu-muted">
              凭证按登录账号各存一份：{status?.owner?.account_id || "当前账号"}
              {status?.owner?.personal ? "（已绑定）" : "（未绑定）"}
            </span>
          </div>
          <div className="temu-shop-status">
            <div>
              <span>当前店铺</span>
              <strong>{ready ? tokenInfo?.mall_name || "已连接" : "未绑定"}</strong>
            </div>
            <div>
              <span>token 有效期</span>
              <strong>
                {tokenInfo
                  ? `${tokenInfo.expired_at?.slice(0, 10) || "未知"}${tokenInfo.remaining_days != null ? `（剩 ${tokenInfo.remaining_days} 天）` : ""}`
                  : "未校验"}
              </strong>
            </div>
            <div>
              <span>服务商凭据</span>
              <strong>{gatewayReady ? "已由服务器下发（不用管）" : "还没拿到"}</strong>
            </div>
            <div>
              <span>
                店铺授权
                {/* 问号放在授权状态旁边：用户是看到「已配置/未绑定」才想问怎么弄，
                    去个人中心翻文档太远。 */}
                <button
                  type="button"
                  className="temu-help-dot"
                  title="怎么拿 access_token？点这里看步骤和图"
                  aria-label="怎么拿 access_token"
                  onClick={() => setTokenHelpOpen(true)}
                >
                  ?
                </button>
              </span>
              <strong>{status?.configured.access_token ? "已配置" : "未绑定"}</strong>
            </div>
          </div>
          {(status?.shops?.length ?? 0) > 0 && (
            <div className="temu-shop-list">
              <div className="temu-shop-list-head">
                <strong>已保存的店铺（{status?.shops?.length ?? 0}）</strong>
                <span className="temu-muted">换店点「切换到这家」，不用重新贴 token</span>
              </div>
              {(status?.shops ?? []).map((shop) => (
                <div className={`temu-shop-row${shop.active ? " is-active" : ""}`} key={shop.id}>
                  <div className="temu-shop-name">
                    <strong>{shop.mall_name || "（未命名店铺）"}</strong>
                    <span className="temu-muted">
                      {shop.masked_token}
                      {shop.added_at ? ` · 添加 ${shop.added_at.slice(0, 10)}` : ""}
                      {shop.last_used_at ? ` · 最近用 ${shop.last_used_at.slice(0, 10)}` : ""}
                    </span>
                  </div>
                  <div className="temu-shop-ops">
                    {shop.active ? (
                      <em className="temu-shop-badge">当前使用</em>
                    ) : (
                      <button
                        type="button"
                        className="temu-btn temu-btn-sm"
                        disabled={busy !== ""}
                        onClick={() => void switchShop(shop.id)}
                      >
                        切换到这家
                      </button>
                    )}
                    <button
                      type="button"
                      className="temu-link"
                      disabled={busy !== ""}
                      onClick={() => void deleteShop(shop)}
                    >
                      删除
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 服务商 client_id / token 由服务器统一下发：安装包里不带，也不让用户填 */}
          <p className="temu-muted">
            服务商凭据（client_id / token）：
            {gatewayReady
              ? "已由服务器下发，不用管；服务商换了凭据会自动更新"
              : "还没拿到 —— 登录后打开本页会自动获取（需要联网）。一直拿不到请联系管理员"}
          </p>
          <label className="temu-field">
            <span>
              新增店铺：粘贴 access_token
              <em className="temu-muted">
                Temu 后台：系统管理 → 授权管理 → 获取授权，复制 access_token 后面的整串。
                保存前会先打一次网关校验，通过才存进列表
              </em>
            </span>
            <textarea
              className="temu-input"
              rows={3}
              value={accessToken}
              onChange={(event) => setAccessToken(event.target.value)}
              placeholder="留空表示不修改"
            />
          </label>
          <div className="temu-inline-actions">
            <button type="button" className="temu-btn temu-btn-primary" onClick={saveCredentials} disabled={busy !== ""}>
              {busy === "settings" ? "校验中…" : "校验并保存为店铺"}
            </button>
            <button type="button" className="temu-btn" onClick={verify} disabled={busy !== "" || !ready}>
              重新校验
            </button>
            <button
              type="button"
              className="temu-link danger"
              onClick={clearShop}
              disabled={busy !== "" || !status?.owner?.personal}
              title="停用后这家店还在列表里，想再用点「切换到这家」即可"
            >
              停用当前店铺
            </button>
          </div>
          <p className="temu-muted">
            每个账号存自己的店铺，互不影响；发布只会进当前选中的那家店。
            可以存多家、随时切换，切回来不用重新贴 token；token 过期后在这里粘贴新的授权即可（同名店铺会被更新）。
          </p>
        </section>
      )}

      {error && (
        <div className="temu-alert" role="alert">
          <span>{error}</span>
          <div className="temu-alert-actions">
            {SECTION_LABELS[errorSection] ? (
              <button type="button" className="is-primary" onClick={() => jumpTo(errorSection)}>
                去改{SECTION_LABELS[errorSection]}
              </button>
            ) : null}
            <button
              type="button"
              onClick={() => {
                setError("");
                setErrorSection("");
              }}
            >
              关闭
            </button>
          </div>
        </div>
      )}

      {/* 横向步骤条：粘在顶部，点一下滚到那一块。
          不做分步向导 —— 发布要在「类目 → 属性 → 变种」之间来回对照，
          一次只显示一步会逼人翻页，反而更慢。
          版式：圆点在上、文字在下，圆点之间用短连接段串起来（像物流轨迹），
          完成的段跟着点亮；右侧给「已完成 n/5」。
          不用「圆点 + 长横线 + 文字」那种排法 —— 连接段会被拉成一米长，
          文字飘在线上，看着散。 */}
      <nav className="temu-stepbar" aria-label="发布步骤">
        <div className="temu-stepbar-flow">
          {flowSteps.map((step, index) => (
            <div className="temu-stepbar-node" key={step.id}>
              <button
                type="button"
                className={`temu-stepbar-item${step.done ? " is-done" : ""}${
                  activeSectionId === step.id ? " is-active" : ""
                }`}
                onClick={() => jumpTo(step.id)}
                aria-current={activeSectionId === step.id ? "step" : undefined}
                title={step.done ? `${step.label}：已完成` : `${step.label}：还差 ${step.missing.join("、") || "未开始"}`}
              >
                <span className="temu-stepbar-num">{step.done ? <CheckGlyph /> : index + 1}</span>
                <span className="temu-stepbar-label">{step.label}</span>
              </button>
              {/* 连接段从本节点圆点中心画到下一个节点圆点中心（等宽单元格 → 宽度 100%） */}
              {index < flowSteps.length - 1 ? (
                <span
                  className={`temu-stepbar-seg${step.done ? " is-done" : ""}`}
                  aria-hidden="true"
                />
              ) : null}
            </div>
          ))}
        </div>
        <span className="temu-stepbar-progress" title="已完成的步骤数">
          <b>{flowSteps.filter((step) => step.done).length}</b>/{flowSteps.length}
        </span>
        {/* 还没带入草稿时，把入口挂在步骤条右侧的空白里：
            原来它单独占一整行（「来源」大条），首屏白白少看两个字段。
            带入之后摘要信息多（标题/图数/申报价/需核对项），才恢复成整行。 */}
        {!prefillInfo && onOpenDraftLibrary && (
          <button
            type="button"
            className="temu-stepbar-source"
            onClick={onOpenDraftLibrary}
            title="草稿库里的 AI 处理 / POD 成品可以一键带入标题、图片和变种"
          >
            <span className="temu-stepbar-source-tag">来源</span>
            从草稿库带入
          </button>
        )}
      </nav>

      <div className="temu-console-body">
        <div className="temu-console-main">
          {/* 状态还没回来：给一张正经的加载卡，别让人以为页面卡住/坏了 */}
          {!statusLoaded && (
            <div className="temu-loading-card" role="status" aria-live="polite">
              <span className="temu-loading-spinner" aria-hidden="true" />
              <span className="temu-loading-text">
                <strong>正在读取店铺配置</strong>
                <em>店铺授权、运费模板与尺码表，通常 1~2 秒</em>
              </span>
            </div>
          )}
          {statusLoaded && !ready && (
            <div className="temu-bind-banner">
              <div>
                <strong>还没绑定你的 Temu 店铺</strong>
                <p>
                  发布用的是当前登录账号名下的 access_token（{status?.owner?.account_id || "当前账号"}）。
                  填一次就行，之后这个账号发商品只会进你自己的店铺，不会用别人的。
                </p>
              </div>
              <button type="button" className="temu-btn temu-btn-primary" onClick={() => setSettingsOpen(true)}>
                去绑定我的店铺
              </button>
            </div>
          )}

          {/* 来源条：有草稿带入就压缩成一行，没有就给一个入口 */}
          {prefillInfo ? (
            <div className={`temu-source-bar${prefillInfo.needsReview?.length ? " is-warn" : ""}`}>
              <span className="temu-prefill-source">
                {prefillInfo.source === "pod" ? "POD 草稿" : prefillInfo.source === "ai" ? "AI 处理草稿" : "草稿库"}
              </span>
              <div className="temu-source-main">
                <strong>{prefillInfo.title || "已带入草稿"}</strong>
                <span className="temu-muted">
                  {[
                    prefillInfo.specLabel ? `变种 ${prefillInfo.specLabel}` : "",
                    prefillInfo.imageCount ? `图 ${prefillInfo.imageCount} 张` : "",
                    prefillInfo.declaredPrice ? `申报价 ${formatYuan(prefillInfo.declaredPrice)}` : "",
                  ]
                    .filter(Boolean)
                    .join(" · ") || "标题/图片已填好，核对后补类目即可"}
                </span>
                {prefillInfo.needsReview?.length ? (
                  <em>需人工核对：{prefillInfo.needsReview.join("、")}（草稿带来的占位值/参考价，提交前请自己确认）</em>
                ) : null}
              </div>
              {onOpenDraftLibrary && (
                <button type="button" className="temu-link" onClick={onOpenDraftLibrary}>
                  换一个草稿
                </button>
              )}
            </div>
          ) : null}

          {mandatory && (
            <div className="temu-mandatory-result" role="status">
              <div>
                <strong>类目必填校验</strong>
                <span>{mandatory.warnings.length ? `另有 ${mandatory.warnings.length} 条提醒` : "无其它提醒"}</span>
              </div>
              {mandatory.missing.length > 0 ? (
                <p className="temu-mandatory-line is-bad">
                  还缺 {mandatory.missing.length} 个必填属性：
                  {mandatory.missing.map((item) => item.name || `pid=${item.pid}`).join("、")}
                </p>
              ) : (
                <p className="temu-mandatory-line is-ok">必填属性都已填写</p>
              )}
              <p className="temu-mandatory-src">
                来源：{mandatory.source}
                {String(mandatory.result?.unsupported || "")
                  ? "（网关暂不支持按类目查必填配置，已改为本地属性校验）"
                  : ""}
              </p>
              <details className="temu-mandatory-raw">
                <summary>查看原始返回</summary>
                <pre className="temu-json">{JSON.stringify(mandatory.result, null, 2)}</pre>
            </details>
            </div>
          )}

          {/* 1 基本信息 */}
          <section
            className={`temu-sec${stepOf("sec-base").done ? " is-done" : ""}${activeSectionId === "sec-base" ? " is-active" : ""}${errorSection === "sec-base" ? " is-error" : ""}`}
            id="sec-base"
          >
            <SectionHead
              step={stepOf("sec-base")}
              active={activeSectionId === "sec-base"}
              index={0}
              extra={
                leaf ? (
                  <span className="temu-pill ok">
                    {categoryName(leaf)} · {leaf.catId}
                  </span>
                ) : (
                  <span className="temu-pill warn">未选分类</span>
                )
              }
            />
            {/* 「店铺账号 / 经营站点」两个只读字段已删：
                店名顶上那颗 pill 就显示着（Little Pony Shop · 剩 N 天）；
                半托管只有美国站一个选项，摆个禁用的下拉纯占地方。 */}

            <div className="temu-field">
              <span>
                产品分类<em className="temu-required">*</em>
                <em className="temu-muted">会记住上次用过的分类</em>
                {/* 「能不能按颜色/尺码发」是选完类目才成立的开关，但它只是一个小决定，
                    原来占掉一整行（一张带说明的大卡），把类目条和属性区隔开。
                    收成一枚小胶囊挂在标题行右侧，说明进 title。 */}
                {leaf?.isLeaf && attributes && !hasPresetSaleProps && canCustomSpecs && (
                  <label
                    className="temu-spec-switch"
                    title={`不勾＝单 SKU（只需价格库存）；勾上＝填颜色/尺码，提交时自动建对应规格值（最多 ${specDimLimit} 个维度）`}
                  >
                    <input
                      type="checkbox"
                      checked={variantsOn}
                      onChange={(event) => setVariantsOn(event.target.checked)}
                    />
                    <b>按规格发（多色 / 多码）</b>
                  </label>
                )}
              </span>

              {/* 类目选择区：以前是「预设 + 标题推荐 + 搜索 + 结果列表 + 面包屑 + 刷新 +
                  浏览全部 + 存预设」八件平铺，扫一眼不知道从哪下手。
                  现在按状态分两副面孔：已选类目 → 一条摘要条；未选 → 只留搜索与推荐。
                  刷新/浏览全部/存预设属于低频操作，收进「高级」折叠里。 */}
              <div className="temu-cat-picker">
                {presets.length > 0 && (
                  <div className="temu-preset-bar">
                    <span className="temu-preset-title">预设</span>
                    {presets.map((preset) => (
                      <span key={preset.name} className="temu-preset-chip">
                        <button
                          type="button"
                          onClick={() => applyPreset(preset)}
                          title={preset.categoryPath.map((item) => item.catName).join(" / ")}
                        >
                          {preset.name}
                        </button>
                        <button
                          type="button"
                          className="temu-preset-del"
                          onClick={() => removePreset(preset.name)}
                          aria-label={`删除预设 ${preset.name}`}
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                )}

                {leaf?.isLeaf ? (
                  <div className="temu-cat-selected">
                    <div className="temu-cat-selected-main">
                      <span className="temu-cat-selected-label">已选类目</span>
                      <strong>{categoryPath.map(categoryName).join(" / ")}</strong>
                      <span className="temu-cat-selected-id">id {leaf.catId}</span>
                    </div>
                    <button
                      type="button"
                      className="temu-btn"
                      onClick={() => resetToLevel(-1)}
                      disabled={busy !== ""}
                    >
                      更换类目
                    </button>
                  </div>
                ) : (
                  <>
                    <div className="temu-search-row">
                      <input
                        className="temu-input"
                        value={categoryQuery}
                        onChange={(event) => setCategoryQuery(event.target.value)}
                        placeholder="搜类目关键词，如 wig / storage basket"
                        onKeyDown={(event) => {
                          if (event.key === "Enter") searchCategory();
                        }}
                      />
                      <button type="button" className="temu-btn" onClick={searchCategory} disabled={busy !== ""}>
                        搜索
                      </button>
                    </div>

                    {(titleMatching || titleMatches.length > 0) && (
                      <div className="temu-title-match">
                        <span>{titleMatching ? "正在按标题匹配类目…" : "按标题推荐（点一下切换）"}</span>
                        {titleMatches.slice(0, 3).map((dto, index) => {
                          const path = categoryPathFromDto(dto);
                          if (!path.length) return null;
                          const isLeaf = path[path.length - 1].isLeaf;
                          return (
                            <button
                              key={`${index}-${path[path.length - 1].catId}`}
                              type="button"
                              onClick={() => void applyCategory(path)}
                              disabled={busy !== ""}
                            >
                              {path.map(categoryName).join(" / ")}
                              {isLeaf ? "" : "（需继续选）"}
                            </button>
                          );
                        })}
                      </div>
                    )}

                    {matchPaths.length > 0 && (
                      <div className="temu-match-list">
                        {matchPaths.slice(0, 8).map((dto, index) => {
                          const path = categoryPathFromDto(dto);
                          return (
                            <button key={index} type="button" onClick={() => chooseMatchedPath(dto)}>
                              {path.map(categoryName).join(" / ")}
                            </button>
                          );
                        })}
                      </div>
                    )}

                    {categoryPath.length > 0 && (
                      <div className="temu-breadcrumb">
                        {categoryPath.map((item, index) => (
                          <button key={`${item.catId}-${index}`} type="button" onClick={() => resetToLevel(index)}>
                            {categoryName(item)}
                          </button>
                        ))}
                        <span>{leaf?.isLeaf ? "叶子类目" : "继续选下一级"}</span>
                      </div>
                    )}
                  </>
                )}

                {leaf?.isLeaf && attributes && !hasPresetSaleProps && !canCustomSpecs && (
                  <p className="temu-cat-hint">
                    该类目在 Temu 没有销售属性，也没有自定义规格入口 —— 只能按单 SKU 发布。
                  </p>
                )}

                <details className="temu-cat-advanced">
                  <summary>高级：刷新类目树 · 浏览全部 · 存为预设</summary>
                  <div className="temu-cat-advanced-body">
                    <div className="temu-cat-refresh">
                      <button
                        type="button"
                        className="temu-link"
                        onClick={() => void refreshCategoryTree()}
                        disabled={busy !== ""}
                        title="Temu 后台改过类目或该类目的必填属性后点这里，不用等缓存过期"
                      >
                        刷新类目树
                      </button>
                      <em className="temu-muted">类目和属性缓存 1 小时；改完看不到新的就点这个</em>
                    </div>

                    {categoryOptions.length > 0 && (
                      <>
                        <button
                          type="button"
                          className="temu-browser-toggle"
                          onClick={() => setShowCategoryBrowser((value) => !value)}
                        >
                          {showCategoryBrowser
                            ? "收起分类浏览"
                            : `浏览全部 ${categoryOptions.length} 个分类（也可以直接在上面搜关键词）`}
                        </button>
                        {showCategoryBrowser && (
                          <div className="temu-category-list">
                            {categoryOptions.map((item) => (
                              <button
                                key={item.catId}
                                type="button"
                                onClick={() => chooseCategory(item)}
                                disabled={busy !== ""}
                              >
                                {categoryName(item)}
                                <span>{item.isLeaf ? "叶子" : "展开"}</span>
                              </button>
                            ))}
                          </div>
                        )}
                      </>
                    )}

                    <div className="temu-preset-save">
                      <input
                        className="temu-input"
                        value={presetName}
                        onChange={(event) => setPresetName(event.target.value)}
                        placeholder="预设名（只记分类+运费+仓库，随手切换用）"
                        title="预设 = 分类 + 运费模板 + 发货仓库，最多存 6 个，用来快速换品"
                        onKeyDown={(event) => {
                          if (event.key === "Enter") savePreset();
                        }}
                      />
                      <button type="button" className="temu-btn" onClick={savePreset} disabled={!categoryPath.length}>
                        存为预设
                      </button>
                    </div>
                  </div>
                </details>
              </div>
            </div>

            {/* 产品模板是「同类商品重复发」才用得上，收到折叠里，首屏不占位 */}
            <details className="temu-more">
              <summary>载入 / 保存产品模板</summary>
              <div className="temu-template-row">
                <input
                  className="temu-input"
                  value={templateName}
                  onChange={(event) => setTemplateName(event.target.value)}
                  placeholder="模板名（记分类+属性+物流+开关，批量发布用）"
                  title="产品模板比预设多记了已填属性、尺码表、定制/敏感开关，草稿库批量发布时要套它"
                />
                <button
                  type="button"
                  className="temu-btn"
                  onClick={saveProductTemplate}
                  disabled={!categoryPath.length}
                >
                  存为产品模板
                </button>
                <select
                  className="temu-input"
                  value=""
                  onChange={(event) => applyProductTemplate(event.target.value)}
                  disabled={!productTemplates.length}
                >
                  <option value="">{productTemplates.length ? "引用产品模板…" : "还没有产品模板"}</option>
                  {productTemplates.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name}（{item.savedAt}）
                    </option>
                  ))}
                </select>
              </div>
            </details>

            {/* 属性是「填完类目之后」的第二步，和上面的类目选择不是同一件事。
                加一条分隔线 + 把说明挪到标题下面单独一行，
                否则标题、说明、9 个下拉挤在一起，看不出这里是新的一段。 */}
            <div className="temu-field temu-field-attrs">
              <span>
                产品属性<em className="temu-required">*</em>
              </span>
              <em className="temu-field-note">
                带 * 必填；不带的收在下面，不填也能发布
                {attributes && attributes.properties.length > visibleProperties.length
                  ? `（已按上一级选择隐藏 ${attributes.properties.length - visibleProperties.length} 项不适用属性）`
                  : ""}
              </em>
              {!attributes ? (
                <p className="temu-empty">选到叶子分类后自动加载属性。</p>
              ) : (
                <>
                  {(autoFill.hits.length > 0 ||
                    autoFill.ambiguous.length > 0 ||
                    carriedAttrCount > 0 ||
                    staleAttrNames.length > 0) && (
                    <div className="temu-autofill-bar">
                      <span>
                        {carriedAttrCount > 0 && (
                          <>
                            草稿库已按类目估了 <strong>{carriedAttrCount}</strong> 项属性，
                            <em className="temu-muted">发布前请核对</em>
                            {autoFill.hits.length > 0 || staleAttrNames.length > 0 ? "；" : ""}
                          </>
                        )}
                        {staleAttrNames.length > 0 && (
                          <>
                            <strong>{staleAttrNames.length}</strong> 项草稿属性
                            在 Temu 那边已经失效（{staleAttrNames.slice(0, 6).join("、")}
                            {staleAttrNames.length > 6 ? " 等" : ""}），已清空 —— 这几项要重新选，
                            否则提交会被拒「请求的内容不存在或已被删除」
                            {autoFill.hits.length > 0 ? "；" : ""}
                          </>
                        )}
                        {autoFill.hits.length > 0 && (
                          <>
                            本次补填 <strong>{autoFill.hits.length}</strong> 项（
                            {describeAutoFill(autoFill.hits)}）
                          </>
                        )}
                        {autoFill.ambiguous.length > 0
                          ? `${autoFill.hits.length > 0 ? "；" : ""}${autoFill.ambiguous.join("、")} 命中多个值，需要你自己选`
                          : ""}
                      </span>
                      <span className="temu-autofill-actions">
                        <button
                          type="button"
                          className="temu-link-button"
                          onClick={reRunAttributeAutofill}
                          title="标题或描述改过之后点这里，按当前文本重新识别"
                        >
                          按标题重新识别
                        </button>
                        {autoFill.hits.length > 0 && (
                          <button
                            type="button"
                            className="temu-link-button"
                            onClick={clearAttributeAutofill}
                          >
                            清空自动填
                          </button>
                        )}
                      </span>
                    </div>
                  )}
                  <TemuPropertyForm
                    attributes={attributes}
                    picks={attrPicks}
                    onChange={handleAttributePicksChange}
                    showErrors={showFieldErrors}
                    optionalHint="不填也能发布，但可能是下级属性的开关"
                    guessedPids={guessedPids}
                  />
                </>
              )}
            </div>
          </section>

          {/* 2 产品信息 */}
          <section
            className={`temu-sec${stepOf("sec-info").done ? " is-done" : ""}${activeSectionId === "sec-info" ? " is-active" : ""}${errorSection === "sec-info" ? " is-error" : ""}`}
            id="sec-info"
          >
            <SectionHead
              step={stepOf("sec-info")}
              active={activeSectionId === "sec-info"}
              index={1}
              extra={
                <span className="temu-muted">
                  {busy === "upload" ? "图片上传中…" : `轮播 ${carouselCount}/10 张`}
                </span>
              }
            />
            <label className="temu-field">
              <span>
                产品标题<em className="temu-required">*</em>
                <em className="temu-muted">{form.title.length}/250，建议英文</em>
                {showFieldErrors && !form.title.trim() ? (
                  <em className="temu-required-empty-hint">必填</em>
                ) : null}
              </span>
              <textarea
                id="temu-title-input"
                className={`temu-input${showFieldErrors && !form.title.trim() ? " is-bad" : ""}`}
                value={form.title}
                onChange={(event) => patch({ title: event.target.value })}
                placeholder="英文标题，突出核心卖点"
                maxLength={250}
                rows={3}
              />
            </label>
            {/* 选填的低频项收进折叠：首屏只留「标题 + 图片 + 两个开关」，
                货号/产地/外链/英文标题大多数品根本不改，摊开只是让人犹豫。 */}
            <details className="temu-more">
              <summary>
                <span>更多选项（英文标题、货号、产地、外链）</span>
                {(form.isSensitive || form.isCustom) && (
                  <em className="temu-muted">
                    {[form.isSensitive ? "敏感货已开" : "", form.isCustom ? "定制已开" : ""]
                      .filter(Boolean)
                      .join("、")}
                  </em>
                )}
              </summary>
            <div className="temu-two-col">
              <label className="temu-field">
                <span>
                  英文标题
                  <em className="temu-muted">
                    {form.titleEn.length}/250，选填；填了会随标题一起提交
                  </em>
                </span>
                <input
                  className="temu-input"
                  value={form.titleEn}
                  onChange={(event) => patch({ titleEn: event.target.value })}
                  placeholder={
                    /[\u4e00-\u9fff]/.test(form.title)
                      ? "英文标题（≤250）"
                      : "标题已是英文，这里可留空"
                  }
                  maxLength={250}
                />
              </label>
              <label className="temu-field">
                <span>
                  产品货号
                  <em className="temu-muted">
                    {form.productCode.trim() && form.productCode.trim() !== productCode
                      ? `含中文/符号，提交时会去掉 → ${productCode || "（空，请重填）"}`
                      : "ParentSKU，变种货号会用它拼前缀"}
                  </em>
                </span>
                <input
                  className={`temu-input${
                    form.productCode.trim() && form.productCode.trim() !== productCode ? " is-bad" : ""
                  }`}
                  value={form.productCode}
                  onChange={(event) => patch({ productCode: event.target.value })}
                  placeholder="如 WIG-BOB，不能含中文"
                />
              </label>
            </div>
            <div className="temu-two-col">
              <label className="temu-field">
                <span>
                  产地
                  <em className="temu-muted">两位国家代码，提交为 productOrigin</em>
                </span>
                <input
                  className="temu-input"
                  value={form.origin}
                  maxLength={2}
                  placeholder="CN"
                  onChange={(event) => patch({ origin: event.target.value.toUpperCase() })}
                />
              </label>
              <label className="temu-field">
                <span>站外产品链接</span>
                <input
                  className="temu-input"
                  value={form.externalUrl}
                  onChange={(event) => patch({ externalUrl: event.target.value })}
                  placeholder="可选，https:// 开头的商品页地址，提交为 outerGoodsUrl"
                />
              </label>
            </div>
            </details>
            {IMAGE_SLOTS.map((slot) => {
              // 服饰类目（必须有尺码表 = 量体类目）主图要 3:4 竖图，文案跟着变，
              // 否则界面写着「1:1」、实际裁成 3:4，用户会以为被裁坏了
              const apparelSlot = slot.key === "carousel" && isApparelCategory;
              const slotHint = apparelSlot
                ? "服饰类目要竖图 3:4（≥1344×1792），上传自动裁切"
                : slot.hint;
              return (
              <div className="temu-img-group" key={slot.key}>
                <div className="temu-img-group-head">
                  <strong>
                    {slot.label}
                    {slot.required && <em className="temu-required">*</em>}
                  </strong>
                  {/* 轮播的说明放进上传位里（那儿位置大），这里再写一遍就是重复 */}
                  {slot.key === "carousel" ? null : <span className="temu-muted">{slotHint}</span>}
                  <button
                    type="button"
                    className="temu-btn is-small"
                    title="从「AI 处理 / POD 定制」的成品里直接挑，不用先存成本地文件"
                    onClick={() => setPickerSlot(slot.key)}
                  >
                    从成品库挑图
                  </button>
                </div>
                {/* 上传位就是空态：虚线落点比「+ 添加图片」小按钮直观，能点、能拖、能粘 */}
                <TemuImageStrip
                  urls={splitLines(slotText(slot.key))}
                  onChange={(next) => setSlotText(slot.key, next.join("\n"))}
                  firstBadge={slot.key === "carousel" ? "主图" : undefined}
                  onAddFiles={(files) => void uploadFiles(slot.key, files)}
                  addHint={`点击上传${slot.label}`}
                  addSubHint={
                    slot.key === "carousel"
                      ? apparelSlot
                        ? "至少 3 张 · 3:4 竖图（≥1344×1792）· 自动裁切压缩"
                        : "至少 3 张 · 1:1 方图（≥800×800）· 自动裁切压缩"
                      : slot.key === "detail"
                        ? "最多 30 张，一张一个详情模块；长图不裁切，提交时统一宽度"
                        : "最多 6 张，建议含正面 / 侧面 / 底部 / 细节特写"
                  }
                  variant={slot.key === "carousel" ? "primary" : "compact"}
                  maxCount={IMAGE_LIMITS[slot.key]}
                  busy={busy !== ""}
                  emptyHint={
                    slot.key === "carousel"
                      ? `草稿带入的图会显示在这里；上传会自动裁成 ${apparelSlot ? "3:4" : "1:1"}`
                      : "可选，从草稿库带入或上传"
                  }
                />
                {slot.key === "carousel" ? (
                  // 规则长句收进 title：正文只留一行关键数字，不再三行灰字压在图下面
                  <p
                    className="temu-image-rules"
                    title={
                      apparelSlot
                        ? "服饰类目轮播图：至少 3 张、最多 10 张；比例 3:4；宽 ≥1340、高 ≥1785；单张 ≤2MB。上传时自动居中裁切并压缩，按顺序提交，第一张是主图。"
                        : "轮播图：至少 3 张、最多 10 张；比例 1:1；宽高 ≥800px；单张 ≤2MB。上传时自动裁切并压缩，按顺序提交，第一张是主图。"
                    }
                  >
                    第一张是主图，可用 ← → 调序；单张 ≤2MB
                    <span className="temu-muted">（悬停看完整规则）</span>
                  </p>
                ) : null}
              </div>
              );
            })}

            <div className="temu-field">
                <span>
                  产品素材图
                  <em className="temu-muted">
                    {isApparelCategory ? "服饰类目必填：1:1 方图，不能沿用 3:4 轮播图" : "留空＝自动取轮播第一张"}
                  </em>
                </span>
                <div className="temu-material-row">
                  <TemuThumb path={effectiveMaterial} onOpen={() => undefined} />
                  <input
                    className="temu-input"
                    value={form.material}
                    onChange={(event) => patch({ material: event.target.value })}
                    // 文案要说清「要不要填」：原来的「覆盖素材图地址（可选）」看着像必填，
                    // 而普通类目其实留空就行（自动用轮播第一张）。
                    placeholder={
                      isApparelCategory
                        ? "必填 · 粘贴 Temu 图片地址，或点右边「上传方图」"
                        : "可选 · 留空自动用轮播第一张"
                    }
                  />
                  {/* 手里只有竖图？这里直接裁成 1:1 再传，省得用户自己找工具转 */}
                  <label className="temu-btn is-small" title="选一张图，自动居中裁成 1:1（≥800×800）后上传">
                    上传方图
                    <input
                      type="file"
                      accept="image/*"
                      hidden
                      disabled={busy !== ""}
                      onChange={(event) => {
                        const file = event.target.files?.[0];
                        event.target.value = "";
                        if (!file) return;
                        void run("material", async () => {
                          const leaf = categoryPath[categoryPath.length - 1];
                          if (!leaf?.catId) {
                            throw new Error("请先选到叶子类目再上传素材图：图片要按类目上传到 Temu 图床。");
                          }
                          const square = await toSquareDataUrl(await blobToDataUrl(file));
                          const uploaded = pickUploadedUrl(
                            await temuPublishApi.uploadImage(square, leaf.catId),
                          );
                          if (!uploaded) throw new Error("素材图上传成功但没有返回图片地址");
                          patch({ material: uploaded });
                        });
                      }}
                    />
                  </label>
                </div>
                {isApparelCategory ? (
                  <em className="temu-field-note">
                    Temu 单独校验素材图「1:1、宽 ≥800」；上传轮播图时会自动另裁一张方图填进来，
                    从草稿库带入的竖图需要在这里换一张
                  </em>
                ) : null}
            </div>
            {/* 合规开关平铺：藏在「校验发布」的原始返回折叠里等于让人漏填，
                敏感货/定制填错的后果是直接被 Temu 拒或被判违规。 */}
            <div className="temu-flags">
              <label className={`temu-flag${form.isSensitive ? " is-on" : ""}`}>
                <input
                  type="checkbox"
                  checked={form.isSensitive}
                  onChange={(event) => patch({ isSensitive: event.target.checked })}
                />
                <span>
                  <strong>敏感货</strong>
                  <em>带电 / 液体 / 粉末等，提交为 isSensitive</em>
                </span>
              </label>
              <label className={`temu-flag${form.isCustom ? " is-on" : ""}`}>
                <input
                  type="checkbox"
                  checked={form.isCustom}
                  onChange={(event) => patch({ isCustom: event.target.checked })}
                />
                <span>
                  <strong>定制产品</strong>
                  <em>需先在卖家中心配好定制模板，否则无法正常上架</em>
                </span>
              </label>
            </div>

            {pickerNotice ? (
              <p className="temu-picker-notice" role="status" aria-live="polite">
                {pickerNotice}
              </p>
            ) : null}

            <details className="temu-advanced">
              <summary>备注（不提交，只留档）</summary>
              <textarea
                className="temu-input"
                rows={3}
                value={form.description}
                onChange={(event) => patch({ description: event.target.value })}
                placeholder="卖点、材质、尺寸说明…Temu 发布接口不接受富文本，这里只作为本款备注保存"
              />
            </details>
          </section>

          {/* 3 变种信息 */}
          <section
            className={`temu-sec${stepOf("sec-variant").done ? " is-done" : ""}${activeSectionId === "sec-variant" ? " is-active" : ""}${errorSection === "sec-variant" ? " is-error" : ""}`}
            id="sec-variant"
          >
            <SectionHead
              step={{
                ...stepOf("sec-variant"),
                // 单 SKU 类目还叫「变种信息」会让人以为漏填了颜色/尺码
                label: singleSkuMode ? "变种信息（单 SKU）" : "变种信息",
                hint: singleSkuMode
                  ? "只有一个 SKU：填价格、库存、尺寸重量即可"
                  : "颜色尺码、申报价格、尺寸重量",
              }}
              active={activeSectionId === "sec-variant"}
              index={2}
              extra={<span className="temu-muted">{rows.length} 个变种</span>}
            />
            {/* 变种模板同样是重复发同款时才用，收起来 */}
            <details className="temu-more">
              <summary>载入 / 保存变种模板</summary>
              <div className="temu-template-row">
                <button type="button" className="temu-btn" onClick={saveVariantTemplate}>
                  存为变种模板
                </button>
                <select
                  className="temu-input"
                  value=""
                  onChange={(event) => applyVariantTemplate(event.target.value)}
                  disabled={!variantTemplates.length}
                >
                  <option value="">{variantTemplates.length ? "引用变种模板…" : "还没有变种模板"}</option>
                  {variantTemplates.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name}
                    </option>
                  ))}
                </select>
              </div>
            </details>
            {activeSpecMismatch.length > 0 && (
              <div className="temu-spec-mismatch" role="status">
                <div className="temu-spec-mismatch-head">
                  <strong>有 {activeSpecMismatch.length} 个规格值不在当前类目的预设里</strong>
                  <div className="temu-spec-mismatch-actions">
                    <button
                      type="button"
                      className="temu-link-button"
                      onClick={fixSpecMismatches}
                    >
                      按最接近的预设值修正
                    </button>
                    <button
                      type="button"
                      className="temu-link-button"
                      onClick={() => setSpecMismatch([])}
                    >
                      知道了
                    </button>
                  </div>
                </div>
                <p className="temu-muted">
                  换类目后规格值必须重选：Temu 只认类目预设值，自己填的提交会被判「不合法的规格属性」。
                  价格、重量这些没动，改完规格就行。
                </p>
                <ul>
                  {/* 第二维被关掉后，之前记下的尺码不匹配就不算数了（提交时根本不带它） */}
                  {activeSpecMismatch.map((item) => {
                    // 能自动对齐的会给「按最接近的预设值修正」；对不上的必须说清楚，
                    // 否则用户点了修正发现这个值还在，会以为功能坏了
                    const options = item.field === "color" ? specOptions(colorParentId) : specOptions(sizeParentId);
                    const fixable = matchPresetValue(item.value, options);
                    return (
                      <li key={`${item.rowKey}-${item.field}`}>
                        第 {item.rowNo} 行{" "}
                        {item.field === "color" ? colorSpecName : sizeSpecName}「{item.value}」
                        <em className="temu-spec-mismatch-manual">
                          {fixable ? `→ 可改成「${fixable}」` : "预设里没有相近值，请用上方「浏览全部预设」手动选"}
                        </em>
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
            <PublishSkuPanel
              rows={rows}
              onRowsChange={setRows}
              onUpdateRow={updateRow}
              onRemoveRow={removeRow}
              parentSpecs={parentSpecs}
              colorParentId={colorParentId}
              colorOptions={specOptions(colorParentId)}
              sizeOptions={specOptions(sizeParentId)}
              colorSpecName={colorSpecName}
              sizeSpecName={sizeSpecName}
              onColorParentChange={setColorParentId}
              sizeParentId={sizeParentId}
              onSizeParentChange={setSizeParentId}
              colorsText={form.colorsText}
              onColorsTextChange={(value) => patch({ colorsText: value })}
              defaultWarehouseId={form.warehouseId}
              sizesText={form.sizesText}
              onSizesTextChange={(value) => patch({ sizesText: value })}
              extCodePrefix={productCode}
              fallbackThumb={effectiveMaterial}
              carouselImages={splitLines(form.carousel)}
              onPreviewImage={(src, alt) => setImagePreview({ src, alt })}
              singleSku={singleSkuMode}
              presetsReady={attributes !== null}
              showErrors={showFieldErrors}
            />
            {/* 显示条件用 required（类目要不要），不是 has（店里有没有）：
                服装类目新店一张表都没有，用 has 判会把整个字段藏掉，
                用户连填 ID 的地方都看不到。 */}
            {(sizeChart.info?.required || sizeChart.has) && (
              <div className="temu-field">
                <span>
                  尺码表模板 ID
                  <em className="temu-muted">
                    {sizeChart.has
                      ? `这个类目需要尺码表（店里已有 ${sizeChart.info?.count ?? 0} 张）：点下面的表直接用，也可以手填 ID`
                      : "这个类目必须绑尺码表：先在 Temu 后台建好，再把模板 ID 填这里"}
                  </em>
                </span>
                {sizeChart.info?.required && !sizeChart.has ? (
                  <p className="temu-sizechart-warn">
                    <strong>店里还没有可用的尺码表。</strong>
                    {sizeChart.info.required_elements?.length
                      ? `Temu 要求必填测量项：${sizeChart.info.required_elements.join("、")}。`
                      : ""}
                    去卖家后台「商品管理 → 尺码表模板」建一张，再把模板 ID 填到这里
                    （接口建表 sizecharts.create 的参数结构还没打通，暂时只能后台建）。
                  </p>
                ) : null}
                {/* 店里的商品既然带尺码表，就该能直接点选 —— 以前只能去后台抄那串 ID。
                    后端会把网关返回的行收敛成 {id, name}，认不出 id 的不会出现在这里。 */}
                {sizeChart.info?.tables?.length ? (
                  <div className="temu-sizechart-picks">
                    <span className="temu-muted">用店里已有的尺码表：</span>
                    {sizeChart.info.tables.map((table) => {
                      const picked = selectedSizeTemplateIds.includes(table.id);
                      return (
                        <button
                          key={table.id}
                          type="button"
                          className={`temu-sizechart-chip${picked ? " is-picked" : ""}`}
                          onClick={() => toggleSizeTemplate(table.id)}
                          title={picked ? "再点一下移除" : `填入模板 ID ${table.id}`}
                        >
                          {table.name || "尺码表"}
                          <em>{table.id}</em>
                          {picked ? <b aria-hidden="true">✓</b> : null}
                        </button>
                      );
                    })}
                  </div>
                ) : null}
                {sizeChart.has && !sizeChart.info?.tables?.length ? (
                  <p className="temu-field-note">
                    店里已有 {sizeChart.info?.count ?? 0} 张尺码表，但网关没返回能用的 ID ——
                    去卖家后台复制模板 ID 填到下面。
                  </p>
                ) : null}
                {sizeChart.info?.sizes?.length ? (
                  <p className="temu-field-note">
                    该尺码组可选尺码：{sizeChart.info.sizes.slice(0, 14).join(" / ")}
                    {sizeChart.info.sizes.length > 14
                      ? ` 等 ${sizeChart.info.sizes.length} 个`
                      : ""}
                  </p>
                ) : null}
                <input
                  className="temu-input"
                  value={form.sizeTemplates}
                  onChange={(event) => patch({ sizeTemplates: event.target.value })}
                  placeholder="可选，如 12345,67890"
                />
              </div>
            )}
          </section>

          {/* 4 包装信息 */}
          <section
            className={`temu-sec${stepOf("sec-pack").done ? " is-done" : ""}${activeSectionId === "sec-pack" ? " is-active" : ""}${errorSection === "sec-pack" ? " is-error" : ""}`}
            id="sec-pack"
          >
            <SectionHead
              step={stepOf("sec-pack")}
              active={activeSectionId === "sec-pack"}
              index={3}
              extra={<span className="temu-muted">{splitLines(form.outerImages).length} 张外包装图</span>}
            />
            <div className="temu-two-col">
              <label className="temu-field">
                <span>
                  运费模板<em className="temu-required">*</em>
                </span>
                <select className="temu-input" value={form.freightId} onChange={(event) => patch({ freightId: event.target.value })}>
                  <option value="">请选择</option>
                  {freightTemplates.map((item) => (
                    <option key={item.freightTemplateId} value={item.freightTemplateId}>
                      {item.templateName} · {item.freightTemplateId}
                    </option>
                  ))}
                </select>
              </label>
              <label className="temu-field">
                <span>
                  默认发货仓库<em className="temu-required">*</em>
                </span>
                <select
                  className="temu-input"
                  value={form.warehouseId}
                  onChange={(event) => {
                    const next = event.target.value;
                    patch({ warehouseId: next });
                    setRows((current) => current.map((row) => ({ ...row, warehouseId: next })));
                  }}
                >
                  <option value="">请选择</option>
                  {warehouses.map((item) => (
                    <option key={item.warehouseId} value={item.warehouseId}>
                      {warehouseLabel(item)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {/* 发货时效原来是 two-col 的左半边，右半边空着、说明又占两行，
                整块看着像「填了一半」。改成占满一行。
                2026-11-05：从数字输入框改成 Temu 后台同款档位单选（1/2/7/9 + 自定义），
                radio 之间互斥要 label 包多个 input —— 外层不能再用 <label>，
                否则点标题会命中第一个 radio（1 个工作日）。 */}
            <div className="temu-field temu-field-shipment">
              <span>
                承诺发货时效<em className="temu-required">*</em>
              </span>
              {/* 这段说明原来塞在 label 里，占了三行小字，把输入框挤到下面去；
                  拆成字段下方的一行注释，label 只留标题。 */}
              <em className="temu-field-note">
                默认 9 个工作日（Temu 半托管预设 777600 秒）；Temu 限选 1/2/7/9 工作日或自定义，
                选太短会被判延迟发货
              </em>
              <ShipmentDaysPicker
                value={form.shipmentDays}
                onChange={(next) => patch({ shipmentDays: next })}
              />
            </div>
            {/* 每件装数量：只有标题写 4 Pack / 12Pcs 这类多件装才需要，默认 1 单件装 */}
            <details className="temu-more">
              <summary>每件装数量（多件装才改，默认 1）</summary>
                <label className="temu-field">
                  <span>
                    每件装数量
                    <em className="temu-muted">
                      标题里的 4 Pack / 12Pcs 就填 4 / 12；填 1 表示单件装（单件装不发这个字段）
                    </em>
                  </span>
                  <input
                    className="temu-input"
                    inputMode="numeric"
                    value={form.piecesPerPack}
                    onChange={(event) => patch({ piecesPerPack: event.target.value })}
                    placeholder="1"
                  />
                </label>
            </details>
          </section>

          {/* 5 校验发布 */}
          <section className="temu-sec temu-sec-last" id="sec-publish">
            <SectionHead
              step={stepOf("sec-publish")}
              index={4}
              extra={
                <span className="temu-muted">
                  {doneCount}/{flowSteps.length} 项就绪
                </span>
              }
            />
            <div className="temu-publish-actions">
              <button type="button" className="temu-btn" onClick={checkMandatory} disabled={busy !== ""}>
                {busy === "mandatory" ? "校验中…" : "检查必填"}
              </button>
              <button type="button" className="temu-btn" onClick={buildPayload} disabled={busy !== ""}>
                {busy === "build" ? "生成中…" : "预览请求体"}
              </button>
              {result && (
                <>
                  <span className="temu-muted">
                    {result.summary.skc_count} 个 SKC / {result.summary.sku_count} 个变种 · 类目{" "}
                    {result.summary.leaf_cat_id}
                  </span>
                  <button type="button" className="temu-btn" onClick={copyPayload}>
                    {copied ? "已复制" : "复制 JSON"}
                  </button>
                </>
              )}
              <button
                type="button"
                className="temu-btn temu-btn-primary"
                disabled={submitDisabled}
                onClick={() => void publishGoods()}
                title="不可撤销；快捷键 Ctrl+Enter"
              >
                {publishing ? "提交中…" : "提交发布"}
              </button>
            </div>

            {publishResult && (
              <div className="temu-publish-done">
                <div>
                  <strong>
                    {publishResult.confirmed_by_lookup
                      ? "已在店铺里查到（提交时网络中断）"
                      : "已提交到 Temu"}
                  </strong>
                  <span>
                    {publishResult.api_type} · {publishResult.summary.skc_count} 个 SKC /{" "}
                    {publishResult.summary.sku_count} 个变种
                    {publishedGoodsId ? ` · 商品 id ${publishedGoodsId}` : ""}
                  </span>
                </div>
                <pre className="temu-json">
                  {JSON.stringify(
                    publishResult.confirmed_by_lookup
                      ? publishResult.matched_goods
                      : publishResult.result,
                    null,
                    2,
                  )}
                </pre>
                <div className="temu-inline-actions">
                  <button
                    type="button"
                    className="temu-btn temu-btn-sm"
                    disabled={shopCheck.loading}
                    onClick={() => {
                      const payload = publishResult.request_payload as {
                        productSkcReqs?: Array<{ extCode?: string }>;
                      };
                      void checkShopGoods(payload?.productSkcReqs?.[0]?.extCode || "");
                    }}
                  >
                    {shopCheck.loading ? "查询中…" : "核对店铺里的商品"}
                  </button>
                  {/* 发完一条最常接的动作就是发下一条：同款同批，只换图/货号/变种 */}
                  <button type="button" className="temu-btn temu-btn-sm temu-btn-primary" onClick={startNextItem}>
                    发下一条
                  </button>
                </div>
                {shopCheck.error && <p className="temu-confirm-warn">查询失败：{shopCheck.error}</p>}
                {shopCheck.items.length > 0 && (
                  <ul className="temu-goods-list">
                    {shopCheck.items.map((item) => (
                      <li key={item.product_id}>
                        <span className="temu-goods-id">{item.product_id}</span>
                        <span className="temu-goods-title">{item.title}</span>
                        <span className="temu-muted">
                          {item.category || "—"} · {item.skc_ext_code || "—"}
                          {item.created_at
                            ? ` · ${new Date(item.created_at).toLocaleString("zh-CN")}`
                            : ""}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}

              </div>
            )}

            {result && result.warnings.length > 0 && (
              <ul className="temu-warnings">
                {result.warnings.map((warning) => (
                  <li key={warning}>{warning}</li>
                ))}
              </ul>
            )}

            <details className="temu-advanced">
              <summary>高级：图片地址原文、请求体</summary>
              <label className="temu-field">
                <span>轮播图地址（每行一个）</span>
                <textarea
                  className="temu-input"
                  rows={2}
                  value={form.carousel}
                  onChange={(event) => patch({ carousel: event.target.value })}
                  placeholder="https://img.kwcdn.com/..."
                />
              </label>
              <label className="temu-field">
                <span>外包装图地址</span>
                <textarea className="temu-input" rows={2} value={form.outerImages} onChange={(event) => patch({ outerImages: event.target.value })} />
              </label>
              <label className="temu-field">
                <span>详情图地址</span>
                <textarea className="temu-input" rows={2} value={form.detailImages} onChange={(event) => patch({ detailImages: event.target.value })} />
              </label>
              {result ? (
                <pre className="temu-json">{JSON.stringify(result.payload, null, 2)}</pre>
              ) : (
                <p className="temu-muted">点上面「预览请求体」后，这里显示即将提交给 Temu 的 goods.add 内容。</p>
              )}
            </details>
          </section>
        </div>

        {/* 右侧检查清单 */}
        <aside className="temu-console-rail">
          <div className="temu-rail-card">
            <div className="temu-rail-head">
              <strong>发布检查</strong>
              <span className={`temu-pill ${remainingContentSteps.length ? "warn" : "ok"}`}>
                {remainingContentSteps.length ? `还差 ${remainingContentSteps.length} 项` : "可提交"}
              </span>
            </div>
            <ol className="temu-rail-list">
              {flowSteps.map((step, index) => (
                <li key={step.id} className={`${step.done ? "is-done" : ""}${errorSection === step.id ? " is-error" : ""}`}>
                  <button type="button" onClick={() => jumpTo(step.id)}>
                    <span className="temu-rail-dot">{step.done ? <CheckGlyph /> : index + 1}</span>
                    <span className="temu-rail-label">
                      {step.label}
                      {/* 没填完就直说差什么；填完了才显示这块包含哪些内容 */}
                      <em>{step.done || step.id === "sec-publish" ? step.hint : `还差：${step.missing.join("、")}`}</em>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
            {/* 提交前的最后一眼：三个数字做成指标卡，缺什么当场变色。
                变种/轮播并排，申报价单独占整行 —— 它是三个里最要命的（填错就是亏钱），
                挤在 72px 里连「¥12.99」都显示不全。 */}
            <dl className="temu-rail-stats">
              <div className={`temu-stat${rows.length ? "" : " is-danger"}`}>
                <span className="temu-stat-ico" aria-hidden="true">
                  <StatIcon kind="variant" />
                </span>
                <div className="temu-stat-body">
                  <dt>变种</dt>
                  <dd>
                    {rows.length}
                    <em>个</em>
                  </dd>
                </div>
              </div>
              <div
                className={`temu-stat${
                  carouselCount >= 3 ? "" : carouselCount > 0 ? " is-warn" : " is-danger"
                }`}
              >
                <span className="temu-stat-ico" aria-hidden="true">
                  <StatIcon kind="image" />
                </span>
                <div className="temu-stat-body">
                  <dt>轮播</dt>
                  <dd>
                    {carouselCount}
                    <em>张</em>
                  </dd>
                </div>
              </div>
              <div className={`temu-stat is-wide${firstPrice && Number(firstPrice) > 0 ? "" : " is-muted"}`}>
                <span className="temu-stat-ico" aria-hidden="true">
                  <StatIcon kind="price" />
                </span>
                <div className="temu-stat-body">
                  <dt>申报价</dt>
                  <dd>
                    {firstPrice && Number(firstPrice) > 0 ? formatYuan(firstPrice) : "未填"}
                    <em>{firstPrice && Number(firstPrice) > 0 ? "首件" : "提交会被拒"}</em>
                  </dd>
                </div>
              </div>
            </dl>
            <div className="temu-rail-actions">
              <button
                type="button"
                className="temu-btn temu-btn-sm"
                disabled={busy !== "" || !ready}
                onClick={() => void checkMandatory()}
              >
                <BtnIcon path={ICON_CHECK} />
                {busy === "mandatory" ? "校验中…" : "检查必填"}
              </button>
              {publishResult ? (
                <button
                  type="button"
                  className="temu-btn temu-btn-primary temu-btn-sm"
                  onClick={startNextItem}
                  title="清掉标题/图片/货号/变种；类目、属性、运费模板、仓库都留着"
                >
                  <BtnIcon path={ICON_NEXT} />
                  发下一条
                </button>
              ) : (
                <button
                  type="button"
                  className="temu-btn temu-btn-primary temu-btn-sm"
                  disabled={submitDisabled}
                  onClick={() => void publishGoods()}
                  title="快捷键 Ctrl+Enter"
                >
                  <BtnIcon path={ICON_SUBMIT} />
                  {publishing ? "提交中…" : "提交发布"}
                </button>
              )}
            </div>
            {publishResult ? (
              <p className="temu-rail-ok" role="status">
                ✓ 已提交到店铺
                {publishedGoodsId ? ` · 商品 id ${publishedGoodsId}` : ""}
                {publishResult.confirmed_by_lookup ? "（网络中断后反查到的）" : ""}
              </p>
            ) : null}
            {error ? (
              <p className="temu-rail-error" role="alert" title={error}>
                {briefError(error)}
              </p>
            ) : null}
            <p className="temu-rail-tip">
              提交会真的在店铺创建商品，不可撤销。也可以按 Ctrl+Enter。
            </p>
          </div>
        </aside>
      </div>

      {imagePreview
        ? createPortal(
            <div
              className="temu-image-preview"
              role="dialog"
              aria-modal="true"
              aria-label="图片预览"
              onClick={() => setImagePreview(null)}
            >
              <img
                src={imagePreview.src}
                alt={imagePreview.alt || ""}
                referrerPolicy="no-referrer"
                onClick={(event) => event.stopPropagation()}
              />
              <button
                type="button"
                className="temu-image-preview-close"
                onClick={() => setImagePreview(null)}
                aria-label="关闭预览"
              >
                ×
              </button>
            </div>,
            document.body,
          )
        : null}

      {publishConfirm
        ? createPortal(
            <div className="temu-edit-backdrop" onClick={() => setPublishConfirm(null)}>
              <div
                className="temu-edit-dialog temu-confirm-dialog"
                role="alertdialog"
                aria-modal="true"
                aria-label={publishConfirm.kind === "submit" ? "确认提交发布" : "可能重复提交"}
                onClick={(event) => event.stopPropagation()}
              >
                <header className="temu-edit-head">
                  <div>
                    <strong>{publishConfirm.kind === "submit" ? "确认提交发布" : "可能重复提交"}</strong>
                    <span>
                      {publishConfirm.kind === "submit"
                        ? "提交后是真的在店铺里创建商品"
                        : "后端台账发现 24 小时内提交过同一条"}
                    </span>
                  </div>
                  <button type="button" onClick={() => setPublishConfirm(null)} aria-label="关闭">
                    ×
                  </button>
                </header>
                {publishConfirm.kind === "submit" ? (
                  <dl className="temu-confirm-summary">
                    <div>
                      <dt>标题</dt>
                      <dd title={publishConfirm.draft.title}>{publishConfirm.draft.title}</dd>
                    </div>
                    <div>
                      <dt>类目</dt>
                      <dd>{publishConfirm.draft.category_ids[publishConfirm.draft.category_ids.length - 1]}</dd>
                    </div>
                    <div>
                      <dt>变种</dt>
                      <dd>{publishConfirm.skuCount} 个</dd>
                    </div>
                  </dl>
                ) : (
                  <p className="temu-confirm-note">{publishConfirm.note}</p>
                )}
                <p className="temu-confirm-note">
                  {publishConfirm.kind === "submit"
                    ? "接口不支持重试（连点可能重复上架）；提交成功后商品会出现在店铺里。"
                    : "确认还要再提交一次吗？确认后店铺里会多出一件相同商品；如果只是想改内容，先取消，回草稿库改完再发。"}
                </p>
                <div className="temu-confirm-actions">
                  <button type="button" className="temu-btn" onClick={() => setPublishConfirm(null)}>
                    取消
                  </button>
                  <button
                    type="button"
                    className="temu-btn temu-btn-primary"
                    onClick={() => void confirmPublish()}
                  >
                    {publishConfirm.kind === "submit" ? "确认提交" : "仍然提交"}
                  </button>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}

      {tokenHelpOpen &&
        createPortal(
          <div className="temu-edit-backdrop" onClick={() => setTokenHelpOpen(false)}>
            <div
              className="temu-edit-dialog temu-help-dialog"
              role="dialog"
              aria-modal="true"
              aria-label="怎么拿店铺 access_token"
              onClick={(event) => event.stopPropagation()}
            >
              <header className="temu-edit-head">
                <div>
                  <strong>怎么拿店铺 access_token</strong>
                  <span>一个店铺一个 token，不能混用</span>
                </div>
                <button type="button" onClick={() => setTokenHelpOpen(false)} aria-label="关闭">
                  ×
                </button>
              </header>

              <p className="temu-help-lead">
                发布商品用的是 <strong>Temu 卖家后台</strong>签发的 access_token，不是你登录工作台的账号。
                跟着下面三步在自己的店铺后台走一次，之后一直用这个 token 发布，直到它过期。
              </p>

              <section className="temu-help-step">
                <div className="temu-help-step-head">
                  <i>1</i>
                  <strong>卖家中心 → 服务市场 → 授权管理</strong>
                </div>
                <p>登录 Temu 卖家中心，左侧菜单最下面的「服务市场」展开后点「授权管理」。</p>
                <img
                  className="temu-help-shot is-narrow"
                  src="/help/temu-auth-menu.png"
                  alt="Temu 卖家中心左侧菜单：服务市场 → 授权管理"
                  title="点击看大图"
                  onClick={() =>
                    setImagePreview({
                      src: "/help/temu-auth-menu.png",
                      alt: "Temu 卖家中心左侧菜单：服务市场 → 授权管理",
                    })
                  }
                />
              </section>

              <section className="temu-help-step">
                <div className="temu-help-step-head">
                  <i>2</i>
                  <strong>点「获取授权」→ 选对接系统 → 确认</strong>
                </div>
                <p>
                  点页面里的「获取授权」。弹窗第一步写着「如需使用自研系统对接，请联系对接运营进行对接」：
                  下拉里显示「无可选系统」时点旁边的「向运营申请」，让对接运营把系统开出来；
                  开通后在下拉里选上系统，点「确认」。
                </p>
                <img
                  className="temu-help-shot"
                  src="/help/temu-auth-grant.png"
                  alt="获取授权弹窗：联系对接运营 → 选择对接系统"
                  title="点击看大图"
                  onClick={() =>
                    setImagePreview({
                      src: "/help/temu-auth-grant.png",
                      alt: "获取授权弹窗：联系对接运营 → 选择对接系统",
                    })
                  }
                />
              </section>

              <section className="temu-help-step">
                <div className="temu-help-step-head">
                  <i>3</i>
                  <strong>「我的授权」里能看到这条授权</strong>
                </div>
                <p>
                  授权成功后，页面下方「我的授权」里会多出这次授权的记录，右边操作列是「取消授权」，
                  有效期约 90 天；到期前点「取消授权」再重新获取一次就行。
                </p>
                <img
                  className="temu-help-shot"
                  src="/help/temu-auth-page.png"
                  alt="授权管理（商品）页的「我的授权」：能看到这次授权记录"
                  title="点击看大图"
                  onClick={() =>
                    setImagePreview({
                      src: "/help/temu-auth-page.png",
                      alt: "授权管理（商品）页的「我的授权」：能看到这次授权记录",
                    })
                  }
                />
              </section>

              <p className="temu-help-note">
                拿到 access_token 后整串粘到下面「新增店铺」，点「校验并保存为店铺」——注意别把
                「服务商凭据」和它混了，那是服务器下发的，不用填。找不到 token 就直接问对接运营。
                截图可以点开看大图。
              </p>
            </div>
          </div>,
          document.body,
        )}

      {pickerSlot ? (
        <DraftImagePicker
          slotLabel={IMAGE_SLOTS.find((item) => item.key === pickerSlot)?.label ?? "图片"}
          existing={splitLines(slotText(pickerSlot))}
          limit={IMAGE_LIMITS[pickerSlot]}
          onClose={() => setPickerSlot(null)}
          onPick={(urls) => {
            appendSlotImages(pickerSlot, urls);
            setPickerSlot(null);
          }}
        />
      ) : null}
    </div>
  );
}
