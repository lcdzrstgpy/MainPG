import { FormEvent, useEffect, useMemo, useState } from "react";

import {
  collectByCriteria,
  confirmCandidates,
  getSelectionRun,
  listSelectionRuns,
  rejectCandidate,
} from "../api/dailySelectionApi";
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

function formatMoney(value: number | string | null): string {
  if (value === null || value === "") return "价格待补齐";
  const parsed = Number(value);
  return Number.isFinite(parsed) ? `¥${parsed.toFixed(2)}` : `¥${value}`;
}

type DailySelectionPageProps = {
  view?: "directions" | "collection";
  initialDirectionId?: string;
  onOpenCollection?: (directionId: string, directionName: string) => void;
};

export function DailySelectionPage({ view = "directions", initialDirectionId, onOpenCollection }: DailySelectionPageProps) {
  const collectionView = view === "collection";
  const [customDirections, setCustomDirections] = useState<Direction[]>(loadCustomDirections);
  const [removedDefaultDirectionIds, setRemovedDefaultDirectionIds] = useState<string[]>(loadRemovedDefaultDirectionIds);
  const directions = useMemo(
    () => [...DEFAULT_DIRECTIONS.filter((item) => !removedDefaultDirectionIds.includes(item.id)), ...customDirections],
    [customDirections, removedDefaultDirectionIds],
  );
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
  const [targetCount, setTargetCount] = useState(String(selectedDirection.target));
  const [excludeRisks, setExcludeRisks] = useState(true);
  const [runs, setRuns] = useState<DailySelectionRunSummary[]>([]);
  const [activeRun, setActiveRun] = useState<DailySelectionRun | null>(null);
  const [selectedCandidates, setSelectedCandidates] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [historyBusy, setHistoryBusy] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
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
  const [deleteMode, setDeleteMode] = useState(false);
  const [pendingDeleteDirection, setPendingDeleteDirection] = useState<Direction | null>(null);

  useEffect(() => {
    void refreshRuns();
  }, []);

  useEffect(() => {
    window.localStorage.setItem(CUSTOM_DIRECTIONS_KEY, JSON.stringify(customDirections));
  }, [customDirections]);

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

  function addCustomPreset(event: FormEvent<HTMLFormElement>) {
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
    const direction: Direction = {
      id: `custom-${Date.now()}`,
      name: presetName.trim(),
      keywords: normalizedKeywords,
      attributes: presetAttributes.trim(),
      price: [parsedMinPrice, parsedMaxPrice],
      target: parsedTarget,
      accent: presetAccent,
      site: presetSite,
      custom: true,
    };
    setCustomDirections((current) => [...current, direction]);
    chooseDirection(direction);
    setPresetDialogOpen(false);
    setPresetName("");
    setPresetKeywords("");
    setPresetAttributes("");
    setPresetMinPrice("3");
    setPresetMaxPrice("50");
    setPresetTarget("16");
    setNotice(`已添加自定义预设“${direction.name}”`);
  }

  function requestDirectionAction(direction: Direction) {
    if (deleteMode) {
      setPendingDeleteDirection(direction);
      return;
    }
    chooseDirection(direction);
    onOpenCollection?.(direction.id, direction.name);
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
      max_api_calls: 50,
      detail_count: 10,
      exclude_risks: excludeRisks,
      site,
    };
    const parsedMinPrice = numberOrUndefined(minPrice);
    const parsedMaxPrice = numberOrUndefined(maxPrice);
    const parsedMinMoq = numberOrUndefined(minMoq);
    if (parsedMinPrice !== undefined) criteria.min_price = parsedMinPrice;
    if (parsedMaxPrice !== undefined) criteria.max_price = parsedMaxPrice;
    if (parsedMinMoq !== undefined) criteria.min_moq = parsedMinMoq;

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
    try {
      const criteria = buildCriteria();
      const run = await collectByCriteria(criteria);
      setActiveRun(run);
      setSelectedCandidates([]);
      setNotice(`批次 ${run.run_id.slice(0, 8)} 已返回 ${run.candidate_count} 个候选`);
      await refreshRuns();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "采集请求失败");
    } finally {
      setBusy(false);
    }
  }

  async function openRun(runId: string) {
    setError("");
    setBusy(true);
    try {
      setActiveRun(await getSelectionRun(runId));
      setSelectedCandidates([]);
      document.querySelector(".daily-results")?.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "批次读取失败");
    } finally {
      setBusy(false);
    }
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
      {!collectionView && (
        <>
      <section className="daily-page-heading">
        <div>
          <p className="daily-kicker">DAILY PRODUCT DISCOVERY</p>
          <h1>每日选品</h1>
          <p>关键词或参考图驱动商品采集，筛选后确认进入产品处理。</p>
        </div>
      </section>

      {(error || notice) && (
        <div className={`daily-message ${error ? "is-error" : "is-success"}`} role="status">
          <span>{error ? "!" : "✓"}</span>{error || notice}
          <button type="button" onClick={() => { setError(""); setNotice(""); }}>×</button>
        </div>
      )}

      <section className="daily-panel direction-panel">
        <div className="daily-panel-title">
          <div><span className="title-icon">▣</span><strong>采集方向</strong></div>
          <div className="direction-panel-actions">
            <span>关键词为中心 · 每词独立采集</span>
            <button className={`toggle-delete-mode-button ${deleteMode ? "is-active" : ""}`} type="button" onClick={() => { setDeleteMode((current) => !current); setPendingDeleteDirection(null); }}>
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
                  className={`direction-card accent-${direction.accent} ${selectedDirectionId === direction.id ? "is-selected" : ""} ${deleteMode ? "is-delete-mode" : ""}`}
                  onClick={() => requestDirectionAction(direction)}
                  aria-describedby={deleteMode ? undefined : tooltipId}
                >
                  {deleteMode && <span className="direction-delete-indicator" aria-hidden="true">×</span>}
                  <div className="direction-card-heading">
                    <span className="direction-card-symbol" aria-hidden="true">{direction.name.slice(0, 1)}</span>
                    <div>
                      <div className="direction-card-meta">
                        <small>{SITE_LABELS[direction.site ?? "US"]}</small>
                        <span className={direction.custom ? "is-custom" : ""}>{direction.custom ? "自定义" : "系统预设"}</span>
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
                {!deleteMode && (
                  <div className="direction-hover-card" id={tooltipId} role="tooltip">
                    <div className="direction-hover-heading">
                      <span>{SITE_LABELS[direction.site ?? "US"]}{direction.custom ? " · 自定义" : " · 系统预设"}</span>
                      <strong>{direction.name}</strong>
                    </div>
                    <dl>
                      <div><dt>采集关键词</dt><dd>{direction.keywords.join("、")}</dd></div>
                      <div><dt>关注属性</dt><dd>{direction.attributes}</dd></div>
                      <div><dt>价格范围</dt><dd>{direction.price[0]}–{direction.price[1]} 元</dd></div>
                      <div><dt>最低起订</dt><dd>2 件</dd></div>
                      <div><dt>目标候选</dt><dd>{direction.target} 个</dd></div>
                    </dl>
                    <small>点击预设打开独立采集面板</small>
                  </div>
                )}
              </div>
            );
          })}
          {!deleteMode && <button type="button" className="add-direction-card" onClick={() => { setPresetError(""); setPresetDialogOpen(true); }}>
            <span aria-hidden="true">＋</span>
            <strong>添加自定义预设</strong>
            <small>保存常用关键词与筛选条件</small>
          </button>}
        </div>
      </section>

      {presetDialogOpen && (
        <div className="preset-dialog-backdrop" role="presentation" onMouseDown={() => setPresetDialogOpen(false)}>
          <form className="preset-dialog" onSubmit={addCustomPreset} onMouseDown={(event) => event.stopPropagation()}>
            <div className="preset-dialog-header">
              <div><span>CUSTOM PRESET</span><strong>添加采集方向</strong></div>
              <button type="button" onClick={() => setPresetDialogOpen(false)} aria-label="关闭添加预设">×</button>
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
              <button type="button" onClick={() => setPresetDialogOpen(false)}>取消</button>
              <button type="submit">保存预设</button>
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
          <section className="daily-collection-surface" aria-label="每日选品采集面板">
            <header className="daily-drawer-header">
              <div>
                <span>DAILY SELECTION</span>
                <strong>采集与候选商品</strong>
              </div>
              <span className="collection-workspace-context">来源：每日选品</span>
            </header>
            <div className="daily-drawer-body">
              {(error || notice) && (
                <div className={`daily-message ${error ? "is-error" : "is-success"}`} role="status">
                  <span>{error ? "!" : "✓"}</span>{error || notice}
                  <button type="button" onClick={() => { setError(""); setNotice(""); }}>×</button>
                </div>
              )}
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
            <label><span>每个关键词</span><input type="number" min="1" value={targetCount} onChange={(event) => setTargetCount(event.target.value)} /></label>
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
            <label><span>最低起订量</span><input type="number" min="1" value={minMoq} onChange={(event) => setMinMoq(event.target.value)} /></label>
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
            <span>{platform === "1688" ? "1688 每批最多调用 50 次 API，并拉取 10 条详情补充。" : "淘宝渠道当前仅展示前端交互，不会发送采集请求或产生 API 费用。"}</span>
            <button className="collect-button" type="submit" disabled={busy}>{busy ? "正在采集…" : "开始采集"}</button>
          </div>
        </form>

        <aside className="daily-panel run-history-panel">
          <div className="daily-panel-title"><div><span className="title-icon">◷</span><strong>最近批次</strong></div><span>{runs.length} 条</span></div>
          <div className="run-list">
            {historyBusy && <div className="run-empty">正在读取批次…</div>}
            {!historyBusy && runs.length === 0 && <div className="run-empty">暂无采集记录<br /><small>完成首次采集后会显示在这里</small></div>}
            {runs.map((run) => (
              <button key={run.run_id} type="button" className={activeRun?.run_id === run.run_id ? "is-active" : ""} onClick={() => void openRun(run.run_id)}>
                <span><strong>{run.run_id.slice(0, 8)}</strong><small>{formatDate(run.created_at)}</small></span>
                <span><b>{run.candidate_count}</b><small>{STATUS_LABELS[run.status] ?? run.status}</small></span>
              </button>
            ))}
          </div>
        </aside>
      </div>

      <section className="daily-panel daily-results">
        <div className="daily-panel-title results-title">
          <div><span className="title-icon">◇</span><strong>候选商品</strong></div>
          <div>
            {activeRun && <span>批次 {activeRun.run_id.slice(0, 8)} · {activeRun.candidate_count} 条</span>}
            <button type="button" className="confirm-button" disabled={busy || selectedCandidates.length === 0} onClick={() => void confirmSelected()}>确认入池（{selectedCandidates.length}）</button>
          </div>
        </div>
        {!activeRun && <div className="result-empty"><span>⌕</span><strong>等待采集结果</strong><p>选择采集方向并提交条件，候选商品将在这里展示。</p></div>}
        {activeRun && activeRun.candidates.length === 0 && <div className="result-empty"><span>○</span><strong>本批次没有候选</strong><p>可以调整关键词、价格范围或关闭风险排除后重试。</p></div>}
        {activeRun && activeRun.candidates.length > 0 && (
          <div className="candidate-grid">
            {activeRun.candidates.map((candidate) => {
              const selectable = candidate.status === "candidate";
              const checked = selectedCandidates.includes(candidate.candidate_id);
              return (
                <article key={candidate.candidate_id} className={`candidate-card status-${candidate.status} ${checked ? "is-checked" : ""}`}>
                  <div className="candidate-image">
                    {candidate.main_image_url ? <img src={candidate.main_image_url} alt="" loading="lazy" /> : <span>暂无图片</span>}
                    <label className="candidate-check"><input type="checkbox" checked={checked} disabled={!selectable} onChange={() => toggleCandidate(candidate.candidate_id)} /><span>选择</span></label>
                    <b>{STATUS_LABELS[candidate.status] ?? candidate.status}</b>
                  </div>
                  <div className="candidate-body">
                    <a href={candidate.source_url} target="_blank" rel="noreferrer" title={candidate.source_title}>{candidate.source_title}</a>
                    <div className="candidate-price"><strong>{formatMoney(candidate.price_cny)}</strong><span>MOQ {candidate.min_order_quantity ?? "—"}</span></div>
                    <div className="candidate-meta"><span>{candidate.shop_name || "店铺待补齐"}</span><span>{candidate.location || "产地待补齐"}</span></div>
                    <div className="candidate-score"><span>选品分</span><b>{Number(candidate.selection_score).toFixed(1)}</b></div>
                    {(candidate.selection_reasons.length > 0 || candidate.risk_tags.length > 0) && (
                      <div className="candidate-tags">
                        {candidate.selection_reasons.slice(0, 2).map((reason) => <span key={reason}>{reason}</span>)}
                        {candidate.risk_tags.slice(0, 2).map((risk) => <span className="is-risk" key={risk}>{risk}</span>)}
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
    </div>
  );
}
