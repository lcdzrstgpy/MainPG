import { FormEvent, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

import {
  collectByCriteria,
  confirmCandidates,
  getSelectionRun,
  listSelectionRuns,
  rejectCandidate,
} from "../api/dailySelectionApi";
import { getApiToken } from "../../../shared/api/apiClient";
import type {
  CollectionMode,
  CollectionPlatform,
  DailySelectionCandidate,
  DailySelectionCriteria,
  DailySelectionRun,
  DailySelectionRunSummary,
  SelectionScope,
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

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false });
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

function formatListedAt(value: string | null): string {
  // OneBound 1688 接口不返回上架时间，空值不再显示“未知”误导，统一显示占位符。
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

/**
 * Renders a candidate image through the authenticated local proxy instead of
 * the raw CDN URL. alicdn rejects browser requests carrying a localhost
 * Referer (403), so images are fetched with the API token and shown as a
 * blob object URL.
 */
function DailySelectionImage({ runId, url }: { runId: string; url: string }) {
  const [objectUrl, setObjectUrl] = useState("");
  const [state, setState] = useState<"loading" | "ready" | "failed">("loading");

  useEffect(() => {
    let alive = true;
    let created: string | null = null;
    setObjectUrl("");
    setState("loading");
    fetch(`/desktop/daily-selection/image?run_id=${encodeURIComponent(runId)}&url=${encodeURIComponent(url)}`, {
      headers: { Authorization: `Bearer ${getApiToken()}` },
    })
      .then((response) => (response.ok ? response.blob() : null))
      .then((blob) => {
        if (!alive || !blob) return;
        created = URL.createObjectURL(blob);
        setObjectUrl(created);
        setState("ready");
      })
      .catch(() => {
        if (alive) setState("failed");
      });
    return () => {
      alive = false;
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
  topbarStatusVisible?: boolean;
};

export function DailySelectionPage({ view = "directions", initialDirectionId, onOpenCollection, topbarStatusVisible = true }: DailySelectionPageProps) {
  // 视图支持内部轮转：主模块默认直接进采集视图，点「模板预设」切到预设页，
  // 在预设页点方向卡再回到采集视图，而不再新开独立面板。
  const [internalView, setInternalView] = useState<"directions" | "collection">(view);
  const collectionView = internalView === "collection";
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
  const validInitialDirection = directions.some((item) => item.id === initialDirectionId) ? initialDirectionId! : directions[0].id;
  const [selectedDirectionId, setSelectedDirectionId] = useState(validInitialDirection);
  const selectedDirection = useMemo(
    () => directions.find((item) => item.id === selectedDirectionId) ?? directions[0],
    [directions, selectedDirectionId],
  );
  const [mode, setMode] = useState<CollectionMode>("keyword");
  const [platform, setPlatform] = useState<CollectionPlatform>("1688");
  const [keywords, setKeywords] = useState(selectedDirection.keywords.join("，"));
  const [referenceImageUrl, setReferenceImageUrl] = useState("");
  const [scope, setScope] = useState<SelectionScope>("divergent");
  const [site, setSite] = useState<TargetSite>("US");
  const [minPrice, setMinPrice] = useState(String(selectedDirection.price[0]));
  const [maxPrice, setMaxPrice] = useState(String(selectedDirection.price[1]));
  const [minMoq, setMinMoq] = useState("2");
  const [minSkuCount, setMinSkuCount] = useState("");
  const [maxSkuCount, setMaxSkuCount] = useState("");
  const [minSkuPrice, setMinSkuPrice] = useState("");
  const [maxSkuPrice, setMaxSkuPrice] = useState("");
  const [minSkuStock, setMinSkuStock] = useState("");
  const [maxSkuStock, setMaxSkuStock] = useState("");
  const [targetCount, setTargetCount] = useState(String(selectedDirection.target));
  const [excludeRisks, setExcludeRisks] = useState(true);
  const [maxParallelCollect, setMaxParallelCollect] = useState(6);
  const [runs, setRuns] = useState<DailySelectionRunSummary[]>([]);
  const [activeRun, setActiveRun] = useState<DailySelectionRun | null>(null);
  const [selectedCandidates, setSelectedCandidates] = useState<string[]>([]);
  const [skuFilterMin, setSkuFilterMin] = useState("");
  const [skuFilterMax, setSkuFilterMax] = useState("");
  const [appliedSkuFilter, setAppliedSkuFilter] = useState<{ min: number | null; max: number | null }>({ min: null, max: null });
  const [busy, setBusy] = useState(false);
  const [collecting, setCollecting] = useState(false);
  const [collectionProgress, setCollectionProgress] = useState(0);
  const [historyBusy, setHistoryBusy] = useState(true);
  const [historyDrawerOpen, setHistoryDrawerOpen] = useState(false);
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

  const filteredCandidates = useMemo(() => {
    if (!activeRun) return [];
    return activeRun.candidates.filter((candidate) => {
      const skuCount = candidate.source_variant_records.length;
      if (appliedSkuFilter.min !== null && skuCount < appliedSkuFilter.min) return false;
      if (appliedSkuFilter.max !== null && skuCount > appliedSkuFilter.max) return false;
      return true;
    });
  }, [activeRun, appliedSkuFilter]);

  // 当前可勾选的候选（状态为 candidate 的）
  const selectableCandidates = filteredCandidates.filter((candidate) => candidate.status === "candidate");
  // 全选复选框状态：可勾选候选均被选中时为 true
  const allCandidatesSelected = selectableCandidates.length > 0
    && selectableCandidates.every((candidate) => selectedCandidates.includes(candidate.candidate_id));

  useEffect(() => {
    void refreshRuns();
  }, []);

  // 悬浮 toast：成功提示 2s、错误提示 3s 后自动消失，不常驻、不占页面空间
  useEffect(() => {
    if (!notice && !error) return;
    const timer = window.setTimeout(() => {
      setNotice("");
      setError("");
    }, error ? 3000 : 2000);
    return () => window.clearTimeout(timer);
  }, [notice, error]);

  useEffect(() => {
    setTopbarStatusTarget(document.getElementById("workspace-topbar-status"));
  }, []);

  useEffect(() => {
    if (!notice) {
      setNoticeLeaving(false);
      return;
    }

    setNoticeLeaving(false);
    const leaveTimer = window.setTimeout(() => setNoticeLeaving(true), 4000);
    const clearTimer = window.setTimeout(() => setNotice(""), 4480);
    return () => {
      window.clearTimeout(leaveTimer);
      window.clearTimeout(clearTimer);
    };
  }, [notice]);

  useEffect(() => {
    if (!collecting) return;
    const timer = window.setInterval(() => {
      setCollectionProgress((current) => {
        if (current >= 92) return current;
        const step = Math.max(1, Math.ceil((92 - current) * 0.08));
        return Math.min(92, current + step);
      });
    }, 420);
    return () => window.clearInterval(timer);
  }, [collecting]);

  useEffect(() => {
    if (!historyDrawerOpen) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setHistoryDrawerOpen(false);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [historyDrawerOpen]);

  useEffect(() => {
    window.localStorage.setItem(CUSTOM_DIRECTIONS_KEY, JSON.stringify(customDirections));
  }, [customDirections]);

  useEffect(() => {
    window.localStorage.setItem(UPDATED_DEFAULT_DIRECTIONS_KEY, JSON.stringify(updatedDefaultDirections));
  }, [updatedDefaultDirections]);

  useEffect(() => {
    window.localStorage.setItem(REMOVED_DEFAULT_DIRECTIONS_KEY, JSON.stringify(removedDefaultDirectionIds));
  }, [removedDefaultDirectionIds]);

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
    setSelectedDirectionId(direction.id);
    setKeywords(direction.keywords.join("，"));
    setSite(direction.site ?? "US");
    setMinPrice(String(direction.price[0]));
    setMaxPrice(String(direction.price[1]));
    setTargetCount(String(direction.target));
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
    const normalizedKeywords = keywords.split(/[，,\n]/).map((item) => item.trim()).filter(Boolean).slice(0, 5);
    const criteria: DailySelectionCriteria = {
      keywords: mode === "image" ? normalizedKeywords : normalizedKeywords,
      selection_scope: scope,
      category: selectedDirection.name,
      target_count: numberOrUndefined(targetCount) ?? selectedDirection.target,
      max_api_calls: 200,
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

    criteria.collection_mode = mode;
    criteria.collection_platform = "1688";
    if (mode === "image") criteria.reference_image_url = referenceImageUrl.trim();
    return criteria;
  }

  async function submitCollection(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setNotice("");
    if (mode === "keyword" && !keywords.trim()) {
      setError("请至少填写一个关键词");
      return;
    }
    if (mode === "image" && !referenceImageUrl.trim()) {
      setError("请填写可公开访问的参考图 URL");
      return;
    }
    if (platform !== "1688") {
      const channelName = platform === "taobao" ? "淘宝" : "1688 + 淘宝组合";
      setNotice(`${channelName}采集界面已就绪，当前后端尚未接入该渠道，本次没有发送采集请求。`);
      return;
    }

    setBusy(true);
    setCollecting(true);
    setCollectionProgress(2);
    try {
      const criteria = buildCriteria();
      const run = await collectByCriteria(criteria);
      setCollectionProgress(100);
      setActiveRun(run);
      setSelectedCandidates([]);
      const intake = run.metadata.api_draft_intake;
      const intakeNotice = intake?.status === "partial"
        ? `；其中 ${intake.errors.length} 个候选未进入 API 草稿视图`
        : "";
      setNotice(`批次 ${run.run_id.slice(0, 8)} 已返回 ${run.candidate_count} 个候选${intakeNotice}`);
      await refreshRuns();
      await new Promise((resolve) => window.setTimeout(resolve, 320));
    } catch (requestError) {
      setCollectionProgress(0);
      setError(requestError instanceof Error ? requestError.message : "采集请求失败");
    } finally {
      setCollecting(false);
      setBusy(false);
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
        .filter((candidate) => candidate.status === "candidate")
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

  // 「全选」按钮：始终选中全部可勾选候选
  function selectAllCandidates() {
    if (!activeRun) return;
    setSelectedCandidates(filteredCandidates
      .filter((candidate) => candidate.status === "candidate")
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
      const handoffs = await confirmCandidates(activeRun.run_id, selectedCandidates);
      setNotice(`已确认 ${handoffs.length} 个候选，等待产品处理模块消费`);
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
            <strong>正在采集</strong>
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

      {presetDialogOpen && (
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
        </div>
      )}

      {pendingDeleteDirection && (
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
        </div>
      )}
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
            {!onOpenCollection && (
              <button type="button" className="preset-entry-button" onClick={() => setInternalView("directions")}>
                <span aria-hidden="true">▦</span> 模板预设
              </button>
            )}
          </section>
          <section className="daily-collection-surface" aria-label="每日选品采集面板">
            <header className="daily-drawer-header">
              <div>
                <span>DAILY SELECTION</span>
                <strong>采集与候选商品</strong>
              </div>
              <span className="collection-workspace-context">来源：每日选品</span>
            </header>
            <div className="daily-drawer-body">
              <div className="daily-work-grid">
        <form className="daily-panel collection-panel" onSubmit={submitCollection}>
          <div className="daily-panel-title">
            <div><span className="title-icon">⌕</span><strong>OneBound API 采集</strong></div>
            <span>当前方向：{selectedDirection.name}</span>
          </div>

          <div className="mode-tabs" role="tablist" aria-label="采集方式">
            {([
              ["keyword", "关键词采集", "输入 1–5 个关键词"],
              ["image", "参考图采集", "使用公开图片 URL"],
            ] as const).map(([value, label, hint]) => (
              <button key={value} type="button" className={mode === value ? "is-active" : ""} onClick={() => setMode(value)}>
                <strong>{label}</strong><span>{hint}</span>
              </button>
            ))}
          </div>

          <div className="collection-fields">
            <label>
              <span>采集平台</span>
              <select value={platform} onChange={(event) => setPlatform(event.target.value as CollectionPlatform)}>
                <option value="1688">1688</option>
                <option value="taobao">淘宝</option>
                <option value="1688+taobao">1688 + 淘宝</option>
              </select>
            </label>
            <label><span>站点</span><select value={site} onChange={(event) => setSite(event.target.value as TargetSite)}><option value="US">美国站 US</option><option value="CO">哥伦比亚 CO</option><option value="EC">厄瓜多尔 EC</option></select></label>
            <label><span>选品范围</span><select value={scope} onChange={(event) => setScope(event.target.value as SelectionScope)}><option value="divergent">发散相似款</option><option value="exact">精准匹配</option></select></label>
            <label><span>采集数量</span><input type="number" min="1" value={targetCount} onChange={(event) => setTargetCount(event.target.value)} /></label>
            <label className="field-wide">
              <span>采集关键词 <em>{mode === "keyword" ? "必填" : "作为图片描述标签"}</em></span>
              <input value={keywords} onChange={(event) => setKeywords(event.target.value)} placeholder="多个关键词用逗号分隔，最多 5 个" />
            </label>
            {mode === "image" && (
              <label className="field-wide">
                <span>参考图 URL <em>必填</em></span>
                <input type="url" value={referenceImageUrl} onChange={(event) => setReferenceImageUrl(event.target.value)} placeholder="https://example.com/product.jpg" />
              </label>
            )}
            <label><span>最低价格（元）</span><input type="number" min="0" step="0.01" value={minPrice} onChange={(event) => setMinPrice(event.target.value)} /></label>
            <label><span>最高价格（元）</span><input type="number" min="0" step="0.01" value={maxPrice} onChange={(event) => setMaxPrice(event.target.value)} /></label>
            <label><span>起订量上限（件）</span><input type="number" min="1" value={minMoq} onChange={(event) => setMinMoq(event.target.value)} /></label>
            <label><span>SKU 规格数 ≥</span><input type="number" min="1" value={minSkuCount} onChange={(event) => setMinSkuCount(event.target.value)} placeholder="如 2" title="商品 SKU（规格）数量大于或等于该值" /></label>
            <label><span>SKU 规格数 ≤</span><input type="number" min="1" value={maxSkuCount} onChange={(event) => setMaxSkuCount(event.target.value)} placeholder="如 20" title="商品 SKU（规格）数量小于或等于该值" /></label>
            <label><span>SKU 最低价（元）≥</span><input type="number" min="0" step="0.01" value={minSkuPrice} onChange={(event) => setMinSkuPrice(event.target.value)} placeholder="如 0.5" title="所有 SKU 价格均不低于该值" /></label>
            <label><span>SKU 最高价（元）≤</span><input type="number" min="0" step="0.01" value={maxSkuPrice} onChange={(event) => setMaxSkuPrice(event.target.value)} placeholder="如 50" title="所有 SKU 价格均不高于该值" /></label>
            <label><span>SKU 库存 ≥</span><input type="number" min="1" value={minSkuStock} onChange={(event) => setMinSkuStock(event.target.value)} placeholder="如 1000" title="每个 SKU 的库存均不低于该值" /></label>
            <label><span>SKU 库存 ≤</span><input type="number" min="1" value={maxSkuStock} onChange={(event) => setMaxSkuStock(event.target.value)} placeholder="如 50000" title="每个 SKU 的库存均不高于该值" /></label>
            <label className="field-slider">
              <span>采集并行数</span>
              <input type="range" min={1} max={10} step={1} value={maxParallelCollect} onChange={(event) => setMaxParallelCollect(Number(event.target.value) || 1)} />
              <em>{maxParallelCollect} 线程{maxParallelCollect <= 1 ? '（串行）' : ''}</em>
            </label>
          </div>

          {platform !== "1688" && (
            <div className="channel-placeholder-note">
              <span>前端预留</span>
              {platform === "taobao" ? "淘宝采集" : "1688 与淘宝组合采集"}暂不调用后端，后续接口接入后可直接补充请求逻辑。
            </div>
          )}

          <label className="risk-switch">
            <input type="checkbox" checked={excludeRisks} onChange={(event) => setExcludeRisks(event.target.checked)} />
            <span aria-hidden="true" />
            自动排除高风险候选
          </label>
          <div className="collection-actions">
            <span>{platform === "1688" ? "1688 每批最多调用 200 次 API，并在预算内尽量拉取全部候选的详情（SKU/发源地/属性），失败或下架商品除外。" : "淘宝渠道当前仅展示前端交互，不会发送采集请求或产生 API 费用。"}</span>
            <div className="collection-submit-area">
              <button className="collect-button" type="submit" disabled={busy}>{collecting ? "正在采集…" : "开始采集"}</button>
            </div>
          </div>
        </form>

      </div>

      <section className="daily-panel daily-results">
        <div className="daily-panel-title results-title">
          <div><span className="title-icon">◇</span><strong>候选商品</strong></div>
          <div className="results-actions">
            {activeRun && (
              <div className="sku-filter">
                <span className="sku-filter-label">SKU筛选</span>
                <input
                  type="number"
                  min={0}
                  placeholder="最小"
                  value={skuFilterMin}
                  onChange={(event) => setSkuFilterMin(event.target.value)}
                  disabled={!activeRun}
                />
                <span className="sku-filter-separator">-</span>
                <input
                  type="number"
                  min={0}
                  placeholder="最大"
                  value={skuFilterMax}
                  onChange={(event) => setSkuFilterMax(event.target.value)}
                  disabled={!activeRun}
                />
                <button
                  type="button"
                  className="sku-filter-button"
                  disabled={!activeRun}
                  onClick={() => {
                    const min = skuFilterMin.trim() === "" ? null : Number(skuFilterMin);
                    const max = skuFilterMax.trim() === "" ? null : Number(skuFilterMax);
                    setAppliedSkuFilter({ min: Number.isFinite(min) ? min : null, max: Number.isFinite(max) ? max : null });
                  }}
                >
                  筛选
                </button>
              </div>
            )}
            {activeRun && <span>批次 {activeRun.run_id.slice(0, 8)} · {activeRun.candidate_count} 条</span>}
            <button type="button" className="history-drawer-trigger" onClick={() => setHistoryDrawerOpen(true)}><span aria-hidden="true">◷</span> 最近批次 <b>{runs.length}</b></button>
            <label className="select-all-check" title={allCandidatesSelected ? "取消全选" : "全选"}>
              <input
                type="checkbox"
                checked={allCandidatesSelected}
                disabled={busy || !activeRun || selectableCandidates.length === 0}
                onChange={toggleSelectAllCandidates}
              />
            </label>
            <button type="button" className="select-all-button" disabled={busy || !activeRun || selectableCandidates.length === 0} onClick={selectAllCandidates}>全选</button>
            <button type="button" className="confirm-button" disabled={busy || selectedCandidates.length === 0} onClick={() => void confirmSelected()}>确认入池（{selectedCandidates.length}）</button>
          </div>
        </div>
        {!activeRun && <div className="result-empty"><span>⌕</span><strong>等待采集结果</strong><p>选择采集方向并提交条件，候选商品将在这里展示。</p></div>}
        {activeRun && activeRun.candidates.length === 0 && <div className="result-empty"><span>○</span><strong>本批次没有候选</strong><p>可以调整关键词、价格范围或关闭风险排除后重试。</p></div>}
        {activeRun && activeRun.candidates.length > 0 && filteredCandidates.length === 0 && (
          <div className="result-empty"><span>○</span><strong>没有符合 SKU 筛选条件的候选商品</strong><p>请调整最小/最大 SKU 数量后重新筛选。</p></div>
        )}
        {activeRun && filteredCandidates.length > 0 && (
          <div className="candidate-grid">
            {filteredCandidates.map((candidate) => {
              const selectable = candidate.status === "candidate";
              const checked = selectedCandidates.includes(candidate.candidate_id);
              const statusText = STATUS_LABELS[candidate.status] ?? candidate.status;
              return (
                <article key={candidate.candidate_id} className={`candidate-card status-${candidate.status} ${checked ? "is-checked" : "is-removed"} ${!selectable ? "is-locked" : ""}`}>
                  <label
                    className="candidate-keep"
                    title={!selectable ? statusText : checked ? "本次确认时保留" : "已从本次确认中剔除"}
                  >
                    <input type="checkbox" checked={checked} disabled={!selectable} onChange={() => toggleCandidate(candidate.candidate_id)} />
                    <span>{!selectable ? statusText : checked ? "保留" : "已剔除"}</span>
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
                  </div>
                  <div className="candidate-body">
                    <a href={candidate.source_url} target="_blank" rel="noreferrer" title={candidate.source_title}>{candidate.source_title}</a>
                    <div className="candidate-tags">
                      <span>{candidate.source_platform ?? "1688"}</span>
                      {candidate.query_keyword && <span title={`中心词：${candidate.query_keyword}`}>中心词 {candidate.query_keyword}</span>}
                      {candidate.selection_result_label && <span>{candidate.selection_result_label}</span>}
                      {candidate.status !== "candidate" && <span className="is-status">{statusText}</span>}
                      {candidate.risk_tags.slice(0, 2).map((risk) => <span className="is-risk" key={risk}>{risk}</span>)}
                    </div>
                    <div className="candidate-facts">
                      <span><b>{formatMoney(candidate.price_cny)}</b><em>价格</em></span>
                      <span title={candidate.listed_at ?? ""}><b>{formatListedAt(candidate.listed_at)}</b><em>上架时间</em></span>
                      <span><b>{candidate.min_order_quantity ?? "未知"}</b><em>起订</em></span>
                      <span title={`${candidate.source_variant_records.length} 个 SKU 规格`}><b>{candidate.source_variant_records.length || "未知"}</b><em>SKU</em></span>
                    </div>
                    <div className="candidate-meta"><span>{candidate.shop_name || "店铺待补齐"}</span><span>{candidate.location || "产地待补齐"}</span></div>
                    <div className="candidate-score"><span>选品分</span><b>{Number(candidate.selection_score).toFixed(1)}</b></div>
                    {candidate.selection_reasons.length > 0 && (
                      <div className="candidate-reasons">
                        {candidate.selection_reasons.slice(0, 2).map((reason) => <span key={reason}>{reason}</span>)}
                      </div>
                    )}
                    {selectable && <button className="reject-button" type="button" disabled={busy} onClick={() => void reject(candidate)}>排除并反馈</button>}
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </section>
            </div>
          </section>
        </div>
      )}
      <div
        className={`history-drawer-layer ${historyDrawerOpen ? "is-open" : ""}`}
        aria-hidden={!historyDrawerOpen}
        onMouseDown={(event) => {
          if (event.currentTarget === event.target) setHistoryDrawerOpen(false);
        }}
      >
        <aside className="history-drawer" role="dialog" aria-modal="true" aria-label="最近批次">
          <header className="history-drawer-header">
            <div><span>COLLECTION HISTORY</span><strong>最近批次</strong><small>选择批次后将加载对应候选商品</small></div>
            <button type="button" onClick={() => setHistoryDrawerOpen(false)} aria-label="关闭最近批次">×</button>
          </header>
          <div className="history-drawer-summary"><span>采集记录</span><b>{runs.length} 条</b></div>
          <div className="run-list history-drawer-list">
            {historyBusy && <div className="run-empty">正在读取批次…</div>}
            {!historyBusy && runs.length === 0 && <div className="run-empty">暂无采集记录<br /><small>完成首次采集后会显示在这里</small></div>}
            {runs.map((run) => {
              const duration = formatRunDuration(run.created_at, run.updated_at);
              return (
                <button
                  key={run.run_id}
                  type="button"
                  className={activeRun?.run_id === run.run_id ? "is-active" : ""}
                  onClick={() => {
                    setHistoryDrawerOpen(false);
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
      </div>
    </div>
  );
}
