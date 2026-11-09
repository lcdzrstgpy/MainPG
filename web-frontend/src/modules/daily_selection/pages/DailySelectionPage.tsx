import { FormEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import {
  cancelCollectionTask,
  cancelSkuRepull,
  confirmCandidates,
  getCollectionRetryState,
  getCollectionTask,
  getSelectionRun,
  getSkuRepullState,
  listSelectionRuns,
  rejectCandidate,
  startCollectionTask,
  startSkuRepull,
} from "../api/dailySelectionApi";
import { getApiToken } from "../../../shared/api/apiClient";
import { toUserMessage } from "../../../transport/http/client";
import { ShopCollectionPanel, type ShopCollectionPanelHandle } from "../components/ShopCollectionPanel";
import { PluginOneboundCapturePanel, type PluginOneboundCapturePanelHandle } from "../components/PluginOneboundCapturePanel";
import { CollectionModeCard, type CollectionModeSlide } from "../components/CollectionModeCard";
import {
  CANDIDATE_SORT_OPTIONS,
  candidateAttributeEntries,
  candidateGalleryUrls,
  candidateSalesLabel,
  candidateSkuPriceRange,
  candidateTierEntries,
  candidateWarningTags,
  estimateQuote,
  galleryCount,
  isCandidateComplete,
  recommendationTags,
  sortCandidates,
  type CandidateSortKey,
} from "../data/candidatePresentation";

/** 三个采集入口的背景轮播素材。定向=合规商品图；整店/插件=脱敏后的平台页截图（店名与供应商已模糊）。 */
const COLLECTION_MODE_SLIDES: Record<"daily" | "shop" | "plugin", CollectionModeSlide[]> = {
  daily: [
    { src: "/assets/daily-selection/desk.jpg" },
    { src: "/assets/daily-selection/pet.jpg" },
    { src: "/assets/daily-selection/case.jpg" },
    { src: "/assets/daily-selection/garden.jpg" },
  ],
  shop: [
    { src: "/assets/daily-selection/store-1.jpg" },
    { src: "/assets/daily-selection/store-2.jpg" },
  ],
  plugin: [
    { src: "/assets/daily-selection/search-1.jpg" },
    { src: "/assets/daily-selection/search-2.jpg" },
  ],
};
import type {
  CollectionMode,
  CollectionPlatform,
  CollectionRetryState,
  DailySelectionCandidate,
  DailySelectionCriteria,
  DailySelectionRun,
  DailySelectionRunSummary,
  SelectionScope,
  SkuRepullState,
  SourceVariantRecord,
  TargetSite,
} from "../types";
import "../styles/daily-selection.css";

type Direction = {
  id: string;
  name: string;
  keywords: string[];
  attributes: string;
  price: [number, number];
  target: number;
  accent: string;
  site?: TargetSite;
  custom?: boolean;
  modified?: boolean;
};

const DEFAULT_DIRECTIONS: Direction[] = [
  { id: "home-storage", name: "家居收纳", keywords: ["盒", "架", "袋", "挂件"], attributes: "材质 / 尺寸 / 容量 / 承重或安装方式", price: [3, 35], target: 20, accent: "cyan" },
  { id: "kitchen", name: "厨房餐饮", keywords: ["收纳", "沥水", "保鲜"], attributes: "食品接触材质 / 尺寸 / 容量 / 耐热", price: [3, 40], target: 18, accent: "orange" },
  { id: "cleaning", name: "清洁日用", keywords: ["刷", "布", "刮", "袋"], attributes: "材质 / 纤维成分 / 适用场景 / 数量", price: [3, 38], target: 18, accent: "green" },
  { id: "bathroom", name: "卫浴用品", keywords: ["置物", "挂件", "过滤"], attributes: "安装方式 / 材质 / 尺寸", price: [3, 42], target: 18, accent: "cyan" },
  { id: "hardware", name: "家装五金", keywords: ["免打孔", "小五金"], attributes: "材质 / 尺寸 / 安装方式", price: [3, 45], target: 18, accent: "slate" },
  { id: "textile", name: "家纺软装", keywords: ["布艺", "罩", "垫"], attributes: "纤维成分 / 织造方式 / 克重 / 厚度", price: [4, 60], target: 16, accent: "cyan" },
  { id: "furniture-parts", name: "家具配件", keywords: ["脚垫", "拉手", "保护"], attributes: "材质 / 适配尺寸 / 安装方式", price: [3, 50], target: 16, accent: "orange" },
  { id: "pet", name: "宠物用品", keywords: ["非食品", "非药品"], attributes: "适用动物 / 材质 / 尺寸 / 重量", price: [4, 45], target: 18, accent: "green" },
  { id: "office", name: "办公文具", keywords: ["桌面", "文件", "标签"], attributes: "材料 / 尺寸 / 张数 / 件数", price: [2, 28], target: 16, accent: "blue" },
  { id: "craft", name: "学习手工", keywords: ["DIY", "贴纸", "包装"], attributes: "材料 / 尺寸 / 数量 / 主题 / 图案", price: [2, 35], target: 16, accent: "purple" },
  { id: "car", name: "车载用品", keywords: ["收纳", "挂钩", "清洁"], attributes: "安装位置 / 材质 / 尺寸", price: [5, 55], target: 16, accent: "slate" },
  { id: "garden", name: "园艺户外", keywords: ["园艺", "庭院", "小工具"], attributes: "材质 / 尺寸 / 安装方式", price: [4, 58], target: 16, accent: "orange" },
  { id: "sports", name: "运动户外", keywords: ["收纳", "辅助", "防护"], attributes: "材质 / 尺寸 / 适用运动", price: [4, 60], target: 16, accent: "green" },
  { id: "travel", name: "包袋出行", keywords: ["旅行包", "内胆", "配件"], attributes: "主体材质 / 里料 / 尺寸 / 容量", price: [5, 65], target: 16, accent: "blue" },
  { id: "apparel-parts", name: "服饰配件", keywords: ["护理", "发饰", "鞋配件"], attributes: "材质 / 尺寸 / 适用对象", price: [2, 40], target: 16, accent: "purple" },
  { id: "holiday", name: "节日派对", keywords: ["装饰", "包装", "场景"], attributes: "主题 / 场合 / 材质 / 尺寸 / 件数", price: [3, 50], target: 20, accent: "purple" },
  { id: "tools", name: "工具耗材", keywords: ["手动工具", "耗材"], attributes: "材质 / 规格 / 数量", price: [3, 55], target: 16, accent: "slate" },
  { id: "electronics", name: "电子配件", keywords: ["非带电", "小配件"], attributes: "适配对象 / 材质 / 尺寸", price: [3, 45], target: 16, accent: "blue" },
  { id: "beauty-storage", name: "美妆收纳", keywords: ["收纳工具", "不采化妆品"], attributes: "材质 / 尺寸 / 容量 / 闭合方式", price: [3, 50], target: 16, accent: "orange" },
  { id: "kids", name: "儿童周边低风险", keywords: ["做收纳", "学习周边"], attributes: "材质 / 适用年龄 / 尺寸", price: [3, 45], target: 12, accent: "slate" },
  { id: "games", name: "派对礼品", keywords: ["礼品袋", "卡", "丝带"], attributes: "材料 / 尺寸 / 数量", price: [2, 35], target: 16, accent: "purple" },
  { id: "business", name: "小型商用", keywords: ["标签", "展示", "包装"], attributes: "材质 / 尺寸 / 数量", price: [3, 60], target: 14, accent: "slate" },
  { id: "appliance-parts", name: "家电周边", keywords: ["罩", "架", "垫", "清洁"], attributes: "适配对象 / 材质 / 尺寸", price: [4, 60], target: 14, accent: "blue" },
  { id: "trend", name: "平台趋势补充", keywords: ["季节", "节日", "缺口"], attributes: "场景 / 材质 / 尺寸 / 数量", price: [3, 55], target: 20, accent: "orange" },
];

const CUSTOM_DIRECTIONS_KEY = "mainpg.daily-selection.custom-directions";
const UPDATED_DEFAULT_DIRECTIONS_KEY = "mainpg.daily-selection.updated-default-directions";
const REMOVED_DEFAULT_DIRECTIONS_KEY = "mainpg.daily-selection.removed-default-directions";
const SITE_LABELS: Record<TargetSite, string> = { US: "美国站", CO: "哥伦比亚站", EC: "厄瓜多尔站" };

function isStoredDirection(value: unknown): value is Direction {
  if (!value || typeof value !== "object") return false;
  const item = value as Partial<Direction>;
  return typeof item.id === "string"
    && typeof item.name === "string"
    && Array.isArray(item.keywords)
    && item.keywords.every((keyword) => typeof keyword === "string")
    && typeof item.attributes === "string"
    && Array.isArray(item.price)
    && item.price.length === 2
    && item.price.every((price) => typeof price === "number")
    && typeof item.target === "number"
    && typeof item.accent === "string";
}

function loadCustomDirections(): Direction[] {
  try {
    const saved = window.localStorage.getItem(CUSTOM_DIRECTIONS_KEY);
    if (!saved) return [];
    const parsed: unknown = JSON.parse(saved);
    return Array.isArray(parsed) ? parsed.filter(isStoredDirection) : [];
  } catch {
    return [];
  }
}

function loadUpdatedDefaultDirections(): Direction[] {
  try {
    const saved = window.localStorage.getItem(UPDATED_DEFAULT_DIRECTIONS_KEY);
    if (!saved) return [];
    const parsed: unknown = JSON.parse(saved);
    return Array.isArray(parsed)
      ? parsed.filter((item): item is Direction => isStoredDirection(item) && DEFAULT_DIRECTIONS.some((direction) => direction.id === item.id))
      : [];
  } catch {
    return [];
  }
}

function loadRemovedDefaultDirectionIds(): string[] {
  try {
    const saved = window.localStorage.getItem(REMOVED_DEFAULT_DIRECTIONS_KEY);
    if (!saved) return [];
    const parsed: unknown = JSON.parse(saved);
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

const STATUS_LABELS: Record<string, string> = {
  succeeded: "采集完成",
  completed: "采集完成",
  partial: "部分完成",
  cancelled: "已中断",
  failed: "采集失败",
  candidate: "候选",
  filtered: "已过滤",
  confirmed: "已入池",
  rejected: "已排除",
};

function numberOrUndefined(value: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/**
 * 采集筛选区间的自洽检查：与后端 DailySelectionCriteria 的模型级校验同一口径，
 * 提交前就拦掉不自洽的区间，避免请求打到后端才失败——后端的校验错误经异步采集
 * 任务回传，用户只会看到一条提示，定位不到具体字段。
 */
function collectionFilterError(input: {
  minPrice: string;
  maxPrice: string;
  minMoq: string;
  minSkuCount: string;
  maxSkuCount: string;
  minSkuPrice: string;
  maxSkuPrice: string;
  minSkuStock: string;
  maxSkuStock: string;
}): string | undefined {
  const orderedRanges = [
    ["最低价", "最高价", input.minPrice, input.maxPrice],
    ["SKU 最低价", "SKU 最高价", input.minSkuPrice, input.maxSkuPrice],
    ["SKU 数量下限", "SKU 数量上限", input.minSkuCount, input.maxSkuCount],
    ["SKU 最低库存", "SKU 最高库存", input.minSkuStock, input.maxSkuStock],
  ] as const;
  for (const [minLabel, maxLabel, rawMin, rawMax] of orderedRanges) {
    const min = numberOrUndefined(rawMin);
    const max = numberOrUndefined(rawMax);
    if (min !== undefined && max !== undefined && min > max) {
      return `${minLabel}不能高于${maxLabel}`;
    }
  }
  const positiveIntegers = [
    ["最小起订量", input.minMoq],
    ["SKU 数量下限", input.minSkuCount],
    ["SKU 最低库存", input.minSkuStock],
  ] as const;
  for (const [label, raw] of positiveIntegers) {
    const value = numberOrUndefined(raw);
    if (value !== undefined && (!Number.isInteger(value) || value < 1)) {
      return `${label}必须是大于 0 的整数`;
    }
  }
  const nonNegativePrices = [
    ["最低价", input.minPrice],
    ["最高价", input.maxPrice],
    ["SKU 最低价", input.minSkuPrice],
    ["SKU 最高价", input.maxSkuPrice],
  ] as const;
  for (const [label, raw] of nonNegativePrices) {
    const value = numberOrUndefined(raw);
    if (value !== undefined && value < 0) return `${label}不能为负数`;
  }
  return undefined;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

/**
 * 采集表单的首屏默认值：直接由当前方向预设带出，用户进来即可点「开始采集」，
 * 不必先补平台/范围/数量。与「点击方向卡」走同一套默认，避免两条入口行为不一致。
 */
function directionFormDefaults(direction: Direction | undefined) {
  return {
    platform: "1688" as CollectionPlatform,
    scope: "divergent" as SelectionScope,
    site: direction?.site ?? ("US" as TargetSite),
    keywords: direction ? direction.keywords.join("，") : "",
    minPrice: direction ? String(direction.price[0]) : "",
    maxPrice: direction ? String(direction.price[1]) : "",
    minMoq: "2",
    targetCount: direction ? String(direction.target) : "",
    excludeRisks: true,
  };
}

/** 把上下限压成一条摘要胶囊；两端都为空时返回 null，不占位置。 */
function rangeChip(prefix: string, min: string, max: string, suffix = ""): string | null {
  const lower = min.trim();
  const upper = max.trim();
  if (!lower && !upper) return null;
  if (lower && upper) return `${prefix}${lower}–${upper}${suffix}`;
  return lower ? `${prefix}≥${lower}${suffix}` : `${prefix}≤${upper}${suffix}`;
}

function formatRunDuration(createdAt: string, updatedAt: string): string {
  const start = new Date(createdAt).getTime();
  const end = new Date(updatedAt).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return "";
  const seconds = Math.round((end - start) / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return rest > 0 ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
}

function formatMoney(value: number | string | null): string {
  if (value === null || value === "") return "-";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `¥${parsed.toFixed(2)}` : `¥${value}`;
}

/** SKU 行优先用上游给的规格文案，缺失时把属性拼成一行展示。 */
function formatSkuSpec(record: SourceVariantRecord): string {
  if (record.spec_text) return record.spec_text;
  const attrs = Object.entries(record.attributes ?? {})
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" / ");
  return attrs || "—";
}

function hasIncompleteSkuCandidates(run: DailySelectionRun): boolean {
  // 存在 SKU 规格读取失败（0 条变种记录）的候选即需要后台补齐。
  return run.candidates.some((candidate) => (candidate.source_variant_records?.length ?? 0) === 0);
}

const IMAGE_MAX_RETRY = 2;
const IMAGE_RETRY_DELAY_MS = 1500;

/**
 * Renders a candidate image through the authenticated local proxy instead of
 * the raw CDN URL. alicdn rejects browser requests carrying a localhost
 * Referer (403), so images are fetched with the API token and shown as a
 * blob object URL. Transient load failures (network blips, proxy hiccups) are
 * retried a bounded number of times before the failure is shown, so a single
 * fluctuation does not leave the image permanently failed.
 */
function DailySelectionImage({ runId, url }: { runId: string; url: string }) {
  const [objectUrl, setObjectUrl] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");
  const attemptRef = useRef(0);

  useEffect(() => {
    let alive = true;
    let created: string | null = null;
    let retryTimer: number | null = null;
    attemptRef.current = 0;

    const scheduleRetry = () => {
      if (!alive) return;
      if (attemptRef.current >= IMAGE_MAX_RETRY) {
        setState("failed");
        return;
      }
      attemptRef.current += 1;
      retryTimer = window.setTimeout(load, IMAGE_RETRY_DELAY_MS);
    };

    const load = () => {
      if (!alive) return;
      setObjectUrl("");
      setState("loading");
      fetch(`/desktop/daily-selection/image?run_id=${encodeURIComponent(runId)}&url=${encodeURIComponent(url)}`, {
        headers: { Authorization: `Bearer ${getApiToken()}` },
      })
        .then((response) => {
          if (!alive) return null;
          if (response.ok) return response.blob();
          // 4xx（404 缺失 / 403 拒绝）是确定性结果，不重试；5xx 代表
          // 本地代理瞬时故障，交由重试恢复。
          if (response.status < 500) {
            setState("failed");
            return null;
          }
          throw new Error("image proxy temporarily unavailable");
        })
        .then((blob) => {
          if (!alive || !blob) return;
          created = URL.createObjectURL(blob);
          setObjectUrl(created);
          setState("ready");
        })
        .catch(() => scheduleRetry());
    };

    load();

    return () => {
      alive = false;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      if (created) URL.revokeObjectURL(created);
    };
  }, [runId, url]);

  if (state === "ready" && objectUrl) return <img src={objectUrl} alt="" loading="lazy" />;
  if (state === "failed") return <span>图片加载失败</span>;
  return <span className="image-loading">加载中…</span>;
}

type DailySelectionPageProps = {
  view?: "directions" | "collection";
  initialDirectionId?: string;
  onOpenCollection?: (directionId: string, directionName: string) => void;
  onOpenProductProcessingDraft?: (draftId: number) => void;
  topbarStatusVisible?: boolean;
  isActive?: boolean;
};

export function DailySelectionPage({ view = "directions", initialDirectionId, onOpenCollection, onOpenProductProcessingDraft, topbarStatusVisible = true, isActive = true }: DailySelectionPageProps) {
  // 视图支持内部轮转：主模块默认直接进采集视图，点「模板预设」切到预设页，
  // 在预设页点方向卡再回到采集视图，而不再新开独立面板。
  const [internalView, setInternalView] = useState<"directions" | "collection">(view);
  const collectionView = internalView === "collection";
  const [collectionWorkspaceMode, setCollectionWorkspaceMode] = useState<"daily" | "shop" | "plugin">("daily");
  const [customDirections, setCustomDirections] = useState<Direction[]>(loadCustomDirections);
  const [updatedDefaultDirections, setUpdatedDefaultDirections] = useState<Direction[]>(loadUpdatedDefaultDirections);
  const [removedDefaultDirectionIds, setRemovedDefaultDirectionIds] = useState<string[]>(loadRemovedDefaultDirectionIds);
  const directions = useMemo(() => {
    const updatesById = new Map(updatedDefaultDirections.map((direction) => [direction.id, direction]));
    const defaultDirections = DEFAULT_DIRECTIONS
      .filter((item) => !removedDefaultDirectionIds.includes(item.id))
      .map((item) => updatesById.get(item.id) ?? item);
    return [...defaultDirections, ...customDirections];
  }, [customDirections, removedDefaultDirectionIds, updatedDefaultDirections]);
  const validInitialDirection = directions.some((item) => item.id === initialDirectionId)
    ? initialDirectionId!
    : directions[0]?.id ?? "";
  const [selectedDirectionId, setSelectedDirectionId] = useState(validInitialDirection);
  const selectedDirection = useMemo(
    () => directions.find((item) => item.id === selectedDirectionId) ?? directions[0],
    [directions, selectedDirectionId],
  );
  // 首屏默认值跟随当前方向预设，平台/范围/数量已就绪，不必逐项补填。
  const initialFormDefaults = directionFormDefaults(selectedDirection);
  const [mode, setMode] = useState<CollectionMode>("keyword");
  const [platform, setPlatform] = useState<CollectionPlatform | "">(initialFormDefaults.platform);
  const [keywords, setKeywords] = useState(initialFormDefaults.keywords);
  const [referenceImageUrl, setReferenceImageUrl] = useState("");
  const [scope, setScope] = useState<SelectionScope | "">(initialFormDefaults.scope);
  const [site, setSite] = useState<TargetSite>(initialFormDefaults.site);
  const [minPrice, setMinPrice] = useState(initialFormDefaults.minPrice);
  const [maxPrice, setMaxPrice] = useState(initialFormDefaults.maxPrice);
  const [minMoq, setMinMoq] = useState(initialFormDefaults.minMoq);
  const [minSkuCount, setMinSkuCount] = useState("");
  const [maxSkuCount, setMaxSkuCount] = useState("");
  const [minSkuPrice, setMinSkuPrice] = useState("");
  const [maxSkuPrice, setMaxSkuPrice] = useState("");
  const [minSkuStock, setMinSkuStock] = useState("");
  const [maxSkuStock, setMaxSkuStock] = useState("");
  const [targetCount, setTargetCount] = useState(initialFormDefaults.targetCount);
  const [excludeRisks, setExcludeRisks] = useState(initialFormDefaults.excludeRisks);
  const [maxParallelCollect, setMaxParallelCollect] = useState(8);
  const [advancedCollectionOpen, setAdvancedCollectionOpen] = useState(false);
  const [runs, setRuns] = useState<DailySelectionRunSummary[]>([]);
  const [activeRun, setActiveRun] = useState<DailySelectionRun | null>(null);
  const [selectedCandidates, setSelectedCandidates] = useState<string[]>([]);
  const [skuFilterMin, setSkuFilterMin] = useState("");
  const [skuFilterMax, setSkuFilterMax] = useState("");
  const [appliedSkuFilter, setAppliedSkuFilter] = useState<{ min: number | null; max: number | null }>({ min: null, max: null });
  const [candidateSort, setCandidateSort] = useState<CandidateSortKey>("score");
  const [onlyCompleteCandidates, setOnlyCompleteCandidates] = useState(false);
  // 候选详情抽屉：记录候选 ID 而不是候选对象，避免批次刷新后抽屉里残留旧数据。
  const [detailCandidateId, setDetailCandidateId] = useState<string | null>(null);
  const [detailImageIndex, setDetailImageIndex] = useState(0);
  const [quoteExchangeRate, setQuoteExchangeRate] = useState("7.2");
  const [quoteSalePrice, setQuoteSalePrice] = useState("");
  const [quoteFreightCny, setQuoteFreightCny] = useState("");
  const [busy, setBusy] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [collectionProgress, setCollectionProgress] = useState(0);
  const [collectionProgressMessage, setCollectionProgressMessage] = useState("正在准备采集");
  const [collectionProgressCompleted, setCollectionProgressCompleted] = useState(0);
  const [collectionProgressTotal, setCollectionProgressTotal] = useState(0);
  const [collectionTaskId, setCollectionTaskId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(true);
  const [historyDrawerOpen, setHistoryDrawerOpen] = useState(false);
  const [shopBatchCount, setShopBatchCount] = useState(0);
  const [pluginBatchCount, setPluginBatchCount] = useState(0);
  const [skuRepull, setSkuRepull] = useState<SkuRepullState | null>(null);
  const [skuRepullBusy, setSkuRepullBusy] = useState(false);
  const [collectionRetry, setCollectionRetry] = useState<CollectionRetryState | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [noticeLeaving, setNoticeLeaving] = useState(false);
  const [topbarStatusTarget, setTopbarStatusTarget] = useState<HTMLElement | null>(null);
  const [presetDialogOpen, setPresetDialogOpen] = useState(false);
  const [presetName, setPresetName] = useState("");
  const [presetKeywords, setPresetKeywords] = useState("");
  const [presetAttributes, setPresetAttributes] = useState("");
  const [presetSite, setPresetSite] = useState<TargetSite>("US");
  const [presetMinPrice, setPresetMinPrice] = useState("3");
  const [presetMaxPrice, setPresetMaxPrice] = useState("50");
  const [presetTarget, setPresetTarget] = useState("16");
  const [presetAccent, setPresetAccent] = useState("cyan");
  const [presetError, setPresetError] = useState("");
  const [editingDirectionId, setEditingDirectionId] = useState<string | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [deleteMode, setDeleteMode] = useState(false);
  const [pendingDeleteDirection, setPendingDeleteDirection] = useState<Direction | null>(null);

  // 抽屉焦点管理：关闭时先把焦点归还给触发按钮，再应用 aria-hidden。
  // 否则 Chrome 会因 aria-hidden 落在仍持有焦点的后代上而阻止该属性并告警。
  const collectionSettingsLayerRef = useRef<HTMLDivElement | null>(null);
  const collectionSettingsTriggerRef = useRef<HTMLButtonElement | null>(null);
  const historyDrawerLayerRef = useRef<HTMLDivElement | null>(null);
  const historyDrawerTriggerRef = useRef<HTMLButtonElement | null>(null);
  const candidateDetailLayerRef = useRef<HTMLDivElement | null>(null);
  const shopCollectionPanelRef = useRef<ShopCollectionPanelHandle | null>(null);
  const pluginCapturePanelRef = useRef<PluginOneboundCapturePanelHandle | null>(null);

  const moveFocusOutOf = (layer: HTMLDivElement | null, fallbackTrigger: HTMLElement | null) => {
    const active = document.activeElement;
    if (!layer || !(active instanceof HTMLElement)) return;
    // 焦点本就不在抽屉里（如点击遮罩关掉）时不动它，避免抢走用户焦点。
    if (!layer.contains(active)) return;
    fallbackTrigger?.focus();
    // focus 失败（如触发按钮不可聚焦）时强制移除焦点，确保 aria-hidden 的元素内无聚焦后代。
    if (layer.contains(document.activeElement)) (document.activeElement as HTMLElement | null)?.blur();
  };

  // 关闭高级设置抽屉：先归还焦点，再切 aria-hidden（同步发生在提交前）。
  const closeCollectionSettings = useCallback(() => {
    moveFocusOutOf(collectionSettingsLayerRef.current, collectionSettingsTriggerRef.current);
    setAdvancedCollectionOpen(false);
  }, []);
  const closeHistoryDrawer = useCallback(() => {
    moveFocusOutOf(historyDrawerLayerRef.current, historyDrawerTriggerRef.current);
    setHistoryDrawerOpen(false);
  }, []);
  const closeCandidateDetail = useCallback(() => {
    moveFocusOutOf(candidateDetailLayerRef.current, null);
    setDetailCandidateId(null);
  }, []);

  // 关闭态加 inert：抽屉隐藏后的 280ms 过渡窗口内仍可被 Tab 聚焦，inert 一并阻止，
  // 同时这也是 Chrome 针对该 aria-hidden 告警给出的推荐做法。
  useEffect(() => {
    const layer = collectionSettingsLayerRef.current;
    if (layer) layer.inert = !advancedCollectionOpen;
  }, [advancedCollectionOpen]);
  useEffect(() => {
    const layer = historyDrawerLayerRef.current;
    if (layer) layer.inert = !historyDrawerOpen;
  }, [historyDrawerOpen]);
  useEffect(() => {
    const layer = candidateDetailLayerRef.current;
    if (layer) layer.inert = !detailCandidateId;
  }, [detailCandidateId]);

  useEffect(() => {
    if (isActive) return;
    // 页面切换到其他模块时若焦点仍在抽屉内，先把焦点移出再收抽屉，
    // 避免 aria-hidden 与聚焦后代冲突产生 Chrome 告警。
    moveFocusOutOf(collectionSettingsLayerRef.current, null);
    moveFocusOutOf(historyDrawerLayerRef.current, null);
    setAdvancedCollectionOpen(false);
    setHistoryDrawerOpen(false);
    setPresetDialogOpen(false);
    setPendingDeleteDirection(null);
    setDetailCandidateId(null);
  }, [isActive]);

  useEffect(() => {
    if (!advancedCollectionOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeCollectionSettings();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [advancedCollectionOpen, closeCollectionSettings]);

  useEffect(() => {
    if (!detailCandidateId) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeCandidateDetail();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [detailCandidateId, closeCandidateDetail]);

  const filteredCandidates = useMemo(() => {
    if (!activeRun) return [];
    const visible = activeRun.candidates.filter((candidate) => {
      const skuCount = candidate.source_variant_records?.length ?? 0;
      if (appliedSkuFilter.min !== null && skuCount < appliedSkuFilter.min) return false;
      if (appliedSkuFilter.max !== null && skuCount > appliedSkuFilter.max) return false;
      if (onlyCompleteCandidates && !isCandidateComplete(candidate)) return false;
      return true;
    });
    return sortCandidates(visible, candidateSort);
  }, [activeRun, appliedSkuFilter, candidateSort, onlyCompleteCandidates]);

  const detailCandidate = useMemo(() => {
    if (!activeRun || !detailCandidateId) return null;
    return activeRun.candidates.find((candidate) => candidate.candidate_id === detailCandidateId) ?? null;
  }, [activeRun, detailCandidateId]);
  const detailGallery = useMemo(
    () => (detailCandidate ? candidateGalleryUrls(detailCandidate) : []),
    [detailCandidate],
  );
  const detailQuote = useMemo(
    () => (detailCandidate
      ? estimateQuote({
        priceCny: detailCandidate.price_cny,
        freightCny: quoteFreightCny,
        exchangeRate: quoteExchangeRate,
        salePriceUsd: quoteSalePrice,
      })
      : null),
    [detailCandidate, quoteFreightCny, quoteExchangeRate, quoteSalePrice],
  );

  const detailTiers = useMemo(
    () => (detailCandidate ? candidateTierEntries(detailCandidate) : []),
    [detailCandidate],
  );
  const detailAttributes = useMemo(
    () => (detailCandidate ? candidateAttributeEntries(detailCandidate) : []),
    [detailCandidate],
  );
  const detailSkuPriceRange = useMemo(
    () => (detailCandidate ? candidateSkuPriceRange(detailCandidate) : null),
    [detailCandidate],
  );
  const detailWarnings = useMemo(
    () => (detailCandidate ? candidateWarningTags(detailCandidate) : []),
    [detailCandidate],
  );
  const detailRecommendations = useMemo(
    () => (detailCandidate ? recommendationTags(detailCandidate) : []),
    [detailCandidate],
  );
  // 详情长图可能很多，抽屉只预载前 16 张缩略图，避免一次打开抽屉触发几十个代理请求。
  const detailImageUrls = detailGallery.slice(0, 16);
  const activeDetailImage = detailImageUrls[Math.min(detailImageIndex, detailImageUrls.length - 1)] ?? null;
  const detailQuoteDisplay = detailQuote
    ? {
      landedUsd: `$${detailQuote.landedCostUsd.toFixed(2)}`,
      landedCny: formatMoney(detailQuote.landedCostCny),
      profitUsd: detailQuote.profitUsd === null
        ? "—"
        : `${detailQuote.profitUsd < 0 ? "-" : ""}$${Math.abs(detailQuote.profitUsd).toFixed(2)}`,
      marginRate: detailQuote.marginRate === null ? "—" : `${(detailQuote.marginRate * 100).toFixed(0)}%`,
      breakEvenUsd: `$${detailQuote.breakEvenUsd.toFixed(2)}`,
      isLoss: detailQuote.profitUsd !== null && detailQuote.profitUsd < 0,
    }
    : null;

  const openCandidateDetail = useCallback((candidate: DailySelectionCandidate) => {
    setDetailCandidateId(candidate.candidate_id);
    setDetailImageIndex(0);
    setQuoteSalePrice("");
    setQuoteFreightCny(
      candidate.freight_cny === null || candidate.freight_cny === undefined ? "" : String(candidate.freight_cny),
    );
  }, []);

  // 当前可勾选的候选（候选与已过滤候选均可勾选，采集到的商品可全部复核后入池）
  const selectableCandidates = filteredCandidates.filter(
    (candidate) => candidate.status === "candidate" || candidate.status === "filtered",
  );
  // 全选复选框状态：可勾选候选均被选中时为 true
  const allCandidatesSelected = selectableCandidates.length > 0
    && selectableCandidates.every((candidate) => selectedCandidates.includes(candidate.candidate_id));

  // 首屏「生效条件」摘要：只把真正会参与下一次采集的条件压成胶囊，
  // 用户不必展开高级设置就能确认价格/SKU/风险等限制是否生效。
  const collectionFilterChips = useMemo(() => {
    const chips: string[] = [];
    if (platform) chips.push(platform === "taobao" ? "淘宝" : "1688");
    if (scope) chips.push(scope === "exact" ? "精准匹配" : "发散相似款");
    chips.push(SITE_LABELS[site]);
    const price = rangeChip("¥", minPrice, maxPrice);
    if (price) chips.push(price);
    if (minMoq.trim()) chips.push(`起订 ≤ ${minMoq.trim()} 件`);
    const skuCount = rangeChip("SKU ", minSkuCount, maxSkuCount, " 个");
    if (skuCount) chips.push(skuCount);
    const skuPrice = rangeChip("SKU 价 ¥", minSkuPrice, maxSkuPrice);
    if (skuPrice) chips.push(skuPrice);
    const skuStock = rangeChip("SKU 库存 ", minSkuStock, maxSkuStock);
    if (skuStock) chips.push(skuStock);
    if (excludeRisks) chips.push("排除高风险");
    if (targetCount.trim()) chips.push(`目标 ${targetCount.trim()} 条`);
    return chips;
  }, [
    excludeRisks, maxPrice, maxSkuCount, maxSkuPrice, maxSkuStock,
    minMoq, minPrice, minSkuCount, minSkuPrice, minSkuStock,
    platform, scope, site, targetCount,
  ]);

  useEffect(() => {
    void refreshRuns();
  }, []);

  // 悬浮 toast：成功提示 2s、错误提示 3s 后自动消失；消失前 300ms 置淡出状态播动画。
  // 两个 effect 曾分别管理"清空"与"淡出"，前者先触发会清掉后者的定时器，淡出永远不播。
  useEffect(() => {
    if (!notice && !error) {
      setNoticeLeaving(false);
      return;
    }
    const duration = error ? 3000 : 2000;
    const leaveTimer = window.setTimeout(() => setNoticeLeaving(true), Math.max(0, duration - 300));
    const timer = window.setTimeout(() => {
      setNotice("");
      setError("");
    }, duration);
    return () => {
      window.clearTimeout(leaveTimer);
      window.clearTimeout(timer);
    };
  }, [notice, error]);

  useEffect(() => {
    setTopbarStatusTarget(document.getElementById("workspace-topbar-status"));
  }, []);

  useEffect(() => {
    if (!collecting || !collectionTaskId || !isActive) return;
    let stopped = false;
    let timer: number | null = null;

    const schedule = (delay: number) => {
      if (stopped || document.visibilityState !== "visible") return;
      timer = window.setTimeout(poll, delay);
    };

    const poll = async () => {
      if (stopped || document.visibilityState !== "visible") return;
      try {
        const task = await getCollectionTask(collectionTaskId);
        if (stopped) return;
        setCollectionProgress(task.progress);
        setCollectionProgressMessage(task.message);
        setCollectionProgressCompleted(task.completed);
        setCollectionProgressTotal(task.total);

        if (task.status === "completed") {
          if (!task.run_id) throw new Error("采集已完成，但未返回批次编号");
          const run = await getSelectionRun(task.run_id);
          if (stopped) return;
          setActiveRun(run);
          setSelectedCandidates([]);
          setCollecting(false);
          setCollectionTaskId(null);
          void listSelectionRuns().then(setRuns).catch(() => undefined);
          if (run.candidate_count === 0) {
            // 空采集：后台已按同一条件自动重采，同步一次状态并让轮询接管展示
            void syncCollectionRetry(run.run_id);
          } else {
            const metadata = run.metadata as {
              deduplicated?: { count?: number };
              filtered_summary?: { count?: number };
            };
            const deduplicated = metadata?.deduplicated;
            const dedupeText =
              deduplicated && deduplicated.count
                ? `，已剔除 ${deduplicated.count} 条已存在/已处理商品`
                : "";
            const filtered = metadata?.filtered_summary;
            const filteredText =
              filtered && filtered.count
                ? `，已过滤 ${filtered.count} 条（风险/重复/SKU 不符，不占用采集数量）`
                : "";
            setNotice(`批次 ${run.run_id.slice(0, 8)} 已返回 ${run.candidate_count} 个候选${dedupeText}${filteredText}`);
          }
          return;
        }
        if (task.status === "failed") {
          // 异步任务错误是后端原文（可能是英文，如 1688 collection provider is not configured），
          // 之前是原样渲染，这里过一遍统一翻译层。
          setError(task.error ? toUserMessage(task.error) : "采集请求失败");
          setCollecting(false);
          setCollectionTaskId(null);
          return;
        }
        if (task.status === "cancelled") {
          setCollecting(false);
          setCollectionTaskId(null);
          if (!task.run_id) {
            setNotice("采集已中断");
            return;
          }
          const run = await getSelectionRun(task.run_id);
          if (stopped) return;
          setActiveRun(run);
          setSelectedCandidates([]);
          setNotice(`采集已中断，已采集 ${run.candidate_count} 个候选`);
          void listSelectionRuns().then(setRuns).catch(() => undefined);
          return;
        }
        schedule(2000);
      } catch (requestError) {
        if (stopped) return;
        setError(requestError instanceof Error ? requestError.message : "采集进度读取失败");
        setCollecting(false);
        setCollectionTaskId(null);
      }
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState !== "visible") {
        if (timer !== null) window.clearTimeout(timer);
        timer = null;
        return;
      }
      schedule(0);
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    schedule(600);
    return () => {
      stopped = true;
      if (timer !== null) window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [collecting, collectionTaskId, isActive]);

  useEffect(() => {
    if (!historyDrawerOpen) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeHistoryDrawer();
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [historyDrawerOpen, closeHistoryDrawer]);

  useEffect(() => {
    window.localStorage.setItem(CUSTOM_DIRECTIONS_KEY, JSON.stringify(customDirections));
  }, [customDirections]);

  useEffect(() => {
    window.localStorage.setItem(UPDATED_DEFAULT_DIRECTIONS_KEY, JSON.stringify(updatedDefaultDirections));
  }, [updatedDefaultDirections]);

  useEffect(() => {
    window.localStorage.setItem(REMOVED_DEFAULT_DIRECTIONS_KEY, JSON.stringify(removedDefaultDirectionIds));
  }, [removedDefaultDirectionIds]);

  // 打开批次时同步一次 SKU 补齐状态（内存任务或历史轮次持久化）；
  // 若存在 SKU 未读取成功的候选且从未补齐过，自动在后台启动第一轮。
  useEffect(() => {
    if (!activeRun) {
      setSkuRepull(null);
      return;
    }
    let alive = true;
    getSkuRepullState(activeRun.run_id)
      .then((state) => {
        if (!alive) return;
        setSkuRepull(state);
        if (state.status === "idle" && hasIncompleteSkuCandidates(activeRun)) {
          startSkuRepull(activeRun.run_id)
            .then((started) => { if (alive) setSkuRepull(started); })
            .catch(() => undefined);
        }
      })
      .catch(() => { if (alive) setSkuRepull(null); });
    return () => { alive = false; };
  }, [activeRun]);

  // SKU 补齐轮询：任务运行中每秒刷新进度；完成后刷新批次候选显示最新 SKU 数。
  // 页签是隐藏保活（切页不卸载），必须自己判 isActive，否则切走后仍每 1.5s 打接口。
  useEffect(() => {
    if (!isActive || !activeRun || skuRepull?.status !== "running") return;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const state = await getSkuRepullState(activeRun.run_id);
        if (stopped) return;
        setSkuRepull(state);
        if (state.status !== "running") {
          const [refreshedRun, refreshedRuns] = await Promise.all([
            getSelectionRun(activeRun.run_id),
            listSelectionRuns(),
          ]);
          if (stopped) return;
          setActiveRun(refreshedRun);
          setRuns(refreshedRuns);
        }
      } catch (requestError) {
        if (stopped) return;
        setError(requestError instanceof Error ? requestError.message : "SKU 补齐进度读取失败");
      }
    }, 1500);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeRun, skuRepull?.status, isActive]);

  // 打开批次时同步一次空采集自动重试状态（内存任务或历史轮次持久化）；
  // 重试已在后台完成后自动刷新批次，避免停留在 0 条的旧数据。
  useEffect(() => {
    if (!activeRun) {
      setCollectionRetry(null);
      return;
    }
    if (activeRun.candidate_count > 0) {
      setCollectionRetry(null);
      return;
    }
    let alive = true;
    getCollectionRetryState(activeRun.run_id)
      .then((state) => {
        if (!alive) return;
        setCollectionRetry(state);
        if (state.status === "completed") {
          void (async () => {
            try {
              const run = await getSelectionRun(activeRun.run_id);
              if (!alive) return;
              setActiveRun(run);
              setSelectedCandidates([]);
              void listSelectionRuns().then(setRuns).catch(() => undefined);
            } catch {
              // 刷新失败保持当前展示
            }
          })();
        }
      })
      .catch(() => { if (alive) setCollectionRetry(null); });
    return () => { alive = false; };
  }, [activeRun]);

  // 空采集自动重试轮询：运行中每秒刷新状态；结束后刷新批次展示最新候选。
  // 同上：切页后必须停，否则后台持续轮询。
  useEffect(() => {
    if (!isActive || !activeRun || collectionRetry?.status !== "running") return;
    let stopped = false;
    const timer = window.setInterval(async () => {
      try {
        const state = await getCollectionRetryState(activeRun.run_id);
        if (stopped) return;
        setCollectionRetry(state);
        if (state.status !== "running") {
          const run = await getSelectionRun(activeRun.run_id);
          if (stopped) return;
          setActiveRun(run);
          setSelectedCandidates([]);
          void listSelectionRuns().then(setRuns).catch(() => undefined);
          if (state.status === "completed") {
            setNotice(`批次 ${activeRun.run_id.slice(0, 8)} 已返回 ${run.candidate_count} 个候选（自动重试成功）`);
          }
        }
      } catch (requestError) {
        if (stopped) return;
        setError(requestError instanceof Error ? requestError.message : "空采集重试进度读取失败");
      }
    }, 1500);
    return () => {
      stopped = true;
      window.clearInterval(timer);
    };
  }, [activeRun, collectionRetry?.status, isActive]);

  // 空采集：同步一次后台自动重试状态；已完成则直接刷新批次为最新候选。
  async function syncCollectionRetry(runId: string) {
    try {
      const state = await getCollectionRetryState(runId);
      setCollectionRetry(state);
      if (state.status === "completed") {
        const run = await getSelectionRun(runId);
        setActiveRun(run);
        setSelectedCandidates([]);
        void listSelectionRuns().then(setRuns).catch(() => undefined);
        setNotice(`批次 ${runId.slice(0, 8)} 已返回 ${run.candidate_count} 个候选（自动重试成功）`);
      } else if (state.status !== "running") {
        setNotice("本次未采集到商品，系统已自动重试仍无结果，可稍后手动重新采集");
      }
    } catch {
      setCollectionRetry(null);
    }
  }

  async function startSkuRepullNow() {
    if (!activeRun || skuRepull?.status === "running") return;
    setSkuRepullBusy(true);
    setError("");
    try {
      const state = await startSkuRepull(activeRun.run_id);
      setSkuRepull(state);
      setActiveRun(await getSelectionRun(activeRun.run_id));
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "SKU 补齐启动失败");
    } finally {
      setSkuRepullBusy(false);
    }
  }

  async function cancelSkuRepullNow() {
    if (!activeRun) return;
    setSkuRepullBusy(true);
    try {
      const state = await cancelSkuRepull(activeRun.run_id);
      setSkuRepull(state);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "SKU 补齐中断失败");
    } finally {
      setSkuRepullBusy(false);
    }
  }

  async function refreshRuns() {
    setHistoryBusy(true);
    try {
      setRuns(await listSelectionRuns());
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "批次加载失败");
    } finally {
      setHistoryBusy(false);
    }
  }

  function chooseDirection(direction: Direction) {
    const defaults = directionFormDefaults(direction);
    setSelectedDirectionId(direction.id);
    setPlatform(defaults.platform);
    setKeywords(defaults.keywords);
    setScope(defaults.scope);
    setSite(defaults.site);
    setMinPrice(defaults.minPrice);
    setMaxPrice(defaults.maxPrice);
    setMinMoq(defaults.minMoq);
    setTargetCount(defaults.targetCount);
    setExcludeRisks(defaults.excludeRisks);
    setMaxParallelCollect(8);
  }

  function resetPresetForm() {
    setPresetName("");
    setPresetKeywords("");
    setPresetAttributes("");
    setPresetSite("US");
    setPresetMinPrice("3");
    setPresetMaxPrice("50");
    setPresetTarget("16");
    setPresetAccent("cyan");
    setPresetError("");
    setEditingDirectionId(null);
  }

  function closePresetDialog() {
    setPresetDialogOpen(false);
    resetPresetForm();
  }

  function openCreatePreset() {
    resetPresetForm();
    setPresetDialogOpen(true);
  }

  function openEditPreset(direction: Direction) {
    setEditingDirectionId(direction.id);
    setPresetName(direction.name);
    setPresetKeywords(direction.keywords.join("，"));
    setPresetAttributes(direction.attributes);
    setPresetSite(direction.site ?? "US");
    setPresetMinPrice(String(direction.price[0]));
    setPresetMaxPrice(String(direction.price[1]));
    setPresetTarget(String(direction.target));
    setPresetAccent(direction.accent);
    setPresetError("");
    setEditMode(false);
    setPresetDialogOpen(true);
  }

  function savePreset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPresetError("");
    const normalizedKeywords = presetKeywords.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean).slice(0, 5);
    const parsedMinPrice = numberOrUndefined(presetMinPrice);
    const parsedMaxPrice = numberOrUndefined(presetMaxPrice);
    const parsedTarget = numberOrUndefined(presetTarget);
    if (!presetName.trim() || normalizedKeywords.length === 0 || !presetAttributes.trim()) {
      setPresetError("请填写预设名称、至少一个关键词和关注属性");
      return;
    }
    if (parsedMinPrice === undefined || parsedMaxPrice === undefined || parsedMinPrice > parsedMaxPrice) {
      setPresetError("请填写正确的价格范围");
      return;
    }
    if (parsedTarget === undefined || !Number.isInteger(parsedTarget) || parsedTarget < 1) {
      setPresetError("候选数量必须是正整数");
      return;
    }
    const existingDirection = editingDirectionId
      ? directions.find((direction) => direction.id === editingDirectionId)
      : undefined;
    const direction: Direction = {
      id: existingDirection?.id ?? `custom-${Date.now()}`,
      name: presetName.trim(),
      keywords: normalizedKeywords,
      attributes: presetAttributes.trim(),
      price: [parsedMinPrice, parsedMaxPrice],
      target: parsedTarget,
      accent: presetAccent,
      site: presetSite,
      custom: existingDirection?.custom ?? true,
      modified: existingDirection && !existingDirection.custom ? true : existingDirection?.modified,
    };
    if (existingDirection?.custom) {
      setCustomDirections((current) => current.map((item) => item.id === direction.id ? direction : item));
    } else if (existingDirection) {
      setUpdatedDefaultDirections((current) => [
        ...current.filter((item) => item.id !== direction.id),
        direction,
      ]);
    } else {
      setCustomDirections((current) => [...current, direction]);
    }
    chooseDirection(direction);
    closePresetDialog();
    setNotice(existingDirection ? `已更改预设“${direction.name}”` : `已添加自定义预设“${direction.name}”`);
  }

  function requestDirectionAction(direction: Direction) {
    if (editMode) {
      openEditPreset(direction);
      return;
    }
    if (deleteMode) {
      setPendingDeleteDirection(direction);
      return;
    }
    chooseDirection(direction);
    if (onOpenCollection) {
      onOpenCollection(direction.id, direction.name);
    } else {
      setInternalView("collection");
    }
  }

  function confirmDeletePreset() {
    if (!pendingDeleteDirection) return;
    if (directions.length <= 1) {
      setError("至少需要保留一个采集方向");
      setPendingDeleteDirection(null);
      return;
    }
    const removedName = pendingDeleteDirection.name;
    if (pendingDeleteDirection.custom) {
      setCustomDirections((current) => current.filter((item) => item.id !== pendingDeleteDirection.id));
    } else {
      setUpdatedDefaultDirections((current) => current.filter((item) => item.id !== pendingDeleteDirection.id));
      setRemovedDefaultDirectionIds((current) => [...new Set([...current, pendingDeleteDirection.id])]);
    }
    if (selectedDirection.id === pendingDeleteDirection.id) {
      const fallback = directions.find((item) => item.id !== pendingDeleteDirection.id)!;
      chooseDirection(fallback);
    }
    setPendingDeleteDirection(null);
    setNotice(`已删除预设“${removedName}”`);
  }

  function buildCriteria(): DailySelectionCriteria {
    if (!platform || !scope) {
      throw new Error("采集平台和选品范围不能为空");
    }
    const parsedTargetCount = numberOrUndefined(targetCount);
    if (parsedTargetCount === undefined || !Number.isInteger(parsedTargetCount) || parsedTargetCount < 1) {
      throw new Error("采集数量必须是正整数");
    }
    const normalizedKeywords = keywords.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean).slice(0, 5);
    // 只填分隔符时 trim 非空但归一化后为空，必须在提交前拦住，否则后端会直接拒绝。
    if (mode === "keyword" && normalizedKeywords.length === 0) {
      throw new Error("关键词不能只填分隔符，请至少填写一个有效关键词");
    }
    const criteria: DailySelectionCriteria = {
      keywords: normalizedKeywords,
      selection_scope: scope,
      category: selectedDirection.name,
      target_count: parsedTargetCount,
      detail_count: 50,
      exclude_risks: excludeRisks,
      site,
      max_parallel_collect: maxParallelCollect,
    };
    const parsedMinPrice = numberOrUndefined(minPrice);
    const parsedMaxPrice = numberOrUndefined(maxPrice);
    const parsedMinMoq = numberOrUndefined(minMoq);
    const parsedMinSkuCount = numberOrUndefined(minSkuCount);
    const parsedMaxSkuCount = numberOrUndefined(maxSkuCount);
    const parsedMinSkuPrice = numberOrUndefined(minSkuPrice);
    const parsedMaxSkuPrice = numberOrUndefined(maxSkuPrice);
    const parsedMinSkuStock = numberOrUndefined(minSkuStock);
    const parsedMaxSkuStock = numberOrUndefined(maxSkuStock);
    if (parsedMinPrice !== undefined) criteria.min_price = parsedMinPrice;
    if (parsedMaxPrice !== undefined) criteria.max_price = parsedMaxPrice;
    if (parsedMinMoq !== undefined) criteria.min_moq = parsedMinMoq;
    if (parsedMinSkuCount !== undefined) criteria.min_sku_count = parsedMinSkuCount;
    if (parsedMaxSkuCount !== undefined) criteria.max_sku_count = parsedMaxSkuCount;
    if (parsedMinSkuPrice !== undefined) criteria.min_sku_price = parsedMinSkuPrice;
    if (parsedMaxSkuPrice !== undefined) criteria.max_sku_price = parsedMaxSkuPrice;
    if (parsedMinSkuStock !== undefined) criteria.min_sku_stock = parsedMinSkuStock;
    if (parsedMaxSkuStock !== undefined) criteria.max_sku_stock = parsedMaxSkuStock;
    // 上下限写反属于用户常见笔误，前端先拦，避免等到后端校验才报错。
    if (parsedMinPrice !== undefined && parsedMaxPrice !== undefined && parsedMinPrice > parsedMaxPrice) {
      throw new Error("最低价格不能大于最高价格");
    }
    if (parsedMinSkuCount !== undefined && parsedMaxSkuCount !== undefined && parsedMinSkuCount > parsedMaxSkuCount) {
      throw new Error("SKU 规格数下限不能大于上限");
    }
    if (parsedMinSkuPrice !== undefined && parsedMaxSkuPrice !== undefined && parsedMinSkuPrice > parsedMaxSkuPrice) {
      throw new Error("SKU 最低价不能大于 SKU 最高价");
    }
    if (parsedMinSkuStock !== undefined && parsedMaxSkuStock !== undefined && parsedMinSkuStock > parsedMaxSkuStock) {
      throw new Error("SKU 库存下限不能大于上限");
    }

    criteria.collection_mode = mode;
    criteria.collection_platform = platform === "taobao" ? "taobao" : "1688";
    if (mode === "image") criteria.reference_image_url = referenceImageUrl.trim();
    return criteria;
  }

  function openCollectionSettings() {
    setHistoryDrawerOpen(false);
    setAdvancedCollectionOpen(true);
  }

  async function submitCollection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (!platform || !scope) {
      setError("请选择采集平台和选品范围");
      return;
    }
    const parsedTargetCount = numberOrUndefined(targetCount);
    if (parsedTargetCount === undefined || !Number.isInteger(parsedTargetCount) || parsedTargetCount < 1) {
      setError("请填写正确的采集数量");
      return;
    }
    if (parsedTargetCount > 200) {
      setError("采集数量最多 200 条");
      return;
    }
    if (mode === "keyword" && !keywords.trim()) {
      setError("请至少填写一个关键词");
      return;
    }
    if (mode === "image" && !referenceImageUrl.trim()) {
      setError("请填写可公开访问的参考图 URL");
      return;
    }
    const filterError = collectionFilterError({
      minPrice,
      maxPrice,
      minMoq,
      minSkuCount,
      maxSkuCount,
      minSkuPrice,
      maxSkuPrice,
      minSkuStock,
      maxSkuStock,
    });
    if (filterError) {
      setError(`筛选条件有误：${filterError}`);
      return;
    }

    setBusy(true);
    setCollecting(true);
    setCollectionProgress(0);
    setCollectionProgressMessage("正在创建采集任务");
    setCollectionProgressCompleted(0);
    setCollectionProgressTotal(0);
    try {
      const criteria = buildCriteria();
      const task = await startCollectionTask(criteria);
      setCollectionTaskId(task.task_id);
      setCollectionProgress(task.progress);
      setCollectionProgressMessage(task.message);
    } catch (requestError) {
      setCollectionProgress(0);
      setCollectionTaskId(null);
      setCollecting(false);
      setError(requestError instanceof Error ? requestError.message : "采集请求失败");
    } finally {
      setBusy(false);
    }
  }

  async function cancelCollection() {
    if (!collectionTaskId) return;
    setError("");
    setCancelling(true);
    try {
      const task = await cancelCollectionTask(collectionTaskId);
      setCollectionProgressMessage(task.message || "正在中断采集");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "中断请求发送失败");
    } finally {
      setCancelling(false);
    }
  }

  async function openRun(runId: string) {
    setError("");
    setBusy(true);
    try {
      const run = await getSelectionRun(runId);
      setActiveRun(run);
      setSelectedCandidates([]);
      document.querySelector(".daily-results")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "批次读取失败");
    } finally {
      setBusy(false);
    }
  }

  function toggleSelectAllCandidates() {
    if (!activeRun) return;
    setSelectedCandidates((current) => {
      const selectable = filteredCandidates
        .filter((candidate) => candidate.status === "candidate" || candidate.status === "filtered")
        .map((candidate) => candidate.candidate_id);
      const allSelected = selectable.length > 0 && selectable.every((id) => current.includes(id));
      if (allSelected) {
        // 已全选 → 取消全选
        return current.filter((id) => !selectable.includes(id));
      }
      // 未全选 → 全选（保留已有的其他选择）
      return [...new Set([...current, ...selectable])];
    });
  }

  // 「全选」按钮：始终选中全部可勾选候选（含已过滤，用户可人工复核后再入池）
  function selectAllCandidates() {
    if (!activeRun) return;
    setSelectedCandidates(filteredCandidates
      .filter((candidate) => candidate.status === "candidate" || candidate.status === "filtered")
      .map((candidate) => candidate.candidate_id));
  }

  function toggleCandidate(candidateId: string) {
    setSelectedCandidates((current) => current.includes(candidateId)
      ? current.filter((id) => id !== candidateId)
      : [...current, candidateId]);
  }

  async function confirmSelected() {
    if (!activeRun || selectedCandidates.length === 0) return;
    setBusy(true);
    setError("");
    try {
      const result = await confirmCandidates(activeRun.run_id, selectedCandidates);
      const replayText = result.replayed_count > 0
        ? `；重复请求 ${result.replayed_count} 个未重复建池`
        : "";
      const pendingText = result.pending_count > 0
        ? `；等待产品处理 ${result.pending_count} 个`
        : "";
      setNotice(`本次选择 ${result.selected_count} 个，新入池 ${result.created_count} 个${replayText}${pendingText}`);
      setActiveRun(await getSelectionRun(activeRun.run_id));
      setSelectedCandidates([]);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "确认入池失败");
    } finally {
      setBusy(false);
    }
  }

  async function reject(candidate: DailySelectionCandidate) {
    if (!activeRun) return;
    setBusy(true);
    setError("");
    try {
      await rejectCandidate(activeRun.run_id, candidate.candidate_id, "前端人工排除");
      setActiveRun(await getSelectionRun(activeRun.run_id));
      setSelectedCandidates((current) => current.filter((id) => id !== candidate.candidate_id));
      setNotice("候选已排除，反馈已保存");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "保存反馈失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`daily-selection-page ${collectionView ? "is-collection-view" : ""}`}>
      {topbarStatusVisible && topbarStatusTarget && (error || notice || collecting) && createPortal(
        collecting ? (
          <div className="daily-topbar-status is-progress" role="status">
            <span className="daily-topbar-status-icon" aria-hidden="true">↻</span>
            <strong>
              {collectionProgressMessage}
              {collectionProgressTotal > 0 ? ` ${collectionProgressCompleted}/${collectionProgressTotal}` : ""}
            </strong>
            <div className="topbar-collection-progress" role="progressbar" aria-label="采集进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={collectionProgress}>
              <span style={{ width: `${collectionProgress}%` }} />
            </div>
            <b>{collectionProgress}%</b>
          </div>
        ) : (
          <div className={`daily-topbar-status ${error ? "is-error" : "is-success"} ${noticeLeaving && !error ? "is-leaving" : ""}`} role="status">
            <span className="daily-topbar-status-icon" aria-hidden="true">{error ? "!" : "✓"}</span>
            <strong>{error || notice}</strong>
            <button type="button" onClick={() => { setError(""); setNotice(""); }} aria-label="关闭提示">×</button>
          </div>
        ),
        topbarStatusTarget,
      )}
      {!collectionView && (
        <>
      <section className="daily-page-heading">
        <div>
          <p className="daily-kicker">DAILY PRODUCT DISCOVERY</p>
          <h1>每日选品</h1>
          <p>关键词或参考图驱动商品采集，筛选后确认进入产品处理。</p>
        </div>
        {!onOpenCollection && (
          <button type="button" className="back-to-collection-button" onClick={() => setInternalView("collection")}>
            <span aria-hidden="true">←</span> 返回采集
          </button>
        )}
      </section>

      <section className="daily-panel direction-panel">
        <div className="daily-panel-title">
          <div><span className="title-icon iconfont icon-aim" aria-hidden="true" /><strong>采集方向</strong></div>
          <div className="direction-panel-actions">
            <span>关键词为中心 · 每词独立采集</span>
            {!deleteMode && !editMode && (
              <button className="add-preset-button" type="button" onClick={openCreatePreset}>
                ＋ 添加预设
              </button>
            )}
            <button className={`toggle-edit-mode-button ${editMode ? "is-active" : ""}`} type="button" onClick={() => { setEditMode((current) => !current); setDeleteMode(false); setPendingDeleteDirection(null); }}>
              {editMode ? "✓ 完成" : "更改预设"}
            </button>
            <button className={`toggle-delete-mode-button ${deleteMode ? "is-active" : ""}`} type="button" onClick={() => { setDeleteMode((current) => !current); setEditMode(false); setPendingDeleteDirection(null); }}>
              {deleteMode ? "✓ 完成" : "删除预设"}
            </button>
          </div>
        </div>
        <div className="direction-grid">
          {directions.map((direction) => {
            const tooltipId = `direction-tooltip-${direction.id}`;
            return (
              <div className="direction-card-wrap" key={direction.id}>
                <button
                  type="button"
                  className={`direction-card accent-${direction.accent} ${selectedDirectionId === direction.id ? "is-selected" : ""} ${editMode ? "is-edit-mode" : ""} ${deleteMode ? "is-delete-mode" : ""}`}
                  onClick={() => requestDirectionAction(direction)}
                  aria-describedby={deleteMode || editMode ? undefined : tooltipId}
                >
                  {editMode && <span className="direction-edit-indicator iconfont icon-edit" aria-hidden="true" />}
                  {deleteMode && <span className="direction-delete-indicator" aria-hidden="true">×</span>}
                  <div className="direction-card-heading">
                    <span className="direction-card-symbol" aria-hidden="true">{direction.name.slice(0, 1)}</span>
                    <div>
                      <div className="direction-card-meta">
                        <small>{SITE_LABELS[direction.site ?? "US"]}</small>
                        <span className={direction.custom || direction.modified ? "is-custom" : ""}>{direction.custom ? "自定义" : direction.modified ? "已更改" : "系统预设"}</span>
                      </div>
                      <strong>{direction.name}</strong>
                    </div>
                  </div>
                  <div className="direction-keywords" aria-label={`关键词：${direction.keywords.join("、")}`}>
                    {direction.keywords.slice(0, 3).map((keyword) => <span key={keyword}>{keyword}</span>)}
                    {direction.keywords.length > 3 && <span>+{direction.keywords.length - 3}</span>}
                  </div>
                  <p className="direction-attributes">{direction.attributes}</p>
                  <div className="direction-card-footer">
                    <span><small>价格范围</small><b>¥{direction.price[0]}–{direction.price[1]}</b></span>
                    <span><small>目标候选</small><b>{direction.target} 个</b></span>
                    <i aria-hidden="true">→</i>
                  </div>
                </button>
                {!deleteMode && !editMode && (
                  <div className="direction-hover-card" id={tooltipId} role="tooltip">
                    <div className="direction-hover-heading">
                      <span>{SITE_LABELS[direction.site ?? "US"]}{direction.custom ? " · 自定义" : direction.modified ? " · 已更改" : " · 系统预设"}</span>
                      <strong>{direction.name}</strong>
                    </div>
                    <dl>
                      <div><dt>采集关键词</dt><dd>{direction.keywords.join("、")}</dd></div>
                      <div><dt>关注属性</dt><dd>{direction.attributes}</dd></div>
                      <div><dt>价格范围</dt><dd>{direction.price[0]}–{direction.price[1]} 元</dd></div>
                      <div><dt>起订量上限</dt><dd>2 件</dd></div>
                      <div><dt>目标候选</dt><dd>{direction.target} 个</dd></div>
                    </dl>
                    <small>点击预设进入采集</small>
                  </div>
                )}
              </div>
            );
          })}
          {!deleteMode && !editMode && <button type="button" className="add-direction-card" onClick={openCreatePreset}>
            <span aria-hidden="true">＋</span>
            <strong>添加自定义预设</strong>
            <small>保存常用关键词与筛选条件</small>
          </button>}
        </div>
      </section>

      {/* portal 到 body：脱离 tab 面板层叠上下文，防被 sticky 顶栏盖住 */}
      {presetDialogOpen && createPortal(
        <div className="preset-dialog-backdrop" role="presentation" onMouseDown={closePresetDialog}>
          <form className="preset-dialog" onSubmit={savePreset} onMouseDown={(event) => event.stopPropagation()}>
            <div className="preset-dialog-header">
              <div><span>{editingDirectionId ? "EDIT PRESET" : "CUSTOM PRESET"}</span><strong>{editingDirectionId ? "更改采集方向" : "添加采集方向"}</strong></div>
              <button type="button" onClick={closePresetDialog} aria-label={editingDirectionId ? "关闭更改预设" : "关闭添加预设"}>×</button>
            </div>
            {presetError && <div className="preset-error">{presetError}</div>}
            <div className="preset-form-grid">
              <label><span>预设名称 <em>必填</em></span><input value={presetName} onChange={(event) => setPresetName(event.target.value)} placeholder="例如：手机周边" /></label>
              <label><span>站点</span><select value={presetSite} onChange={(event) => setPresetSite(event.target.value as TargetSite)}><option value="US">美国站 US</option><option value="CO">哥伦比亚 CO</option><option value="EC">厄瓜多尔 EC</option></select></label>
              <label className="preset-wide"><span>关键词 <em>1–5 个</em></span><input value={presetKeywords} onChange={(event) => setPresetKeywords(event.target.value)} placeholder="多个关键词用逗号分隔" /></label>
              <label className="preset-wide"><span>关注属性 <em>必填</em></span><input value={presetAttributes} onChange={(event) => setPresetAttributes(event.target.value)} placeholder="例如：材质 / 尺寸 / 适配型号" /></label>
              <label><span>最低价格（元）</span><input type="number" min="0" step="0.01" value={presetMinPrice} onChange={(event) => setPresetMinPrice(event.target.value)} /></label>
              <label><span>最高价格（元）</span><input type="number" min="0" step="0.01" value={presetMaxPrice} onChange={(event) => setPresetMaxPrice(event.target.value)} /></label>
              <label><span>每词候选数</span><input type="number" min="1" value={presetTarget} onChange={(event) => setPresetTarget(event.target.value)} /></label>
              <label><span>卡片颜色</span><select value={presetAccent} onChange={(event) => setPresetAccent(event.target.value)}><option value="cyan">青色</option><option value="blue">蓝色</option><option value="green">绿色</option><option value="orange">橙色</option><option value="purple">紫色</option><option value="slate">灰色</option></select></label>
            </div>
            <div className="preset-dialog-actions">
              <button type="button" onClick={closePresetDialog}>取消</button>
              <button type="submit">{editingDirectionId ? "保存更改" : "保存预设"}</button>
            </div>
          </form>
        </div>, document.body)}

      {pendingDeleteDirection && createPortal(
        <div className="preset-dialog-backdrop" role="presentation" onMouseDown={() => setPendingDeleteDirection(null)}>
          <div className="delete-confirm-dialog" role="alertdialog" aria-modal="true" aria-label="确认删除预设" onMouseDown={(event) => event.stopPropagation()}>
            <span className="delete-confirm-icon">!</span>
            <h2>确定删除这个预设吗？</h2>
            <p>“{pendingDeleteDirection.name}”删除后将从采集方向列表中移除。</p>
            <div className="delete-confirm-actions">
              <button type="button" onClick={() => setPendingDeleteDirection(null)}>取消</button>
              <button type="button" onClick={confirmDeletePreset}>确定删除</button>
            </div>
          </div>
        </div>, document.body)}
        </>
      )}

      {collectionView && (
        <div className="daily-collection-workspace">
          <section className="daily-page-heading">
            <div>
              <p className="daily-kicker">DAILY PRODUCT DISCOVERY</p>
              <h1>每日选品</h1>
              <p>关键词或参考图驱动商品采集，筛选后确认进入产品处理。</p>
            </div>
            {(!onOpenCollection || collectionWorkspaceMode === "shop" || collectionWorkspaceMode === "plugin") && (
              <div className="daily-page-heading-actions">
                {!onOpenCollection && (
                  <button type="button" className="preset-entry-button" onClick={() => setInternalView("directions")}>
                    <span aria-hidden="true">▦</span> 模板预设
                  </button>
                )}
                {collectionWorkspaceMode === "shop" && (
                  <button type="button" className="shop-batch-manager-trigger" onClick={() => shopCollectionPanelRef.current?.openBatchManager()}>
                    <span className="iconfont icon-time-circle" aria-hidden="true"></span> 批次管理 <b>{shopBatchCount}</b>
                  </button>
                )}
                {collectionWorkspaceMode === "plugin" && (
                  <button type="button" className="shop-batch-manager-trigger" onClick={() => pluginCapturePanelRef.current?.openBatchManager()}>
                    <span className="iconfont icon-time-circle" aria-hidden="true"></span> 批次管理 <b>{pluginBatchCount}</b>
                  </button>
                )}
              </div>
            )}
          </section>
          <section className="daily-collection-surface" aria-label="每日选品采集面板">
            <div className="collection-entry-intro">
              <div className="collection-entry-copy">
                <span className="collection-entry-eyebrow">COLLECTION ENTRY</span>
                <strong>选择采集入口</strong>
                <small>三种入口共用同一套筛选、评分与入池规则，切换只改变货源来源。</small>
              </div>
              <svg className="collection-entry-art" viewBox="0 0 148 62" aria-hidden="true" focusable="false">
                <defs>
                  <linearGradient id="collectionEntryArtStroke" x1="0" y1="0" x2="1" y2="1">
                    <stop offset="0" stopColor="#38a9d6" />
                    <stop offset="1" stopColor="#19bda3" />
                  </linearGradient>
                </defs>
                <g stroke="url(#collectionEntryArtStroke)" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" fill="none">
                  <path d="M50 52h46" strokeDasharray="3 5" opacity=".55" />
                  <path d="M60 30 75 22l15 8-15 8z" />
                  <path d="M60 30v14l15 8 15-8V30" />
                  <path d="M75 38v14" opacity=".7" />
                  <circle cx="99" cy="17" r="7.5" />
                  <path d="M105 23l7 7" />
                  <path d="M121 35l1.8 4.2 4.2 1.8-4.2 1.8L121 47l-1.8-4.2L115 41l4.2-1.8z" strokeWidth="1.2" />
                  <path d="M42 18l1.4 3.2 3.2 1.4-3.2 1.4L42 27l-1.4-3.2-3.2-1.4 3.2-1.4z" strokeWidth="1.2" opacity=".75" />
                </g>
                <g fill="#19bda3" opacity=".28">
                  <circle cx="34" cy="42" r="1.4" />
                  <circle cx="128" cy="18" r="1.2" />
                  <circle cx="114" cy="52" r="1.1" />
                </g>
              </svg>
            </div>
            <div
              className="collection-workspace-tabs"
              role="tablist"
              aria-label="采集入口"
            >
              <CollectionModeCard
                label="定向采集"
                iconClass="icon-search"
                slides={COLLECTION_MODE_SLIDES.daily}
                isActive={collectionWorkspaceMode === "daily"}
                onSelect={() => setCollectionWorkspaceMode("daily")}
                description="关键词 / 参考图 · 逐条拉详情"
              />
              <CollectionModeCard
                label="整店采集"
                iconClass="icon-shop"
                slides={COLLECTION_MODE_SLIDES.shop}
                isActive={collectionWorkspaceMode === "shop"}
                onSelect={() => setCollectionWorkspaceMode("shop")}
                description="整店链接 · 批量筛款"
              />
              <CollectionModeCard
                label="插件采集"
                iconClass="icon-cloud"
                slides={COLLECTION_MODE_SLIDES.plugin}
                isActive={collectionWorkspaceMode === "plugin"}
                onSelect={() => setCollectionWorkspaceMode("plugin")}
                description="浏览器插件 · 一键收录"
              />
            </div>
            {collectionWorkspaceMode === "plugin" ? (
              <PluginOneboundCapturePanel ref={pluginCapturePanelRef} isActive={isActive} onOpenDraft={onOpenProductProcessingDraft} onBatchCountChange={setPluginBatchCount} />
            ) : collectionWorkspaceMode === "shop" ? (
              <ShopCollectionPanel ref={shopCollectionPanelRef} isActive={isActive} onBatchCountChange={setShopBatchCount} />
            ) : (
            <div className="daily-drawer-body">
              <div className="daily-work-grid">
        <form className="daily-panel collection-panel" onSubmit={submitCollection}>
          <div className="daily-panel-title">
            <div><span className="title-icon iconfont icon-search" aria-hidden="true"></span><strong>关键词/参考图采集</strong></div>
            <span>当前方向：{selectedDirection.name}</span>
          </div>

          <div className="collection-primary-fields">
            <div className="collection-field-grid">
              <label>
                <span>采集平台</span>
                <select value={platform} onChange={(event) => setPlatform(event.target.value as CollectionPlatform | "")}>
                  <option value="" disabled>请选择</option>
                  <option value="1688">1688</option>
                  <option value="taobao">淘宝</option>
                </select>
              </label>
              <label><span>选品范围</span><select value={scope} onChange={(event) => setScope(event.target.value as SelectionScope | "")}><option value="" disabled>请选择</option><option value="divergent">发散相似款</option><option value="exact">精准匹配</option></select></label>
              <label><span>采集数量</span><input type="number" min="1" max="200" value={targetCount} onChange={(event) => setTargetCount(event.target.value)} /></label>
              <label className="collection-keyword-field">
                <span>采集关键词 <em>{mode === "keyword" ? "必填" : "作为图片描述标签"}</em></span>
                <input value={keywords} onChange={(event) => setKeywords(event.target.value)} placeholder="多个关键词用逗号分隔，最多 5 个" />
              </label>
              {mode === "image" && (
                <label className="collection-reference-field">
                  <span>参考图 URL <em>必填</em></span>
                  <input type="url" value={referenceImageUrl} onChange={(event) => setReferenceImageUrl(event.target.value)} placeholder="https://example.com/product.jpg" />
                </label>
              )}
            </div>
          </div>

          {/* 生效条件摘要与提交区：摘要靠左说明"这次会怎么采"，主按钮靠右承担唯一主操作 */}
          <div className="collection-summary-row">
            <div className="collection-filter-summary" aria-label="当前生效条件">
              <span className="collection-filter-summary-label">生效条件</span>
              {collectionFilterChips.map((chip, index) => (
                <span className="collection-filter-chip" key={`${index}-${chip}`}>{chip}</span>
              ))}
              <button type="button" className="collection-filter-edit" onClick={openCollectionSettings}>调整</button>
            </div>
            <div className="collection-form-actions">
              <div className="collection-secondary-actions">
                <button type="button" className="collection-mode-toggle" title={mode === "keyword" ? "切换为参考图采集：用公开图片 URL 搜索相似款" : "切换为关键词采集：用 1–5 个关键词搜索"} onClick={() => setMode(mode === "keyword" ? "image" : "keyword")}>
                  <span aria-hidden="true">⇄</span>{mode === "keyword" ? "参考图采集" : "关键词采集"}
                </button>
                <button type="button" className="collection-settings-entry" ref={collectionSettingsTriggerRef} aria-expanded={advancedCollectionOpen} aria-controls="collection-settings-drawer" title="价格、SKU、并发与风险规则" onClick={openCollectionSettings}>
                  <span className="iconfont icon-setting" aria-hidden="true"></span>高级设置
                </button>
              </div>
              <button className="collect-button" type="submit" disabled={busy || collecting}>{collecting ? <><i className="app-spinner is-sm" aria-hidden="true" />采集中 {collectionProgress}%</> : "开始采集"}</button>
              {collecting && (
                <button className="collect-cancel-button" type="button" disabled={cancelling} onClick={cancelCollection}>
                  {cancelling ? <><i className="app-spinner is-sm" aria-hidden="true" />正在中断…</> : "中断采集"}
                </button>
              )}
            </div>
          </div>

          <p className="collection-hint">{!platform || platform === "1688"
            ? "1688 会尽量拉取全部候选的详情（SKU/发源地/属性），失败或下架商品除外。"
            : "淘宝会尽量拉取全部候选的详情，失败或下架商品除外。"}</p>
        </form>

      </div>

      <section className="daily-panel daily-results">
        <div className="daily-panel-title results-title">
          <div><span className="title-icon">◇</span><strong>候选商品</strong></div>
          <div className="results-actions">
            {activeRun && <span>批次 {activeRun.run_id.slice(0, 8)} · {activeRun.candidate_count} 条</span>}
            <button type="button" ref={historyDrawerTriggerRef} className="history-drawer-trigger" onClick={() => { setAdvancedCollectionOpen(false); setHistoryDrawerOpen(true); }}><span aria-hidden="true">◷</span> 最近批次 <b>{runs.length}</b></button>
            {activeRun && (
              <div className={`sku-repull-control ${skuRepull?.status === "running" ? "is-running" : ""}`}>
                <button
                  type="button"
                  className="sku-repull-button"
                  disabled={skuRepullBusy || skuRepull?.status === "running"}
                  onClick={() => void startSkuRepullNow()}
                  title="对 SKU 规格未读取成功的商品后台自动重新拉取详情"
                >
                  {skuRepullBusy ? <i className="app-spinner is-sm" aria-hidden="true" /> : <span aria-hidden="true">↻</span>} SKU补齐
                </button>
                {skuRepull && skuRepull.status !== "idle" && (
                  <span className="sku-repull-state">
                    {skuRepull.status === "running" ? (
                      <>
                        <b>第 {skuRepull.round} 轮</b>
                        <i>{skuRepull.done}/{skuRepull.total} · {Math.round((skuRepull.done / Math.max(1, skuRepull.total)) * 100)}%</i>
                        <button type="button" className="sku-repull-cancel" disabled={skuRepullBusy} onClick={() => void cancelSkuRepullNow()}>{skuRepullBusy ? <><i className="app-spinner is-sm" aria-hidden="true" />中断</> : "中断"}</button>
                      </>
                    ) : (
                      <i>
                        {skuRepull.status === "completed"
                          ? `第 ${skuRepull.round} 轮完成（成功 ${skuRepull.succeeded} / 失败 ${skuRepull.failed}）`
                          : skuRepull.status === "cancelled"
                            ? `第 ${skuRepull.round} 轮已中断（完成 ${skuRepull.done}/${skuRepull.total}）`
                            : skuRepull.message}
                      </i>
                    )}
                  </span>
                )}
              </div>
            )}
            {activeRun && collectionRetry && collectionRetry.status !== "idle" && activeRun.candidate_count === 0 && (
              <div className={`collection-retry-control ${collectionRetry.status === "running" ? "is-running" : ""}`}>
                <span className="collection-retry-state">
                  {collectionRetry.status === "running" ? (
                    <>
                      <span className="collection-retry-icon" aria-hidden="true">↻</span>
                      <b>正在自动重新尝试采集（第 {collectionRetry.round} 轮）…</b>
                    </>
                  ) : collectionRetry.status === "completed" ? (
                    <>
                      <span className="collection-retry-icon" aria-hidden="true">✓</span>
                      <b>自动重试成功，正在更新候选…</b>
                    </>
                  ) : (
                    <i>{collectionRetry.message}</i>
                  )}
                </span>
              </div>
            )}
            <label className="select-all-check" title={allCandidatesSelected ? "取消全选" : "全选"}>
              <input
                type="checkbox"
                checked={allCandidatesSelected}
                disabled={busy || !activeRun || selectableCandidates.length === 0}
                onChange={toggleSelectAllCandidates}
              />
            </label>
            <button type="button" className="select-all-button" disabled={busy || !activeRun || selectableCandidates.length === 0} onClick={selectAllCandidates}>{busy && <i className="app-spinner is-sm" aria-hidden="true" />}全选</button>
            <button type="button" className="confirm-button" disabled={busy || selectedCandidates.length === 0} onClick={() => void confirmSelected()}>{busy && <i className="app-spinner is-sm" aria-hidden="true" />}确认入池（{selectedCandidates.length}）</button>
          </div>
        </div>
        {activeRun && activeRun.candidates.length > 0 && (
          <div className="results-filter-bar">
            <label className="candidate-sort" title="候选排序方式">
              <span className="candidate-sort-label">排序</span>
              <select
                value={candidateSort}
                onChange={(event) => setCandidateSort(event.target.value as CandidateSortKey)}
              >
                {CANDIDATE_SORT_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </label>
            <div className="sku-filter">
              <span className="sku-filter-label">SKU筛选</span>
              <input
                type="number"
                min={0}
                placeholder="最小"
                value={skuFilterMin}
                onChange={(event) => setSkuFilterMin(event.target.value)}
              />
              <span className="sku-filter-separator">-</span>
              <input
                type="number"
                min={0}
                placeholder="最大"
                value={skuFilterMax}
                onChange={(event) => setSkuFilterMax(event.target.value)}
              />
              <button
                type="button"
                className="sku-filter-button"
                onClick={() => {
                  const min = skuFilterMin.trim() === "" ? null : Number(skuFilterMin);
                  const max = skuFilterMax.trim() === "" ? null : Number(skuFilterMax);
                  setAppliedSkuFilter({ min: Number.isFinite(min) ? min : null, max: Number.isFinite(max) ? max : null });
                }}
              >
                筛选
              </button>
            </div>
            <button
              type="button"
              className={`completeness-toggle ${onlyCompleteCandidates ? "is-active" : ""}`}
              aria-pressed={onlyCompleteCandidates}
              title="只看同时具备价格、主图和 SKU 的候选，避免选中后才发现缺资料"
              onClick={() => setOnlyCompleteCandidates((current) => !current)}
            >
              只看完整数据
            </button>
            <span className="results-filter-summary">
              当前展示 {filteredCandidates.length} / {activeRun.candidates.length} 条
            </span>
          </div>
        )}
        {activeRun && filteredCandidates.length > 1 && (
          <p className="confirm-tip">
            <span aria-hidden="true">ⓘ</span>
            <span>提示：多个商品一起入池时，<b>多 SKU 商品会明显拖慢速度</b>。建议先用上方「SKU筛选」把最大 SKU 规格数调小，筛掉多 SKU 商品后再点「确认入池」。</span>
          </p>
        )}
        {!activeRun && (
          <div className="candidates-placeholder">
            <div className="result-empty">
              <span>{collecting ? "↻" : "⌕"}</span>
              <strong>{collecting ? collectionProgressMessage : "等待采集结果"}</strong>
              <p>
                {collecting
                  ? `正在采集并拉取详情，完成后候选会按综合分排序展示（${collectionProgress}%）。`
                  : "选择采集方向并提交条件，候选商品将在这里展示。"}
              </p>
            </div>
            <div className="candidate-skeleton-grid" aria-hidden="true">
              {Array.from({ length: 10 }).map((_, index) => (
                <div className={`candidate-skeleton ${collecting ? "is-loading" : ""}`} key={index}>
                  <span className="candidate-skeleton-image" />
                  <span className="candidate-skeleton-line" />
                  <span className="candidate-skeleton-line is-short" />
                  <span className="candidate-skeleton-facts" />
                </div>
              ))}
            </div>
          </div>
        )}
        {activeRun && activeRun.candidates.length === 0 && <div className="result-empty"><span>○</span><strong>本批次没有候选</strong><p>可以调整关键词、价格范围或关闭风险排除后重试。</p></div>}
        {activeRun && activeRun.candidates.length > 0 && filteredCandidates.length === 0 && (
          <div className="result-empty">
            <span>○</span>
            <strong>{onlyCompleteCandidates ? "没有同时具备价格、主图和 SKU 的候选" : "没有符合筛选条件的候选商品"}</strong>
            <p>可以放宽 SKU 数量或关闭「只看完整数据」后再试。</p>
          </div>
        )}
        {activeRun && filteredCandidates.length > 0 && (
          <div className="candidate-grid">
            {filteredCandidates.map((candidate) => {
              const selectable = candidate.status === "candidate" || candidate.status === "filtered";
              const checked = selectedCandidates.includes(candidate.candidate_id);
              const statusText = STATUS_LABELS[candidate.status] ?? candidate.status;
              const warnings = candidateWarningTags(candidate);
              const recommendations = recommendationTags(candidate);
              const imageCount = galleryCount(candidate);
              return (
                <article key={candidate.candidate_id} className={`candidate-card status-${candidate.status} ${checked ? "is-checked" : "is-removed"} ${!selectable ? "is-locked" : ""}`}>
                  <label
                    className="candidate-keep"
                    title={!selectable ? statusText : checked ? "本次确认时保留" : candidate.status === "filtered" ? "已过滤候选，可手动勾选后再次入池" : "已从本次确认中剔除"}
                  >
                    <input type="checkbox" checked={checked} disabled={!selectable} onChange={() => toggleCandidate(candidate.candidate_id)} />
                    <span>{!selectable ? statusText : checked ? "保留" : candidate.status === "filtered" ? "已过滤" : "已剔除"}</span>
                  </label>
                  <div
                    className={`candidate-image ${selectable ? "is-clickable" : ""}`}
                    onClick={() => {
                      if (selectable) toggleCandidate(candidate.candidate_id);
                    }}
                    role={selectable ? "button" : undefined}
                    tabIndex={selectable ? 0 : undefined}
                    aria-label={selectable ? (checked ? "点击剔除" : "点击保留") : undefined}
                    onKeyDown={(event) => {
                      if (selectable && (event.key === "Enter" || event.key === " ")) {
                        event.preventDefault();
                        toggleCandidate(candidate.candidate_id);
                      }
                    }}
                  >
                    {candidate.main_image_url
                      ? <DailySelectionImage runId={activeRun.run_id} url={candidate.main_image_url} />
                      : <span>无有效图片</span>}
                    {candidate.main_image_url && imageCount > 0 && (
                      <span className="candidate-gallery-badge" title={`可展示图集 ${imageCount} 张`}>图集 {imageCount}</span>
                    )}
                  </div>
                  <div className="candidate-body">
                    <a href={candidate.source_url} target="_blank" rel="noreferrer" title={candidate.source_title}>{candidate.source_title}</a>
                    <div className="candidate-tags">
                      <span>{candidate.source_platform === "taobao" ? "淘宝" : candidate.source_platform ?? "1688"}</span>
                      {candidate.query_keyword && <span title={`中心词：${candidate.query_keyword}`}>中心词 {candidate.query_keyword}</span>}
                      {candidate.selection_result_label && <span>{candidate.selection_result_label}</span>}
                      {candidate.status !== "candidate" && <span className="is-status">{statusText}</span>}
                    </div>
                    <div className="candidate-facts">
                      <span><b>{formatMoney(candidate.price_cny)}</b><em>价格</em></span>
                      <span title={candidate.sales_text ?? "上游未返回销量"}><b>{candidateSalesLabel(candidate)}</b><em>销量</em></span>
                      <span><b>{candidate.min_order_quantity ?? "未知"}</b><em>起订</em></span>
                      <span title={`${candidate.source_variant_records?.length ?? 0} 个 SKU 规格`}><b>{candidate.source_variant_records?.length || "未知"}</b><em>SKU</em></span>
                    </div>
                    <div className="candidate-meta">
                      <span title={[candidate.shop_name, candidate.location].filter(Boolean).join(" · ")}>
                        {[candidate.shop_name, candidate.location].filter(Boolean).join(" · ") || "店铺与产地待补齐"}
                      </span>
                    </div>
                    {(warnings.length > 0 || recommendations.length > 0) && (
                      <div className="candidate-reasons">
                        {warnings.slice(0, 2).map((label) => <span className="is-warning" key={label}>{label}</span>)}
                        {recommendations.slice(0, 3).map((label) => <span key={label}>{label}</span>)}
                      </div>
                    )}
                    <div className="candidate-card-actions">
                      <button
                        className="candidate-detail-button"
                        type="button"
                        onClick={() => openCandidateDetail(candidate)}
                      >
                        详情
                      </button>
                      {selectable && <button className="reject-button" type="button" disabled={busy} onClick={() => void reject(candidate)}>排除并反馈</button>}
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
            </div>
            )}
          </section>
        </div>
      )}
      {/* portal 到 body：workspace-tab-panel 的 fill-mode 入场动画创建层叠上下文，
          会把 fixed 抽屉的 z-index(170) 锁在面板内、被 sticky 顶栏(z:18)盖住头部 */}
      {createPortal(
        <div
          ref={collectionSettingsLayerRef}
          className={`collection-settings-layer ${advancedCollectionOpen ? "is-open" : ""}`}
          aria-hidden={!advancedCollectionOpen}
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) closeCollectionSettings();
          }}
        >
        <aside id="collection-settings-drawer" className="collection-settings-drawer" role="dialog" aria-modal="true" aria-labelledby="collection-settings-title">
          <header className="collection-settings-drawer-header">
            <div><span>COLLECTION SETTINGS</span><strong id="collection-settings-title">高级设置</strong><small>设置会立即用于下一次采集</small></div>
            <button type="button" onClick={closeCollectionSettings} aria-label="关闭高级设置">×</button>
          </header>
          <div className="collection-settings-drawer-body">
            <section className="collection-settings-section">
              <div className="collection-settings-section-title"><strong>商品筛选</strong><span>留空表示不限制</span></div>
              <div className="collection-fields collection-settings-fields">
                <label><span>最低价格（元）</span><input type="number" min="0" step="0.01" value={minPrice} onChange={(event) => setMinPrice(event.target.value)} /></label>
                <label><span>最高价格（元）</span><input type="number" min="0" step="0.01" value={maxPrice} onChange={(event) => setMaxPrice(event.target.value)} /></label>
                <label><span>起订量上限（件）</span><input type="number" min="1" value={minMoq} onChange={(event) => setMinMoq(event.target.value)} /></label>
                <label><span>SKU 规格数 ≥</span><input type="number" min="1" value={minSkuCount} onChange={(event) => setMinSkuCount(event.target.value)} placeholder="如 2" title="商品 SKU（规格）数量大于或等于该值" /></label>
                <label><span>SKU 规格数 ≤</span><input type="number" min="1" value={maxSkuCount} onChange={(event) => setMaxSkuCount(event.target.value)} placeholder="如 20" title="商品 SKU（规格）数量小于或等于该值" /></label>
                <label><span>SKU 最低价（元）≥</span><input type="number" min="0" step="0.01" value={minSkuPrice} onChange={(event) => setMinSkuPrice(event.target.value)} placeholder="如 0.5" title="所有 SKU 价格均不低于该值" /></label>
                <label><span>SKU 最高价（元）≤</span><input type="number" min="0" step="0.01" value={maxSkuPrice} onChange={(event) => setMaxSkuPrice(event.target.value)} placeholder="如 50" title="所有 SKU 价格均不高于该值" /></label>
                <label><span>SKU 库存 ≥</span><input type="number" min="1" value={minSkuStock} onChange={(event) => setMinSkuStock(event.target.value)} placeholder="如 1000" title="每个 SKU 的库存均不低于该值" /></label>
                <label><span>SKU 库存 ≤</span><input type="number" min="1" value={maxSkuStock} onChange={(event) => setMaxSkuStock(event.target.value)} placeholder="如 50000" title="每个 SKU 的库存均不高于该值" /></label>
              </div>
            </section>
            <section className="collection-settings-section">
              <div className="collection-settings-section-title"><strong>采集执行</strong><span>控制速度与风险过滤</span></div>
              <div className="collection-settings-runtime">
                <label className="field-slider">
                  <span>采集并行数</span>
                  <input type="range" min={1} max={10} step={1} value={maxParallelCollect} onChange={(event) => setMaxParallelCollect(Number(event.target.value) || 1)} />
                  <em>{maxParallelCollect} 线程{maxParallelCollect <= 1 ? "（串行）" : ""}</em>
                </label>
                <label className="risk-switch">
                  <input type="checkbox" checked={excludeRisks} onChange={(event) => setExcludeRisks(event.target.checked)} />
                  <span aria-hidden="true" />
                  <b>自动排除高风险候选</b>
                </label>
              </div>
            </section>
          </div>
          <footer className="collection-settings-drawer-footer">
            <span>已填写的设置会保留</span>
            <button type="button" onClick={closeCollectionSettings}>完成</button>
          </footer>
        </aside>
      </div>,
        document.body,
      )}
      {/* 同上：portal 到 body 脱离 tab 面板层叠上下文，避免被 sticky 顶栏盖住 */}
      {createPortal(
        <div
          ref={historyDrawerLayerRef}
          className={`history-drawer-layer ${historyDrawerOpen ? "is-open" : ""}`}
          aria-hidden={!historyDrawerOpen}
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) closeHistoryDrawer();
          }}
        >
        <aside className="history-drawer" role="dialog" aria-modal="true" aria-label="最近批次">
          <header className="history-drawer-header">
            <div><span>COLLECTION HISTORY</span><strong>最近批次</strong><small>选择批次后将加载对应候选商品</small></div>
            <button type="button" onClick={closeHistoryDrawer} aria-label="关闭最近批次">×</button>
          </header>
          <div className="history-drawer-summary"><span>采集记录</span><b>{runs.length} 条</b></div>
          <div className="run-list history-drawer-list">
            {historyBusy && (
              <div className="app-loading-block">
                <i className="app-spinner" aria-hidden="true" />
                正在读取批次…
              </div>
            )}
            {!historyBusy && runs.length === 0 && <div className="run-empty">暂无采集记录<br /><small>完成首次采集后会显示在这里</small></div>}
            {runs.map((run) => {
              const duration = formatRunDuration(run.created_at, run.updated_at);
              return (
                <button
                  key={run.run_id}
                  type="button"
                  className={activeRun?.run_id === run.run_id ? "is-active" : ""}
                  onClick={() => {
                    closeHistoryDrawer();
                    void openRun(run.run_id);
                  }}
                >
                  <span><strong>{run.run_id.slice(0, 8)}</strong><small>{formatDate(run.created_at)}</small></span>
                  <span><b>{run.candidate_count}</b><small>{STATUS_LABELS[run.status] ?? run.status}{duration ? ` · 耗时 ${duration}` : ""}</small></span>
                </button>
              );
            })}
          </div>
        </aside>
      </div>,
        document.body,
      )}
      {/* 候选详情抽屉：同样 portal 到 body，避免被工作区面板的层叠上下文裁剪 */}
      {createPortal(
        <div
          ref={candidateDetailLayerRef}
          className={`candidate-detail-layer ${detailCandidate ? "is-open" : ""}`}
          aria-hidden={!detailCandidate}
          onMouseDown={(event) => {
            if (event.currentTarget === event.target) closeCandidateDetail();
          }}
        >
          <aside
            className="candidate-detail-drawer"
            role="dialog"
            aria-modal="true"
            aria-labelledby="candidate-detail-title"
          >
            {detailCandidate && activeRun && (
              <>
                <header className="candidate-detail-header">
                  <div>
                    <span>CANDIDATE DETAIL</span>
                    <strong id="candidate-detail-title" title={detailCandidate.source_title}>{detailCandidate.source_title}</strong>
                    <small>
                      {detailCandidate.source_platform === "taobao" ? "淘宝" : "1688"}
                      {" · "}
                      {STATUS_LABELS[detailCandidate.status] ?? detailCandidate.status}
                      {" · 综合分 "}
                      {detailCandidate.selection_score}
                    </small>
                  </div>
                  <button type="button" onClick={closeCandidateDetail} aria-label="关闭候选详情">×</button>
                </header>
                <div className="candidate-detail-body">
                  <section className="candidate-detail-gallery">
                    <div className="candidate-detail-stage">
                      {activeDetailImage
                        ? <DailySelectionImage runId={activeRun.run_id} url={activeDetailImage} />
                        : <span>无有效图片</span>}
                    </div>
                    {detailImageUrls.length > 1 && (
                      <div className="candidate-detail-thumbs">
                        {detailImageUrls.map((url, index) => (
                          <button
                            key={`${url}-${index}`}
                            type="button"
                            className={index === detailImageIndex ? "is-active" : ""}
                            onClick={() => setDetailImageIndex(index)}
                            aria-label={`查看第 ${index + 1} 张图片`}
                          >
                            <DailySelectionImage runId={activeRun.run_id} url={url} />
                          </button>
                        ))}
                      </div>
                    )}
                    {detailGallery.length > detailImageUrls.length && (
                      <p className="candidate-detail-gallery-more">共 {detailGallery.length} 张图片，这里展示前 {detailImageUrls.length} 张</p>
                    )}
                  </section>

                  <section className="candidate-detail-section">
                    <div className="candidate-detail-section-title"><strong>关键数据</strong><small>来自上游详情接口</small></div>
                    <div className="candidate-detail-facts">
                      <span><b>{formatMoney(detailCandidate.price_cny)}</b><em>采购价</em></span>
                      <span><b>{candidateSalesLabel(detailCandidate)}</b><em>销量</em></span>
                      <span><b>{detailCandidate.min_order_quantity ?? "未知"}</b><em>起订量</em></span>
                      <span><b>{detailCandidate.source_variant_records?.length || "未知"}</b><em>SKU 规格</em></span>
                      {detailSkuPriceRange && (
                        <span><b>{formatMoney(detailSkuPriceRange.min)}–{formatMoney(detailSkuPriceRange.max)}</b><em>SKU 价格区间</em></span>
                      )}
                      <span><b>{detailCandidate.stock_quantity ?? "未知"}</b><em>库存</em></span>
                      <span><b>{formatMoney(detailCandidate.freight_cny)}</b><em>单件运费</em></span>
                      {detailCandidate.original_price_cny !== null && detailCandidate.original_price_cny !== undefined && (
                        <span><b>{formatMoney(detailCandidate.original_price_cny)}</b><em>原价</em></span>
                      )}
                      {detailCandidate.shop_name && <span><b title={detailCandidate.shop_name}>{detailCandidate.shop_name}</b><em>店铺</em></span>}
                      {detailCandidate.location && <span><b>{detailCandidate.location}</b><em>产地</em></span>}
                      {detailCandidate.unit && <span><b>{detailCandidate.unit}</b><em>计价单位</em></span>}
                      {detailCandidate.brand && <span><b>{detailCandidate.brand}</b><em>品牌</em></span>}
                      {detailCandidate.category_path && (
                        <span className="is-wide"><b title={detailCandidate.category_path}>{detailCandidate.category_path}</b><em>类目路径</em></span>
                      )}
                    </div>
                  </section>

                  <section className="candidate-detail-section">
                    <div className="candidate-detail-section-title"><strong>风险与推荐依据</strong><small>完整清单，卡片上只显示前几条</small></div>
                    <div className="candidate-detail-chips">
                      {detailWarnings.map((label) => <span className="is-warning" key={`w-${label}`}>{label}</span>)}
                      {detailRecommendations.map((label) => <span key={`r-${label}`}>{label}</span>)}
                      {detailWarnings.length === 0 && detailRecommendations.length === 0 && <span className="is-muted">暂无风险与推荐标签</span>}
                    </div>
                  </section>

                  <section className="candidate-detail-section">
                    <div className="candidate-detail-section-title"><strong>阶梯价</strong><small>上游按起订量分档</small></div>
                    {detailTiers.length === 0
                      ? <p className="candidate-detail-empty">上游未返回阶梯价，采购价按单品价估算</p>
                      : (
                        <div className="candidate-detail-table-scroll">
                          <table className="candidate-detail-table">
                            <thead><tr><th>起订量</th><th>单价</th></tr></thead>
                            <tbody>
                              {detailTiers.map((tier) => (
                                <tr key={`${tier.minOrderQuantity}-${tier.priceCny}`}>
                                  <td>{tier.minOrderQuantity} 件起</td>
                                  <td>{formatMoney(tier.priceCny)}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                  </section>

                  <section className="candidate-detail-section">
                    <div className="candidate-detail-section-title">
                      <strong>SKU 明细</strong>
                      <small>{detailCandidate.source_variant_records?.length ?? 0} 条</small>
                    </div>
                    {(detailCandidate.source_variant_records?.length ?? 0) === 0
                      ? <p className="candidate-detail-empty">未取到 SKU 明细，可在结果区用「补齐 SKU」重试</p>
                      : (
                        <div className="candidate-detail-table-scroll">
                          <table className="candidate-detail-table">
                            <thead><tr><th>规格</th><th>价格</th><th>起订</th><th>库存</th><th>销量</th></tr></thead>
                            <tbody>
                              {detailCandidate.source_variant_records.map((record) => {
                                const spec = formatSkuSpec(record);
                                return (
                                  <tr key={record.sku_id}>
                                    <td title={spec}>{spec}</td>
                                    <td>{record.price_cny === null || record.price_cny === "" ? "—" : formatMoney(record.price_cny)}</td>
                                    <td>{record.min_order_quantity ?? "—"}</td>
                                    <td>{record.quantity ?? "—"}</td>
                                    <td>{record.sales ?? "—"}</td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      )}
                  </section>

                  <section className="candidate-detail-section">
                    <div className="candidate-detail-section-title">
                      <strong>商品属性</strong>
                      <small>{detailAttributes.length} 项</small>
                    </div>
                    {detailAttributes.length === 0
                      ? <p className="candidate-detail-empty">上游未返回结构化属性</p>
                      : (
                        <div className="candidate-detail-attributes">
                          {detailAttributes.map((entry) => (
                            <div key={entry.label}><span>{entry.label}</span><b>{entry.value}</b></div>
                          ))}
                        </div>
                      )}
                  </section>

                  <section className="candidate-detail-section">
                    <div className="candidate-detail-section-title"><strong>比价试算</strong><small>按单件采购价 + 运费折算</small></div>
                    <div className="candidate-detail-quote-fields">
                      <label><span>汇率（1 USD = ? CNY）</span><input type="number" min="0" step="0.01" value={quoteExchangeRate} onChange={(event) => setQuoteExchangeRate(event.target.value)} /></label>
                      <label><span>预计售价（USD）</span><input type="number" min="0" step="0.01" placeholder="如 9.99" value={quoteSalePrice} onChange={(event) => setQuoteSalePrice(event.target.value)} /></label>
                      <label><span>单件运费（¥）</span><input type="number" min="0" step="0.01" placeholder="按上游运费或自行估算" value={quoteFreightCny} onChange={(event) => setQuoteFreightCny(event.target.value)} /></label>
                    </div>
                    {detailQuoteDisplay
                      ? (
                        <div className="candidate-detail-quote-result">
                          <span><b>{detailQuoteDisplay.landedUsd}</b><em>到手成本（{detailQuoteDisplay.landedCny}）</em></span>
                          <span className={detailQuoteDisplay.isLoss ? "is-loss" : ""}><b>{detailQuoteDisplay.profitUsd}</b><em>单件毛利</em></span>
                          <span className={detailQuoteDisplay.isLoss ? "is-loss" : ""}><b>{detailQuoteDisplay.marginRate}</b><em>毛利率</em></span>
                          <span><b>{detailQuoteDisplay.breakEvenUsd}</b><em>盈亏平衡价</em></span>
                        </div>
                      )
                      : <p className="candidate-detail-empty">填写采购价与汇率后自动试算</p>}
                  </section>
                </div>
                <footer className="candidate-detail-footer">
                  <a href={detailCandidate.source_url} target="_blank" rel="noreferrer">打开货源页 ↗</a>
                  <div>
                    {(detailCandidate.status === "candidate" || detailCandidate.status === "filtered") && (
                      <>
                        <button type="button" className="candidate-detail-toggle" onClick={() => toggleCandidate(detailCandidate.candidate_id)}>
                          {selectedCandidates.includes(detailCandidate.candidate_id) ? "移出本次确认" : "加入本次确认"}
                        </button>
                        <button type="button" className="candidate-detail-reject" disabled={busy} onClick={() => void reject(detailCandidate)}>排除并反馈</button>
                      </>
                    )}
                  </div>
                </footer>
              </>
            )}
          </aside>
        </div>,
        document.body,
      )}
    </div>
  );
}
