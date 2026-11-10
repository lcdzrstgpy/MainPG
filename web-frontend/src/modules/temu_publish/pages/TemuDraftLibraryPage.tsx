import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { httpBlob } from "../../../transport/http/client";
import { temuPublishApi } from "../api/temuPublishApi";
import { TemuDraftBulkBar, type BulkField } from "../components/TemuDraftBulkBar";
import { localStore, parsePublishError } from "../data/localStore";
import type { DraftCategoryMemo, ProductTemplate, VariantTemplate } from "../data/localStore";
import { BatchManualConfig } from "../components/BatchManualConfig";
import { categorySearchTexts } from "../utils/categoryMatch";
import { categoryPathFromDto } from "../utils/categoryPath";
import { buildDraftForPublish } from "../data/batchPublish";
import { precheckBatchItem } from "../data/batchPrecheck";
import { autoFillSourceTexts } from "../data/attrAutofill";
import { estimateAttributePicks } from "../data/attrEstimate";
import {
  readAttrPicksHistory,
  readAttrPicksHistoryAt,
  readDraftAttrPrefills,
  saveDraftAttrPrefills,
  storageScope,
  type DraftAttrPrefill,
} from "../data/attrPicksStore";
import type { AttrPickMap } from "../data/attributeModel";
import type {
  TemuAttributes,
  TemuCategory,
  TemuDraftEdit,
  TemuDraftEntry,
  TemuDraftLibrary,
  TemuPublishPrefill,
} from "../types";
import { TEMU_LIBRARY_FOCUS_KEY } from "../types";
import "../styles/temuPublish.css";

type Filter = "all" | "ai" | "pod";

/** 「编辑」「分类」「备注」「隐藏」都只改本机：写 localStorage，刷新后仍生效，但不回写产品处理。 */
const DRAFT_EDITS_KEY = "temu_draft_edits_v1";
const DRAFT_CATS_KEY = "temu_draft_categories_v1";
const DRAFT_HIDDEN_KEY = "temu_draft_hidden_v1";
const DRAFT_REMARKS_KEY = "temu_draft_remarks_v1";

/**
 * 每页条数：30。
 *
 * 之前一次拉 80 条、前端切 50/页 —— 首屏要同时下载 50 张缩略图（每张都走鉴权 blob），
 * 滚动和翻页都在等图。改成服务端分页 30/页：只拉当前页、只解码当前页的图。
 */
const PAGE_SIZE = 30;
/**
 * 入草稿库时自动按标题匹配 Temu 类目：一次最多补这么多条、并发这么多路。
 * 限流是为了别在打开页面时同时打几十个 category.match（网关有每分钟限流）。
 */
const AUTO_LIMIT = 24;
/**
 * 入草稿库时就给每条草稿把产品属性估一遍。
 *
 * 一次最多处理这么多条：同一类目只打一次 attrs（后端还有 1 小时缓存），
 * 限流只是防止一次开页面把网关打满；剩下的下次进页面继续。
 */
const ATTR_ESTIMATE_LIMIT = 60;
const AUTO_CONCURRENCY = 3;
/** 搜索输入的防抖：边打边发请求会打断列表 */
const SEARCH_DEBOUNCE_MS = 300;

/** 本机存储按登录账号分 key：换账号不能看到上一个人的编辑/备注/隐藏/分类。 */
function scoped(key: string): string {
  return `${key}:${localStore.storageScope()}`;
}

function loadMap<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? (parsed as T) : fallback;
  } catch {
    return fallback;
  }
}

function saveMap(key: string, value: unknown) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 本机存储不可用时不阻塞操作
  }
}

function loadDraftEdits(): Record<string, TemuDraftEdit> {
  try {
    const raw = window.localStorage.getItem(scoped(DRAFT_EDITS_KEY));
    const parsed = raw ? JSON.parse(raw) : {};
    return parsed && typeof parsed === "object" ? (parsed as Record<string, TemuDraftEdit>) : {};
  } catch {
    return {};
  }
}

const EDIT_FIELDS: Array<{ key: keyof TemuDraftEdit; label: string; placeholder?: string }> = [
  { key: "title", label: "产品标题" },
  { key: "sku", label: "SKU 货号" },
  { key: "declared_price", label: "申报价格" },
  { key: "suggested_price", label: "建议售价" },
  { key: "stock", label: "库存" },
  { key: "length_cm", label: "长 (cm)" },
  { key: "width_cm", label: "宽 (cm)" },
  { key: "height_cm", label: "高 (cm)" },
  { key: "weight_g", label: "重量 (g)" },
];

/**
 * 这条草稿还缺什么才能直接发（发布 payload 里缺了会被 Temu 拒的字段）。
 *
 * 依据 docs/temu-goods-add-成功样例.json 的必填结构 + 实测清单：
 * 类目 / 申报价 / 货号 / 重量 / 三边尺寸 是硬性前置，库存 0 允许（商品建出来是不可售）。
 */
function draftMissingFields(
  entry: TemuDraftEntry,
  categoryMemo: Record<string, DraftCategoryMemo>,
): string[] {
  const missing: string[] = [];
  // 只认「发布页真选过」的 Temu 类目：草稿自带的 category_name 是自由文本，
  // 不是类目 id，发布时照样得选，所以不能算齐
  if (!categoryMemo[entry.id]?.label) missing.push("类目");
  const price = Number(entry.declared_price);
  if (!Number.isFinite(price) || price <= 0) missing.push("申报价");
  if (!entry.sku) missing.push("货号");
  const weight = Number(entry.weight_g);
  if (!Number.isFinite(weight) || weight <= 0) missing.push("重量");
  const dims = [entry.length_cm, entry.width_cm, entry.height_cm].map(Number);
  if (dims.some((value) => !Number.isFinite(value) || value <= 0)) missing.push("尺寸");
  return missing;
}

