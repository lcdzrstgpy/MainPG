import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, ReactNode } from "react";

import { httpBlob } from "../../../transport/http/client";
import { temuPublishApi } from "../api/temuPublishApi";
import {
  EMPTY_BATCH,
  PublishSkuPanel,
  createVariantRow,
  withFreshRowKeys,
} from "../components/PublishSkuPanel";
import type { VariantRow } from "../components/PublishSkuPanel";
import { TemuImageStrip, TemuThumb } from "../components/TemuImageStrip";
import { localStore, parsePublishError } from "../data/localStore";
import { categorySearchTexts, dedupeCategoryPaths } from "../utils/categoryMatch";
import { matchPresetValue } from "../utils/specMatch";
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
 * 默认发货时效（天）。
 *
 * 原来默认 1 天：填了就等于向 Temu 承诺 24 小时发货，POD/定制根本做不到，会被判延迟发货。
 * 7 天是 POD 印制的合理生产周期；Temu 允许更长（官方成功案例用的是 90 天），
 * 需要的可以自己改大。
 */
const DEFAULT_SHIPMENT_DAYS = "7";
const EMPTY_ATTR = { vid: 0, text: "" };

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

type AttrPick = { vid: number; text: string };
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
/** 本机存储也按账号分开：同一台电脑换账号登录，不能把上一个人的草稿带出来。 */
function storageScope(): string {
  try {
    const account = JSON.parse(window.localStorage.getItem("wh_demo_account") ?? "null") as
      | { user_id?: string }
      | null;
    return account?.user_id || "local";
  } catch {
    return "local";
  }
}
const PRESETS_KEY = "temu_publish_presets_v1";
/** 变种行（价格/库存/尺寸/重量都是手填的）也要落盘，刷新后不能只剩颜色尺码文本。 */
const ROWS_KEY = "temu_publish_rows_v1";
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

function categoryPathFromDto(dto: TemuCategoryPath): TemuCategory[] {
  const result: TemuCategory[] = [];
  for (let index = 1; index <= 10; index += 1) {
    const key = `cat${index}DTO` as keyof TemuCategoryPath;
    const item = dto[key];
    if (item) result.push(item);
  }
  return result;
}

