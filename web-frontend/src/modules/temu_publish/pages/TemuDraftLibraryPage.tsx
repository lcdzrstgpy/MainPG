import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { httpBlob } from "../../../transport/http/client";
import { temuPublishApi } from "../api/temuPublishApi";
import { TemuDraftBulkBar, type BulkField } from "../components/TemuDraftBulkBar";
import { localStore, parsePublishError } from "../data/localStore";
import type { DraftCategoryMemo, ProductTemplate, VariantTemplate } from "../data/localStore";
import { buildDraftForPublish } from "../data/batchPublish";
import type { TemuDraftEdit, TemuDraftEntry, TemuDraftLibrary, TemuPublishPrefill } from "../types";
import "../styles/temuPublish.css";

type Filter = "all" | "ai" | "pod";

/** 「编辑」「分类」「备注」「隐藏」都只改本机：写 localStorage，刷新后仍生效，但不回写产品处理。 */
const DRAFT_EDITS_KEY = "temu_draft_edits_v1";
const DRAFT_CATS_KEY = "temu_draft_categories_v1";
const DRAFT_HIDDEN_KEY = "temu_draft_hidden_v1";
const DRAFT_REMARKS_KEY = "temu_draft_remarks_v1";

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
    return <img className="temu-draft-thumb" src={src} alt={alt} loading="lazy" ref={thumbRef} />;
  return (
    <button
      type="button"
      className="temu-draft-thumb-button"
      onClick={() => onPreview(src, alt)}
      title="点击放大预览"
      ref={thumbRef}
    >
      <img className="temu-draft-thumb" src={src} alt={alt} loading="lazy" />
    </button>
  );
}

/**
 * 草稿里的金额都是「元」（POD 导出表、AI 结果口径一致），发布页用「分」。
 * 带入时统一 ×100，避免把 ¥150 当成 1.5 元发出去。
 */
function yuanToCents(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const number = Number(value);
  if (!Number.isFinite(number) || number <= 0) return "";
  return String(Math.round(number * 100));
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
  return memo[entry.id]?.label || entry.category_name || "发布页选类目";
}

function entryPrefill(
  entry: TemuDraftEntry,
  memo?: Record<string, DraftCategoryMemo>,
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
    // 草稿是「元」，发布页是「分」
    supplierPrice: yuanToCents(entry.declared_price),
    suggestedPrice: yuanToCents(entry.suggested_price),
    // 库存 0 / 空：不带进去（半托管必须 ≥1，带 0 只会让人以为可以直接提交）
    stock: Number(entry.stock) > 0 ? numericText(entry.stock) : "",
    specName1: entry.spec_name_1 || "",
    specValue1: entry.spec_value_1 || "",
    specName2: entry.spec_name_2 || "",
    specValue2: entry.spec_value_2 || "",
    description: entry.description || "",
    sourceUrl: entry.source_url || "",
    // 带上 id 和上次选过的类目：发布页选完会回写，下次带入直接选中
    draftId: entry.id,
    categoryPath: memo?.[entry.id]?.path,
    // POD 导出里申报价 / 重量 / 尺寸是模板占位值，界面上要提醒人工核对
    needsReview:
      entry.source === "pod" ? ["申报价", "重量", "长宽高"] : undefined,
  };
}