/** 尺寸/重量摘要：30×22×4 cm · 350 g，缺的用 — 占位 */
function specSummary(entry: TemuDraftEntry): string {
  const dim = [entry.length_cm, entry.width_cm, entry.height_cm]
    .map((value) => {
      const number = Number(value);
      return Number.isFinite(number) && number > 0 ? String(number) : "—";
    })
    .join("×");
  const weight = Number(entry.weight_g);
  const weightText = Number.isFinite(weight) && weight > 0 ? `${weight} g` : "— g";
  return `${dim} cm · ${weightText}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isPublicHttp(path: string): boolean {
  if (!/^https?:\/\//i.test(path)) return false;
  return !/^https?:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/i.test(path);
}

/** 本地媒体走带鉴权的 httpBlob，公网图片直接给 <img>。 */
/** 元素接近视口才返回 true：缩略图懒加载用，避免一屏几十张同时打鉴权请求。 */
function useNearViewport(margin = "300px"): [(node: Element | null) => void, boolean] {
  const [visible, setVisible] = useState(false);
  const observerRef = useRef<IntersectionObserver | null>(null);
  const ref = useCallback(
    (node: Element | null) => {
      observerRef.current?.disconnect();
      observerRef.current = null;
      if (!node) return;
      if (typeof IntersectionObserver === "undefined") {
        setVisible(true);
        return;
      }
      observerRef.current = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting)) {
            setVisible(true);
            observerRef.current?.disconnect();
            observerRef.current = null;
          }
        },
        { rootMargin: margin },
      );
      observerRef.current.observe(node);
    },
    [margin],
  );
  return [ref, visible];
}

function useDraftImage(path?: string): string {
  const [src, setSrc] = useState("");
  useEffect(() => {
    let alive = true;
    let objectUrl = "";
    setSrc("");
    if (!path) return;
    if (isPublicHttp(path)) {
      setSrc(path);
      return;
    }
    void httpBlob(path)
      .then((blob) => {
        if (!alive) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (alive) setSrc("");
      });
    return () => {
      alive = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);
  return src;
}

function DraftThumb({
  path,
  alt,
  onPreview,
}: {
  path: string;
  alt: string;
  onPreview?: (src: string, alt: string) => void;
}) {
  // 滚到附近才去取图；没有 path 直接占位
  const [thumbRef, inView] = useNearViewport();
  const src = useDraftImage(inView || !path ? path : "");
  // 还没滚到 / 还没取回图：占位框；真没有图才显示「无预览」
  if (!src)
    return path ? (
      <div className="temu-draft-thumb" ref={thumbRef} aria-busy="true" />
    ) : (
      <div className="temu-draft-thumb is-empty" ref={thumbRef}>
        无预览
      </div>
    );
  if (!onPreview)
    // alicdn 图床校验 Referer：带上本机地址会 403，缩略图整个空白
    return (
      <img
        className="temu-draft-thumb"
        src={src}
        alt={alt}
        loading="lazy"
        referrerPolicy="no-referrer"
        ref={thumbRef}
      />
    );
  return (
    <button
      type="button"
      className="temu-draft-thumb-button"
      onClick={() => onPreview(src, alt)}
      title="点击放大预览"
      ref={thumbRef}
    >
      <img
        className="temu-draft-thumb"
        src={src}
        alt={alt}
        loading="lazy"
        referrerPolicy="no-referrer"
      />
    </button>
  );
}

/**
 * 草稿里的金额都是「元」（POD 导出表、AI 结果口径一致），发布页用「分」。
 * 带入时统一 ×100，避免把 ¥150 当成 1.5 元发出去。
 */
/**
 * 价格文本：四舍五入到「分」（两位小数）后去掉多余的零。
 *
 * 草稿里的价可能带多位小数（导出表按成本算出来的），直接带进发布页会在提交时
 * 被「最多两位小数」拦下；这里先规整到分，用户看到的数和最终提交的一分不差。
 */
function moneyText(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const amount = Number(value);
  if (!Number.isFinite(amount) || amount <= 0) return "";
  return String(Math.round(amount * 100) / 100);
}

function numericText(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  return Number.isFinite(number) ? String(number) : "";
}

/** 金额（元）展示；空值显示破折号。 */
function yuanText(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const amount = Number(value);
  return Number.isFinite(amount) ? `¥${amount.toFixed(2)}` : "—";
}

/** 长×宽×高（cm），缺任一项就按已知的显示。 */
function dimensionText(entry: TemuDraftEntry): string {
  const parts = [entry.length_cm, entry.width_cm, entry.height_cm].map((item) => numericText(item) || "-");
  return parts.every((item) => item === "-") ? "—" : `${parts.join(" × ")} cm`;
}

/** 变种规格：颜色 / 尺码（名称取草稿里的变种属性名）。 */
function specText(entry: TemuDraftEntry): string {
  const pairs = [
    entry.spec_name_1 && entry.spec_value_1 ? `${entry.spec_name_1}：${entry.spec_value_1}` : "",
    entry.spec_name_2 && entry.spec_value_2 ? `${entry.spec_name_2}：${entry.spec_value_2}` : "",
  ].filter(Boolean);
  return pairs.length ? pairs.join(" · ") : "—";
}

/** 站点下面的第二行：优先显示上次选过的 Temu 类目，其次草稿自带类目，都没有才提示去发布页选。 */
function siteCategoryLabel(
  entry: TemuDraftEntry,
  memo: Record<string, DraftCategoryMemo>,
): string {
  const hit = memo[entry.id];
  if (hit?.label) return hit.auto ? `自动 · ${hit.label}` : hit.label;
  return entry.category_name || "发布页选类目";
}

/**
 * 类目这一格的三态：
 * - picked：发布页真选过 Temu 叶子类目，能直接发
 * - draft：只有草稿自带的分类（POD 模板里手填的文字、AI 采集的路径），
 *   它**不是** Temu 类目 ID，发布前还得去发布页选一次
 * - none：两样都没有，必须选
 */
/** 类目路径里的叶子 catId；还没选到叶子就返回 0。 */
function leafCatIdOf(path?: TemuCategory[]): number {
  const leaf = path?.[path.length - 1];
  return leaf?.isLeaf ? Number(leaf.catId) || 0 : 0;
}

/**
 * 从 goods.add 的返回里挑商品 id。
 *
 * 不同网关字段名不一样（goodsId / goods_id / productId / gid / id），还有把结果
 * 再包一层（result / data）的。后台 submissions.goods_id_from_result 是同一套
 * 顺序，这里只用于「批量完成」那行提示，取不到就不显示，别影响发布结果。
 */
/**
 * 批次运行标记（localStorage + 心跳）。
 *
 * 为什么不用组件 state：跑批途中切到别的模块，本页会被卸载（WorkspaceShell 是条件渲染），
 * 而旧批次还在后台一条条提交。切回来 state 归零 → 用户以为没在跑 → 再开一批 →
 * 同一批草稿被提交两次（接口非幂等，店铺里就多出重复商品）。
 * 存 localStorage 连刷新都能识别；2 分钟没心跳视为已死（刷新会真的杀掉 JS 循环）。
 */
const BATCH_RUN_KEY = "temu_publish_batch_running_at";
const BATCH_STOP_KEY = "temu_publish_batch_stop";
const BATCH_HEARTBEAT_TIMEOUT = 120_000;

function readBatchStore(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function writeBatchStore(key: string, value: string) {
  try {
    if (value) window.localStorage.setItem(key, value);
    else window.localStorage.removeItem(key);
  } catch {
    // 配额满 / 隐私模式不该让发布流程崩掉：标记失效最坏只是少一层并发保护
  }
}

/** 是否有别的批次还在提交（含切页 / 刷新前发起的那一批）。 */
function batchRunningElsewhere(): boolean {
  const at = Number(readBatchStore(BATCH_RUN_KEY) || 0);
  return at > 0 && Date.now() - at < BATCH_HEARTBEAT_TIMEOUT;
}

/** 用户点了「停止」：写标记，跑批的循环每轮读一次（跨实例也停得掉）。 */
function batchStopRequested(): boolean {
  return readBatchStore(BATCH_STOP_KEY) === "1";
}

function pickGoodsId(result: unknown): string {
  if (!result || typeof result !== "object") return "";
  const record = result as Record<string, unknown>;
  for (const key of ["goodsId", "goods_id", "productId", "product_id", "gid", "id"]) {
    const value = record[key];
    if (value !== undefined && value !== null && String(value).trim() !== "") return String(value);
  }
  for (const key of ["result", "data"]) {
    const nested = pickGoodsId(record[key]);
    if (nested) return nested;
  }
  return "";
}

/**
 * 属性预填里「填了几个」：估出来的 picks 里真正有值的项数。
 * 只用于界面显示，不参与任何判定。
 */
function estimatedPickCount(prefill?: DraftAttrPrefill, leafCatId?: number): number {
  if (!prefill) return 0;
  // 类目换过了但还没重估：旧数字属于另一个类目，不能拿来显示
  if (leafCatId && Number(prefill.catId) !== Number(leafCatId)) return 0;
  return Object.values(prefill.picks ?? {}).filter(
    (pick) => Number(pick?.vid ?? 0) > 0 || String(pick?.text ?? "").trim(),
  ).length;
}

function categoryState(
  entry: TemuDraftEntry,
  memo: Record<string, DraftCategoryMemo>,
): "picked" | "auto" | "draft" | "none" {
  const hit = memo[entry.id];
  if (hit?.label) return hit.auto ? "auto" : "picked";
  if (entry.category_name) return "draft";
  return "none";
}

/** POD 导出里申报价 / 重量 / 尺寸是模板占位值，界面上要提醒人工核对。 */
function buildNeedsReview(entry: TemuDraftEntry): string[] | undefined {
  return entry.source === "pod" ? ["申报价", "重量", "长宽高"] : undefined;
}

function entryPrefill(
  entry: TemuDraftEntry,
  memo?: Record<string, DraftCategoryMemo>,
  attrPrefills?: Record<string, DraftAttrPrefill>,
): TemuPublishPrefill {
  const weightG = numericText(entry.weight_g);
  const length = numericText(entry.length_cm);
  const width = numericText(entry.width_cm);
  const height = numericText(entry.height_cm);
  return {
    title: entry.title || entry.subtitle,
    images: entry.carousel_images?.length ? entry.carousel_images : entry.images.slice(0, 1),
    detailImages: entry.detail_images ?? [],
    sku: entry.sku || "",
    weightKg: weightG ? (Number(weightG) / 1000).toFixed(3) : "",
    lengthCm: length,
    widthCm: width,
    heightCm: height,
    sourceLabel: entry.source === "ai" ? "AI 处理" : "POD",
    sourceKind: entry.source,
    // 草稿和发布页现在都用「元」，原样带过去；换算成「分」是提交前的事
    supplierPrice: moneyText(entry.declared_price),
    suggestedPrice: moneyText(entry.suggested_price),
    // 库存原样带入（0 也是有效值：先建品后补库存）；只有真的没值才留空
    stock: numericText(entry.stock),
    specName1: entry.spec_name_1 || "",
    specValue1: entry.spec_value_1 || "",
    specName2: entry.spec_name_2 || "",
    specValue2: entry.spec_value_2 || "",
    // 全部规格值 + 每个 SKU 一行：发布页据此一次铺满变种表，
    // 不用用户再手填「颜色」和「尺码」，也不用再点一次生成
    specValues1: entry.spec_values_1 ?? [],
    specValues2: entry.spec_values_2 ?? [],
    variants: entry.variants ?? [],
    description: entry.description || "",
    sourceUrl: entry.source_url || "",
    // 带上 id 和上次选过的类目：发布页选完会回写，下次带入直接选中
    draftId: entry.id,
    categoryPath: memo?.[entry.id]?.path,
    // 入草稿库时按类目估好的属性：带进去直接铺上，发布页只补剩下没估到的
    attrPicks: attrPrefills?.[entry.id]?.picks,
    attrPicksCatId: attrPrefills?.[entry.id]?.catId,
    attrGuessedPids: attrPrefills?.[entry.id]?.guessedPids,
    // 进发布页要人工核对的字段：POD 导出里是模板占位值，参考价则是「算出来的」不是填的，
    // 产品属性则是按类目「估」的（第一个可用选项），更要人看
    needsReview: [...(buildNeedsReview(entry) ?? []), ...(attrPrefills?.[entry.id] ? ["产品属性（自动估）"] : [])],
  };
}

export function TemuDraftLibraryPage({
  onUseDraft,
  onCreateProduct,
  onOpenHistory,
}: {
  onUseDraft: (prefill: TemuPublishPrefill) => void;
  onCreateProduct?: () => void;
  /** 跳「发布历史」：发过的商品在草稿库里已经看不到了，得有个地方能回看 */
  onOpenHistory?: () => void;
}) {
  const [data, setData] = useState<TemuDraftLibrary | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  /** 只看「发布失败过」的草稿（按本机失败台账的标题匹配） */
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<{ src: string; alt: string } | null>(null);
  const [edits, setEdits] = useState<Record<string, TemuDraftEdit>>(loadDraftEdits);
  const [categories, setCategories] = useState<string[]>(() =>
    loadMap<string[]>(scoped(DRAFT_CATS_KEY), []),
  );
  const [catOf, setCatOf] = useState<Record<string, string>>(() =>
    loadMap<Record<string, string>>(scoped(DRAFT_CATS_KEY) + ":map", {}),
  );
  const [remarks, setRemarks] = useState<Record<string, string>>(() =>
    loadMap<Record<string, string>>(scoped(DRAFT_REMARKS_KEY), {}),
  );
  const [hidden, setHidden] = useState<string[]>(() => loadMap<string[]>(scoped(DRAFT_HIDDEN_KEY), []));
  /** 批量备注 / 批量删除的弹窗：选中哪些条、输入了什么 */
  const [remarkTargets, setRemarkTargets] = useState<string[]>([]);
  const [remarkText, setRemarkText] = useState("");
  const [deleteTargets, setDeleteTargets] = useState<string[]>([]);
  const [page, setPage] = useState(1);
  /**
   * 每个批次的起始偏移：后端每次只取每批的一页（20 个），
   * 点「加载更多」把它推进 20 —— 一个批次里放 500 个商品也能一个个翻出来，
   * 而每次请求的成本恒定（不会因为 500 而变慢）。
   */
  const [itemOffset, setItemOffset] = useState(0);
  /** 后续页追加进来的条目（第一页仍在 data.entries 里，避免重复拉） */
  const [deepEntries, setDeepEntries] = useState<TemuDraftEntry[]>([]);
  /** 输入框里的即时值；防抖 300ms 后才写进 query 触发请求 */
  const [queryInput, setQueryInput] = useState("");
  const [query, setQuery] = useState("");
  const [productTemplates, setProductTemplates] = useState<ProductTemplate[]>(() =>
    localStore.productTemplates(),
  );
  const [variantTemplates, setVariantTemplates] = useState<VariantTemplate[]>(() =>
    localStore.variantTemplates(),
  );
  const [picker, setPicker] = useState<"product" | "variant" | null>(null);
  // 点「用于发布」先弹基础信息确认，确认后才带进发布页
  const [confirmEntry, setConfirmEntry] = useState<TemuDraftEntry | null>(null);
  // 草稿 → 上次在发布页选过的 Temu 类目：进页面/切回来都重读，刚选完的能立刻显示
  const [categoryMemo, setCategoryMemo] = useState<Record<string, DraftCategoryMemo>>(() =>
    localStore.draftCategories(),
  );
  /** 草稿 → 入草稿库时按叶子类目估出来的产品属性（点「用于发布」直接带进发布页） */
  const [attrPrefills, setAttrPrefills] = useState<Record<string, DraftAttrPrefill>>(() =>
    readDraftAttrPrefills(storageScope()),
  );
  const [productTpl, setProductTpl] = useState("");
  const [variantTpl, setVariantTpl] = useState("");
  const [batchState, setBatchState] = useState<{ running: boolean; text: string }>({
    running: false,
    text: "",
  });
  // 批量发布弹窗：勾完直接发，模板在弹窗里选（不再要求先去菜单里「引用产品模板」）
  const [batchOpen, setBatchOpen] = useState(false);
  // 批量发布「现场配置」出来的临时产品模板（不落盘）：没选产品模板时用它
  const [manualTemplate, setManualTemplate] = useState<ProductTemplate | null>(null);
  // 批量发布里被「24 小时内重复提交」挡下的条目：不算失败，单独列出来
  const [batchSkipped, setBatchSkipped] = useState<string[]>([]);
  /**
   * 中止标志：批量发布是逐条提交（接口非幂等），发到一半发现模板/类目选错时
   * 必须能停 —— 否则 30 条错货全发出去，得去 Temu 后台一条条删。
   * 用 ref 而不是 state：循环里要读到最新值，state 会是闭包里的旧快照。
   */
  const batchAbortRef = useRef(false);
  /** 预检里有问题的条目：默认跳过（发出去也是白跑一趟），用户可改成照发 */
  const [skipBroken, setSkipBroken] = useState(true);

  const [editing, setEditing] = useState<TemuDraftEntry | null>(null);
  const [editForm, setEditForm] = useState<TemuDraftEdit>({});

  /**
   * 请求序号：搜索是边打边发的，两个请求可能乱序回来。
   * 只有「最后一次」的结果才允许写进状态，否则用户会看到上一秒那个关键字的列表
   * （或者刚清空的搜索又被旧结果填回来）。
   */
  const loadSeqRef = useRef(0);

  const load = async (
    refresh = false,
    override?: { page?: number; filter?: Filter; query?: string },
  ) => {
    const seq = ++loadSeqRef.current;
    setLoading(true);
    setError("");
    try {
      const nextFilter = override?.filter ?? filter;
      const result = await temuPublishApi.draftLibrary({
        page: override?.page ?? page,
        pageSize: PAGE_SIZE,
        source: nextFilter === "all" ? "" : nextFilter,
        q: override?.query ?? query,
        itemOffset,
        refresh,
      });
      if (seq !== loadSeqRef.current) return; // 已经有更新的请求了，丢掉这份结果
      setData(result);
      if (itemOffset > 0) {
        // 「加载更多」拿到的是每批的下一段，和第一页不重叠 → 追加而不是替换
        setDeepEntries((prev) => {
          const merged = new Map(prev.map((entry) => [entry.id, entry]));
          for (const entry of result.entries) merged.set(entry.id, entry);
          return [...merged.values()];
        });
      } else {
        setDeepEntries([]);
      }
      // 后端会把越界页码夹回最后一页，以前端返回值为准
      setPage(result.page);
    } catch (cause) {
      if (seq !== loadSeqRef.current) return;
      setError(errorMessage(cause));
    } finally {
      // 旧请求收尾时不能把 loading 关掉：新请求还在飞
      if (seq === loadSeqRef.current) setLoading(false);
    }
  };

  /**
   * 切页 / 换筛选 / 改关键字：把「加载更多」的进度一起归零。
   * 不归零的话，上一屏追加进来的条目会跟新结果混在一起（数量对不上、还删不掉）。
   */
  const goPage = (next: number) => {
    setItemOffset(0);
    setDeepEntries([]);
    setPage(next);
  };

  // 页码 / 页签 / 关键字任一变化都走一次请求：翻页只拉这一页，不再一次拉 80 条
  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, filter, query, itemOffset]);

  // 翻页、换页签、改关键字都清空勾选：批量操作必须只作用于「你现在看得见的这些」，
  // 否则跨页残留的勾选会让人以为在发 30 条、实际发了 60 条。
  useEffect(() => {
    setSelected(new Set());
  }, [page, filter, query]);

  // 搜索防抖：停止输入 300ms 后才发请求
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const next = queryInput.trim();
      if (next !== query) {
        setQuery(next);
        goPage(1);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [queryInput, query]);

  useEffect(() => {
    const sync = () => setCategoryMemo(localStore.draftCategories());
    window.addEventListener("focus", sync);
    document.addEventListener("visibilitychange", sync);
    return () => {
      window.removeEventListener("focus", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, []);

  // 本页数据：来源筛选与分页都已在后端完成，这里只合并「本机编辑」并剔除本机隐藏
  const entries = useMemo(
    () => [...(data?.entries ?? []), ...deepEntries]
      .filter((entry) => !hidden.includes(entry.id))
      .map((entry) => (edits[entry.id] ? { ...entry, ...edits[entry.id] } : entry)),
    [data, deepEntries, edits, hidden],
  );

  /**
   * 发布失败过的标题（本机台账）。
   *
   * 发成功的草稿会自动移出成品库，所以这里没有「已发布」筛选的意义；
   * 反过来「失败过」是真实存在的状态 —— 这类草稿要么改完重发、要么删掉，
   * 不该和人堆在一起靠翻找。标题匹配够用（失败记录存的就是标题）。
   */
  // 不用 memo 缓存：发布页新增失败记录后切回来要立刻看到标记（读 LS 很便宜）
  const failedTitles = new Set(
    localStore.failures().map((item) => item.draftTitle.trim()).filter(Boolean),
  );
  const failedCount = entries.filter((entry) => failedTitles.has(entry.title.trim())).length;
  const visibleEntries = onlyFailed
    ? entries.filter((entry) => failedTitles.has(entry.title.trim()))
    : entries;

  /**
   * 发布历史点「去修这条」过来：找到那条草稿自动打开（=整表预填进发布页）。
   * 只尝试一次：条目可能不在当前页，找不到就清标记，避免以后每次进库都触发。
   */
  const focusHandledRef = useRef(false);
  useEffect(() => {
    if (focusHandledRef.current || !entries.length) return;
    let focusId = "";
    try {
      focusId = window.sessionStorage.getItem(TEMU_LIBRARY_FOCUS_KEY) || "";
    } catch {
      focusId = "";
    }
    if (!focusId) return;
    focusHandledRef.current = true;
    try {
      window.sessionStorage.removeItem(TEMU_LIBRARY_FOCUS_KEY);
    } catch {
      // 清不掉也无所谓：标记只消费一次
    }
    const target = entries.find((entry) => entry.id === focusId);
    if (target) onUseDraft(entryPrefill(target, categoryMemo, attrPrefills));
  }, [entries, categoryMemo, attrPrefills, onUseDraft]);
  /**
   * 入草稿库时**按标题自动分好 Temu 类目**，省掉「先选一次类目」这一步。
   *
   * 只处理「还没有类目记忆」的草稿，一次最多 AUTO_LIMIT 条、并发 3：
   * - 结果写成 memo 但带 `auto: true`，UI 上标「自动」，人工选过的不会被覆盖
   * - 匹配失败/没有叶子路径就跳过，下次进页面再试（不记空值，否则永远不重试）
   * - 只是为了少点一次，**不代替确认**：类目错了发出去要下架重发，界面必须看得出来
   */
  const autoCategorizingRef = useRef(false);
  useEffect(() => {
    if (autoCategorizingRef.current || !entries.length) return;
    const pending = entries
      .filter((entry) => !categoryMemo[entry.id]?.label)
      .filter((entry) => (entry.title || entry.subtitle || "").trim().length >= 8)
      .slice(0, AUTO_LIMIT);
    if (!pending.length) return;
    autoCategorizingRef.current = true;
    let alive = true;
    void (async () => {
      const queue = [...pending];
      const worker = async () => {
        for (;;) {
          const entry = queue.shift();
          if (!entry || !alive) return;
          const text = categorySearchTexts(entry.title || entry.subtitle)[0];
          if (!text) continue;
          try {
            const result = await temuPublishApi.matchCategory(text, 100);
            const leafPath = (result.categoryPathDTOS ?? [])
              .map((dto) => categoryPathFromDto(dto))
              .find((path) => path.length > 0 && path[path.length - 1].isLeaf);
            if (!leafPath) continue;
            localStore.rememberDraftCategory(entry.id, leafPath, { auto: true });
            if (alive) setCategoryMemo(localStore.draftCategories());
          } catch {
            // 单条匹配失败不影响其它草稿；下次进页面会再试
          }
        }
      };
      await Promise.all(Array.from({ length: AUTO_CONCURRENCY }, () => worker()));
    })().finally(() => {
      if (alive) autoCategorizingRef.current = false;
    });
    return () => {
      alive = false;
      autoCategorizingRef.current = false;
    };
    // categoryMemo 故意不进依赖：写入后会触发自己，加进去会无限循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries]);

  /**
   * 入草稿库时把产品属性**估一遍**，点「用于发布」就不用再手点 8 个下拉。
   *
   * 估的口径见 `data/attrEstimate.ts`：同类目上次填的 > 标题点名 > 第一个可用选项。
   * 猜出来的值都会标 `guessed`，发布页会提示「按类目估的，请核对」。
   *
   * 限流：attrs 按 catId 缓存在内存里（同类目 N 条草稿只打一次网关），
   * 一次最多 ATTR_ESTIMATE_LIMIT 条，剩下的下次进页面继续。
   */
  const attrEstimateCacheRef = useRef(new Map<number, TemuAttributes>());
  /** 本次会话里拉失败的类目：不再重试（否则「一直失败 + 一直重跑」会把网关打爆） */
  const attrEstimateFailedRef = useRef(new Set<number>());
  const attrEstimatingRef = useRef(false);
  /**
   * 跑完一轮后自增，触发下一轮。
   *
   * 必须有这一下：自动分类是异步分批补类目的，估填启动时 memo 里可能只有一部分；
   * 若那一轮还在跑，新的 memo 进来会被 `attrEstimatingRef` 挡掉，之后再也没有
   * 触发点 —— 剩下的草稿就永远估不上。
   */
  const [estimateTick, setEstimateTick] = useState(0);
  useEffect(() => {
    if (attrEstimatingRef.current || !entries.length) return;
    const scope = storageScope();
    const pending = entries
      .map((entry) => ({ entry, leaf: leafCatIdOf(categoryMemo[entry.id]?.path) }))
      .filter(({ entry, leaf }) => {
        // 没有叶子类目就没法知道有哪些属性 —— 等自动分类把类目补上再估
        if (!leaf) return false;
        // 这个类目刚拉失败过：本次会话不再试，别把网关打爆
        if (attrEstimateFailedRef.current.has(leaf)) return false;
        const saved = attrPrefills[entry.id];
        if (!saved || Number(saved.catId) !== leaf) return true;
        // 同类目历史变新了（用户在发布页把属性改对了）→ 重估，
        // 否则其他草稿还留着上一次猜出来的值，用户会以为「改一次都跟着变」
        return Number(saved.historyAt ?? 0) !== readAttrPicksHistoryAt(scope, leaf);
      })
      .slice(0, ATTR_ESTIMATE_LIMIT);
    if (!pending.length) return;

    attrEstimatingRef.current = true;
    let alive = true;
    void (async () => {
      const next: Record<string, DraftAttrPrefill> = {};
      for (const { entry, leaf } of pending) {
        if (!alive) break;
        let attrs = attrEstimateCacheRef.current.get(leaf);
        if (!attrs) {
          try {
            attrs = await temuPublishApi.attributes(leaf);
            attrEstimateCacheRef.current.set(leaf, attrs);
          } catch {
            // 拉不到就不写结果（写了空值等于「这条永远不再试」），
            // 但本次会话先别重复打网关
            attrEstimateFailedRef.current.add(leaf);
            continue;
          }
        }
        try {
          const result = estimateAttributePicks({
            attributes: attrs,
            texts: autoFillSourceTexts({
              // subtitle 是任务名（"AI 视频"这类），不是商品描述，不进匹配文本
              title: entry.title || entry.subtitle,
              description: entry.description,
              categoryNames: [entry.category_name ?? ""],
            }),
            history: readAttrPicksHistory(scope, leaf),
          });
          next[entry.id] = {
            catId: leaf,
            picks: result.picks,
            at: Date.now(),
            guessed: result.guessed,
            guessedPids: result.items
              .filter((item) => item.source === "guess" || item.source === "number-guess")
              .map((item) => item.pid),
            // 记下这次用的是哪一版历史：历史变新了就说明该重估
            historyAt: readAttrPicksHistoryAt(scope, leaf),
          };
        } catch {
          // 单条估挂不能让整批停：这条下次进页面再试。
          // 也不能往外抛 —— 抛了会让 finally 的 tick 再跑一轮，异常再抛 → 死循环。
        }
      }
      if (alive && Object.keys(next).length) {
        saveDraftAttrPrefills(scope, next);
        setAttrPrefills((current) => ({ ...current, ...next }));
      }
    })().finally(() => {
      if (!alive) return;
      attrEstimatingRef.current = false;
      // 再跑一轮：这期间可能又有草稿被自动分类补上了类目。
      // 没有 pending 时下一轮会直接 return，不会自激。
      setEstimateTick((value) => value + 1);
    });
    return () => {
      alive = false;
      attrEstimatingRef.current = false;
    };
    // attrPrefills 故意不进依赖：写入后会触发自己，加进去会无限循环
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, categoryMemo, estimateTick]);

  const totalCount = data?.total ?? 0;
  const totalPages = Math.max(1, data?.pages ?? 1);
  const currentPage = Math.min(page, totalPages);

  useEffect(() => {
    if (!preview && !confirmEntry) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (preview) setPreview(null);
      if (confirmEntry) setConfirmEntry(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [preview, confirmEntry, batchOpen]);

  /**
   * 勾选的草稿如果都被匹配到同一个叶子类目，现场配置就直接预填它：
   * 用户只需填属性 + 选运费仓库，不用再搜一次类目。
   */
  const batchCommonCategory = useMemo(() => {
    const ids = Array.from(selected);
    if (!ids.length) return [];
    const paths = ids.map((id) => categoryMemo[id]?.path);
    // 必须每一条都匹配到了类目：只要有一条没有，整批套同一个类目就会把那条塞错类目
    if (paths.some((path) => !path?.length)) return [];
    const leafId = paths[0]?.[paths[0].length - 1]?.catId;
    if (!leafId) return [];
    const same = paths.every((path) => path?.[path.length - 1]?.catId === leafId);
    const first = paths[0];
    return same && first ? first : [];
  }, [selected, categoryMemo]);

  /**
   * 勾了多条却没法整批共用一套类目/属性时，得说清原因 ——
   * 否则用户只看到「类目是空的」，以为功能坏了，然后整批套错类目。
   */
  const batchCategoryConflict = useMemo(() => {
    // 勾 1 条也一样要说：没类目时批量发照样发不出去，别只对多选提示
    if (!selected.size || batchCommonCategory.length) return "";
    const missing = Array.from(selected).filter((id) => !categoryMemo[id]?.path?.length).length;
    if (missing) {
      return `有 ${missing} 条还没匹配到 Temu 类目，整批没法共用一套属性：先单独「用于发布」把它们选好类目，或分批发。`;
    }
    return "勾选的草稿不在同一个叶子类目（属性按类目定，不同类目不能共用），请分批发或在下面手动选一个类目。";
  }, [selected, categoryMemo, batchCommonCategory]);

  /**
   * 整批同一个叶子类目时，用第一条草稿估好的属性当批量配置的初值。
   * 类目不一致就不传：不同类目的 pid 完全不同，混着填只会塞错值。
   */
  const batchCommonPicks = useMemo(() => {
    if (!batchCommonCategory.length) return undefined;
    const leaf = batchCommonCategory[batchCommonCategory.length - 1]?.catId;
    const first = Array.from(selected)
      .map((id) => attrPrefills[id])
      .find((item) => item && Number(item.catId) === Number(leaf));
    return first?.picks;
  }, [batchCommonCategory, selected, attrPrefills]);

  const selectedIds = () => Array.from(selected);

  /** 「用于发布」二次确认：确认后才把这条草稿带进发布页。 */
  const confirmUseDraft = () => {
    if (!confirmEntry) return;
    onUseDraft(entryPrefill(confirmEntry, categoryMemo, attrPrefills));
    setConfirmEntry(null);
  };

  /** 勾选的草稿（批量发布/批量编辑都按这个口径，和表格里看到的行一致）。 */
  const batchTargets = useMemo(
    () => entries.filter((entry) => selected.has(entry.id)),
    [entries, selected],
  );

  /**
   * 切页 / 刷新前发起的那一批可能还在后台跑（本页组件被卸载过，state 已归零）。
   * 挂载时认领一下：把进度条和「停止」放回来；心跳停了就放开状态并刷新列表。
   */
  useEffect(() => {
    if (!batchRunningElsewhere()) return;
    setBatchState({ running: true, text: "另一处发起的批量发布还在提交…" });
    const timer = window.setInterval(() => {
      if (batchRunningElsewhere()) return;
      window.clearInterval(timer);
      setBatchState({
        running: false,
        text: "上一次批量发布已结束（在别的页签 / 切页前跑完的）",
      });
      void load();
    }, 2000);
    return () => window.clearInterval(timer);
    // 只在挂载时认领一次：本实例自己的进度由 runBatchPublish 维护
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /**
   * 发布前预检：把「这条注定发不出去」的原因先算出来，别等一条条跑完才失败。
   * 纯本地计算（不传图、不打网关），所以可以跟着模板切换实时刷新。
   */
  const batchPrecheck = useMemo(() => {
    const template = productTemplates.find((item) => item.name === productTpl) ?? manualTemplate;
    const variant = variantTemplates.find((item) => item.name === variantTpl) ?? null;
    if (!template) {
      return batchTargets.map((entry) => ({
        id: entry.id,
        title: entry.title || entry.sku || "（无标题）",
        blocked: ["还没选产品模板（或在下面现场配置完）"],
        warnings: [] as string[],
      }));
    }
    return batchTargets.map((entry) => {
      const precheck = precheckBatchItem(entryPrefill(entry, categoryMemo, attrPrefills), template, variant);
      return {
        id: entry.id,
        title: entry.title || entry.sku || "（无标题）",
        blocked: precheck.blocked,
        warnings: precheck.warnings,
      };
    });
  }, [
    batchTargets,
    productTemplates,
    productTpl,
    manualTemplate,
    variantTemplates,
    variantTpl,
    categoryMemo,
    attrPrefills,
  ]);

  const blockedPrecheck = batchPrecheck.filter((item) => item.blocked.length > 0);
  const warnPrecheck = batchPrecheck.filter((item) => !item.blocked.length && item.warnings.length > 0);
  /** 真正会被提交的条目：勾了「跳过发不出去的」就把阻塞项剔掉 */
  const runnableTargets = skipBroken
    ? batchTargets.filter((entry) => !blockedPrecheck.some((item) => item.id === entry.id))
    : batchTargets;

  /** 打开「批量发布」弹窗：勾选的草稿按所选模板逐条提交。 */
  const openBatchDialog = () => {
    if (batchState.running) return;
    // 切页前发起的那批可能还在跑（本实例的 state 不知道）：点按钮就拦，别等填完模板才说
    if (batchRunningElsewhere()) {
      setError(
        "上一批还在提交（可能是在别的页签 / 切页前发起的）：等它跑完、或点进度条上的「停止」再发 —— 并行提交会把同一批草稿重复上架",
      );
      return;
    }
    if (!selected.size) {
      setError("先勾选要发布的产品");
      return;
    }
    // 只有一个模板就直接选中，省一次点击；有多个则让用户自己挑
    if (!productTpl && productTemplates.length) setProductTpl(productTemplates[0].name);
    // 每次打开都重配：上一批的类目/属性留着，换一批货时很容易发错类目
    setManualTemplate(null);
    setBatchOpen(true);
  };

  /** 批量发布：勾选的草稿逐条套「产品模板 + 变种模板」→ 组装 → 提交，失败的进失败列表。 */
  const runBatchPublish = async () => {
    // 上一批还在跑（接口非幂等）：不允许再开一批并行提交。
    // 本实例未必看得见那批（切页会卸载组件、state 归零），所以还要查跨实例的运行标记。
    if (batchState.running) return;
    if (batchRunningElsewhere()) {
      setError(
        "上一批还在提交（可能是在别的页签 / 切页前发起的）：等它跑完、或点进度条上的「停止」再发 —— 并行提交会把同一批草稿重复上架",
      );
      return;
    }
    // 产品模板和「现场配置」二选一：现场配置出来的等价于一份临时模板
    const template = productTemplates.find((item) => item.name === productTpl) ?? manualTemplate;
    if (!template) {
      setError("先选一个产品模板，或在下面「现场配置」里把类目/属性/运费模板/仓库填完");
      return;
    }
    // 预检挡下的条目默认不跑：发出去也是白等一次失败（图都传完了才被拒）
    const targets = runnableTargets;
    const blockedCount = skipBroken ? batchTargets.length - targets.length : 0;
    if (!targets.length) {
      setError(
        batchTargets.length
          ? `勾选的 ${batchTargets.length} 条都有阻塞问题（见上面的预检），先按原因改掉或关掉「跳过发不出去的」`
          : "先勾选要发布的产品",
      );
      return;
    }
    setBatchOpen(false);

    const variant = variantTemplates.find((item) => item.name === variantTpl) ?? null;
    batchAbortRef.current = false;
    writeBatchStore(BATCH_STOP_KEY, "");
    writeBatchStore(BATCH_RUN_KEY, String(Date.now()));
    setBatchState({ running: true, text: `0/${targets.length}` });
    setBatchSkipped([]);
    let ok = 0;
    let fail = 0;
    let skipped = 0;
    // 批量提交的 warnings（库存 0、标题含中文、详情图尺寸兜底…）不能静默丢掉：
    // 一条一条弹窗太吵，这里汇总条数 + 头两条，完整内容让用户去发布页看。
    let warned = 0;
    const warnSamples: string[] = [];
    const publishedIds: string[] = [];
    /** 已处理掉的条目（发出去了 / 判定重复）：中止后靠它只摘这些，剩下的保留勾选 */
    const handledIds: string[] = [];
    let aborted = false;
    let crashed = "";
    try {
      for (const [index, entry] of targets.entries()) {
        // 用户点了「停止」：当前这条发完就收工，把已完成的数字如实报出去
        if (batchAbortRef.current || batchStopRequested()) {
          aborted = true;
          break;
        }
        // 心跳：别的页签 / 切页回来的实例靠它判断「这批还活着」
        writeBatchStore(BATCH_RUN_KEY, String(Date.now()));
        // 标题可能压根没有（草稿只存了 subtitle）：直接 .slice 会把整批炸在循环外
        const label = (entry.title || entry.sku || "（无标题）").slice(0, 16);
        setBatchState({
          running: true,
          text: `${index + 1}/${targets.length} · ${label}`,
        });
        try {
          const draft = await buildDraftForPublish(entryPrefill(entry, categoryMemo, attrPrefills), template, variant);
          const result = await temuPublishApi.publish(draft);
          const goodsId = pickGoodsId(result.result);
          if (goodsId) publishedIds.push(goodsId);
          if (result.warnings?.length) {
            warned += 1;
            for (const text of result.warnings) {
              if (warnSamples.length < 2 && !warnSamples.includes(text)) warnSamples.push(text);
            }
          }
          // 批量发布用的是产品模板的类目：记到这条草稿上，列表里就能看到类目路径
          localStore.rememberDraftCategory(entry.id, template.categoryPath);
          handledIds.push(entry.id);
          ok += 1;
        } catch (cause) {
          const reasonText = errorMessage(cause);
          // 台账挡下的重复提交：这条大概率已经在店铺里了，不算失败、也不进失败历史
          if (/重复提交/.test(reasonText)) {
            skipped += 1;
            handledIds.push(entry.id);
            setBatchSkipped((current) => [...current, entry.title || entry.sku || "（无标题）"]);
            continue;
          }
          fail += 1;
          const parsed = parsePublishError(cause);
          const price = entry.declared_price == null ? "" : String(entry.declared_price);
          localStore.addFailure({
            id: `${Date.now()}-${index}`,
            draftTitle: entry.title,
            image: entry.main_image,
            siteLabel: "美国站",
            categoryLabel: template.categoryPath.map((item) => item.catName).join(" > "),
            skuLabel: entry.sku || "",
            priceLabel: price ? `CNY ${price}` : "",
            at: new Date().toLocaleString("zh-CN"),
            reason: parsed.reason,
            traceId: parsed.traceId,
            // 带上重试要用的字段 + 批量用的类目，否则「重试」回填不出类目，只会再失败一次
            prefill: {
              ...entryPrefill(entry, categoryMemo, attrPrefills),
              categoryPath: categoryMemo?.[entry.id]?.path ?? template.categoryPath,
            },
          });
        }
      }
    } catch (cause) {
      // 循环本身炸了（localStorage 写满、草稿字段缺失…）：必须把 running 放掉。
      // 否则界面永远停在「批量发布中」，「批量发布」按钮和弹窗一起锁死，只能刷新页面。
      crashed = errorMessage(cause) || "未知错误";
    } finally {
      // 批次结束（跑完 / 中止 / 中断）都要把跨实例标记清掉，否则下次开批会被自己拦住
      writeBatchStore(BATCH_RUN_KEY, "");
      writeBatchStore(BATCH_STOP_KEY, "");
      const skippedText = skipped ? `，跳过 ${skipped} 条（24 小时内提交过）` : "";
      const warnText = warned
        ? `，${warned} 条有提示（${warnSamples.join("；")}${warned > 1 ? "…" : ""}）`
        : "";
      const idText = publishedIds.length
        ? `，商品 id ${publishedIds.slice(0, 3).join("、")}${publishedIds.length > 3 ? "…" : ""}`
        : "";
      // 中止 / 预检跳过 / 中途炸掉都要说清「还有多少条没发」：
      // 不然用户只看到"完成"，以为整批都处理过了
      const restCount = targets.length - ok - fail - skipped;
      const restText = restCount > 0 ? `，还剩 ${restCount} 条未发` : "";
      const blockedText = blockedCount ? `，预检跳过 ${blockedCount} 条` : "";
      setBatchState({
        running: false,
        text: crashed
          ? `中断：${crashed}（已成功 ${ok} 条，失败 ${fail} 条${restText}）`
          : `${aborted ? "已停止" : "完成"}：成功 ${ok} 条${idText}${skippedText}，失败 ${fail} 条${blockedText}${warnText}${aborted ? restText : ""}（失败原因见发布页列表）`,
      });
      if (aborted || crashed) {
        // 中止 / 中断时只摘掉「已经处理掉的」，没发的保留勾选：用户点一下就能接着发
        const done = new Set(handledIds);
        setSelected((current) => new Set([...current].filter((id) => !done.has(id))));
      } else if (ok > 0) {
        // 整批跑完：成功的已从草稿库发出去了，清空勾选避免误点又提交一遍
        setSelected(new Set());
      }
      setProductTemplates(localStore.productTemplates());
      void load();
    }
  };

  const applyBulkField = (field: BulkField, value: string) => {
    const ids = selectedIds();
    if (!ids.length) return;
    const next = { ...edits };
    ids.forEach((id) => {
      next[id] = { ...(next[id] || {}), [field]: value } as TemuDraftEdit;
    });
    setEdits(next);
    saveMap(scoped(DRAFT_EDITS_KEY), next);
  };

  // 备注 / 删除走应用内弹窗，不用 window.prompt / window.confirm：
  // 原生弹窗样式跟工作台完全不搭，而且它在 WebView 里可能被宿主禁用
  // （禁用时 prompt 返回 null、confirm 返回 false，功能静默失效）。
  const bulkRemark = () => {
    const ids = selectedIds();
    if (!ids.length) return;
    setRemarkTargets(ids);
    setRemarkText("");
  };

  const confirmBulkRemark = () => {
    const text = remarkText.trim();
    if (!text) {
      setRemarkTargets([]);
      return;
    }
    const next = { ...remarks };
    remarkTargets.forEach((id) => {
      next[id] = text;
    });
    setRemarks(next);
    saveMap(scoped(DRAFT_REMARKS_KEY), next);
    setRemarkTargets([]);
    setRemarkText("");
  };

  const bulkDelete = () => {
    const ids = selectedIds();
    if (!ids.length) return;
    setDeleteTargets(ids);
  };

  const confirmBulkDelete = () => {
    const next = Array.from(new Set([...hidden, ...deleteTargets]));
    setHidden(next);
    saveMap(scoped(DRAFT_HIDDEN_KEY), next);
    setSelected(new Set());
    setDeleteTargets([]);
  };

  const restoreHidden = () => {
    setHidden([]);
    saveMap(scoped(DRAFT_HIDDEN_KEY), []);
  };

  const moveCategory = (category: string) => {
    const ids = selectedIds();
    if (!ids.length) return;
    const next = { ...catOf };
    ids.forEach((id) => {
      if (category) next[id] = category;
      else delete next[id];
    });
    setCatOf(next);
    saveMap(scoped(DRAFT_CATS_KEY) + ":map", next);
  };

  const addCategory = (category: string) => {
    if (!category || categories.includes(category)) return;
    const next = [...categories, category];
    setCategories(next);
    saveMap(scoped(DRAFT_CATS_KEY), next);
  };

  const openEditor = (entry: TemuDraftEntry) => {
    setEditing(entry);
    setEditForm({
      title: entry.title || "",
      sku: entry.sku || "",
      declared_price: numericText(entry.declared_price),
      suggested_price: numericText(entry.suggested_price),
      // 库存原样带入（0 也是有效值：先建品后补库存）；只有真的没值才留空
    stock: numericText(entry.stock),
      length_cm: numericText(entry.length_cm),
      width_cm: numericText(entry.width_cm),
      height_cm: numericText(entry.height_cm),
      weight_g: numericText(entry.weight_g),
    });
  };

  const saveEdit = () => {
    if (!editing) return;
    const next = { ...edits, [editing.id]: editForm };
    setEdits(next);
    try {
      window.localStorage.setItem(scoped(DRAFT_EDITS_KEY), JSON.stringify(next));
    } catch {
      setError("本机保存失败（浏览器存储不可用），修改只在本次会话有效");
    }
    setEditing(null);
  };

  const resetEdit = () => {
    if (!editing) return;
    const next = { ...edits };
    delete next[editing.id];
    setEdits(next);
    try {
      window.localStorage.setItem(scoped(DRAFT_EDITS_KEY), JSON.stringify(next));
    } catch {
      // 忽略：重置只影响本机展示
    }
    setEditing(null);
  };

  const toggleSelect = (id: string) =>
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const allSelected = entries.length > 0 && entries.every((entry) => selected.has(entry.id));
  const toggleSelectAll = () =>
    setSelected(allSelected ? new Set() : new Set(entries.map((entry) => entry.id)));

  return (
    <div className="temu-publish-page">
      <header className="temu-library-head">
        <div className="temu-library-title">
          <p className="eyebrow">PRODUCT LIBRARY</p>
          <h1>成品库</h1>
          <p>AI 处理与 POD 定制的成品都在这里。成功发布过的会自动移出，可在「发布历史」回看。</p>
        </div>

        {/* 分类筛选做成统计卡：一眼看到各类有多少条，点一下就筛 */}
        <div className="temu-library-stats" role="tablist" aria-label="草稿来源筛选">
          {(
            [
              // counts 也要兜：接口回 `{}`（网关吞错 / 老版本没有这个字段）时
              // data 存在但 data.counts 是 undefined，`data?.counts.total` 照样抛，
              // 整个草稿库白屏。
              ["all", "全部", data?.counts?.total ?? 0, ""],
              ["ai", "AI 处理", data?.counts?.ai ?? 0, "ai"],
              ["pod", "POD", data?.counts?.pod ?? 0, "pod"],
            ] as Array<[Filter, string, number, string]>
          ).map(([key, label, count, tone]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={filter === key}
              className={`temu-library-stat${tone ? ` is-${tone}` : ""}${filter === key ? " is-active" : ""}`}
              onClick={() => {
                setFilter(key);
                goPage(1);
                // 切筛选清空勾选：勾选数与「当前页可见待发布条数」必须一致，
                // 否则批量发布只发可见的那些，界面上的数字会误导人
                setSelected(new Set());
              }}
            >
              <span>{label}</span>
              <strong>{count}</strong>
            </button>
          ))}
        </div>

        <button
          type="button"
          className={`temu-library-failed-toggle${onlyFailed ? " is-active" : ""}`}
          aria-pressed={onlyFailed}
          title="按本机失败记录筛选：只显示发布失败过的草稿"
          onClick={() => {
            setOnlyFailed((current) => !current);
            setSelected(new Set());
          }}
        >
          失败过 {failedCount}
        </button>

        <div className="temu-library-head-actions">
          {onOpenHistory ? (
            <button type="button" className="temu-btn" onClick={onOpenHistory}>
              发布历史
            </button>
          ) : null}
          <button type="button" className="temu-btn" onClick={() => void load(true)} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
          </button>
          <button type="button" className="temu-btn temu-btn-primary" onClick={() => onCreateProduct?.()}>
            创建产品
          </button>
        </div>
      </header>

      {error && (
        <div className="temu-alert" role="alert">
          <span>{error}</span>
          <button type="button" onClick={() => setError("")}>关闭</button>
        </div>
      )}

      {data && data.warnings.length > 0 && (
        <ul className="temu-warnings">
          {data.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}

      <section className="temu-card">
        <div className="temu-library-toolbar">
          <TemuDraftBulkBar
            selectedCount={selected.size}
            totalCount={totalCount}
            hiddenCount={hidden.length}
            categories={categories}
            onApplyField={applyBulkField}
            onRemark={bulkRemark}
            onDelete={bulkDelete}
            onMoveCategory={moveCategory}
            onAddCategory={addCategory}
            onRestoreHidden={restoreHidden}
            canBatchPublish={Boolean(productTpl)}
            batchRunning={batchState.running}
            templateCount={productTemplates.length + variantTemplates.length}
            onBatchPublish={openBatchDialog}
            onPickTemplate={setPicker}
            onClearSelection={() => setSelected(new Set())}
          />
          <label className="temu-draft-search">
            <input
              className="temu-input"
              value={queryInput}
              onChange={(event) => setQueryInput(event.target.value)}
              placeholder="搜标题 / 货号 / 类目"
              aria-label="搜索草稿"
            />
            {queryInput ? (
              <button
                type="button"
                className="temu-link"
                onClick={() => {
                  setQueryInput("");
                  setQuery("");
                  setPage(1);
                }}
              >
                清空
              </button>
            ) : null}
          </label>
        </div>


          {(batchState.running || batchState.text) && (
            <p className="temu-batch-progress">
              {batchState.running ? "批量发布中：" : ""}
              {batchState.text}
              {batchState.running ? (
                <button
                  type="button"
                  className="temu-batch-stop"
                  onClick={() => {
                    // 只置标志：当前这已经在提交的路上，硬中断会留下一半状态
                    batchAbortRef.current = true;
                    writeBatchStore(BATCH_STOP_KEY, "1");
                    setBatchState((current) => ({
                      running: current.running,
                      text: `${current.text} · 正在停止（当前这条发完就停）`,
                    }));
                  }}
                >
                  停止
                </button>
              ) : null}
            </p>
          )}

          {batchSkipped.length > 0 && (
            <p className="temu-muted">
              已跳过（24 小时内提交过，确认要重复上架请回发布页单条发）：
              {batchSkipped.slice(0, 3).map((title) => title.slice(0, 20)).join("、")}
              {batchSkipped.length > 3 ? ` 等 ${batchSkipped.length} 条` : ""}
            </p>
          )}



          {picker && (
            <div className="temu-template-picker">
              <div className="temu-template-picker-head">
                <strong>{picker === "product" ? "选择产品模板" : "选择变种模板"}</strong>
                <button type="button" className="temu-link" onClick={() => setPicker(null)}>
                  关闭
                </button>
              </div>
              {picker === "product" ? (
                productTemplates.length === 0 ? (
                  <p className="temu-muted">还没有产品模板：去「发布商品」页填好类目属性后点「存为产品模板」。</p>
                ) : (
                  productTemplates.map((item) => (
                    <button
                      key={item.name}
                      type="button"
                      className={productTpl === item.name ? "is-picked" : ""}
                      onClick={() => {
                        setProductTpl(item.name);
                        setPicker(null);
                      }}
                    >
                      {item.name}
                      <em>{item.savedAt}</em>
                    </button>
                  ))
                )
              ) : variantTemplates.length === 0 ? (
                <p className="temu-muted">还没有变种模板：去「发布商品」页填好颜色/尺码后点「存为变种模板」。</p>
              ) : (
                variantTemplates.map((item) => (
                  <button
                    key={item.name}
                    type="button"
                    className={variantTpl === item.name ? "is-picked" : ""}
                    onClick={() => {
                      setVariantTpl(item.name);
                      setPicker(null);
                    }}
                  >
                    {item.name}
                    <em>
                      {item.colorsText} × {item.sizesText}
                    </em>
                  </button>
                ))
              )}
            </div>
          )}

        {entries.length === 0 ? (
          // 首次进入（还没拿到过任何数据）先铺骨架：原来只有一行「正在读取草稿…」，
          // 聚合要跑 8 个任务的预览（约 1 秒），这行小字看着像没反应，
          // 而且 data 为 null 时 loading 初值还是 false，会先闪一下「暂无可用成品」。
          !data ? (
            <div className="temu-draft-skeleton" aria-busy="true" aria-live="polite">
              {[0, 1, 2, 3, 4, 5].map((row) => (
                <div className="temu-draft-skeleton-row" key={row}>
                  <span className="temu-skel is-thumb" />
                  <span className="temu-skel is-title" />
                  <span className="temu-skel is-mid" />
                  <span className="temu-skel is-mid is-short" />
                  <span className="temu-skel is-num" />
                </div>
              ))}
              <p className="temu-muted">正在聚合 AI 处理与 POD 成品…</p>
            </div>
          ) : (
            <p className="temu-empty">
              {query
                ? `没有匹配「${query}」的成品：换个关键字，或清空搜索看全部。`
                : hidden.length > 0
                  ? `当前页的 ${hidden.length} 条都被本机隐藏了，翻页或点「恢复隐藏」查看。`
                  : "暂无可用成品：先完成一批 AI 处理或 POD 生成。"}
            </p>
          )
        ) : (
          // 翻页时保留上一页内容并压暗，避免整块塌成「加载中」闪一下
          <div className={`temu-draft-table-wrap${loading ? " is-loading" : ""}`}>
            <table className="temu-draft-table">
              <thead>
                <tr>
                  <th className="col-check">
                    <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} aria-label="全选草稿" />
                  </th>
                  <th className="col-image">图片</th>
                  <th className="col-title">产品</th>
                  <th className="col-cat" title="Temu 类目：发布前必须选到叶子类目，否则发不出去">
                    Temu 类目
                  </th>
                  <th className="col-sku">货号</th>
                  <th
                    className="col-price"
                    title="单位是元。发布时系统自动换算成 Temu 要的「分」；没在预览里改过的价按导出表同一规则给出：成本 × 4，下限 ¥150"
                  >
                    申报价
                  </th>
                  <th className="col-stock" title="库存 0 也能建品，只是不可售">
                    库存
                  </th>
                  <th className="col-spec" title="发布 payload 里的长/宽/高（cm）与重量（g），缺了会被拒">
                    尺寸 · 重量
                  </th>
                  <th
                    className="col-ready"
                    title="草稿自身字段齐不齐；产品属性跟【能不能发】无关，一律要在发布页填"
                  >
                    草稿字段
                  </th>
                  <th className="col-time">更新</th>
                  <th className="col-actions">操作</th>
                </tr>
              </thead>
              <tbody>
                {visibleEntries.map((entry) => (
                  <tr key={entry.id} className={selected.has(entry.id) ? "is-selected" : ""}>
                    <td className="col-check">
                      <input
                        type="checkbox"
                        checked={selected.has(entry.id)}
                        onChange={() => toggleSelect(entry.id)}
                        aria-label={`选择 ${entry.title || "草稿"}`}
                      />
                    </td>
                    <td className="col-image">
                      <span className="temu-draft-thumb-wrap">
                        <DraftThumb
                          path={entry.main_image || entry.images[0]}
                          alt={entry.title || "草稿图"}
                          onPreview={(src, alt) => setPreview({ src, alt })}
                        />
                        {/* 角标「+N」= 除了这张缩略图还有 N 张（轮播 + 详情合集）。
                            鼠标悬浮缩略图会放大并隐藏它，所以这里补个 title 说明。 */}
                        {entry.images.length > 1 ? (
                          <span
                            className="temu-draft-more"
                            title={`共 ${entry.images.length} 张图（轮播 + 详情），另有 ${entry.images.length - 1} 张`}
                          >
                            +{entry.images.length - 1}
                          </span>
                        ) : null}
                      </span>
                    </td>
                    <td className="col-title">
                      <p className="temu-draft-title" title={entry.title}>
                        {entry.title || "（无标题）"}
                      </p>
                      <span className={`temu-draft-source ${entry.source}`}>
                        {entry.source === "ai" ? "AI 处理" : "POD"}
                      </span>
                      {failedTitles.has(entry.title.trim()) ? (
                        <span className="temu-draft-failed" title="这条草稿发布失败过：点「编辑」进去改完再发">
                          失败过
                        </span>
                      ) : null}
                      {entry.variant_count && entry.variant_count > 1 ? (
                        <span className="temu-draft-variants">· {entry.variant_count} 个变体</span>
                      ) : null}
                      {catOf[entry.id] ? (
                        <span className="temu-draft-cat">「{catOf[entry.id]}」</span>
                      ) : null}
                      {remarks[entry.id] ? (
                        <span className="temu-draft-remark">备注：{remarks[entry.id]}</span>
                      ) : null}
                    </td>
                    <td className="col-cat">
                      {(() => {
                        const state = categoryState(entry, categoryMemo);
                        const label = siteCategoryLabel(entry, categoryMemo);
                        return (
                          <>
                            <small
                              className={
                                state === "picked"
                                  ? "is-picked"
                                  : state === "auto"
                                    ? "is-auto"
                                    : state === "draft"
                                      ? "is-draft"
                                      : "is-missing"
                              }
                              title={
                                state === "picked"
                                  ? label
                                  : state === "auto"
                                    ? `按标题自动匹配的类目：${label}（只是大概方向，发布前建议确认）`
                                    : state === "draft"
                                      ? `草稿自带的分类：${label}（不是 Temu 类目，发布时还要选一次）`
                                      : "还没选 Temu 类目，发布前必须选到叶子类目"
                              }
                            >
                              {state === "picked"
                                ? label
                                : state === "auto"
                                  ? label
                                  : state === "draft"
                                    ? `草稿分类：${label}`
                                    : label}
                            </small>
                            <em className="temu-draft-site">美国站</em>
                          </>
                        );
                      })()}
                    </td>
                    <td className="col-sku">
                      {entry.sku ? (
                        <code>{entry.sku}</code>
                      ) : (
                        <span className="temu-draft-missing">未填</span>
                      )}
                    </td>
                    <td className="col-price">
                      {(() => {
                        const price = Number(entry.declared_price);
                        if (!Number.isFinite(price) || price <= 0) {
                          return <span className="temu-draft-missing">未填</span>;
                        }
                        return <strong>¥{price.toFixed(2)}</strong>;
                      })()}
                    </td>
                    <td className="col-stock">
                      {(() => {
                        const stock = Number(entry.stock);
                        if (!Number.isFinite(stock)) return <span className="temu-draft-missing">—</span>;
                        if (stock <= 0) {
                          return <span className="is-zero" title="0 库存仍可建品，但在店里是不可售状态">0</span>;
                        }
                        return <strong>{stock}</strong>;
                      })()}
                    </td>
                    <td className="col-spec">
                      <em>{specSummary(entry)}</em>
                    </td>
                    <td className="col-ready">
                      {(() => {
                        const missing = draftMissingFields(entry, categoryMemo);
                        const leafForEntry = leafCatIdOf(categoryMemo[entry.id]?.path);
                        const prefill = attrPrefills[entry.id];
                        const estimated = estimatedPickCount(prefill, leafForEntry);
                        const guessedInPrefill =
                          estimated && prefill ? Number(prefill.guessed ?? 0) : 0;
                        const pendingHint = !leafForEntry
                          ? "还没有 Temu 类目：点「用于发布」选一次，之后会自动按类目估属性"
                          : "属性正在按类目估（同类目只请求一次），稍等一下或刷新";
                        if (!missing.length) {
                          return (
                            <span
                              className="temu-ready-stack"
                              title={
                                estimated
                                  ? `产品属性已按类目估了 ${estimated} 项` +
                                    (guessedInPrefill
                                      ? `，其中 ${guessedInPrefill} 项是凭类目猜的（没有依据，取了第一个可用选项）`
                                      : "") +
                                    "；带入发布页时会直接填好，发布前请核对"
                                  : `草稿字段齐了，不用回上游补数据。${pendingHint}`
                              }
                            >
                              <span className="temu-ready is-ok">字段齐</span>
                              {estimated ? (
                                <span className="temu-ready is-estimated">属性已估 {estimated}</span>
                              ) : (
                                <span className="temu-ready is-pending">属性待填</span>
                              )}
                            </span>
                          );
                        }
                        return (
                          <span
                            className="temu-ready is-warn"
                            title={`缺：${missing.join("、")}。另外产品属性也要在发布页填`}
                          >
                            缺 {missing.join(" / ")}
                          </span>
                        );
                      })()}
                    </td>
                    <td className="col-time" title={entry.updated_at || undefined}>
                      {/* 只到分钟：列宽有限，年份对「哪批最新」没有帮助 */}
                      {(entry.updated_at || "").replace("T", " ").slice(5, 16) || "—"}
                    </td>
                    <td className="col-actions">
                      <button
                        type="button"
                        className="temu-btn"
                        title="带这条草稿进发布页，改完所有信息后再提交"
                        onClick={() => onUseDraft(entryPrefill(entry, categoryMemo, attrPrefills))}
                      >
                        编辑
                      </button>
                      <button
                        type="button"
                        className="temu-btn temu-btn-primary"
                        onClick={() => setConfirmEntry(entry)}
                      >
                        用于发布
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <p className="temu-muted temu-draft-meta is-foot">
          {query ? `匹配 ${totalCount} 条 · ` : ""}
          {/* 搜索是在库里全量筛的（不是只筛当前这几页），这点必须说清楚，
              否则用户会以为「搜不到 = 没有」 */}
          {query ? "已在全部商品里搜过" : "最近 8 个 AI 批次 / 6 个 POD 批次"}
          {productTpl ? ` · 产品模板：${productTpl}` : ""}
          {variantTpl ? ` · 变种模板：${variantTpl}` : ""}
        </p>

        {/* 每个批次每次只取一页（20 个）：批次里放了 500 个商品时，
            以前只显示前 20 个、也没有任何提示。这里明确告诉用户「加载到第几个」，
            点一下让后端把每批的偏移往后推一页。成本恒定，不会因为 500 而变慢。 */}
        {data?.has_more ? (
          <div className="temu-draft-load-more">
            <span className="temu-muted">
              每个批次已加载前 {itemOffset + 20} 个，还有更多
            </span>
            <button
              type="button"
              className="temu-btn temu-btn-sm"
              disabled={loading}
              onClick={() => setItemOffset((current) => current + 20)}
            >
              {loading ? "加载中…" : "加载更多"}
            </button>
          </div>
        ) : null}

        {totalCount > 0 && (
          <div className="temu-draft-pager">
            <span>
              第 {(currentPage - 1) * PAGE_SIZE + 1}-{Math.min(currentPage * PAGE_SIZE, totalCount)} 条，
              共 {totalCount} 条
              {hidden.length > 0 ? `（另有 ${hidden.length} 条本机隐藏）` : ""}
            </span>
            <div className="temu-draft-pager-ctrl">
              <button type="button" disabled={currentPage <= 1} onClick={() => goPage(currentPage - 1)}>
                ‹
              </button>
              <button
                type="button"
                disabled={currentPage >= totalPages}
                onClick={() => goPage(currentPage + 1)}
              >
                ›
              </button>
              <span>前往</span>
              <input
                type="number"
                min={1}
                max={totalPages}
                value={currentPage}
                onChange={(event) => {
                  const next = Number(event.target.value);
                  if (Number.isFinite(next)) goPage(Math.min(Math.max(1, next), totalPages));
                }}
              />
              <span>/ {totalPages} 页 · {PAGE_SIZE} 条/页</span>
            </div>
          </div>
        )}
      </section>


      {batchOpen &&
        createPortal(
          <div className="temu-edit-backdrop" onClick={() => setBatchOpen(false)}>
            <div
              className="temu-edit-dialog temu-batch-dialog"
              role="dialog"
              aria-modal="true"
              aria-label="批量发布"
              onClick={(event) => event.stopPropagation()}
            >
              <header className="temu-edit-head">
                <div>
                  <strong>批量发布</strong>
                  <span>已选 {batchTargets.length} 条，将逐条提交到店铺</span>
                </div>
                <button type="button" onClick={() => setBatchOpen(false)} aria-label="关闭">
                  ×
                </button>
              </header>

              <div className="temu-batch-body">
                <label className="temu-field">
                  <span>
                    产品模板<em className="temu-required">*</em>
                    <em className="temu-muted">类目、必填属性、运费模板、仓库都取自这里</em>
                  </span>
                  <select
                    className="temu-input"
                    value={productTpl}
                    onChange={(event) => setProductTpl(event.target.value)}
                  >
                    <option value="">
                      {productTemplates.length ? "请选择产品模板" : "还没有产品模板"}
                    </option>
                    {productTemplates.map((item) => (
                      <option key={item.name} value={item.name}>
                        {item.name}（{item.categoryPath.map((step) => step.catName).join(" > ")}）
                      </option>
                    ))}
                  </select>
                </label>

                <label className="temu-field">
                  <span>
                    变种模板<em className="temu-muted">可选：按模板的颜色/尺码铺变种</em>
                  </span>
                  <select
                    className="temu-input"
                    value={variantTpl}
                    onChange={(event) => setVariantTpl(event.target.value)}
                  >
                    <option value="">不套用变种模板</option>
                    {variantTemplates.map((item) => (
                      <option key={item.name} value={item.name}>
                        {item.name}（{item.colorsText} × {item.sizesText}）
                      </option>
                    ))}
                  </select>
                </label>

                {batchCategoryConflict && (
                  <p className="temu-confirm-warn">{batchCategoryConflict}</p>
                )}
                {batchCommonPicks && (
                  <p className="temu-confirm-warn">
                    下面的产品属性取自本批第一条草稿的自动估值，<strong>整批共用</strong> ——
                    发布前请核对，尤其是标着「估」的项。
                  </p>
                )}
                <BatchManualConfig
                  onChange={setManualTemplate}
                  initialPath={batchCommonCategory}
                  initialPicks={batchCommonPicks}
                />

                {/*
                  发布前预检：能本地算出来的失败原因先摆出来。
                  以前是点了就开始逐条传图，10 条里 6 条缺图，等它跑完才知道。
                */}
                <div className="temu-batch-precheck">
                  <div className="temu-batch-precheck-head">
                    <strong>发布前预检</strong>
                    {blockedPrecheck.length ? (
                      <span className="temu-batch-precheck-bad">
                        可发 {batchTargets.length - blockedPrecheck.length} 条 · 预计失败{" "}
                        {blockedPrecheck.length} 条
                      </span>
                    ) : (
                      <span className="temu-batch-precheck-ok">
                        可发 {batchTargets.length} 条 · 本地检查全过
                      </span>
                    )}
                    {blockedPrecheck.length ? (
                      <label className="temu-batch-precheck-skip">
                        <input
                          type="checkbox"
                          checked={skipBroken}
                          onChange={(event) => setSkipBroken(event.target.checked)}
                        />
                        跳过发不出去的 {blockedPrecheck.length} 条
                      </label>
                    ) : null}
                  </div>
                  {blockedPrecheck.length ? (
                    <ul className="temu-batch-precheck-list">
                      {blockedPrecheck.slice(0, 5).map((item) => (
                        <li key={item.id}>
                          <span>{item.title}</span>
                          <em>{item.blocked.join("；")}</em>
                        </li>
                      ))}
                      {blockedPrecheck.length > 5 ? (
                        <li className="is-more">… 还有 {blockedPrecheck.length - 5} 条同样发不出去</li>
                      ) : null}
                    </ul>
                  ) : null}
                  {warnPrecheck.length ? (
                    <p className="temu-batch-precheck-warn">
                      {warnPrecheck.length} 条有提醒（
                      {Array.from(new Set(warnPrecheck.flatMap((item) => item.warnings)))
                        .slice(0, 2)
                        .join("；")}
                      ）：能发，但结果可能不是你要的
                    </p>
                  ) : null}
                  {blockedPrecheck.length && !skipBroken ? (
                    <p className="temu-batch-precheck-warn">
                      已关掉跳过：这 {blockedPrecheck.length} 条会照样提交，大概率逐个失败并进失败列表。
                    </p>
                  ) : null}
                </div>

                <ul className="temu-batch-list">
                  {batchTargets.slice(0, 6).map((entry) => (
                    <li key={entry.id}>
                      <span>{entry.title || entry.subtitle || "（没有标题）"}</span>
                      <em>{entry.source === "ai" ? "AI" : "POD"}</em>
                    </li>
                  ))}
                  {batchTargets.length > 6 ? <li className="is-more">… 还有 {batchTargets.length - 6} 条</li> : null}
                </ul>

                {productTemplates.length === 0 && !manualTemplate ? (
                  <p className="temu-confirm-warn">
                    还没有产品模板：在上面「现场配置」里选一个类目、填完必填属性、选好运费模板和仓库，就能直接发这批。
                  </p>
                ) : (
                  <p className="temu-edit-note">
                    接口非幂等：重复提交会在店铺里多出商品；中途某条失败只跳过它，原因进「发布历史」。
                    同一条草稿 24 小时内重复提交会被拦一次，需要你二次确认。
                  </p>
                )}
              </div>

              <footer className="temu-edit-foot">
                <button type="button" className="temu-btn" onClick={() => setBatchOpen(false)}>
                  取消
                </button>
                <div>
                  {productTemplates.length === 0 ? (
                    <button
                      type="button"
                      className="temu-btn"
                      onClick={() => {
                        setBatchOpen(false);
                        onCreateProduct?.();
                      }}
                    >
                      去创建模板
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="temu-btn temu-btn-primary"
                    // 现场配置（manualTemplate）也是一份合法模板：只看 productTpl 的话，
                    // 一个模板都没存过的用户按提示填完现场配置，按钮还是灰的 —— 死路。
                    disabled={(!productTpl && !manualTemplate) || batchState.running || !runnableTargets.length}
                    onClick={() => void runBatchPublish()}
                  >
                    {batchState.running
                      ? "提交中…"
                      : runnableTargets.length
                        ? `开始发布（${runnableTargets.length} 条）`
                        : "没有可发的条目"}
                  </button>
                </div>
              </footer>
            </div>
          </div>,
          document.body,
        )}

      {remarkTargets.length > 0 &&
        createPortal(
          <div className="temu-edit-backdrop" onClick={() => setRemarkTargets([])}>
            <div
              className="temu-edit-dialog temu-confirm-dialog"
              role="dialog"
              aria-modal="true"
              aria-label="批量备注"
              onClick={(event) => event.stopPropagation()}
            >
              <header className="temu-edit-head">
                <div>
                  <strong>批量备注</strong>
                  <span>
                    给选中的 {remarkTargets.length} 条加一句备注 · 只在本机显示，不会提交到 Temu
                  </span>
                </div>
                <button type="button" onClick={() => setRemarkTargets([])} aria-label="关闭">
                  ×
                </button>
              </header>
              <label className="temu-field">
                <span>备注内容</span>
                <input
                  className="temu-input"
                  autoFocus
                  value={remarkText}
                  onChange={(event) => setRemarkText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") confirmBulkRemark();
                  }}
                  placeholder="例如：主图待补 / 价格待核 / 等供应商回复"
                />
              </label>
              <footer className="temu-edit-foot">
                <button type="button" className="temu-btn" onClick={() => setRemarkTargets([])}>
                  取消
                </button>
                <button
                  type="button"
                  className="temu-btn temu-btn-primary"
                  disabled={!remarkText.trim()}
                  onClick={confirmBulkRemark}
                >
                  保存备注
                </button>
              </footer>
            </div>
          </div>,
          document.body,
        )}

      {deleteTargets.length > 0 &&
        createPortal(
          <div className="temu-edit-backdrop" onClick={() => setDeleteTargets([])}>
            <div
              className="temu-edit-dialog temu-confirm-dialog"
              role="dialog"
              aria-modal="true"
              aria-label="批量删除"
              onClick={(event) => event.stopPropagation()}
            >
              <header className="temu-edit-head">
                <div>
                  <strong>从成品库移除</strong>
                  <span>只影响本机展示，Temu 店铺里的商品不受影响，随时可以恢复</span>
                </div>
                <button type="button" onClick={() => setDeleteTargets([])} aria-label="关闭">
                  ×
                </button>
              </header>
              <p className="temu-edit-note">
                选中的 {deleteTargets.length} 条会从成品库隐藏，右上角「已隐藏 N 条 · 恢复」可以全部还原。
              </p>
              <footer className="temu-edit-foot">
                <button type="button" className="temu-btn" onClick={() => setDeleteTargets([])}>
                  取消
                </button>
                <button type="button" className="temu-btn temu-btn-primary" onClick={confirmBulkDelete}>
                  隐藏这 {deleteTargets.length} 条
                </button>
              </footer>
            </div>
          </div>,
          document.body,
        )}

      {confirmEntry &&
        createPortal(
          <div className="temu-edit-backdrop" onClick={() => setConfirmEntry(null)}>
            <div
              className="temu-edit-dialog temu-confirm-dialog"
              role="dialog"
              aria-modal="true"
              aria-label="确认发布信息"
              onClick={(event) => event.stopPropagation()}
            >
              <header className="temu-edit-head">
                <div>
                  <strong>确认发布信息</strong>
                  <span>{confirmEntry.source === "ai" ? "AI 处理" : "POD"}</span>
                </div>
                <button type="button" onClick={() => setConfirmEntry(null)} aria-label="关闭">
                  ×
                </button>
              </header>

              <div className="temu-confirm-top">
                <span className="temu-confirm-thumb">
                  <DraftThumb
                    path={confirmEntry.main_image || confirmEntry.images[0]}
                    alt={confirmEntry.title || "草稿图"}
                    onPreview={(src, alt) => setPreview({ src, alt })}
                  />
                </span>
                <div className="temu-confirm-title">
                  <strong>{confirmEntry.title || confirmEntry.subtitle || "（没有标题）"}</strong>
                  <span>美国 · {siteCategoryLabel(confirmEntry, categoryMemo)}</span>
                </div>
              </div>

              <dl className="temu-confirm-grid">
                <div>
                  <dt>SKU 货号</dt>
                  <dd>{confirmEntry.sku || "—"}</dd>
                </div>
                <div>
                  <dt>申报价格</dt>
                  <dd className="is-strong">{yuanText(confirmEntry.declared_price)}</dd>
                </div>
                <div>
                  <dt>建议售价</dt>
                  <dd>{yuanText(confirmEntry.suggested_price)}</dd>
                </div>
                <div>
                  <dt>库存</dt>
                  <dd className={Number(confirmEntry.stock) > 0 ? undefined : "is-bad"}>
                    {Number(confirmEntry.stock) > 0 ? numericText(confirmEntry.stock) : "未填"}
                  </dd>
                </div>
                <div>
                  <dt>重量</dt>
                  <dd>{confirmEntry.weight_g ? `${numericText(confirmEntry.weight_g)} g` : "—"}</dd>
                </div>
                <div>
                  <dt>尺寸（cm）</dt>
                  <dd>{dimensionText(confirmEntry)}</dd>
                </div>
                <div>
                  <dt>变种</dt>
                  <dd>{specText(confirmEntry)}</dd>
                </div>
                <div>
                  <dt>图片</dt>
                  <dd className={confirmEntry.images.length ? undefined : "is-bad"}>
                    {confirmEntry.images.length ? `${confirmEntry.images.length} 张` : "没有图"}
                  </dd>
                </div>
              </dl>

              {Number(confirmEntry.stock) > 0 ? null : (
                <p className="temu-confirm-warn">
                  库存没有可用值：Temu 半托管要求 ≥1，进发布页后得先填上，否则提交会被拒。
                </p>
              )}

              <p className="temu-edit-note">
                确认后会带着这些信息进入「发布商品」页；还要选类目、核对类目属性，点那里的「提交发布」才真正上架。
              </p>
              <footer className="temu-edit-foot">
                <button type="button" className="temu-btn" onClick={() => setConfirmEntry(null)}>
                  再看看
                </button>
                <div>
                  <button
                    type="button"
                    className="temu-btn"
                    onClick={() => {
                      openEditor(confirmEntry);
                      setConfirmEntry(null);
                    }}
                  >
                    先改一下
                  </button>
                  <button type="button" className="temu-btn temu-btn-primary" onClick={confirmUseDraft}>
                    确定发布
                  </button>
                </div>
              </footer>
            </div>
          </div>,
          document.body,
        )}

      {editing &&
        createPortal(
          <div className="temu-edit-backdrop" onClick={() => setEditing(null)}>
            <div
              className="temu-edit-dialog"
              role="dialog"
              aria-modal="true"
              aria-label="编辑草稿"
              onClick={(event) => event.stopPropagation()}
            >
              <header className="temu-edit-head">
                <div>
                  <strong>编辑草稿</strong>
                  <span>{editing.source === "ai" ? "AI 处理" : "POD"}</span>
                </div>
                <button type="button" onClick={() => setEditing(null)} aria-label="关闭">
                  ×
                </button>
              </header>
              <div className="temu-edit-body">
                {EDIT_FIELDS.map((field) => (
                  <label
                    key={field.key}
                    className={field.key === "title" ? "is-wide" : undefined}
                  >
                    <span>{field.label}</span>
                    <input
                      value={editForm[field.key] ?? ""}
                      onChange={(event) =>
                        setEditForm((current) => ({ ...current, [field.key]: event.target.value }))
                      }
                    />
                  </label>
                ))}
              </div>
              <p className="temu-edit-note">
                修改只保存在本机，不会写回产品处理；点「用于发布」时会把改动一起带进发布页。
              </p>
              <footer className="temu-edit-foot">
                <button type="button" className="temu-btn" onClick={resetEdit} disabled={!edits[editing.id]}>
                  恢复默认
                </button>
                <div>
                  <button type="button" className="temu-btn" onClick={() => setEditing(null)}>
                    取消
                  </button>
                  <button type="button" className="temu-btn temu-btn-primary" onClick={saveEdit}>
                    保存
                  </button>
                </div>
              </footer>
            </div>
          </div>,
          document.body,
        )}

      {preview &&
        createPortal(
        <div
          className="temu-image-preview"
          role="dialog"
          aria-modal="true"
          aria-label="图片预览"
          onClick={() => setPreview(null)}
        >
          <img
            src={preview.src}
            alt={preview.alt}
            referrerPolicy="no-referrer"
            onClick={(event) => event.stopPropagation()}
          />
          <button
            type="button"
            className="temu-image-preview-close"
            onClick={() => setPreview(null)}
            aria-label="关闭预览"
          >
            ×
          </button>
        </div>,
          document.body,
        )}
    </div>
  );
}