function categoryName(item: TemuCategory): string {
  return item.catName || String(item.catId);
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
function formatCents(text?: string): string {
  const value = Number(text);
  if (!text || !Number.isFinite(value) || value <= 0) return text || "";
  return `¥${(value / 100).toFixed(2)}`;
}

function findParentSpec(specs: TemuParentSpec[], keyword: string, fallbackIndex: number): TemuParentSpec | null {
  const hit = specs.find((item) => item.parentSpecName.includes(keyword));
  return hit ?? specs[fallbackIndex] ?? specs[0] ?? null;
}

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

/**
 * 把图片裁成 1:1 并缩放到 800×800。
 *
 * Temu 非服饰类目轮播图强制「宽高比 1:1、宽高 ≥800px、≤2MB」，
 * 原图直传会被拒（实测报「非服饰类目货品轮播图……校验失败」）。
 */
function toSquareDataUrl(dataUrl: string, size = 800): Promise<string> {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const side = Math.min(img.naturalWidth, img.naturalHeight);
      if (!side) {
        resolve(dataUrl);
        return;
      }
      const canvas = document.createElement("canvas");
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        resolve(dataUrl);
        return;
      }
      // PNG 透明区域转 JPEG 会变黑，先铺白底
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, size, size);
      ctx.drawImage(
        img,
        (img.naturalWidth - side) / 2,
        (img.naturalHeight - side) / 2,
        side,
        side,
        0,
        0,
        size,
        size,
      );
      resolve(canvas.toDataURL("image/jpeg", 0.9));
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
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
      <span className={`temu-sec-num${step.done ? " is-done" : ""}`}>{step.done ? "✓" : index + 1}</span>
      <div className="temu-sec-title">
        <h2>{step.label}</h2>
        <p>{step.hint}</p>
      </div>
      {extra ? <div className="temu-sec-extra">{extra}</div> : null}
    </div>
  );
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
  const [busy, setBusy] = useState("");
  // 提交发布进行中：别的任务跑完不能把它「解除」，否则连点就是重复上架
  const [publishing, setPublishing] = useState(false);
  const publishingRef = useRef(false);
  // 只有「点过发布被拦下来」之后才把必填项标红：一进页面满屏红框反而让人焦虑
  const [showFieldErrors, setShowFieldErrors] = useState(false);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [accessToken, setAccessToken] = useState("");

  const storageSuffix = useMemo(() => storageScope(), []);
  const formKey = `${FORM_KEY}:${storageSuffix}`;
  const presetsKey = `${PRESETS_KEY}:${storageSuffix}`;
  const recentCategoryKey = `${RECENT_CATEGORY_KEY}:${storageSuffix}`;
  const rowsKey = `${ROWS_KEY}:${storageSuffix}`;

  const [form, setForm] = useState<PublishForm>(() => {
    const saved = loadJsonLocal<Partial<PublishForm>>(`${FORM_KEY}:${storageScope()}`, {});
    // 老版本默认值是 1 天，已存在本机的也纠正过来，否则一进来还是承诺 24 小时发货
    if (saved.shipmentDays === "1") saved.shipmentDays = DEFAULT_SHIPMENT_DAYS;
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
  const [attrPicks, setAttrPicks] = useState<Record<number, AttrPick>>({});

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
  // 当前类目要不要尺码表：false 就把「尺码表模板 ID」整块藏掉（宠物用品类目全是 false）
  const [sizeChart, setSizeChart] = useState<{ loading: boolean; has: boolean | null }>({
    loading: false,
    has: null,
  });
  // 店铺切换版本号：运费模板/发货仓库/尺码表都是「每家店各自一套」，
  // 换店后必须重新取一遍，否则会拿着上一家店的 id 去发新店的商品。
  const [shopEpoch, setShopEpoch] = useState(0);

  const [rows, setRows] = useState<VariantRow[]>(() => {
    const saved = loadJsonLocal<VariantRow[]>(`${ROWS_KEY}:${storageScope()}`, []);
    return Array.isArray(saved) ? withFreshRowKeys(saved.filter((row) => row && row.key)) : [];
  });

  // 模板与发布失败记录（都存在本机，按账号隔离）
  const [productTemplates, setProductTemplates] = useState<ProductTemplate[]>(() =>
    localStore.productTemplates(),
  );
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
        }
        if (catsR.ok) {
          setCategoryOptions(catsR.value.categoryDTOList ?? []);
        } else {
          missed.push("类目树");
        }
        const specItems = specsR.ok ? specsR.value.parentSpecDTOS ?? [] : [];
        if (specsR.ok) {
          setParentSpecs(specItems);
          setColorParentId(findParentSpec(specItems, "颜色", 0)?.parentSpecId ?? 0);
          setSizeParentId(findParentSpec(specItems, "尺码", 1)?.parentSpecId ?? 0);
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
        const seedRow = prefill
          ? prefillToRow(createVariantRow(whId, "", "均码", EMPTY_BATCH, 0), prefill)
          : null;
        setRows((current) => {
          // 带草稿进来：整表按这条草稿重建，别把上一条的颜色/价格/尺寸留在后面几行
          if (seedRow) return [seedRow];
          if (current.length) return current;
          return [createVariantRow(whId, "", "均码", EMPTY_BATCH, 0)];
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

  /** 草稿值铺到表单（不动变种行）。 */
  function prefillToForm(current: PublishForm, prefill: TemuPublishPrefill): Partial<PublishForm> {
    return {
      title: prefill.title || current.title,
      // Temu 的变种属性值一/值二
      // 草稿里的「变种属性值一/二」= 颜色列表 / 尺码列表
      colorsText: prefill.specValue1 || current.colorsText,
      sizesText: prefill.specValue2 || current.sizesText,
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
      const seed = createVariantRow(current[0].warehouseId, "", "均码", EMPTY_BATCH, 0);
      return [prefillToRow(seed, prefill)];
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
    try {
      await task();
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      // 只清自己那次：发布这种长任务进行中，别的短任务结束不能把按钮重新解禁
      setBusy((current) => (current === label ? "" : current));
    }
  };

  const loadLevel = async (parentCatId: number) => {
    const response = await temuPublishApi.categories(parentCatId);
    setCategoryOptions(response.categoryDTOList ?? []);
  };

  const chooseCategory = (item: TemuCategory) =>
    run("category", async () => {
      const next = [...categoryPath, item];
      setCategoryPath(next);
      if (item.isLeaf) {
        const attrs = await temuPublishApi.attributes(item.catId);
        setAttributes(attrs);
        setAttrPicks({});
        saveJsonLocal(recentCategoryKey, next);
      } else {
        setAttributes(null);
        setAttrPicks({});
        await loadLevel(item.catId);
      }
    });

  const resetToLevel = (index: number) =>
    run("category", async () => {
      if (index < 0) {
        setCategoryPath([]);
        setAttributes(null);
        setAttrPicks({});
        await loadLevel(0);
        return;
      }
      const next = categoryPath.slice(0, index + 1);
      setCategoryPath(next);
      setAttributes(null);
      setAttrPicks({});
      await loadLevel(next[next.length - 1].catId);
    });

  /** 直接套用一条完整类目路径（预设、最近使用都走这里）。 */
  const applyCategory = (path: TemuCategory[], options?: { auto?: boolean }) =>
    run("category", async () => {
      // 自动推荐不算「用户选过」：标题一改还能继续跟着改
      if (!options?.auto) categoryTouchedRef.current = true;
      const leaf = path[path.length - 1];
      // 同一个叶子类目再套一次（标题反复改、自动推荐重复命中）不该把用户填好的属性清掉
      const sameLeaf = !!leaf?.catId && leaf.catId === categoryPath[categoryPath.length - 1]?.catId;
      setCategoryPath(path);
      setMatchPaths([]);
      if (!leaf?.isLeaf) {
        setAttributes(null);
        await loadLevel(leaf?.catId ?? 0);
        return;
      }
      const attrs = await temuPublishApi.attributes(leaf.catId);
      setAttributes(attrs);
      if (!sameLeaf) setAttrPicks({});
      setCategoryOptions([]);
      saveJsonLocal(recentCategoryKey, path);
      // 回写给来源草稿：草稿库那行会显示类目路径，下次带入也直接选中
      if (prefillDraftId.current) {
        localStore.rememberDraftCategory(prefillDraftId.current, path);
      }
    });

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
      setSizeChart({ loading: false, has: null });
      return;
    }
    let alive = true;
    setSizeChart((current) => ({ loading: true, has: current.has }));
    void temuPublishApi
      .sizeCharts(leafId)
      .then((info) => {
        if (alive) setSizeChart({ loading: false, has: !!info.has_size_chart });
      })
      .catch(() => {
        if (alive) setSizeChart({ loading: false, has: null });
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
  const applyProductTemplate = (name: string) => {
    const template = productTemplates.find((item) => item.name === name);
    if (!template) return;
    patch({
      freightId: form.freightId || template.freightId,
      warehouseId: form.warehouseId || template.warehouseId,
      shipmentDays:
        form.shipmentDays && form.shipmentDays !== "1"
          ? form.shipmentDays
          : template.shipmentDays && template.shipmentDays !== "1"
            ? template.shipmentDays
            : DEFAULT_SHIPMENT_DAYS,
      origin: form.origin || template.origin,
      sizeTemplates: form.sizeTemplates || template.sizeTemplates,
      isCustom: template.isCustom,
      isSensitive: template.isSensitive,
    });
    setAttrPicks((current) => {
      const next = { ...current };
      Object.entries(template.attrs).forEach(([pid, pick]) => {
        if (!next[Number(pid)]) next[Number(pid)] = pick;
      });
      return next;
    });
    if (template.categoryPath.length) void applyCategory(template.categoryPath);
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
      setColorParentId(findParentSpec(specItems, "颜色", 0)?.parentSpecId ?? 0);
      setSizeParentId(findParentSpec(specItems, "尺码", 1)?.parentSpecId ?? 0);
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

  /**
   * 上传图片：轮播/外包装裁 1:1 再传（Temu 主图要求方图），
   * 详情图**不能裁方** —— 商详楼层是长图，裁成 1:1 会把内容切掉；
   * 详情图交给服务端按原宽上传（target_width=0 表示不缩放），宽度在提交时统一。
   */
  const uploadTo = (slot: ImageSlot, event: ChangeEvent<HTMLInputElement>) =>
    run("upload", async () => {
      const files = Array.from(event.target.files ?? []);
      event.target.value = "";
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
      // 并发 3 张：串行传 6 张要等 6~18 秒，并发后基本等于最慢那张
      const uploadedUrls = await mapWithConcurrency(files, 3, async (file) => {
        const raw = await blobToDataUrl(file);
        const uploaded =
          slot === "detail"
            ? await temuPublishApi.uploadDetailImage(raw, 0, leaf?.catId)
            : await temuPublishApi.uploadImage(await toSquareDataUrl(raw), leaf?.catId);
        return pickUploadedUrl(uploaded);
      });
      const urls = uploadedUrls.filter(Boolean);
      if (!urls.length) throw new Error("图片上传成功但没有返回图片地址");
      setSlotText(slot, [...splitLines(slotText(slot)), ...urls].join("\n"));
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

  /** 取某个父规格下的预设值（给变种表下拉用）。 */
  const specOptions = (parentSpecId: number): string[] =>
    (saleValueIndex.get(parentSpecId) ?? []).map((item) => item.name);

  const ensureSpec = async (
    parentSpec: TemuParentSpec | null,
    specName: string,
  ): Promise<TemuSkuSpec> => {
    if (!parentSpec) throw new Error("Temu 父规格列表为空，无法创建规格");
    const name = specName.trim();
    if (!name) throw new Error("规格名称不能为空");
    const options = saleValueIndex.get(parentSpec.parentSpecId) ?? [];
    // 草稿带来的是「安卓通用款-黑色」「迷雾蓝」这种描述，先自动对齐到预设值
    const matchedName = matchPresetValue(name, options.map((item) => item.name));
    const preset = matchedName ? options.find((item) => item.name === matchedName) : undefined;
    if (!preset) {
      const samples = specOptions(parentSpec.parentSpecId).slice(0, 6).join("、");
      throw new Error(
        `「${name}」不是该 ${parentSpec.parentSpecName} 的合法值：Temu 只认类目预设值` +
          (samples ? `，请从下拉里选（如 ${samples}）` : ""),
      );
    }
    return {
      parent_spec_id: parentSpec.parentSpecId,
      parent_spec_name: parentSpec.parentSpecName,
      spec_id: preset.specId,
      spec_name: preset.name,
    };
  };

  /** 本地媒体路径/外链统一上传到 Temu 图床；已是 Temu 地址则复用。 */
  const resolveImage = async (url: string, cateId?: number): Promise<string> => {
    const text = url.trim();
    if (!text) throw new Error("存在空图片地址");
    if (/img\.kwcdn\.com/i.test(text)) return text;
    const cached = imageCache.current.get(text);
    if (cached) return cached;
    let uploaded: TemuImageUploadResult;
    if (isPublicHttpUrl(text)) {
      // 公网外链（如 COS）交给后端代下载，避免浏览器跨域。
      uploaded = await temuPublishApi.uploadImageUrl(text, cateId);
    } else {
      const blob = await httpBlob(text);
      uploaded = await temuPublishApi.uploadImage(await blobToDataUrl(blob), cateId);
    }
    const resolved = pickUploadedUrl(uploaded);
    if (!resolved) throw new Error(`图片上传成功但没有返回地址：${text}`);
    imageCache.current.set(text, resolved);
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
          const blob = await httpBlob(text);
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
      const image = isPublicHttpUrl(text) ? text : await blobToDataUrl(await httpBlob(text));
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

  const resolveImages = async (urls: string[], cateId?: number): Promise<string[]> => {
    const resolved: string[] = [];
    for (const url of urls) resolved.push(await resolveImage(url, cateId));
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
    if (!carouselSource.length) throw new Error("至少需要一张产品轮播图");
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

    // 本地草稿图/外链图在生成前统一上传到 Temu 图床；已上传过的复用缓存。
    const carousel = await resolveImages(carouselSource, leaf.catId);
    const outerImages = await resolveImages(splitLines(form.outerImages), leaf.catId);
    const detailImages = await resolveDetailImages(splitLines(form.detailImages), leaf.catId);
    const material = form.material.trim() ? await resolveImage(form.material, leaf.catId) : carousel[0];

    if (attributes) {
      const missing = visibleProperties
        .filter((item) => item.required)
        .filter((item) => {
          const pick = attrPicks[item.pid];
          if (!pick || (pick.vid <= 0 && !pick.text.trim())) return true;
          // 带单位的属性（成分比例 %、厚度 mm）Temu 要的是数值：只选下拉不够
          if (item.valueUnit?.length && !pick.text.trim()) return true;
          return false;
        })
        .map((item) => item.name || item.propertyName || `pid=${item.pid}`);
      if (missing.length) throw new Error(`缺少必填属性：${missing.join("、")}`);
    }

    const attrByPid = new Map((attributes?.properties ?? []).map((item) => [item.pid, item]));
    // 联动隐藏的属性不能带进 payload（留着上次填的值会被判「属性不合法」）
    const visiblePids = new Set(visibleProperties.map((item) => item.pid));
    const properties = Object.entries(attrPicks)
      .filter(([pid]) => visiblePids.has(Number(pid)))
      .filter(([, pick]) => pick && (pick.vid > 0 || pick.text.trim()))
      .map(([pid, pick]) => {
        const attr = attrByPid.get(Number(pid));
        const text = pick.text.trim();
        return {
          pid: Number(pid),
          vid: pick.vid,
          // 类目属性表里的名称/模板 id 必须带上，否则 Temu 判「属性不合法」
          prop_name: attr?.name || attr?.propertyName || "",
          ref_pid: attr?.refPid ?? undefined,
          template_pid: attr?.templatePid ?? undefined,
          prop_value: text,
          // 数值型属性（带单位）要把数值和单位分开给，否则会被要求「请输入数值」
          number_input_value: attr?.valueUnit?.length ? text : "",
          value_unit: attr?.valueUnit?.[0] ?? "",
        };
      });

    if (!rows.length) throw new Error("至少需要一个变种");
    if (rows.length > 20) throw new Error("Temu 多变种上限 20 个");
    const colorParent = parentSpecs.find((item) => item.parentSpecId === colorParentId) ?? null;
    const sizeParent = parentSpecs.find((item) => item.parentSpecId === sizeParentId) ?? null;

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
          `${at}建议售价 ${row.suggestedPrice} 明显小于申报价 ${row.supplierPrice}：` +
            "建议售价单位也是「分」，草稿带入的是店铺币种金额时请改成 ×100。",
        );
      }
      const skuCode = cleanExtCode(row.extCode);
      if (!skuCode) throw new Error(`${at}缺少 SKU 货号（只能用字母/数字/-/_）`);
      if (skuCode !== row.extCode.trim()) {
        warnings.push(`${at}SKU 货号里的中文/符号已自动去掉：${row.extCode.trim()} → ${skuCode}`);
      }
      if (!row.warehouseId) throw new Error(`${at}缺少发货仓库`);
      return {
        ext_code: skuCode,
        // 半托管供货价按人民币结算：传其它币种会被拒「请选择正确的币种：CNY」
        currency_type: "CNY",
        site_id: SITE_ID,
        supplier_price: Math.round(parsePositive(row.supplierPrice, `${at}申报价格`)),
        suggested_price: row.suggestedPrice.trim()
          ? Math.round(parsePositive(row.suggestedPrice, `${at}建议售价`))
          : null,
        // 库存 0 是合法的（Temu 会建品但不可售），别拿价格的规则卡它
        stock: parseStock(row.stock, `${at}库存`),
        warehouse_id: row.warehouseId,
        // 页面填 g，接口要毫克；尺寸填 cm，接口要毫米
        weight_mg: Math.round(parsePositive(row.weightG, `${at}重量`) * 1000),
        length_mm: Math.round(parsePositive(row.lengthCm, `${at}长`) * 10),
        width_mm: Math.round(parsePositive(row.widthCm, `${at}宽`) * 10),
        height_mm: Math.round(parsePositive(row.heightCm, `${at}高`) * 10),
        thumb_url: row.thumbUrl.trim() || skcImage,
        // 多件装（4 Pack / 12Pcs）：1 就是单件装，接口里不发这个字段
        pieces_per_pack: Math.max(1, Math.round(Number(form.piecesPerPack || "1") || 1)),
        is_sensitive: form.isSensitive,
        specs,
      };
    };

    const skcs: TemuGoodsDraft["skcs"] = [];
    /**
     * 类目没有销售属性（如「桌布/亚麻布」这类只有商品属性的类目）：
     * Temu 不给颜色/尺码变种，只能建单 SKU 商品 —— 规格声明与 SKU 规格都发空数组。
     * 以前这里一律走 ensureSpec，这类类目直接抛「不是合法的规格值」，等于永远发不出去。
     */
    const singleSku = saleValueIndex.size === 0;
    if (singleSku) {
      if (rows.length > 1) {
        warnings.push(
          `该类目没有销售属性（不能建颜色/尺码变种）：只提交第 1 行，后面 ${rows.length - 1} 行已忽略。`,
        );
      }
      const row = rows[0];
      const skcImage = row.thumbUrl.trim() || carousel[0];
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
        if (!color) throw new Error(`第 ${index + 1} 行变种缺少颜色`);
        const list = grouped.get(color) ?? [];
        list.push(row);
        grouped.set(color, list);
      });

      let colorIndex = 0;
      for (const [color, colorRows] of grouped) {
        colorIndex += 1;
        const colorSpec = await ensureSpec(colorParent, color);
        // SKC 图：优先用该色第一行单独设的变种图，否则取轮播首图
        const skcImage = colorRows.find((row) => row.thumbUrl.trim())?.thumbUrl.trim() || carousel[0];
        const seenSizes = new Set<string>();
        const skus: TemuSkuDraft[] = [];
        for (const [index, row] of colorRows.entries()) {
          const at = `颜色「${color}」第 ${index + 1} 行`;
          if (!row.sizeName.trim()) throw new Error(`${at}缺少尺码`);
          if (seenSizes.has(row.sizeName.trim())) throw new Error(`${at}尺码「${row.sizeName}」重复`);
          seenSizes.add(row.sizeName.trim());
          const sizeSpec = await ensureSpec(sizeParent, row.sizeName);
          skus.push(buildSku(row, at, [colorSpec, sizeSpec], skcImage));
        }
        skcs.push({
          // SKC 货号：产品货号 + 颜色，避免多色撞号
          ext_code: [productCode || "SKC", cleanExtCode(color)].filter(Boolean).join("-").slice(0, 120),
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
      title: form.title.trim(),
      title_en: form.titleEn.trim(),
      carousel_image_urls: carousel,
      outer_package_image_urls: outerImages,
      detail_images: detailImages,
      detail_image_urls: detailImages.map((item) => item.url),
      material_image_url: material,
      freight_template_id: form.freightId,
      shipment_limit_seconds: Math.round(parsePositive(form.shipmentDays, "发货时效") * 86400),
      outer_goods_url: form.externalUrl.trim(),
      properties,
      skcs,
      site_ids: [SITE_ID],
      // 类目明确不需要尺码表（sizeChart.has === false）时不要带旧的模板 id：
      // 字段被藏起来了，用户看不到也改不掉，带上去只会让 Temu 回一句莫名其妙的话
      size_template_ids: sizeChart.has === false ? [] : sizeTemplates,
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
        noteValidationBlocked();
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

  /** 真正提交到店铺。非幂等 + 有外部副作用，必须先让用户确认。 */
  const publishGoods = () => {
    // 双击 / 发布期间又点了一次：直接忽略（接口非幂等，重复提交就重复上架）
    if (publishingRef.current) return;
    publishingRef.current = true;
    setPublishing(true);
    return run("publish", async () => {
      // 校验没过：把必填项标红，并滚到第一个还没填完的区块 ——
      // 否则长表单里只看到顶部一句「缺少必填属性」，还得自己往下找在哪
      const draft = await composeDraft().catch((cause: unknown) => {
        noteValidationBlocked();
        throw cause;
      });
      const skuCount = draft.skcs.reduce((total, skc) => total + skc.skus.length, 0);
      const confirmed = window.confirm(
        [
          "即将把这件商品直接提交到 Temu 店铺：",
          "",
          `标题：${draft.title}`,
          `类目：${draft.category_ids[draft.category_ids.length - 1]}`,
          `变种：${skuCount} 个`,
          "",
          "提交成功后是真的在店铺里创建商品；接口不支持重试（重复提交可能重复上架）。确认提交？",
        ].join("\n"),
      );
      if (!confirmed) return;
      setPublishResult(null);
      try {
        const published = await temuPublishApi.publish(draft);
        setPublishResult(published);
      } catch (cause) {
        const text = errorMessage(cause);
        // 后端台账发现 24 小时内提交过同一条：不是失败，是「可能已经在店铺里了」
        if (/重复提交/.test(text)) {
          const again = window.confirm(
            [
              text,
              "",
              "确认还要再提交一次吗？确认后店铺里会多出一件相同商品；",
              "如果只是想改内容，先点取消，回草稿库改完再发。",
            ].join("\n"),
          );
          if (!again) return;
          const published = await temuPublishApi.publish(draft, true);
          setPublishResult(published);
          return;
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
          hint: parsed.hint,
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
    }).finally(() => {
      publishingRef.current = false;
      setPublishing(false);
    });
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

  const removeRow = (key: string) =>
    setRows((current) => (current.length > 1 ? current.filter((row) => row.key !== key) : current));

  const leaf = categoryPath[categoryPath.length - 1];
  // 产品货号（ParentSKU）：Temu 只认字母/数字/-/_，渲染层也用它拼变种货号前缀
  const productCode = cleanExtCode(form.productCode);
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
  const visibleProperties = useMemo<TemuProperty[]>(() => {
    const props = attributes?.properties ?? [];
    const byTemplatePid = new Map<number, TemuProperty>();
    props.forEach((item) => {
      const key = Number(item.templatePid ?? 0);
      if (key) byTemplatePid.set(key, item);
    });
    const pickedVid = (pid: number) => Number(attrPicks[pid]?.vid ?? 0);
    const list: TemuProperty[] = [];
    for (const prop of props) {
      const rules = prop.templatePropertyValueParentList ?? [];
      if (!rules.length) {
        list.push(prop);
        continue;
      }
      const parent = byTemplatePid.get(Number(prop.parentTemplatePid ?? 0));
      if (!parent) {
        // 拿不到父属性：宁可显示也不误杀
        list.push(prop);
        continue;
      }
      const parentVid = pickedVid(parent.pid);
      if (!parentVid) continue;
      const matched = rules.filter((rule) => (rule.parentVidList ?? []).includes(parentVid));
      if (!matched.length) continue;
      const allowed = new Set(matched.flatMap((rule) => rule.vidList ?? []));
      list.push({
        ...prop,
        values: (prop.values ?? []).filter((value) => !allowed.size || allowed.has(value.vid)),
      });
    }
    return list;
  }, [attributes, attrPicks]);

  const requiredProps = visibleProperties.filter((item) => item.required);
  const optionalProps = visibleProperties.filter((item) => !item.required);

  /**
   * 一个类目属性的输入控件。三种形态：
   *  - 只有预设值 → 下拉
   *  - 只有单位 → 数值输入框（厚度 mm 这类）
   *  - 两者都有 → 下拉 + 数值输入框（「封面材质」选成分 + 填成分比例 %）
   * 之前只渲染下拉，导致「下拉+比例」的属性永远填不进数值，Temu 回「请输入数值」。
   */
  const attrField = (item: TemuProperty, required: boolean) => {
    const label = item.name || item.propertyName || `属性 ${item.pid}`;
    const pick = attrPicks[item.pid] ?? EMPTY_ATTR;
    const unit = item.valueUnit?.[0] ?? "";
    const unitHint = unit
      ? `${item.numberInputTitle || "请输入数值"}（${unit}）`
      : item.numberInputTitle || "请输入";
    // 必填没填：只在「点过发布被拦」之后标红，不然一进页面满屏红框
    const emptyRequired = required && !pick.vid && !pick.text.trim();
    return (
      <label
        className={`temu-field${showFieldErrors && emptyRequired ? " is-required-empty" : ""}`}
        key={item.pid}
      >
        <span>
          {label}
          {required && <em className="temu-required">*</em>}
        </span>
        {item.values?.length ? (
          <div className={unit ? "temu-spec-row" : undefined}>
            <select
              className="temu-input"
              value={pick.vid || ""}
              onChange={(event) =>
                setAttrPicks((current) => ({
                  ...current,
                  [item.pid]: { ...pick, vid: Number(event.target.value) || 0 },
                }))
              }
            >
              <option value="">请选择</option>
              {item.values.map((value) => (
                <option key={value.vid} value={value.vid}>
                  {value.value}
                </option>
              ))}
            </select>
            {unit ? (
              <input
                className="temu-input"
                value={pick.text}
                title={unitHint}
                placeholder={unitHint}
                onChange={(event) =>
                  setAttrPicks((current) => ({
                    ...current,
                    [item.pid]: { ...pick, text: event.target.value },
                  }))
                }
              />
            ) : null}
          </div>
        ) : (
          <input
            className="temu-input"
            value={pick.text}
            title={unitHint}
            placeholder={unitHint}
            onChange={(event) =>
              setAttrPicks((current) => ({
                ...current,
                [item.pid]: { ...pick, text: event.target.value },
              }))
            }
          />
        )}
      </label>
    );
  };
  const filledRequired = requiredProps.filter((item) => {
    const pick = attrPicks[item.pid];
    return !!pick && (pick.vid > 0 || !!pick.text.trim());
  }).length;
  const carouselCount = splitLines(form.carousel).length;

  // 分区顺序按 Temu 刊登习惯，且只保留「真会提交」的字段：基本信息 → 产品信息 → 变种信息 → 包装信息 → 发布
  /** 一行变种是否填全（价格/尺寸/重量/货号/仓库都要有） */
  const rowIncomplete = (row: VariantRow) =>
    !(
      row.sizeName.trim() &&
      row.extCode.trim() &&
      row.warehouseId &&
      row.supplierPrice.trim() &&
      row.weightG.trim() &&
      row.lengthCm.trim() &&
      row.widthCm.trim() &&
      row.heightCm.trim()
    );
  const incompleteRows = rows.filter(rowIncomplete).length;
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
        ...(rows.length && incompleteRows ? [`${incompleteRows} 行没填全`] : []),
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

  /** 校验没通过时的统一反馈：标红必填项 + 滚到第一个缺口。 */
  const noteValidationBlocked = () => {
    setShowFieldErrors(true);
    const firstGap = flowSteps.find((step) => step.id !== "sec-publish" && !step.done);
    if (firstGap) jumpTo(firstGap.id);
  };
  const stepOf = (id: string) => flowSteps.find((item) => item.id === id) ?? flowSteps[0];
  const ready = !!status?.configured.ready;
  // 服务商凭据：跟具体店铺无关，解绑店铺也保留
  const gatewayReady = !!(status?.configured.yl_client && status?.configured.yl_token);
  // 发布中一律禁用：接口非幂等，重复点就重复上架
  const submitDisabled = busy !== "" || !ready || publishing;
  const firstPrice = rows[0]?.supplierPrice?.trim() ?? "";
  const effectiveMaterial = form.material.trim() || splitLines(form.carousel)[0] || "";

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
          {/* 五段进度条：一眼看出走到第几步。悬停能看到这一步还差什么 */}
          <div className="temu-progress" role="presentation">
            {flowSteps
              .filter((step) => step.id !== "sec-publish")
              .map((step) => (
                <span
                  key={step.id}
                  className={step.done ? "is-done" : ""}
                  title={`${step.label}：${step.done ? "已完成" : `还差 ${step.missing.join("、")}`}`}
                />
              ))}
          </div>
        </div>
        <div className="temu-console-actions">
          <button
            type="button"
            className={`temu-pill temu-store-chip ${ready ? "ok" : "warn"}`}
            onClick={() => {
              const next = !settingsOpen;
              setSettingsOpen(next);
              // 面板可能是在别处改过凭证后打开的：重读一次，避免显示成「未配置」
              if (next) {
                void temuPublishApi
                  .settings()
                  .then((fresh) => {
                    setStatus(fresh);
                    // 凭证齐全就顺手校验一次，面板里才能显示店铺名/有效期（服务端有 10 分钟缓存）
                    if (!fresh.configured.ready) return undefined;
                    return temuPublishApi
                      .verifyToken()
                      .then(setTokenInfo)
                      .catch(() => undefined);
                  })
                  .catch(() => undefined);
              }
            }}
            title="点击查看/修改凭证"
          >
            {ready ? tokenInfo?.mall_name || "我的店铺已连接" : "绑定我的店铺"}
            {tokenInfo?.remaining_days != null ? ` · 剩 ${tokenInfo.remaining_days} 天` : ""}
          </button>
          <button type="button" className="temu-btn" onClick={verify} disabled={busy !== "" || !ready}>
            验证店铺
          </button>
          <button type="button" className="temu-btn" onClick={checkMandatory} disabled={busy !== ""}>
            {busy === "mandatory" ? "校验中…" : "检查必填"}
          </button>
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
              <strong>{gatewayReady ? "已配置（解绑店铺不影响）" : "未配置"}</strong>
            </div>
            <div>
              <span>店铺授权</span>
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

          {/* 服务商 client_id / token 是预置的，不让用户填也不展示 */}
          <p className="temu-muted">
            服务商凭据（client_id / token）：{gatewayReady ? "已预置，不用管" : "还没预置，请联系服务商开通；这里不用填"}
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
          <button type="button" onClick={() => setError("")}>关闭</button>
        </div>
      )}

      <div className="temu-console-body">
        <div className="temu-console-main">
          {/* 状态还没回来：给一行轻提示，别让人以为页面坏了 */}
          {!statusLoaded && <p className="temu-muted temu-console-loading">正在读取店铺配置…</p>}
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
                    prefillInfo.declaredPrice ? `申报价 ${formatCents(prefillInfo.declaredPrice)}` : "",
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
          ) : (
            <div className="temu-source-bar is-empty">
              <span className="temu-prefill-source">来源</span>
              <div className="temu-source-main">
                <strong>从草稿库带入，标题/图片/变种一次填好</strong>
                <span className="temu-muted">也可以直接在下面手工填，草稿库里的 AI / POD 成品都能一键带入</span>
              </div>
              {onOpenDraftLibrary && (
                <button type="button" className="temu-btn" onClick={onOpenDraftLibrary}>
                  去草稿库挑一个
                </button>
              )}
            </div>
          )}

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
            className={`temu-sec${stepOf("sec-base").done ? " is-done" : ""}${activeSectionId === "sec-base" ? " is-active" : ""}`}
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
            <div className="temu-two-col">
              <label className="temu-field">
                <span>店铺账号</span>
                <input className="temu-input" value={tokenInfo?.mall_name || "未连接"} readOnly />
              </label>
              <label className="temu-field">
                <span>经营站点</span>
                <select className="temu-input" value={String(SITE_ID)} disabled>
                  <option value="100">美国站（100）</option>
                </select>
              </label>
            </div>

            <div className="temu-field">
              <span>
                产品分类<em className="temu-required">*</em>
                <em className="temu-muted">会记住上次用过的分类</em>
              </span>
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
                  <button type="button" className="temu-link" onClick={() => resetToLevel(-1)} disabled={busy !== ""}>
                    重选
                  </button>
                </div>
              )}
              {leaf?.isLeaf && attributes && saleValueIndex.size === 0 && (
                <p className="temu-cat-hint">
                  该类目在 Temu 没有销售属性（建不了颜色/尺码变种），只能按单 SKU 发布；想按颜色卖就换一个带变种的类目。
                </p>
              )}
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
                        <button key={item.catId} type="button" onClick={() => chooseCategory(item)} disabled={busy !== ""}>
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

            <div className="temu-field">
              <span>
                产品属性<em className="temu-required">*</em>
                <em className="temu-muted">
                  带 * 必填；不带的收在下面，不填也能发布
                  {attributes && attributes.properties.length > visibleProperties.length
                    ? `（已按上一级选择隐藏 ${attributes.properties.length - visibleProperties.length} 项不适用属性）`
                    : ""}
                </em>
              </span>
              {!attributes ? (
                <p className="temu-empty">选到叶子分类后自动加载属性。</p>
              ) : (
                <>
                  {requiredProps.length > 0 && (
                    <div className="temu-attr-grid">
                      {requiredProps.map((item) => attrField(item, true))}
                    </div>
                  )}
                  {optionalProps.length > 0 && (
                    <details className="temu-optional-attrs">
                      <summary>选填属性 {optionalProps.length} 项（不填也能发布）</summary>
                      <div className="temu-attr-grid">
                        {optionalProps.map((item) => attrField(item, false))}
                      </div>
                    </details>
                  )}
                  {requiredProps.length === 0 && optionalProps.length === 0 && (
                    <p className="temu-empty">这个分类没有可填属性。</p>
                  )}
                </>
              )}
            </div>
          </section>

          {/* 2 产品信息 */}
          <section
            className={`temu-sec${stepOf("sec-info").done ? " is-done" : ""}${activeSectionId === "sec-info" ? " is-active" : ""}`}
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
                className={`temu-input${showFieldErrors && !form.title.trim() ? " is-bad" : ""}`}
                value={form.title}
                onChange={(event) => patch({ title: event.target.value })}
                placeholder="英文标题，突出核心卖点"
                maxLength={250}
                rows={3}
              />
            </label>
            <div className="temu-two-col">
              <label className="temu-field">
                <span>
                  英文标题
                  <em className="temu-muted">选填，需全英文；填了会随标题一起提交</em>
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
            <div className="temu-two-col">
              <div className="temu-field">
                <span>
                  敏感货
                  <em className="temu-muted">带电/液体/粉末等，提交为 isSensitive</em>
                </span>
                <label className="temu-check">
                  <input
                    type="checkbox"
                    checked={form.isSensitive}
                    onChange={(event) => patch({ isSensitive: event.target.checked })}
                  />
                  <span>这个是敏感货（填错会被 Temu 拒）</span>
                </label>
              </div>
              <div className="temu-field">
                <span>
                  定制 / POD
                  <em className="temu-muted">提交为 personalizationSwitch</em>
                </span>
                <label className="temu-check">
                  <input type="checkbox" checked={form.isCustom} onChange={(event) => patch({ isCustom: event.target.checked })} />
                  <span>这是定制商品（需先在卖家中心配好定制模板）</span>
                </label>
              </div>
            </div>

            {IMAGE_SLOTS.map((slot) => (
              <div className="temu-img-group" key={slot.key}>
                <div className="temu-img-group-head">
                  <strong>
                    {slot.label}
                    {slot.required && <em className="temu-required">*</em>}
                  </strong>
                  <span className="temu-muted">{slot.hint}</span>
                  <label className="temu-btn is-small">
                    + 添加图片
                    <input
                      type="file"
                      accept="image/*"
                      multiple
                      hidden
                      onChange={(event) => uploadTo(slot.key, event)}
                      disabled={busy !== ""}
                    />
                  </label>
                </div>
                <TemuImageStrip
                  urls={splitLines(slotText(slot.key))}
                  onChange={(next) => setSlotText(slot.key, next.join("\n"))}
                  firstBadge={slot.key === "carousel" ? "主图" : undefined}
                  emptyHint={
                    slot.key === "carousel"
                      ? "草稿带入的图会显示在这里；上传会自动裁成 1:1"
                      : "可选，从草稿库带入或上传"
                  }
                />
              </div>
            ))}

            <div className="temu-field">
                <span>
                  产品素材图
                  <em className="temu-muted">留空＝自动取轮播第一张</em>
                </span>
                <div className="temu-material-row">
                  <TemuThumb path={effectiveMaterial} onOpen={() => undefined} />
                  <input
                    className="temu-input"
                    value={form.material}
                    onChange={(event) => patch({ material: event.target.value })}
                    placeholder="覆盖素材图地址（可选）"
                  />
                </div>
            </div>
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
            className={`temu-sec${stepOf("sec-variant").done ? " is-done" : ""}${activeSectionId === "sec-variant" ? " is-active" : ""}`}
            id="sec-variant"
          >
            <SectionHead
              step={stepOf("sec-variant")}
              active={activeSectionId === "sec-variant"}
              index={2}
              extra={<span className="temu-muted">{rows.length} 个变种</span>}
            />
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
            <PublishSkuPanel
              rows={rows}
              onRowsChange={setRows}
              onUpdateRow={updateRow}
              onRemoveRow={removeRow}
              parentSpecs={parentSpecs}
              colorParentId={colorParentId}
              colorOptions={specOptions(colorParentId)}
              sizeOptions={specOptions(sizeParentId)}
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
              singleSku={saleValueIndex.size === 0}
            />
            {sizeChart.has !== false && (
              <div className="temu-field">
                <span>
                  尺码表模板 ID
                  <em className="temu-muted">
                    {sizeChart.has
                      ? "这个类目需要尺码表：填 Temu 后台建好的模板 ID，多个用逗号"
                      : "服装/鞋类必填，多个用逗号；需先在 Temu 后台建好（无关类目会自动隐藏）"}
                  </em>
                </span>
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
            className={`temu-sec${stepOf("sec-pack").done ? " is-done" : ""}${activeSectionId === "sec-pack" ? " is-active" : ""}`}
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
            <div className="temu-two-col">
              <label className="temu-field">
                <span>
                  发货时效（天）<em className="temu-required">*</em>
                  <em className="temu-muted">
                    承诺给 Temu 的备货发货时限。默认 7 天（POD 生产周期），填太短会被判延迟发货，
                    可改大（官方成功案例用的是 90 天）
                  </em>
                </span>
                <input
                  className="temu-input"
                  inputMode="numeric"
                  value={form.shipmentDays}
                  onChange={(event) => patch({ shipmentDays: event.target.value })}
                  placeholder={DEFAULT_SHIPMENT_DAYS}
                />
              </label>
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
            </div>
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
                className="temu-btn is-danger"
                disabled={submitDisabled}
                onClick={() => void publishGoods()}
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
                <li key={step.id} className={step.done ? "is-done" : ""}>
                  <button type="button" onClick={() => jumpTo(step.id)}>
                    <span className="temu-rail-dot">{step.done ? "✓" : index + 1}</span>
                    <span className="temu-rail-label">
                      {step.label}
                      {/* 没填完就直说差什么；填完了才显示这块包含哪些内容 */}
                      <em>{step.done || step.id === "sec-publish" ? step.hint : `还差：${step.missing.join("、")}`}</em>
                    </span>
                  </button>
                </li>
              ))}
            </ol>
            <dl className="temu-rail-stats">
              <div>
                <dt>变种</dt>
                <dd>{rows.length}</dd>
              </div>
              <div>
                <dt>轮播</dt>
                <dd>{carouselCount}</dd>
              </div>
              <div>
                <dt>申报价</dt>
                <dd>{firstPrice ? formatCents(firstPrice) : "—"}</dd>
              </div>
            </dl>
            <div className="temu-rail-actions">
              <button
                type="button"
                className="temu-btn temu-btn-sm"
                disabled={busy !== "" || !ready}
                onClick={() => void checkMandatory()}
              >
                {busy === "mandatory" ? "校验中…" : "检查必填"}
              </button>
              <button
                type="button"
                className="temu-btn temu-btn-primary temu-btn-sm"
                disabled={submitDisabled}
                onClick={() => void publishGoods()}
              >
                {publishing ? "提交中…" : "提交发布"}
              </button>
            </div>
            {publishResult ? (
              <p className="temu-rail-ok" role="status">
                ✓ 已提交到店铺
                {publishedGoodsId ? ` · 商品 id ${publishedGoodsId}` : ""}
                {publishResult.confirmed_by_lookup ? "（网络中断后反查到的）" : ""}
              </p>
            ) : null}
            {error ? (
              <p className="temu-rail-error" role="alert">
                {error}
              </p>
            ) : null}
            <p className="temu-rail-tip">提交会真的在店铺创建商品，不可撤销。填完到最下面「校验发布」提交。</p>
          </div>
        </aside>
      </div>
    </div>
  );
}