export function TemuDraftLibraryPage({
  onUseDraft,
  onCreateProduct,
}: {
  onUseDraft: (prefill: TemuPublishPrefill) => void;
  onCreateProduct?: () => void;
}) {
  const [data, setData] = useState<TemuDraftLibrary | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
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
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const [productTemplates, setProductTemplates] = useState<ProductTemplate[]>(() =>
    localStore.productTemplates(),
  );
  const [variantTemplates, setVariantTemplates] = useState<VariantTemplate[]>(() =>
    localStore.variantTemplates(),
  );
  const [picker, setPicker] = useState<"product" | "variant" | null>(null);
  // 点「用于发布」先弹基础信息确认，确认后才带进发布页
  const [confirmEntry, setConfirmEntry] = useState<TemuDraftEntry | null>(null);
  const [failures, setFailures] = useState(() => localStore.failures());
  // 草稿 → 上次在发布页选过的 Temu 类目：进页面/切回来都重读，刚选完的能立刻显示
  const [categoryMemo, setCategoryMemo] = useState<Record<string, DraftCategoryMemo>>(() =>
    localStore.draftCategories(),
  );
  const [showFailures, setShowFailures] = useState(false);
  const [productTpl, setProductTpl] = useState("");
  const [variantTpl, setVariantTpl] = useState("");
  const [batchState, setBatchState] = useState<{ running: boolean; text: string }>({
    running: false,
    text: "",
  });
  // 批量发布弹窗：勾完直接发，模板在弹窗里选（不再要求先去菜单里「引用产品模板」）
  const [batchOpen, setBatchOpen] = useState(false);
  // 批量发布里被「24 小时内重复提交」挡下的条目：不算失败，单独列出来
  const [batchSkipped, setBatchSkipped] = useState<string[]>([]);
  const [editing, setEditing] = useState<TemuDraftEntry | null>(null);
  const [editForm, setEditForm] = useState<TemuDraftEdit>({});

  const load = async (refresh = false) => {
    setLoading(true);
    setError("");
    try {
      setData(await temuPublishApi.draftLibrary(80, refresh));
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    const sync = () => setCategoryMemo(localStore.draftCategories());
    window.addEventListener("focus", sync);
    document.addEventListener("visibilitychange", sync);
    return () => {
      window.removeEventListener("focus", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, []);

  const visibleEntries = useMemo(() => {
    const all = (data?.entries ?? [])
      .filter((entry) => !hidden.includes(entry.id))
      .map((entry) => (edits[entry.id] ? { ...entry, ...edits[entry.id] } : entry));
    return filter === "all" ? all : all.filter((entry) => entry.source === filter);
  }, [data, filter, edits, hidden]);

  const totalPages = Math.max(1, Math.ceil(visibleEntries.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const entries = useMemo(
    () => visibleEntries.slice((currentPage - 1) * pageSize, currentPage * pageSize),
    [visibleEntries, currentPage],
  );

  useEffect(() => {
    setPage(1);
  }, [filter]);

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

  const selectedIds = () => Array.from(selected);

  /** 「用于发布」二次确认：确认后才把这条草稿带进发布页。 */
  const confirmUseDraft = () => {
    if (!confirmEntry) return;
    onUseDraft(entryPrefill(confirmEntry, categoryMemo));
    setConfirmEntry(null);
  };

  /** 勾选的草稿（批量发布/批量编辑都按这个口径，和表格里看到的行一致）。 */
  const batchTargets = useMemo(
    () => visibleEntries.filter((entry) => selected.has(entry.id)),
    [visibleEntries, selected],
  );

  /** 打开「批量发布」弹窗：勾选的草稿按所选模板逐条提交。 */
  const openBatchDialog = () => {
    if (batchState.running) return;
    if (!selected.size) {
      setError("先勾选要发布的产品");
      return;
    }
    // 只有一个模板就直接选中，省一次点击；有多个则让用户自己挑
    if (!productTpl && productTemplates.length) setProductTpl(productTemplates[0].name);
    setBatchOpen(true);
  };

  /** 批量发布：勾选的草稿逐条套「产品模板 + 变种模板」→ 组装 → 提交，失败的进失败列表。 */
  const runBatchPublish = async () => {
    // 上一批还在跑（接口非幂等）：不允许再开一批并行提交
    if (batchState.running) return;
    const template = productTemplates.find((item) => item.name === productTpl);
    if (!template) {
      setError("先选一个产品模板（没有就去「发布商品」页存一个）");
      return;
    }
    const targets = visibleEntries.filter((entry) => selected.has(entry.id));
    if (!targets.length) {
      setError("先勾选要发布的产品");
      return;
    }
    setBatchOpen(false);

    const variant = variantTemplates.find((item) => item.name === variantTpl) ?? null;
    setBatchState({ running: true, text: `0/${targets.length}` });
    setBatchSkipped([]);
    let ok = 0;
    let fail = 0;
    let skipped = 0;
    for (const [index, entry] of targets.entries()) {
      setBatchState({
        running: true,
        text: `${index + 1}/${targets.length} · ${entry.title.slice(0, 16)}`,
      });
      try {
        const draft = await buildDraftForPublish(entryPrefill(entry, categoryMemo), template, variant);
        await temuPublishApi.publish(draft);
        // 批量发布用的是产品模板的类目：记到这条草稿上，列表里就能看到类目路径
        localStore.rememberDraftCategory(entry.id, template.categoryPath);
        ok += 1;
      } catch (cause) {
        const reasonText = errorMessage(cause);
        // 台账挡下的重复提交：这条大概率已经在店铺里了，不算失败、也不进失败历史
        if (/重复提交/.test(reasonText)) {
          skipped += 1;
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
          hint: parsed.hint,
          traceId: parsed.traceId,
          // 带上重试要用的字段 + 批量用的类目，否则「重试」回填不出类目，只会再失败一次
          prefill: {
            ...entryPrefill(entry, categoryMemo),
            categoryPath: categoryMemo?.[entry.id]?.path ?? template.categoryPath,
          },
        });
      }
    }
    const skippedText = skipped ? `，跳过 ${skipped} 条（24 小时内提交过）` : "";
    setBatchState({
      running: false,
      text: `完成：成功 ${ok} 条${skippedText}，失败 ${fail} 条（失败原因见发布页列表）`,
    });
    // 成功的已经从草稿库发出去了：清空勾选，避免下一次误点又提交一遍
    if (ok > 0) setSelected(new Set());
    setProductTemplates(localStore.productTemplates());
    void load();
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

  const bulkRemark = () => {
    const ids = selectedIds();
    if (!ids.length) return;
    const text = window.prompt(`给选中的 ${ids.length} 条加备注：`) ?? "";
    if (!text.trim()) return;
    const next = { ...remarks };
    ids.forEach((id) => {
      next[id] = text.trim();
    });
    setRemarks(next);
    saveMap(scoped(DRAFT_REMARKS_KEY), next);
  };

  const bulkDelete = () => {
    const ids = selectedIds();
    if (!ids.length) return;
    if (!window.confirm(`把选中的 ${ids.length} 条从草稿库隐藏？（只影响本机展示，可随时恢复）`)) return;
    const next = Array.from(new Set([...hidden, ...ids]));
    setHidden(next);
    saveMap(scoped(DRAFT_HIDDEN_KEY), next);
    setSelected(new Set());
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
      // 库存 0 / 空：不带进去（半托管必须 ≥1，带 0 只会让人以为可以直接提交）
    stock: Number(entry.stock) > 0 ? numericText(entry.stock) : "",
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
      <header className="temu-publish-head">
        <div>
          <p className="eyebrow">DRAFT LIBRARY</p>
          <h1>草稿库</h1>
          <p>汇总 AI 处理与 POD 的成品数据，选中后可直接带入「发布商品」。</p>
        </div>
        <div className="temu-publish-head-actions">
          <button type="button" className="temu-btn" onClick={() => void load(true)} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
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
        <div className="temu-card-head">
          <div className="temu-draft-tabs">
            {(
              [
                ["all", `全部 ${data?.counts.total ?? 0}`],
                ["ai", `AI 处理 ${data?.counts.ai ?? 0}`],
                ["pod", `POD ${data?.counts.pod ?? 0}`],
              ] as Array<[Filter, string]>
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={`temu-draft-tab${filter === key ? " is-active" : ""}`}
                onClick={() => {
                  setFilter(key);
                  // 切页签清空勾选：勾选数与「当前页可见待发布条数」必须一致，
                  // 否则批量发布只发可见的那些，界面上的数字会误导人
                  setSelected(new Set());
                }}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="temu-muted">
            最近 8 个 AI 批次 / 6 个 POD 批次
            {productTpl ? ` · 产品模板：${productTpl}` : ""}
            {variantTpl ? ` · 变种模板：${variantTpl}` : ""}
          </span>
          <button
            type="button"
            className={`temu-fail-entry${failures.length ? " has-dot" : ""}`}
            onClick={() => setShowFailures((value) => !value)}
            title="发布失败的产品会记在这里，点开看原因并可重试"
          >
            发布失败历史{failures.length ? ` (${failures.length})` : ""}
            {failures.length > 0 && <em className="temu-fail-dot" aria-hidden="true" />}
          </button>
        </div>

          <TemuDraftBulkBar
            selectedCount={selected.size}
            totalCount={visibleEntries.length}
            hiddenCount={hidden.length}
            categories={categories}
            onApplyField={applyBulkField}
            onRemark={bulkRemark}
            onDelete={bulkDelete}
            onMoveCategory={moveCategory}
            onAddCategory={addCategory}
            onRestoreHidden={restoreHidden}
            onCreateProduct={() => onCreateProduct?.()}
            canBatchPublish={Boolean(productTpl)}
            batchRunning={batchState.running}
            templateCount={productTemplates.length + variantTemplates.length}
            onBatchPublish={openBatchDialog}
            onPickTemplate={setPicker}
          />

          {(batchState.running || batchState.text) && (
            <p className="temu-batch-progress">
              {batchState.running ? "批量发布中：" : ""}
              {batchState.text}
            </p>
          )}

          {batchSkipped.length > 0 && (
            <p className="temu-muted">
              已跳过（24 小时内提交过，确认要重复上架请回发布页单条发）：
              {batchSkipped.slice(0, 3).map((title) => title.slice(0, 20)).join("、")}
              {batchSkipped.length > 3 ? ` 等 ${batchSkipped.length} 条` : ""}
            </p>
          )}

          {showFailures && (
            <div className="temu-fail-panel">
              <div className="temu-fail-panel-head">
                <strong>发布失败历史（{failures.length}）</strong>
                {failures.length > 0 && (
                  <button
                    type="button"
                    className="temu-link"
                    onClick={() => {
                      localStore.clearFailures();
                      setFailures([]);
                    }}
                  >
                    清空
                  </button>
                )}
              </div>
              {failures.length === 0 ? (
                <p className="temu-muted">暂无失败记录。发布失败时会自动记在这里。</p>
              ) : (
                failures.map((item) => (
                  <div className="temu-fail-row" key={item.id}>
                    <div className="temu-fail-main">
                      <strong>{item.draftTitle}</strong>
                      <span className="temu-muted">
                        {item.siteLabel} · {item.categoryLabel} · {item.skuLabel} · {item.priceLabel} · {item.at}
                      </span>
                      <em>失败原因：{item.reason}</em>
                      <em className="is-hint">解决方案：{item.hint}</em>
                      {item.traceId && <span className="temu-muted">Trace {item.traceId}</span>}
                    </div>
                    <div className="temu-fail-ops">
                      <button
                        type="button"
                        className="temu-btn temu-btn-sm"
                        onClick={() => {
                          if (item.prefill) onUseDraft(item.prefill as never);
                          setShowFailures(false);
                        }}
                      >
                        重试
                      </button>
                      <button
                        type="button"
                        className="temu-link"
                        onClick={() => setFailures(localStore.removeFailure(item.id))}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
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
          <p className="temu-empty">
            {loading ? "正在读取草稿…" : "暂无可用草稿：先完成一批 AI 处理或 POD 生成。"}
          </p>
        ) : (
          <div className="temu-draft-table-wrap">
            <table className="temu-draft-table">
              <thead>
                <tr>
                  <th className="col-check">
                    <input type="checkbox" checked={allSelected} onChange={toggleSelectAll} aria-label="全选草稿" />
                  </th>
                  <th className="col-image">图片</th>
                  <th className="col-title">产品标题</th>
                  <th className="col-site">站点</th>
                  <th className="col-sku">SKU 货号</th>
                  <th
                    className="col-price"
                    title="没在预览里改过申报价的，按导出表同一规则计算：成本 × 4，下限 ¥150"
                  >
                    申报价格
                  </th>
                  <th className="col-time">时间</th>
                  <th className="col-actions">操作</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((entry) => (
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
                        {entry.images.length > 1 ? (
                          <span className="temu-draft-more">+{entry.images.length - 1}</span>
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
                    <td className="col-site">
                      美国
                      <small
                        className={categoryMemo[entry.id]?.label ? "is-picked" : undefined}
                        title={siteCategoryLabel(entry, categoryMemo)}
                      >
                        {siteCategoryLabel(entry, categoryMemo)}
                      </small>
                    </td>
                    <td className="col-sku">{entry.sku || "—"}</td>
                    <td className="col-price">
                      {entry.declared_price == null || entry.declared_price === ""
                        ? "—"
                        : `¥${Number(entry.declared_price).toFixed(2)}`}
                    </td>
                    <td className="col-time">
                      {(entry.updated_at || "").slice(0, 16).replace("T", " ") || "—"}
                    </td>
                    <td className="col-actions">
                      <button
                        type="button"
                        className="temu-btn"
                        title="带这条草稿进发布页，改完所有信息后再提交"
                        onClick={() => onUseDraft(entryPrefill(entry, categoryMemo))}
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

        {visibleEntries.length > 0 && (
          <div className="temu-draft-pager">
            <span>
              第 {visibleEntries.length === 0 ? 0 : (currentPage - 1) * pageSize + 1}-
              {Math.min(currentPage * pageSize, visibleEntries.length)} 条，共 {visibleEntries.length} 条
            </span>
            <div className="temu-draft-pager-ctrl">
              <button type="button" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>
                ‹
              </button>
              <button
                type="button"
                disabled={currentPage >= totalPages}
                onClick={() => setPage(currentPage + 1)}
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
                  if (Number.isFinite(next)) setPage(Math.min(Math.max(1, next), totalPages));
                }}
              />
              <span>/ {totalPages} 页 · {pageSize} 条/页</span>
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

                <ul className="temu-batch-list">
                  {batchTargets.slice(0, 6).map((entry) => (
                    <li key={entry.id}>
                      <span>{entry.title || entry.subtitle || "（没有标题）"}</span>
                      <em>{entry.source === "ai" ? "AI" : "POD"}</em>
                    </li>
                  ))}
                  {batchTargets.length > 6 ? <li className="is-more">… 还有 {batchTargets.length - 6} 条</li> : null}
                </ul>

                {productTemplates.length === 0 ? (
                  <p className="temu-confirm-warn">
                    还没有产品模板：批量发布要用模板里的类目和属性，先去「发布商品」页填好一款再点「存为产品模板」。
                  </p>
                ) : (
                  <p className="temu-edit-note">
                    接口非幂等：重复提交会在店铺里多出商品；中途某条失败只跳过它，原因进「发布失败历史」。
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
                    disabled={!productTpl || batchState.running || !batchTargets.length}
                    onClick={() => void runBatchPublish()}
                  >
                    {batchState.running ? "提交中…" : `开始发布（${batchTargets.length} 条）`}
                  </button>
                </div>
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
          <img src={preview.src} alt={preview.alt} onClick={(event) => event.stopPropagation()} />
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
