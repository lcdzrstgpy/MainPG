import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ppDownload, ppRequest, type ApiContext } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import {
  excludePreviewItems,
  exportMiaoshouPreview,
  fetchProductPreview,
  fetchProductPreviewMedia,
  finalizeProductPreview,
  getListingAdvice,
  getPreviewFinalizeRun,
  importPreviewAssetFromUrl,
  listPreviewFinalizeRuns,
  miaoshouDownloadUrl,
  miaoshouFileName,
  regeneratePreviewDetail,
  retryMediaAsset,
  retryPreviewFinalizeRun,
  saveProductPreview,
  uploadPreviewAssets,
  type PreviewSavePayload,
  type ListingAdvice,
} from '../api/productProcessingApi';
import { DimensionChangeSetReview } from '../components/DimensionChangeSetReview';
import {
  BatchCompositionDialog,
  type BatchCompositionItem,
} from '../components/BatchCompositionDialog';
import { EXPORT_FORMAT_LABELS, PrecheckFinalizeProgress } from '../components/PrecheckFinalizeProgress';
import { PrecheckImageManager } from '../components/PrecheckImageManager';
import {
  compositionSlogan,
  compositionSubtitle,
} from '../data/compositionSlogans';
import {
  PrecheckSkuManager,
  type VariantImageMode,
  type VariantImageOption,
  type VariantRef,
} from '../components/PrecheckSkuManager';
import { variantLabel } from '../utils/skuFilter';
import {
  addAssets,
  assetThumbUrl,
  restoreRemovedAsset,
  type PrecheckImageTarget,
  type RemovedAssetUndo,
} from '../data/precheckImageModel';
import {
  createPrecheckFinalizeRefresh,
  type PrecheckFinalizeRefresh,
} from '../data/precheckFinalizeRefresh';
import { resolveFinalizeRequest, stableStringify } from '../data/precheckFinalizeRequest';
import type {
  MiaoshouTemplateKind,
  PreviewCoreFields,
  PreviewExportFormat,
  PreviewFinalizeRun,
  PreviewImageAsset,
  PreviewImageManifest,
  PreviewItem,
  PreviewMediaPayload,
  PreviewResponse,
  PreviewTextReviewStatus,
  ShippingPackageRecord,
  ShippingPackageRecordOverride,
} from '../types';
import '../styles/ProductProcessingVerifyPage.css';

const LocalCompositionDrawer = lazy(
  () => import('../components/LocalCompositionDrawer'),
);

const API_BASE = '/api/product-processing';
const MAX_UPLOAD_FILES = 20;
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const ALLOWED_UPLOAD_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
const PRECHECK_PAGE_SIZES = [10, 30, 50, 100] as const;

// 第 4 格尺寸图状态文案：预估必须一眼可见，避免把估算尺寸当确认值导出。
const DIMENSION_IMAGE_LABELS: Record<string, string> = {
  confirmed: '尺寸：已确认',
  estimated: '尺寸：预估',
  missing: '尺寸：缺失',
  failed: '尺寸图失败',
  pending: '尺寸图生成中',
};

const formatCmValue = (value: number | null | undefined): string =>
  value == null ? '' : String(Math.round(value * 10) / 10);
const PRECHECK_DEFAULT_PAGE_SIZE = 30;
// 首屏只投影当前页（30 条）的图片；其余链接翻页时按需拉取，避免 200+ 链接首屏卡住。
const PRECHECK_MEDIA_PAGE_SIZE = 30;
// 元数据分页：首屏只拿 30 条；剩余元数据在后台按 100 条/页渐进补齐。
const PRECHECK_ITEM_PAGE_SIZE = 30;
const PRECHECK_ITEM_PREFETCH_SIZE = 100;
// 保存/导出前的图片清单补齐：按 30 条分批请求，每批最多重试 2 次。
// 一次性把 200+ 条草稿塞进同一个请求，会撞上 30s 前端超时；超时后静默返回空，
// 导出就会提交空 manifest，后台生成表格时报「没有可导出的 AI 处理图」。
const PRECHECK_MEDIA_HYDRATE_BATCH_SIZE = 30;
const PRECHECK_MEDIA_HYDRATE_RETRIES = 2;
const PRECHECK_MEDIA_RETRY_DELAY_MS = 500;

type MediaFetchResult = {
  payloads: Record<string, PreviewMediaPayload>;
  /** 重试后仍失败的草稿 id（导出/保存前必须为零）。 */
  failed: number[];
};

type Props = {
  taskId: number;
  initialChangeSetId?: string;
  onOpenDimensionItem: (taskId: number, taskItemId: number) => void;
  isActive?: boolean;
};

type ItemEdits = {
  title?: string;
  description?: string;
  imageManifest?: PreviewImageManifest;
  addedAssets?: PreviewImageAsset[];
  core_fields?: PreviewCoreFields;
  shipping_package_records?: Record<string, ShippingPackageRecordOverride>;
  /** SKU 规格图导出策略：source=规格原图 / main=全部用商品主图替代。 */
  variantImageMode?: VariantImageMode;
  /** 被整行剔除（导出时不进表）的 SKU 变种键。 */
  excludedVariantKeys?: string[];
  /** 逐个 SKU 的规格图替换：键=变种键，值=预览资产 ID 或 http(s) 直链。 */
  variantImageOverrides?: Record<string, string>;
};

/** 侧栏里可用于替换 SKU 规格图的图片来源及分组标题。 */
const VARIANT_IMAGE_ORIGIN_GROUPS: Array<{ origin: PreviewImageAsset['origin']; label: string }> = [
  { origin: 'generated', label: 'AI 处理图' },
  { origin: 'upload', label: '本地导入的图片' },
  { origin: 'dimension', label: '尺寸图' },
];

type UndoSnackbar = {
  draftId: number;
  undo: RemovedAssetUndo;
  expiresAt: number;
};

function api(): ApiContext {
  return productProcessingApiContext();
}

function taskStatusLabel(status: string): string {
  return ({
    queued: '等待处理',
    running: '处理中',
    paused: '已暂停',
    cancelled: '已终止，仅保留成功商品',
    completed: '已完成',
    partial_failure: '部分完成',
    failed: '任务失败',
  })[status] || status;
}

const PROVENANCE_META: Record<string, { label: string; tone: string }> = {
  source: { label: '采集值', tone: 'ok' },
  manual: { label: '手动', tone: 'attn' },
  ai: { label: 'AI预估', tone: 'ok' },
};

function provenanceBadge(
  provenance: PreviewItem['dimension_provenance'],
  confidence: PreviewItem['dimension_confidence'],
  field: 'length_cm' | 'width_cm' | 'height_cm' | 'weight_g',
): ReactNode {
  const value = provenance?.[field];
  const meta = (value && PROVENANCE_META[value]) || PROVENANCE_META.ai;
  const confidenceValue = confidence?.[field];
  const confidenceLabel = confidenceValue === 'low'
    ? '低置信'
    : confidenceValue === 'medium'
      ? '中置信'
      : confidenceValue === 'high'
        ? '高置信'
        : '';
  const label = value === 'ai' && confidenceLabel ? `${meta.label}·${confidenceLabel}` : meta.label;
  const tone = value === 'ai' && confidenceValue === 'low' ? 'attn' : meta.tone;
  return (
    <span className={`precheck-provenance-badge tone-${tone}`} title={`该字段来源：${meta.label}${confidenceLabel ? `；${confidenceLabel}` : ''}`}>
      {label}
    </span>
  );
}

function cloneManifest(manifest: PreviewImageManifest): PreviewImageManifest {
  return {
    main_asset_id: manifest.main_asset_id,
    carousel_asset_ids: [...manifest.carousel_asset_ids],
    detail_asset_ids: [...manifest.detail_asset_ids],
    library_asset_ids: [...(manifest.library_asset_ids ?? [])],
    semantic_asset_ids: { ...manifest.semantic_asset_ids },
  };
}

function mergeAssets(...groups: Array<PreviewImageAsset[] | undefined>): PreviewImageAsset[] {
  const byId = new Map<string, PreviewImageAsset>();
  for (const assets of groups) {
    for (const asset of assets ?? []) byId.set(asset.id, asset);
  }
  return Array.from(byId.values());
}

function readSession(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeSession(key: string, value: string): void {
  try {
    window.sessionStorage.setItem(key, value);
  } catch {
    // Private browsing/storage denial must not block the server-side workflow.
  }
}

function removeSession(key: string): void {
  try {
    window.sessionStorage.removeItem(key);
  } catch {
    // Best-effort cleanup only.
  }
}

/** 历史导出列表的状态文案（与结果卡片同一口径）。 */
const FINALIZE_RUN_STATUS_LABELS: Record<PreviewFinalizeRun['status'], string> = {
  queued: '等待发布',
  publishing: '发布中',
  publish_failed: '发布失败',
  stale: '版本已变化',
  completed: '已完成',
};

/** ISO 时间转成本地「年-月-日 时:分」，供历史导出列表区分是哪一次。 */
function formatFinalizeTime(value?: string): string {
  if (!value) return '时间未知';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  const pad = (input: number) => String(input).padStart(2, '0');
  return `${parsed.getFullYear()}-${pad(parsed.getMonth() + 1)}-${pad(parsed.getDate())} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

function mergePreviewItems(base: PreviewItem[], extra: PreviewItem[]): PreviewItem[] {
  if (!extra.length) return base;
  const seen = new Set(base.map((item) => item.item_id));
  return [...base, ...extra.filter((item) => !seen.has(item.item_id))];
}

export function ProductProcessingPrecheckPage({ taskId, initialChangeSetId, onOpenDimensionItem, isActive = true }: Props) {
  const ctx = useMemo(() => api(), []);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const previewRef = useRef<PreviewResponse | null>(null);
  const itemsGenerationRef = useRef(0);
  const itemsLoadPromiseRef = useRef<Promise<PreviewItem[]> | null>(null);
  const itemsLoadedCountRef = useRef(0);
  const [itemsLoading, setItemsLoading] = useState(false);
  useEffect(() => {
    previewRef.current = preview;
  }, [preview]);
  const [edits, setEdits] = useState<Record<number, ItemEdits>>({});
  const [retryingMediaAssetIds, setRetryingMediaAssetIds] = useState<Set<string>>(new Set());
  const [regeneratingDetailDraftIds, setRegeneratingDetailDraftIds] = useState<Set<number>>(new Set());
  const [loading, setLoading] = useState(false);
  const [pendingUploads, setPendingUploads] = useState(0);
  const [saving, setSaving] = useState(false);
  const [startingFinalize, setStartingFinalize] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [msExporting, setMsExporting] = useState<MiaoshouTemplateKind | null>(null);
  const [finalizeRun, setFinalizeRun] = useState<PreviewFinalizeRun | null>(null);
  // 该任务的历史导出记录：结果卡片只活在当前页面会话里，关掉页面就找不到表格了，
  // 这份列表从服务端读回，保证「文件弄丢了」始终有入口重新下载。
  const [historyRuns, setHistoryRuns] = useState<PreviewFinalizeRun[]>([]);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [activeImage, setActiveImage] = useState<string | null>(null);
  const [compositionDraftId, setCompositionDraftId] = useState<number | null>(null);
  const [batchComposeOpen, setBatchComposeOpen] = useState(false);
  const [undoSnackbar, setUndoSnackbar] = useState<UndoSnackbar | null>(null);
  const refreshRef = useRef<PrecheckFinalizeRefresh | null>(null);
  // 已经同步进历史列表的 run id，避免完成态轮询时反复刷新历史。
  const historySyncedRunRef = useRef('');
  const [pageSize, setPageSize] = useState<number>(PRECHECK_DEFAULT_PAGE_SIZE);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  // 默认展示全部链接（方便运营核对失败项）；导出时只提交 exportable 的链接，
  // 失败链接不会进入表格、也不参与计费。
  const [onlySuccess, setOnlySuccess] = useState(false);
  const [onlyFailed, setOnlyFailed] = useState(false);
  // 只看尺寸图用了预估值、尚未人工确认的链接；导出前重点复核。
  const [onlyEstimated, setOnlyEstimated] = useState(false);
  // 只看尺寸图缺失/失败的链接；处理完先集中补这些。
  const [onlyDimensionIssues, setOnlyDimensionIssues] = useState(false);
  // 第 4 格尺寸图：本体长宽高（厘米）编辑态 + 单条重烧中的草稿 id。
  const [dimensionEdits, setDimensionEdits] = useState<
    Record<number, { length: string; width: string; height: string }>
  >({});
  const [reburningDraftId, setReburningDraftId] = useState<number | null>(null);
  // 尺寸卡在处理中按条并发烧制：预检页短暂轮询，异步结果回来后自动补徽标。
  const dimensionPollRef = useRef(0);
  const [confirmingDimensions, setConfirmingDimensions] = useState(false);
  const [excludingDraftIds, setExcludingDraftIds] = useState<Set<number>>(new Set());
  const [selectedDraftIds, setSelectedDraftIds] = useState<Set<number>>(new Set());
  const [imageZoomed, setImageZoomed] = useState(false);
  const [expandedDraftIds, setExpandedDraftIds] = useState<Set<number>>(new Set());
  const [listingAdviceByDraftId, setListingAdviceByDraftId] = useState<Record<number, ListingAdvice>>({});
  const [listingAdviceLoadingIds, setListingAdviceLoadingIds] = useState<Set<number>>(new Set());
  const [listingAdviceErrors, setListingAdviceErrors] = useState<Record<number, string>>({});
  const listingAdviceRequestRef = useRef<Record<number, string>>({});
  const [skuManagerOpen, setSkuManagerOpen] = useState(false);

  useEffect(() => {
    if (!isActive) setActiveImage(null);
    if (!isActive) setSkuManagerOpen(false);
  }, [isActive]);

  const runStorageKey = `pp-preview-finalize:${ctx.workspaceId}:${taskId}`;
  const idempotencyStorageKey = `${runStorageKey}:idempotency`;
  const downloadedStorageKey = `${runStorageKey}:downloaded`;

  const notify = useCallback((ok: string) => {
    setMessage(ok);
    setError('');
  }, []);

  const fail = useCallback((err: unknown) => {
    setError(err instanceof Error ? err.message : String(err));
    setMessage('');
  }, []);

  // 本地预览变更（删除/恢复/一键剔除）会递增该版本号；后台静默轮询在发起前记录版本，
  // 若返回时版本已变化（期间发生了删除等变更），丢弃过期结果，避免旧快照覆盖新状态导致删除项「回弹」。
  const previewVersionRef = useRef(0);

  // 元数据分页：首屏只拿 30 条，剩余在后台按 100 条/页渐进补齐（图片仍按页懒加载）。
  const prefetchItems = useCallback(
    async (
      initialItems: PreviewItem[],
      total: number,
      generation: number,
    ): Promise<PreviewItem[]> => {
      setItemsLoading(true);
      let accumulated = initialItems;
      try {
        let offset = accumulated.length;
        while (offset < total) {
          if (itemsGenerationRef.current !== generation) return accumulated;
          const data = await fetchProductPreview(ctx, taskId, {
            itemOffset: offset,
            itemLimit: PRECHECK_ITEM_PREFETCH_SIZE,
            mediaLimit: 0,
          });
          if (itemsGenerationRef.current !== generation) return accumulated;
          const page = data.items ?? [];
          if (!page.length) break;
          accumulated = mergePreviewItems(accumulated, page);
          itemsLoadedCountRef.current = accumulated.length;
          setPreview((prev) =>
            prev ? { ...prev, items: mergePreviewItems(prev.items, page) } : prev,
          );
          offset = accumulated.length;
          if (!data.has_more) break;
        }
      } catch {
        // 后台补齐失败不阻断首屏；保存/导出前 ensureAllItemsLoaded 会重试。
      } finally {
        if (itemsGenerationRef.current === generation) setItemsLoading(false);
      }
      return accumulated;
    },
    [ctx, taskId],
  );

  const ensureAllItemsLoaded = useCallback(async (): Promise<PreviewItem[]> => {
    const current = previewRef.current;
    if (!current) return [];
    let items = current.items;
    if (itemsLoadPromiseRef.current) {
      items = await itemsLoadPromiseRef.current;
    }
    let guard = 0;
    // 后台补齐失败时，保存/导出前再重试；连续两次没有进展就返回已加载部分，
    // 由后端 finalize 的完整性与权限校验兜底。
    while (items.length < current.item_count && guard < 20) {
      guard += 1;
      const generation = itemsGenerationRef.current;
      const next = await prefetchItems(items, current.item_count, generation);
      if (next.length <= items.length) return next;
      items = next;
    }
    return items;
  }, [prefetchItems]);

  const load = useCallback(async (preserveLocalEdits = false, quiet = false) => {
    if (!quiet) setLoading(true);
    const versionAtDispatch = previewVersionRef.current;
    const generation = ++itemsGenerationRef.current;
    // 新一轮加载作废旧的分页补齐（旧 promise 会在 generation 校验处自行退出）。
    itemsLoadPromiseRef.current = null;
    try {
      // 首屏只投影前 30 条元数据 + 图片；其余元数据后台分页补齐、图片翻页懒加载。
      const data = await fetchProductPreview(ctx, taskId, {
        itemLimit: PRECHECK_ITEM_PAGE_SIZE,
        mediaLimit: PRECHECK_MEDIA_PAGE_SIZE,
      });
      if (previewVersionRef.current !== versionAtDispatch) return;
      if (!quiet) {
        setPreview(data);
        itemsLoadedCountRef.current = data.items.length;
      } else {
        setPreview((prev) => {
          if (!prev) return data;
          // 静默轮询只刷新首屏这一段；已渐进补齐的后续元数据继续保留。
          const previousByDraft = new Map(
            prev.items
              .filter((item) => item.product_draft_id != null && item.media_loaded)
              .map((item) => [item.product_draft_id as number, item]),
          );
          const freshByItemId = new Map(data.items.map((item) => [item.item_id, item]));
          const merged = prev.items.map((item) => {
            const fresh = freshByItemId.get(item.item_id);
            if (!fresh) return item;
            if (fresh.media_loaded !== false || fresh.product_draft_id == null) {
              return fresh;
            }
            const loaded = previousByDraft.get(fresh.product_draft_id);
            if (!loaded) return fresh;
            return {
              ...fresh,
              assets: loaded.assets,
              image_manifest: loaded.image_manifest,
              main_image: loaded.main_image,
              carousel_images: loaded.carousel_images,
              detail_images: loaded.detail_images,
              exportable: loaded.exportable,
              export_blockers: loaded.export_blockers,
              media_loaded: true,
            };
          });
          const seen = new Set(prev.items.map((item) => item.item_id));
          return {
            ...data,
            items: [
              ...merged,
              ...data.items.filter((item) => !seen.has(item.item_id)),
            ],
          };
        });
        itemsLoadedCountRef.current = Math.max(
          itemsLoadedCountRef.current,
          data.items.length,
        );
      }
      if (!preserveLocalEdits) setEdits({});
      // 首屏之后后台补齐剩余元数据（图片仍为懒加载）；静默轮询不重复补齐。
      if (
        itemsLoadedCountRef.current < data.item_count &&
        !itemsLoadPromiseRef.current
      ) {
        const startingItems = quiet
          ? previewRef.current?.items ?? data.items
          : data.items;
        const promise = prefetchItems(startingItems, data.item_count, generation);
        itemsLoadPromiseRef.current = promise;
        void promise.finally(() => {
          if (itemsLoadPromiseRef.current === promise) {
            itemsLoadPromiseRef.current = null;
          }
        });
      }
    } catch (err) {
      if (!quiet) fail(err);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [ctx, fail, taskId]);

  // ---- 图片懒加载：首屏只拿当前页，翻页/保存/导出前再批量补齐 ----
  const [mediaLoadingCount, setMediaLoadingCount] = useState(0);
  // 保存/导出前补齐图片清单的真实进度（已处理条数 / 总条数）；null 表示没有进行中的补齐。
  const [mediaHydrate, setMediaHydrate] = useState<{ done: number; total: number } | null>(null);
  const mediaRequestedRef = useRef<Set<number>>(new Set());

  const mergePreviewMedia = useCallback(
    (payloads: Record<string, PreviewMediaPayload>) => {
      if (!Object.keys(payloads).length) return;
      setPreview((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          items: prev.items.map((item) => {
            const payload =
              item.product_draft_id != null
                ? payloads[String(item.product_draft_id)]
                : undefined;
            return payload ? { ...item, ...payload, media_loaded: true } : item;
          }),
        };
      });
    },
    [],
  );

  const loadPreviewMedia = useCallback(
    async (
      draftIds: number[],
      onProgress?: (done: number, total: number) => void,
    ): Promise<MediaFetchResult> => {
      const unique = [
        ...new Set(draftIds.filter((value) => Number.isFinite(value) && value > 0)),
      ];
      if (!unique.length) return { payloads: {}, failed: [] };
      setMediaLoadingCount((count) => count + 1);
      onProgress?.(0, unique.length);
      const payloads: Record<string, PreviewMediaPayload> = {};
      const failed: number[] = [];
      try {
        // 分批 + 重试：单请求只覆盖 30 条，避免大批量导出前补齐时整请求超时。
        for (
          let start = 0;
          start < unique.length;
          start += PRECHECK_MEDIA_HYDRATE_BATCH_SIZE
        ) {
          const chunk = unique.slice(start, start + PRECHECK_MEDIA_HYDRATE_BATCH_SIZE);
          let chunkPayloads: Record<string, PreviewMediaPayload> | null = null;
          for (let attempt = 0; attempt <= PRECHECK_MEDIA_HYDRATE_RETRIES; attempt += 1) {
            try {
              const data = await fetchProductPreviewMedia(ctx, taskId, chunk);
              chunkPayloads = data.items || {};
              break;
            } catch {
              if (attempt >= PRECHECK_MEDIA_HYDRATE_RETRIES) break;
              await new Promise((resolve) => {
                window.setTimeout(resolve, PRECHECK_MEDIA_RETRY_DELAY_MS * (attempt + 1));
              });
            }
          }
          if (chunkPayloads === null) {
            failed.push(...chunk);
          } else {
            Object.assign(payloads, chunkPayloads);
            mergePreviewMedia(chunkPayloads);
          }
          onProgress?.(Math.min(start + chunk.length, unique.length), unique.length);
        }
      } finally {
        setMediaLoadingCount((count) => Math.max(0, count - 1));
      }
      return { payloads, failed };
    },
    [ctx, mergePreviewMedia, taskId],
  );

  const hydrateAllMedia = useCallback(
    async (
      items: PreviewItem[],
      onProgress?: (done: number, total: number) => void,
    ): Promise<{ items: PreviewItem[]; missing: number[] }> => {
      const missing = items
        .filter((item) => item.media_loaded === false && item.product_draft_id != null)
        .map((item) => item.product_draft_id as number);
      if (!missing.length) return { items, missing: [] };
      const { payloads } = await loadPreviewMedia(missing, onProgress);
      const hydrated = items.map((item) => {
        const payload =
          item.product_draft_id != null
            ? payloads[String(item.product_draft_id)]
            : undefined;
        return payload ? { ...item, ...payload, media_loaded: true } : item;
      });
      const stillMissing = hydrated
        .filter((item) => item.media_loaded === false && item.product_draft_id != null)
        .map((item) => item.product_draft_id as number);
      return { items: hydrated, missing: stillMissing };
    },
    [loadPreviewMedia],
  );

  // 存在仍在同步（pending/materializing）的原始源图时，持续静默轮询刷新，
  // 使后台物化完成后「等待同步」自动变为「可用」，无需手动重进页面。
  const hasPendingSourceSync = useMemo(() => {
    if (!preview) return false;
    return preview.items.some((item) =>
      item.assets.some(
        (a) =>
          a.bucket === 'source' &&
          (a.media_status === 'pending' || a.media_status === 'materializing'),
      ),
    );
  }, [preview]);

  const sourceSyncPollRef = useRef<number | null>(null);
  const sourceSyncInFlightRef = useRef(false);
  useEffect(() => {
    // 非激活页签不轮询：工作台页签是常驻挂载，隐藏页仍每 3 秒拉整份预检会叠加
    // 请求与重渲染（多链接/多 SKU 时响应很大）。
    if (!hasPendingSourceSync || !isActive) return;
    let stopped = false;
    const tick = async () => {
      const hidden =
        typeof document !== 'undefined' && document.visibilityState === 'hidden';
      // 串行 + 在飞跳过：后端忙时上一轮还没回来就不再发下一轮，避免请求堆积。
      if (!hidden && !sourceSyncInFlightRef.current) {
        sourceSyncInFlightRef.current = true;
        try {
          await load(true, true);
        } finally {
          sourceSyncInFlightRef.current = false;
        }
      }
      if (!stopped) sourceSyncPollRef.current = window.setTimeout(tick, 3000);
    };
    sourceSyncPollRef.current = window.setTimeout(tick, 3000);
    return () => {
      stopped = true;
      if (sourceSyncPollRef.current !== null) {
        window.clearTimeout(sourceSyncPollRef.current);
        sourceSyncPollRef.current = null;
      }
    };
  }, [hasPendingSourceSync, isActive, load]);

  const downloadRun = useCallback(async (run: PreviewFinalizeRun, automatic: boolean) => {
    if (!run.workbook_ready || !run.download || !run.file) return;
    if (automatic) {
      if (readSession(downloadedStorageKey) === run.id) return;
      writeSession(downloadedStorageKey, run.id);
    }
    setDownloading(true);
    try {
      await ppDownload(ctx, run.download, run.file);
      // 说清文件叫什么、去了哪：原先只说「表格已生成」，用户下载完就找不到文件了。
      notify(
        `表格 ${run.file} 已下载（${run.product_count} 个商品 / ${run.row_count} 行）：` +
          '文件保存在浏览器的默认下载目录；找不到了可以在下方「历史导出」里重新下载',
      );
    } catch (err) {
      fail(err);
    } finally {
      setDownloading(false);
    }
  }, [ctx, downloadedStorageKey, fail, notify]);

  /**
   * 生成并下载妙手模板（服饰类 / 非服饰类）。
   *
   * 显式接收 runId：妙手是从**某一次已完成的导出快照**现场生成的，后端只要求该 run 是
   * completed 且有快照（见 export_miaoshou_workbook），与页面会话无关。历史导出列表也要
   * 用它——否则一旦「最终版已生成」卡片丢失（从「历史记录」进入预检、页面会话被清、或排除
   * 商品后主动作废 run），妙手就再无入口，用户只能重新点「完成预检并导出」，还会撞上幂等
   * 保护提示「这次导出和上一次重复提交了」。
   */
  const exportMiaoshou = useCallback(async (runId: string, kind: MiaoshouTemplateKind) => {
    if (!runId) return;
    setMsExporting(kind);
    try {
      const payload = await exportMiaoshouPreview(ctx, taskId, runId, kind);
      if (!payload.download) throw new Error('妙手导出未生成下载链接');
      await ppDownload(ctx, payload.download, payload.file || `miaoshou_${kind}_task_${taskId}.xlsx`);
      const kindLabel = kind === 'apparel' ? '服饰类' : '非服饰类';
      notify(`妙手${kindLabel}表已导出（${payload.product_count} 个商品 / ${payload.row_count} 行），请按妙手要求核对字段后导入`);
    } catch (err) {
      fail(err);
    } finally {
      setMsExporting(null);
    }
  }, [ctx, taskId, fail, notify]);

  /**
   * 直接重新下载此前已生成的妙手模板（不重新生成）。
   *
   * 妙手文件生成后一直留在该次导出的目录里（后端 miaoshou-download 直接取文件），
   * 历史导出据此给出「重新下载」，省掉每次都要重跑一遍生成。
   */
  const downloadMiaoshou = useCallback(async (runId: string, kind: MiaoshouTemplateKind) => {
    if (!runId) return;
    setMsExporting(kind);
    try {
      await ppDownload(ctx, miaoshouDownloadUrl(taskId, runId, kind), miaoshouFileName(kind, taskId));
      const kindLabel = kind === 'apparel' ? '服饰类' : '非服饰类';
      notify(`妙手${kindLabel}表已重新下载（文件一直保存在本机，未重新生成）`);
    } catch (err) {
      fail(err);
    } finally {
      setMsExporting(null);
    }
  }, [ctx, taskId, fail, notify]);

  /** 妙手按钮：该次导出已生成过就直接重下，否则按快照现场生成。 */
  const runMiaoshouAction = useCallback((run: PreviewFinalizeRun, kind: MiaoshouTemplateKind) => {
    if ((run.miaoshou_generated ?? []).includes(kind)) {
      void downloadMiaoshou(run.id, kind);
      return;
    }
    void exportMiaoshou(run.id, kind);
  }, [downloadMiaoshou, exportMiaoshou]);

  const refreshHistory = useCallback(async () => {
    try {
      setHistoryRuns(await listPreviewFinalizeRuns(ctx, taskId));
    } catch {
      // 历史列表只用于找回文件，读失败不该打断预检主流程。
    }
  }, [ctx, taskId]);

  const acceptFinalizeRun = useCallback(
    (run: PreviewFinalizeRun) => {
      // 完成后不自动下载：表格按格式统一在结果卡片里下载/生成。
      // 保留 runStorageKey，刷新页面后仍能恢复结果卡片继续下载。
      setFinalizeRun(run);
      // 每次有 run 完成就刷新一次历史（同一 run 只同步一次，避免轮询期间反复请求）。
      if (run.status === 'completed' && historySyncedRunRef.current !== run.id) {
        historySyncedRunRef.current = run.id;
        void refreshHistory();
      }
    },
    [refreshHistory],
  );

  useEffect(() => {
    setPreview(null);
    setEdits({});
    setRetryingMediaAssetIds(new Set());
    setFinalizeRun(null);
    setHistoryRuns([]);
    historySyncedRunRef.current = '';
    setUndoSnackbar(null);
    setActiveImage(null);
    setExcludingDraftIds(new Set());
    setSelectedDraftIds(new Set());
    setListingAdviceByDraftId({});
    setListingAdviceLoadingIds(new Set());
    setListingAdviceErrors({});
    listingAdviceRequestRef.current = {};
    setMessage('');
    setError('');
  }, [taskId]);

  useEffect(() => {
    void load();
    void refreshHistory();
  }, [load, refreshHistory]);

  useEffect(() => {
    const refresh = createPrecheckFinalizeRefresh<PreviewFinalizeRun>({
      fetchRun: (runId) => getPreviewFinalizeRun(ctx, taskId, runId),
      onRun: acceptFinalizeRun,
      onError: fail,
      intervalMs: 1500,
    });
    refreshRef.current = refresh;

    const updateVisibility = () => refresh.setVisible(document.visibilityState !== 'hidden');
    updateVisibility();
    document.addEventListener('visibilitychange', updateVisibility);

    const storedRunId = readSession(runStorageKey);
    let cancelled = false;
    if (storedRunId) {
      refresh.watch(storedRunId);
    } else {
      // 「历史记录 → 进入预检」是新开的页标签，页面会话里没有 run，「最终版已生成」卡片会整个
      // 消失——连带丢掉「妙手·服饰类 / 妙手·非服饰类」的生成入口（只有店小秘还能从历史导出重下）。
      // 这里回落到最近一次已完成的导出：卡片和三种格式的下载入口都回来。
      void (async () => {
        try {
          const runs = await listPreviewFinalizeRuns(ctx, taskId);
          if (cancelled) return;
          const latest = runs.find((run) => run.status === 'completed');
          if (!latest) return;
          writeSession(runStorageKey, latest.id);
          acceptFinalizeRun(latest);
        } catch {
          // 只影响「能不能找回已生成的表格」，失败不该打断预检主流程。
        }
      })();
    }

    return () => {
      cancelled = true;
      document.removeEventListener('visibilitychange', updateVisibility);
      refresh.stop();
      if (refreshRef.current === refresh) refreshRef.current = null;
    };
  }, [acceptFinalizeRun, ctx, fail, runStorageKey, taskId]);

  useEffect(() => {
    if (!undoSnackbar) return undefined;
    const delay = Math.max(0, undoSnackbar.expiresAt - Date.now());
    const timer = window.setTimeout(() => setUndoSnackbar(null), delay);
    return () => window.clearTimeout(timer);
  }, [undoSnackbar]);

  // 图片预览：锁定背景滚动、ESC 关闭、打开时重置缩放。
  useEffect(() => {
    if (!activeImage) return undefined;
    setImageZoomed(false);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setActiveImage(null);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [activeImage]);

  // 分页 + 序列号搜索（仅影响展示；保存/导出/计数仍作用于全部商品）。
  // 必须放在 `if (!preview)` 提前返回之前，保证各渲染轮次 Hook 数量一致。
  const allItems = (preview?.items ?? []).filter((item) => !item.excluded);
  const dimensionSummary = useMemo(() => {
    let estimated = 0;
    let reused = 0;
    let missing = 0;
    let failed = 0;
    for (const item of allItems) {
      const info = item.dimension_image;
      if (!info) continue;
      if (info.state === 'estimated') estimated += 1;
      if (info.source === 'applied') reused += 1;
      if (info.state === 'missing') missing += 1;
      if (info.state === 'failed') failed += 1;
    }
    return { estimated, reused, missing, failed };
  }, [allItems]);
  const excludedItems = (preview?.items ?? []).filter((item) => item.excluded);
  const totalItemCount = preview?.item_summary?.total ?? allItems.length;
  // 元数据未补齐前，全局统计先用服务端 summary，避免显示成「只有当前页」。
  const itemsFullyLoaded = !itemsLoading && allItems.length >= totalItemCount;
  // 从 finalize run 的失败明细中提取参与发布失败的草稿 ID，用于「只看失败链接」筛选与一键剔除。
  const failedDraftIds = useMemo(() => {
    const ids = new Set<number>();
    for (const failure of finalizeRun?.errors ?? []) {
      if (failure.product_draft_id != null) ids.add(failure.product_draft_id);
    }
    return ids;
  }, [finalizeRun]);
  const itemHasFailure = useCallback(
    (item: PreviewItem): boolean =>
      (item.product_draft_id != null && failedDraftIds.has(item.product_draft_id)) ||
      item.assets.some((asset) => asset.publication_status === 'publish_failed') ||
      // AI 生图失败回退来源图：未真正产出可用处理后主图/轮播图（导出回退来源图），
      // 属于「不能直接拿来用」的链接，纳入「只看失败链接」筛选与一键剔除。
      item.image_ai_failed === true ||
      // 链接级失败（失败/待确认、部分产出、媒体完整性未通过）统一按失败处理。
      item.processing_failed === true ||
      item.exportable === false,
    // image_ai_failed 是 item 自身属性，无需其它外部依赖。
    [failedDraftIds],
  );
  // 带失败标记的商品数：导出前提示先剔除，避免把回退来源图的商品一起导出去。
  const localFailedItemCount = useMemo(
    () => allItems.filter(itemHasFailure).length,
    [allItems, itemHasFailure],
  );
  const failedItemCount = itemsFullyLoaded
    ? localFailedItemCount
    : preview?.item_summary?.failed_count ?? localFailedItemCount;
  const filteredItems = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    let scoped = allItems;
    if (onlySuccess) {
      // 「成功链接」= 没有失败标记。原先按 item.status === 'completed' 判成功，但
      // 生图失败回退来源图的商品同样是 completed，于是勾「只看成功链接」也筛不掉
      // 失败项，看起来像筛选没生效；失败集合改由 itemHasFailure 统一定义。
      scoped = scoped.filter((item) => !itemHasFailure(item));
    } else if (onlyFailed) {
      scoped = scoped.filter(itemHasFailure);
    }
    if (onlyEstimated) {
      scoped = scoped.filter((item) => item.dimension_image?.state === 'estimated');
    }
    if (onlyDimensionIssues) {
      scoped = scoped.filter(
        (item) =>
          item.dimension_image?.state === 'missing' ||
          item.dimension_image?.state === 'failed',
      );
    }
    if (!keyword) return scoped;
    return scoped.filter((item) => {
      const candidates = [
        item.skc,
        item.core_fields?.sku,
        String(item.item_id),
        item.product_draft_id != null ? String(item.product_draft_id) : '',
      ];
      return candidates.some((value) => value && value.toLowerCase().includes(keyword));
    });
  }, [allItems, search, onlySuccess, onlyFailed, onlyEstimated, onlyDimensionIssues, itemHasFailure]);

  useEffect(() => {
    setPage(1);
  }, [search, pageSize]);

  const totalPages = Math.max(1, Math.ceil(filteredItems.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pagedItems = filteredItems.slice((safePage - 1) * pageSize, safePage * pageSize);

  // 当前页里还没投影图片的链接：进页即批量拉取（一次请求覆盖整页）。
  useEffect(() => {
    if (!preview) return;
    const ids = pagedItems
      .filter((item) => item.media_loaded === false && item.product_draft_id != null)
      .map((item) => item.product_draft_id as number)
      .filter((id) => !mediaRequestedRef.current.has(id));
    if (!ids.length) return;
    ids.forEach((id) => mediaRequestedRef.current.add(id));
    void (async () => {
      const { payloads } = await loadPreviewMedia(ids);
      ids.forEach((id) => {
        // 有返回（含空媒体）就允许下次重新拉；草稿已删/接口失败的保留标记，
        // 避免每次渲染都重复请求（保存/导出前仍会强制补齐一次）。
        if (payloads[String(id)]) mediaRequestedRef.current.delete(id);
      });
    })();
  }, [pagedItems, preview, loadPreviewMedia]);

  // 尺寸卡在批次处理中并发烧制：页面打开后短暂轮询（最多约 48 秒），
  // 异步结果回来即自动补「预估/已确认/原图加工」徽标，不用手动刷新。
  useEffect(() => {
    if (!isActive) {
      dimensionPollRef.current = 0;
      return;
    }
    const pending = allItems.some(
      (item) => item.status === 'completed' && item.dimension_image == null,
    );
    if (!pending || dimensionPollRef.current >= 12) return;
    const timer = window.setTimeout(() => {
      dimensionPollRef.current += 1;
      void load(true, true);
    }, 4000);
    return () => window.clearTimeout(timer);
  }, [isActive, allItems, load]);

  // 勾选作用于「当前筛选结果」（含分页之外的商品）：「全选」即选中筛选出的全部商品，
  // 「删除所选」只删除其中仍在当前筛选结果里的项，避免筛选切换后误删不可见商品。
  const selectableDraftIds = useMemo(
    () => filteredItems
      .map((item) => item.product_draft_id)
      .filter((id): id is number => id != null),
    [filteredItems],
  );
  const selectedDraftIdsInView = useMemo(
    () => selectableDraftIds.filter((id) => selectedDraftIds.has(id)),
    [selectableDraftIds, selectedDraftIds],
  );
  const allInViewSelected = selectableDraftIds.length > 0
    && selectedDraftIdsInView.length === selectableDraftIds.length;

  if (!preview) {
    return (
      <div className="verify-page verify-precheck-page">
        <header className="verify-commandbar">
          <div className="verify-command-title">
            <h1>预检与最终发布</h1>
            <p>图片先以稳定素材 ID 在本地清单中增删排序；点击完成后，仅发布最终保留图片并生成店小秘表格。</p>
          </div>
        </header>

        {(message || error) && <div className={`verify-message ${error ? 'error' : ''}`}>{error || message}</div>}
        {loading ? (
          <div className="app-loading-block">
            <i className="app-spinner" aria-hidden="true" />
            加载预检数据…
            <small className="app-loading-hint">产品较多时加载较慢，请耐心等待</small>
          </div>
        ) : (
          <p className="verify-empty">任务尚未完成，无法预检</p>
        )}
      </div>
    );
  }

  const editFor = (item: PreviewItem): ItemEdits => edits[item.product_draft_id ?? item.item_id] ?? {};

  const effectiveManifest = (item: PreviewItem): PreviewImageManifest => {
    const edit = editFor(item);
    return cloneManifest(edit.imageManifest ?? item.image_manifest);
  };

  const effectiveAssets = (item: PreviewItem): PreviewImageAsset[] => {
    const edit = editFor(item);
    return mergeAssets(item.assets, edit.addedAssets);
  };

  const effectiveCoreFields = (item: PreviewItem): PreviewCoreFields => ({
    ...item.core_fields,
    ...(editFor(item).core_fields ?? {}),
  });

  const requestListingAdvice = async (item: PreviewItem) => {
    const draftId = item.product_draft_id ?? item.item_id;
    if (item.product_draft_id == null || listingAdviceLoadingIds.has(draftId)) return;
    const edit = editFor(item);
    const coreFields = effectiveCoreFields(item);
    const requestMarker = crypto.randomUUID();
    listingAdviceRequestRef.current[draftId] = requestMarker;
    setListingAdviceLoadingIds((current) => new Set(current).add(draftId));
    setListingAdviceErrors((current) => {
      const next = { ...current };
      delete next[draftId];
      return next;
    });
    try {
      const advice = await getListingAdvice(ctx, taskId, item.product_draft_id, {
        title: edit.title ?? item.title,
        description: edit.description ?? item.description,
        category_path: String(coreFields.category_path ?? ''),
      });
      if (listingAdviceRequestRef.current[draftId] !== requestMarker) return;
      setListingAdviceByDraftId((current) => ({ ...current, [draftId]: advice }));
    } catch (err) {
      if (listingAdviceRequestRef.current[draftId] !== requestMarker) return;
      setListingAdviceErrors((current) => ({
        ...current,
        [draftId]: err instanceof Error ? err.message : String(err),
      }));
    } finally {
      if (listingAdviceRequestRef.current[draftId] !== requestMarker) return;
      delete listingAdviceRequestRef.current[draftId];
      setListingAdviceLoadingIds((current) => {
        const next = new Set(current);
        next.delete(draftId);
        return next;
      });
    }
  };

  const isEditableShippingPackageRecord = (record: ShippingPackageRecord): boolean => (
    record.match_status === 'matched' && record.variant_key.trim().length > 0
  );

  const effectiveShippingPackageOverrides = (
    item: PreviewItem,
  ): Record<string, ShippingPackageRecordOverride> => {
    const sourceRecords = item.shipping_package_records ?? [];
    const combined = {
      ...(item.overrides.shipping_package_records ?? {}),
      ...(editFor(item).shipping_package_records ?? {}),
    };
    return Object.fromEntries(
      Object.entries(combined).filter(([variantKey]) => sourceRecords.some(
        (record) => isEditableShippingPackageRecord(record) && record.variant_key === variantKey,
      )),
    );
  };

  const effectiveShippingPackageRecords = (item: PreviewItem): ShippingPackageRecord[] => {
    const overrides = effectiveShippingPackageOverrides(item);
    return (item.shipping_package_records ?? []).map((record) => ({
      ...record,
      ...(overrides[record.variant_key] ?? {}),
    }));
  };

  const effectiveVariantImageMode = (item: PreviewItem): VariantImageMode => (
    editFor(item).variantImageMode ?? item.overrides.variant_image_mode ?? 'auto'
  );

  /** 被整行剔除的 SKU 变种键（本地编辑优先，其次已保存值）。 */
  const effectiveExcludedVariants = (item: PreviewItem): string[] => (
    editFor(item).excludedVariantKeys ?? item.overrides.excluded_variant_keys ?? []
  );

  /** 逐个 SKU 的换图值；空串表示「清除换图」，不参与提交。 */
  const effectiveVariantImageOverrides = (item: PreviewItem): Record<string, string> => {
    const combined = {
      ...(item.overrides.variant_image_overrides ?? {}),
      ...(editFor(item).variantImageOverrides ?? {}),
    };
    return Object.fromEntries(
      Object.entries(combined).filter(([, value]) => String(value ?? '').trim() !== ''),
    );
  };

  /** 某 SKU 换图值的可访问地址：资产 ID 走预览地址，http(s) 直链原样返回。 */
  const variantImageUrlOf = (item: PreviewItem, key: string): string => {
    const value = String(effectiveVariantImageOverrides(item)[key] ?? '').trim();
    if (!value) return '';
    if (/^https?:\/\//i.test(value)) return value;
    const asset = effectiveAssets(item).find((entry) => entry.id === value);
    return asset ? asset.preview_url || asset.public_url || '' : '';
  };

  const collectDesiredState = (item: PreviewItem): PreviewSavePayload => {
    if (item.product_draft_id == null) throw new Error(`商品 #${item.item_id} 缺少草稿 ID，无法保存预检`);
    const edit = editFor(item);
    return {
      product_draft_id: item.product_draft_id,
      expected_preview_revision: item.preview_revision,
      expected_result_version: item.result_version,
      overrides: {
        title: edit.title ?? item.title,
        description: edit.description ?? item.description,
        core_fields: effectiveCoreFields(item),
        image_manifest_v2: effectiveManifest(item),
        shipping_package_records: effectiveShippingPackageOverrides(item),
        variant_image_mode: effectiveVariantImageMode(item),
        excluded_variant_keys: effectiveExcludedVariants(item),
        variant_image_overrides: effectiveVariantImageOverrides(item),
      },
    };
  };

  const initialDesiredState = (item: PreviewItem): PreviewSavePayload | null => {
    if (item.product_draft_id == null) return null;
    return {
      product_draft_id: item.product_draft_id,
      expected_preview_revision: item.preview_revision,
      expected_result_version: item.result_version,
      overrides: {
        title: item.title,
        description: item.description,
        core_fields: { ...item.core_fields },
        image_manifest_v2: cloneManifest(item.image_manifest),
        shipping_package_records: effectiveShippingPackageOverrides(item),
        variant_image_mode: item.overrides.variant_image_mode ?? 'auto',
        excluded_variant_keys: item.overrides.excluded_variant_keys ?? [],
        variant_image_overrides: item.overrides.variant_image_overrides ?? {},
      },
    };
  };

  const itemIsDirty = (item: PreviewItem): boolean => {
    const initial = initialDesiredState(item);
    if (!initial) return false;
    return stableStringify(collectDesiredState(item).overrides) !== stableStringify(initial.overrides);
  };

  const setEdit = (draftId: number, patch: Partial<ItemEdits>) => {
    setEdits((previous) => ({
      ...previous,
      [draftId]: { ...(previous[draftId] ?? {}), ...patch },
    }));
  };

  const setManifest = (draftId: number, manifest: PreviewImageManifest) => {
    setEdit(draftId, { imageManifest: cloneManifest(manifest) });
  };

  const applyVariantImageMode = (mode: VariantImageMode, draftIds: number[]) => {
    if (draftIds.length === 0) return;
    setEdits((previous) => {
      const next = { ...previous };
      for (const draftId of draftIds) {
        next[draftId] = { ...(next[draftId] ?? {}), variantImageMode: mode };
      }
      return next;
    });
  };

  const itemOfDraft = (draftId: number): PreviewItem | undefined => (
    allItems.find((entry) => (entry.product_draft_id ?? entry.item_id) === draftId)
  );

  /** 整行剔除 / 恢复 SKU 规格：导出时被剔除的变种不产生表格行。 */
  const applyVariantExclusion = (entries: VariantRef[], excluded: boolean) => {
    if (entries.length === 0) return;
    const grouped = new Map<number, string[]>();
    for (const entry of entries) {
      const list = grouped.get(entry.draftId) ?? [];
      list.push(entry.variantKey);
      grouped.set(entry.draftId, list);
    }
    setEdits((previous) => {
      const next = { ...previous };
      for (const [draftId, keys] of grouped) {
        const item = itemOfDraft(draftId);
        if (!item) continue;
        const current = new Set(previous[draftId]?.excludedVariantKeys ?? item.overrides.excluded_variant_keys ?? []);
        for (const key of keys) {
          if (excluded) current.add(key);
          else current.delete(key);
        }
        next[draftId] = { ...(previous[draftId] ?? {}), excludedVariantKeys: Array.from(current) };
      }
      return next;
    });
  };

  /** 设置/清除逐个 SKU 的规格图替换（value 为空串表示恢复规格原图）。 */
  const applyVariantImageOverrides = (entries: Array<VariantRef & { value: string }>) => {
    if (entries.length === 0) return;
    const grouped = new Map<number, Array<{ variantKey: string; value: string }>>();
    for (const entry of entries) {
      const list = grouped.get(entry.draftId) ?? [];
      list.push({ variantKey: entry.variantKey, value: entry.value });
      grouped.set(entry.draftId, list);
    }
    setEdits((previous) => {
      const next = { ...previous };
      for (const [draftId, list] of grouped) {
        const item = itemOfDraft(draftId);
        if (!item) continue;
        const current = {
          ...(item.overrides.variant_image_overrides ?? {}),
          ...(previous[draftId]?.variantImageOverrides ?? {}),
        };
        for (const entry of list) {
          if (entry.value) current[entry.variantKey] = entry.value;
          else delete current[entry.variantKey];
        }
        next[draftId] = { ...(previous[draftId] ?? {}), variantImageOverrides: current };
      }
      return next;
    });
  };

  /** 该商品可用于替换 SKU 规格图的候选图：AI 处理图 / 本地导入 / 尺寸图 / 其他 SKU 规格原图。 */
  const variantImageOptionsOf = (item: PreviewItem): VariantImageOption[] => {
    const options: VariantImageOption[] = [];
    const seen = new Set<string>();
    const assets = effectiveAssets(item);
    for (const { origin, label } of VARIANT_IMAGE_ORIGIN_GROUPS) {
      for (const asset of assets) {
        if (asset.origin !== origin) continue;
        const url = asset.preview_url || asset.public_url || '';
        if (!url || seen.has(asset.id)) continue;
        seen.add(asset.id);
        options.push({
          value: asset.id,
          thumb: assetThumbUrl(asset),
          cropSrc: url,
          group: label,
          label: `${label} · ${asset.id.slice(0, 8)}`,
        });
      }
    }
    const variants = Array.isArray(item.source_variant_records) ? item.source_variant_records : [];
    variants.forEach((variant, index) => {
      const url = String(variant.image_url ?? variant.imageUrl ?? '').trim();
      if (!/^https?:\/\//i.test(url) || seen.has(url)) return;
      seen.add(url);
      const label = variantLabel(variant) || variant.display_name || `SKU ${index + 1}`;
      options.push({
        value: url,
        thumb: url,
        cropSrc: url,
        group: '其他 SKU 的规格原图',
        label: `${label}（规格原图）`,
      });
    });
    return options;
  };

  const overrideUrlOfDraft = (draftId: number, key: string): string => {
    const item = itemOfDraft(draftId);
    return item ? variantImageUrlOf(item, key) : '';
  };

  /** 该 SKU 原图的中文复核状态：按 sku_id 关联「原始 SKU」来源资产。 */
  const textReviewOfDraft = (draftId: number, skuId: string): PreviewTextReviewStatus => {
    const target = String(skuId || '').trim();
    if (!target) return '';
    const item = itemOfDraft(draftId);
    if (!item) return '';
    const asset = effectiveAssets(item).find(
      (entry) => entry.source_kind === 'sku' && String(entry.sku_id || '') === target,
    );
    return asset?.text_review_status ?? '';
  };

  /** 把裁剪产物上传成该商品的预览资产，返回可直接写入换图值的候选项。 */
  const uploadVariantCrop = async (draftId: number, file: File): Promise<VariantImageOption> => {
    if (!ALLOWED_UPLOAD_TYPES.has(file.type)) throw new Error('裁剪结果不是支持的 JPEG / PNG / WebP 图片');
    if (file.size <= 0 || file.size > MAX_UPLOAD_BYTES) throw new Error('裁剪结果必须大于 0 字节且不超过 25 MiB');
    setPendingUploads((count) => count + 1);
    try {
      const data = await uploadPreviewAssets(ctx, taskId, draftId, [file]);
      const asset = data.assets[0];
      if (!asset) throw new Error('裁剪图上传失败：服务端未返回素材');
      const item = itemOfDraft(draftId);
      if (item) {
        setEdits((previous) => {
          const current = previous[draftId] ?? {};
          return {
            ...previous,
            [draftId]: {
              ...current,
              addedAssets: mergeAssets(item.assets, current.addedAssets, data.assets),
            },
          };
        });
      }
      const url = asset.preview_url || asset.public_url || '';
      if (!url) throw new Error('裁剪图已上传，但暂无可用预览地址，请稍后重试');
      return {
        value: asset.id,
        thumb: assetThumbUrl(asset),
        cropSrc: url,
        group: '本地导入的图片',
        label: '刚裁剪的图片',
      };
    } finally {
      setPendingUploads((count) => Math.max(0, count - 1));
    }
  };

  /**
   * 裁剪前确保图片可被 canvas 读取：外部跨域地址（如 1688 商品图）先经后端图床
   * 转存成本商品的预览资产，拿到本服务签名的同源地址后再交给裁剪弹层。
   */
  const prepareVariantCropSrc = async (draftId: number, url: string): Promise<string> => {
    const raw = String(url || '').trim();
    if (!raw) throw new Error('这张候选图没有可用的图片地址，无法裁剪');
    if (!/^https?:\/\//i.test(raw)) return raw;
    let origin = '';
    try {
      origin = new URL(raw).origin;
    } catch {
      throw new Error('图片地址无效，无法转存后裁剪');
    }
    if (origin === window.location.origin) return raw;
    setPendingUploads((count) => count + 1);
    try {
      const data = await importPreviewAssetFromUrl(ctx, taskId, draftId, raw);
      const asset = data.asset;
      if (!asset) throw new Error('图片转存失败：服务端未返回素材');
      const item = itemOfDraft(draftId);
      if (item) {
        setEdits((previous) => {
          const current = previous[draftId] ?? {};
          return {
            ...previous,
            [draftId]: {
              ...current,
              addedAssets: mergeAssets(item.assets, current.addedAssets, [asset]),
            },
          };
        });
      }
      const sameOrigin = asset.preview_url || asset.public_url || '';
      if (!sameOrigin) throw new Error('图片已转存，但暂无可用预览地址，请稍后重试');
      return sameOrigin;
    } finally {
      setPendingUploads((count) => Math.max(0, count - 1));
    }
  };

  const clearListingAdvice = (draftId: number) => {
    delete listingAdviceRequestRef.current[draftId];
    setListingAdviceLoadingIds((current) => {
      if (!current.has(draftId)) return current;
      const next = new Set(current);
      next.delete(draftId);
      return next;
    });
    setListingAdviceByDraftId((current) => {
      if (!current[draftId]) return current;
      const next = { ...current };
      delete next[draftId];
      return next;
    });
    setListingAdviceErrors((current) => {
      if (!current[draftId]) return current;
      const next = { ...current };
      delete next[draftId];
      return next;
    });
  };

  const setField = (draftId: number, key: keyof PreviewCoreFields, value: string) => {
    const item = allItems.find((candidate) => (candidate.product_draft_id ?? candidate.item_id) === draftId);
    if (!item) return;
    const current = editFor(item);
    const base = item.core_fields[key];
    const numericKeys: Array<keyof PreviewCoreFields> = [
      'declared_price', 'suggested_price', 'stock', 'length_cm', 'width_cm', 'height_cm', 'weight_g',
    ];
    const numericValue = Number(value);
    const parsed: string | number | null = numericKeys.includes(key) && value.trim() !== '' && Number.isFinite(numericValue)
      ? numericValue
      : value;
    const nextCore: PreviewCoreFields = { ...(current.core_fields ?? {}) };
    if (parsed !== base) nextCore[key] = parsed as never;
    else delete nextCore[key];
    setEdit(draftId, { core_fields: nextCore });
    if (key === 'category_path') clearListingAdvice(draftId);
  };

  const setShippingPackageField = (
    draftId: number,
    variantKey: string,
    key: keyof ShippingPackageRecordOverride,
    value: string,
  ) => {
    const item = allItems.find((candidate) => (candidate.product_draft_id ?? candidate.item_id) === draftId);
    const source = item?.shipping_package_records?.find((record) => record.variant_key === variantKey);
    if (!item || !source || !isEditableShippingPackageRecord(source)) return;
    const numericValue = Number(value);
    const parsed: number | string | null = value.trim() !== '' && Number.isFinite(numericValue)
      ? numericValue
      : value;
    const current = editFor(item).shipping_package_records ?? {};
    const inherited = item.overrides.shipping_package_records?.[variantKey] ?? {};
    setEdit(draftId, {
      shipping_package_records: {
        ...current,
        [variantKey]: { ...inherited, ...(current[variantKey] ?? {}), [key]: parsed },
      },
    });
  };

  const uploadAssets = async (
    draftId: number,
    target: PrecheckImageTarget,
    files: File[],
    options?: { throwOnError?: boolean },
  ) => {
    if (files.length > MAX_UPLOAD_FILES) {
      fail(`单次最多选择 ${MAX_UPLOAD_FILES} 张图片`);
      return;
    }
    const invalidType = files.find((file) => !ALLOWED_UPLOAD_TYPES.has(file.type));
    if (invalidType) {
      fail(`${invalidType.name} 不是支持的 JPEG、PNG 或 WebP 图片`);
      return;
    }
    const invalidSize = files.find((file) => file.size <= 0 || file.size > MAX_UPLOAD_BYTES);
    if (invalidSize) {
      fail(`${invalidSize.name} 必须大于 0 字节且不超过 25 MiB`);
      return;
    }
    setPendingUploads((count) => count + 1);
    try {
      const data = await uploadPreviewAssets(ctx, taskId, draftId, files);
      const item = allItems.find((candidate) => candidate.product_draft_id === draftId);
      if (!item) throw new Error(`未找到商品草稿 #${draftId}`);
      setEdits((previous) => {
        const current = previous[draftId] ?? {};
        const assets = mergeAssets(item.assets, current.addedAssets, data.assets);
        const manifest = addAssets(
          current.imageManifest ?? item.image_manifest,
          target,
          data.assets.map((asset) => asset.id),
        );
        return {
          ...previous,
          [draftId]: { ...current, addedAssets: assets, imageManifest: manifest },
        };
      });
      notify(`已添加 ${data.assets.length} 张图片，尚未发布到 COS`);
    } catch (err) {
      fail(err);
      if (options?.throwOnError) throw err;
    } finally {
      setPendingUploads((count) => Math.max(0, count - 1));
    }
  };

  const retryMediaSource = async (assetId: string) => {
    setRetryingMediaAssetIds((current) => new Set(current).add(assetId));
    try {
      await retryMediaAsset(ctx, assetId);
      notify('素材已加入重新同步队列');
      await load(true);
    } catch (err) {
      fail(err);
    } finally {
      setRetryingMediaAssetIds((current) => {
        const next = new Set(current);
        next.delete(assetId);
        return next;
      });
    }
  };

  const regenerateDetailForItem = async (item: PreviewItem) => {
    const draftId = item.product_draft_id;
    if (draftId == null) return;
    if (pendingUploads > 0) {
      fail('图片仍在导入，请等待完成后更新');
      return;
    }
    setRegeneratingDetailDraftIds((current) => new Set(current).add(draftId));
    setError('');
    setMessage('');
    try {
      // 先把当前轮播图（含本地未保存编辑）持久化，让后端按最新清单合成详情图；
      // 否则后端只会读取到保存前的旧轮播图清单。
      await saveProductPreview(ctx, taskId, [collectDesiredState(item)]);
      const data = await regeneratePreviewDetail(ctx, taskId, draftId);
      previewVersionRef.current += 1;
      setPreview(data);
      const refreshed = data.items.find(
        (candidate) => (candidate.product_draft_id ?? candidate.item_id) === draftId,
      );
      if (refreshed) {
        setEdits((previous) => {
          const current = previous[draftId] ?? {};
          return {
            ...previous,
            [draftId]: { ...current, imageManifest: cloneManifest(refreshed.image_manifest) },
          };
        });
      }
      notify('已根据当前轮播图更新详情图');
    } catch (err) {
      fail(err);
    } finally {
      setRegeneratingDetailDraftIds((current) => {
        const next = new Set(current);
        next.delete(draftId);
        return next;
      });
    }
  };

  // 第 4 格尺寸图重烧：只改本体长宽高，其他图片与文字保持不变。
  const reburnDimensionImage = async (item: PreviewItem) => {
    const info = item.dimension_image;
    const draftId = item.product_draft_id;
    if (!info || draftId == null) return;
    const edit = dimensionEdits[draftId];
    const pick = (key: 'length' | 'width' | 'height', fallback: number | null) => {
      const raw = edit?.[key];
      if (raw !== undefined && raw !== '') return Number(raw);
      return fallback;
    };
    const length = pick('length', info.length_cm);
    const width = pick('width', info.width_cm);
    const height = pick('height', info.height_cm);
    if (
      length == null || width == null || height == null ||
      ![length, width, height].every((value) => Number.isFinite(value) && value > 0)
    ) {
      fail('请填写完整的本体长/宽/高（厘米）');
      return;
    }
    setReburningDraftId(draftId);
    setError('');
    try {
      await ppRequest(
        ctx,
        `${API_BASE}/dimension-canvas/tasks/${taskId}/items/${item.item_id}/reburn`,
        {
          body: {
            length_cm: length,
            width_cm: width,
            height_cm: height,
            unit: 'in',
            corner: 'bottom-right',
          },
        },
      );
      setDimensionEdits((previous) => {
        const next = { ...previous };
        delete next[draftId];
        return next;
      });
      await load(true);
      notify('尺寸图已按新尺寸重烧（英寸）');
    } catch (err) {
      fail(err);
    } finally {
      setReburningDraftId(null);
    }
  };

  // 一键把本批预估尺寸标为已确认：只改来源标记，图上数字不变，不重烧。
  const confirmAllEstimated = async () => {
    if (dimensionSummary.estimated <= 0) return;
    setConfirmingDimensions(true);
    try {
      const result = await ppRequest<{ confirmed: number }>(
        ctx,
        `${API_BASE}/dimension-canvas/tasks/${taskId}/confirm-estimated`,
        { body: {} },
      );
      await load(true, true);
      notify(`已确认 ${Number(result?.confirmed || 0)} 条预估尺寸`);
    } catch (err) {
      fail(err);
    } finally {
      setConfirmingDimensions(false);
    }
  };

  const saveAll = async () => {
    if (pendingUploads > 0) {
      fail('图片仍在导入，请等待完成后保存');
      return;
    }
    setSaving(true);
    setError('');
    setMessage('');
    try {
      // 保存会提交完整图片清单：先补齐剩余元数据与未投影图片，避免空 manifest 覆盖已有图片。
      const allLoadedItems = await ensureAllItemsLoaded();
      const expectedTotal = previewRef.current?.item_count ?? allLoadedItems.length;
      if (allLoadedItems.length < expectedTotal) {
        throw new Error(`还有 ${expectedTotal - allLoadedItems.length} 条链接未加载完成，请稍后重试`);
      }
      setMediaHydrate(null);
      const { items: hydratedItems, missing: mediaMissing } = await hydrateAllMedia(
        allLoadedItems.filter((item) => !item.excluded),
        (done, total) => setMediaHydrate({ done, total }),
      );
      // 缺图片清单时绝不能保存：后端会用空 manifest 覆盖掉草稿里已保存的图片。
      if (mediaMissing.length > 0) {
        throw new Error(
          `还有 ${mediaMissing.length} 条链接的图片清单未加载完成（本地服务繁忙或请求超时），请稍后重试`,
        );
      }
      const items = hydratedItems
        .filter((item) => item.product_draft_id != null && !item.excluded)
        .map(collectDesiredState);
      const data = await saveProductPreview(ctx, taskId, items);
      // 后端保存时会把「不属于本任务/草稿」的图片引用剔除（dropped_asset_ids）。这种
      // 情况必须明确提示，否则用户以为已保存成功，到导出时才发现少了几张图。
      const droppedCount = (data.items ?? []).reduce(
        (total, item) => total + (item.dropped_asset_ids?.length ?? 0),
        0,
      );
      if (droppedCount > 0) {
        notify(
          `已保存 ${data.saved_count ?? items.length} 条商品的预检修改；其中 ${droppedCount} 张图片的引用已失效（不属于本单），已自动移除，请重新选择这几张图片`,
        );
      } else {
        notify(`已保存 ${data.saved_count ?? items.length} 条商品的预检修改`);
      }
      await load();
    } catch (err) {
      fail(err);
    } finally {
      setSaving(false);
      setMediaHydrate(null);
    }
  };

  const startFinalize = async (format: PreviewExportFormat = 'dxm') => {
    if (pendingUploads > 0) {
      fail('图片仍在导入，请等待完成后再完成预检');
      return;
    }
    setStartingFinalize(true);
    setError('');
    setMessage('');
    try {
      // 导出前补齐所有链接的元数据与图片投影：否则未翻到页的链接会用空 manifest 提交。
      const allLoadedItems = await ensureAllItemsLoaded();
      const expectedTotal = previewRef.current?.item_count ?? allLoadedItems.length;
      if (allLoadedItems.length < expectedTotal) {
        throw new Error(`还有 ${expectedTotal - allLoadedItems.length} 条链接未加载完成，请稍后重试`);
      }
      setMediaHydrate(null);
      const { items: hydratedItems } = await hydrateAllMedia(
        allLoadedItems.filter((item) => !item.excluded),
        (done, total) => setMediaHydrate({ done, total }),
      );
      // 「只看成功链接」以统一失败标记为准：失败/待确认、部分产出、回退来源图都不算成功。
      const exportableItems = hydratedItems.filter(
        (item) =>
          !item.excluded &&
          item.exportable &&
          (!onlySuccess || !itemHasFailure(item)),
      );
      if (exportableItems.length === 0) throw new Error(onlySuccess ? '没有可导出的成功链接' : '任务没有可完成并导出的商品');
      // 图片清单没补齐就提交，后台生成表格会报「没有可导出的 AI 处理图」。
      // 这里直接拦住并说清楚：不是商品数据问题，是本地服务这次没把清单拉回来。
      const mediaMissing = exportableItems.filter(
        (item) => item.media_loaded === false && item.product_draft_id != null,
      );
      if (mediaMissing.length > 0) {
        throw new Error(
          `还有 ${mediaMissing.length} 个可导出商品的图片清单未加载完成（本地服务繁忙或请求超时），请稍后重试`,
        );
      }
      const missingDraft = exportableItems.find((item) => item.product_draft_id == null);
      if (missingDraft) throw new Error(`可导出商品 #${missingDraft.item_id} 缺少草稿 ID，已阻止不完整提交`);
      const missingMainItems = exportableItems.filter((item) => !effectiveManifest(item).main_asset_id);
      if (missingMainItems.length > 0) {
        setMessage(`${missingMainItems.length} 个商品未选择主图，导出时将自动回退使用来源主图`);
      }

      const desiredItems = exportableItems.map(collectDesiredState);
      const request = await resolveFinalizeRequest({
        workspaceId: ctx.workspaceId,
        taskId,
        items: desiredItems,
        storageKey: idempotencyStorageKey,
        storage: { read: readSession, write: writeSession },
      });
      const run = await finalizeProductPreview(
        ctx,
        taskId,
        request.items,
        request.idempotencyKey,
        format,
      );
      writeSession(runStorageKey, run.id);
      acceptFinalizeRun(run);
      if (run.status === 'queued' || run.status === 'publishing') refreshRef.current?.watch(run.id);
    } catch (err) {
      const raw = err instanceof Error ? err.message : String(err);
      // 后端偶发只回一句通用「操作失败，请稍后重试」时，用户不知道该做什么。
      // 这里补上可执行的两条路：缩小到成功链接，或刷新核对后重试。
      fail(
        raw.includes('操作失败')
          ? '导出没有提交成功：可以勾选「只看成功链接」只导出已成功的商品，或点「重新加载」核对后再试；如果还是不行，请把这条提示截图发给我们'
          : err,
      );
    } finally {
      setStartingFinalize(false);
      setMediaHydrate(null);
    }
  };

  const retryFinalize = async () => {
    if (!finalizeRun) return;
    setRetrying(true);
    setError('');
    try {
      const run = await retryPreviewFinalizeRun(ctx, taskId, finalizeRun.id);
      writeSession(runStorageKey, run.id);
      acceptFinalizeRun(run);
      if (run.status === 'queued' || run.status === 'publishing') refreshRef.current?.watch(run.id);
    } catch (err) {
      fail(err);
    } finally {
      setRetrying(false);
    }
  };

  const reloadAfterStale = async () => {
    notify('正在读取最新版本；当前未保存编辑会继续保留，请重新核对后再完成预检。');
    await load(true);
    removeSession(runStorageKey);
    removeSession(idempotencyStorageKey);
    setFinalizeRun(null);
  };

  /**
   * 工具栏「重新加载」：重拉预检数据，并丢掉会话里缓存的导出幂等键。
   *
   * 导出本身会把 preview_revision 推进一格，若继续复用那份旧键，后端会按
   * 「同一个键换了另一个请求」拒绝，而且因为键是按期望状态算出来的，
   * 不清掉就永远算回同一个键。清掉之后重算的键包含 revision，能正常重新发起。
   */
  const reloadForRetry = () => {
    removeSession(idempotencyStorageKey);
    void load();
  };

  const restoreUndo = () => {
    if (!undoSnackbar || Date.now() > undoSnackbar.expiresAt) {
      setUndoSnackbar(null);
      return;
    }
    const { draftId, undo } = undoSnackbar;
    const item = allItems.find((candidate) => (candidate.product_draft_id ?? candidate.item_id) === draftId);
    if (!item) return;
    setEdits((previous) => {
      const current = previous[draftId] ?? {};
      const manifest = restoreRemovedAsset(current.imageManifest ?? item.image_manifest, undo);
      return { ...previous, [draftId]: { ...current, imageManifest: manifest } };
    });
    setUndoSnackbar(null);
    notify('已撤销图片删除');
  };

  /**
   * 把排除/恢复结果本地合并进当前预览，不再拉取或重建整份预检
   * （逐条重建整份预检正是删除请求 30s 超时的原因）。
   */
  const applyExclusionLocally = (draftIds: number[], excluded: boolean) => {
    const target = new Set(draftIds);
    // 递增版本号让并发中的静默轮询结果失效，避免旧快照把刚删除的链接「回弹」回来。
    previewVersionRef.current += 1;
    setPreview((current) => {
      if (!current) return current;
      return {
        ...current,
        items: current.items.map((item) => (
          item.product_draft_id != null && target.has(item.product_draft_id)
            ? { ...item, excluded }
            : item
        )),
        excluded_draft_ids: excluded
          ? Array.from(new Set([...current.excluded_draft_ids, ...draftIds])).sort((a, b) => a - b)
          : current.excluded_draft_ids.filter((id) => !target.has(id)),
      };
    });
    setSelectedDraftIds((current) => {
      if (!excluded) return current;
      const next = new Set(current);
      for (const draftId of draftIds) next.delete(draftId);
      return next;
    });
    // 预检清单已变化，旧 finalize run 的快照（含已删除/新恢复的链接）已失效；
    // 必须清除 run 状态，否则「仅重试失败图片」会复用旧快照继续被删除项阻挡。
    setFinalizeRun(null);
    removeSession(runStorageKey);
    removeSession(idempotencyStorageKey);
  };

  /** 批量排除/恢复：一次请求写设置 + 本地合并结果。 */
  const applyExclusion = async (draftIds: number[], excluded: boolean, successMessage: string) => {
    const ids = Array.from(new Set(draftIds.filter((draftId) => draftId > 0)));
    if (ids.length === 0) return;
    setError('');
    setMessage('');
    setExcludingDraftIds((current) => new Set([...current, ...ids]));
    try {
      const result = await excludePreviewItems(ctx, taskId, ids, excluded);
      applyExclusionLocally(result.applied_draft_ids, excluded);
      notify(successMessage);
    } catch (err) {
      fail(err);
    } finally {
      setExcludingDraftIds((current) => {
        const next = new Set(current);
        for (const draftId of ids) next.delete(draftId);
        return next;
      });
    }
  };

  const excludeItem = async (draftId: number) => {
    const item = allItems.find((candidate) => candidate.product_draft_id === draftId);
    const label = item?.skc || `商品 #${draftId}`;
    if (!window.confirm(`确定从预检中删除「${label}」吗？删除后该商品不再参与最终导出，可在页面底部「已排除」列表中恢复。`)) return;
    await applyExclusion([draftId], true, `已从预检删除「${label}」`);
  };

  const excludeItems = async (draftIds: number[]) => {
    if (draftIds.length === 0) return;
    const labels = draftIds
      .slice(0, 5)
      .map((draftId) => itemOfDraft(draftId)?.skc || `商品 #${draftId}`)
      .join('、');
    const more = draftIds.length > 5 ? ` 等共 ${draftIds.length} 个` : '';
    if (!window.confirm(`确定从预检中删除选中的 ${draftIds.length} 个商品吗？删除后不再参与最终导出，可在页面底部「已排除」列表中恢复。\n${labels}${more}`)) return;
    await applyExclusion(draftIds, true, `已从预检删除 ${draftIds.length} 个商品`);
  };

  const restoreItems = async (draftIds: number[]) => {
    const ids = draftIds.filter((draftId) => draftId > 0);
    if (ids.length === 0) return;
    await applyExclusion(
      ids,
      false,
      ids.length === 1 ? `已恢复商品 #${ids[0]} 到预检列表` : `已恢复 ${ids.length} 个商品到预检列表`,
    );
  };

  const restoreItem = async (draftId: number) => {
    await restoreItems([draftId]);
  };

  const restoreAllExcluded = async () => {
    const ids = excludedItems
      .map((item) => item.product_draft_id)
      .filter((id): id is number => id != null);
    if (ids.length === 0) return;
    if (!window.confirm(`确定恢复全部 ${ids.length} 个已删除商品吗？`)) return;
    await restoreItems(ids);
  };

  const excludeFailedItems = async () => {
    const failedItems = allItems.filter(itemHasFailure);
    const failedDraftIds = failedItems
      .map((item) => item.product_draft_id)
      .filter((id): id is number => id != null);
    if (failedDraftIds.length === 0) return;
    const previewLabels = failedItems
      .slice(0, 5)
      .map((item) => item.skc || `商品 #${item.product_draft_id}`)
      .join('、');
    const more = failedItems.length > 5 ? ` 等共 ${failedItems.length} 个` : '';
    if (!window.confirm(`确定一键剔除 ${failedItems.length} 个失败/需处理的商品吗？剔除后不再参与最终导出，可在页面底部「已排除」列表中恢复。\n${previewLabels}${more}`)) return;
    await applyExclusion(failedDraftIds, true, `已一键剔除 ${failedItems.length} 个失败/需处理的商品`);
  };

  const finalizing = finalizeRun?.status === 'queued' || finalizeRun?.status === 'publishing';
  const finalizeNeedsResolution = finalizeRun?.status === 'publish_failed' || finalizeRun?.status === 'stale';
  const mutationsLocked = Boolean(finalizing || startingFinalize || retrying);
  const dirtyCount = allItems.filter(itemIsDirty).length;
  const exportableCount = itemsFullyLoaded
    ? allItems.filter((item) => item.exportable).length
    : preview?.item_summary?.exportable_count ??
      allItems.filter((item) => item.exportable).length;
  const allAssets = mergeAssets(...allItems.map(effectiveAssets));

  const compositionItem =
    compositionDraftId == null
      ? null
      : allItems.find(
          (item) => (item.product_draft_id ?? item.item_id) === compositionDraftId,
        ) ?? null;
  const compositionAssets = compositionItem ? effectiveAssets(compositionItem) : [];
  const compositionMainAsset = compositionItem
    ? compositionAssets.find(
        (asset) => asset.id === compositionItem.image_manifest.main_asset_id,
      )
    : undefined;
  const compositionProductImage = compositionItem
    ? compositionMainAsset?.preview_url ||
      compositionAssets.find(
        (asset) =>
          asset.bucket === 'processed' &&
          asset.preview_url &&
          !asset.preview_url.includes('grid_image_summary'),
      )?.preview_url ||
      compositionItem.main_image ||
      compositionItem.carousel_images[0] ||
      ''
    : '';
  const compositionCore = compositionItem ? effectiveCoreFields(compositionItem) : {};
  const compositionSize = [
    compositionCore.length_cm,
    compositionCore.width_cm,
    compositionCore.height_cm,
  ]
    .filter((value) => value != null && String(value).trim() !== '')
    .join(' × ');
  const compositionSpecRows = compositionItem
    ? [
        { label: '尺寸', value: compositionSize ? `${compositionSize} cm` : '—' },
        { label: '重量', value: compositionCore.weight_g ? `${compositionCore.weight_g} g` : '—' },
        { label: 'SKU', value: compositionItem.skc || compositionCore.sku || '—' },
      ]
    : [];
  const compositionCategory = compositionItem
    ? String(compositionCore.category_path || compositionItem.title || '')
    : '';

  const batchComposeItems: BatchCompositionItem[] = (() => {
    const source = selectedDraftIds.size
      ? allItems.filter(
          (item) =>
            item.product_draft_id != null &&
            selectedDraftIds.has(item.product_draft_id),
        )
      : allItems.filter((item) => item.product_draft_id != null && item.exportable);
    return source.map((item) => {
      const assets = effectiveAssets(item);
      const mainAsset = assets.find(
        (asset) => asset.id === item.image_manifest.main_asset_id,
      );
      const productImageUrl =
        mainAsset?.preview_url ||
        assets.find(
          (asset) =>
            asset.bucket === 'processed' &&
            asset.preview_url &&
            !asset.preview_url.includes('grid_image_summary'),
        )?.preview_url ||
        item.main_image ||
        item.carousel_images[0] ||
        '';
      const core = effectiveCoreFields(item);
      const size = [core.length_cm, core.width_cm, core.height_cm]
        .filter((value) => value != null && String(value).trim() !== '')
        .join(' × ');
      return {
        draftId: item.product_draft_id as number,
        headline: compositionSlogan(item.title, core.category_path || ''),
        subtitle: compositionSubtitle(core.category_path || ''),
        productImageUrl,
        specRows: [
          { label: '尺寸', value: size ? `${size} cm` : '—' },
          { label: '重量', value: core.weight_g ? `${core.weight_g} g` : '—' },
          { label: 'SKU', value: item.skc || core.sku || '—' },
        ],
      };
    });
  })();

  const toggleExpanded = (draftId: number) => {
    setExpandedDraftIds((current) => {
      const next = new Set(current);
      if (next.has(draftId)) next.delete(draftId);
      else next.add(draftId);
      return next;
    });
  };

  const expandAllItems = () => {
    setExpandedDraftIds(new Set(allItems.map((item) => item.product_draft_id ?? item.item_id)));
  };

  const collapseAllItems = () => {
    setExpandedDraftIds(new Set());
  };

  return (
    <div className="verify-page verify-precheck-page">
      {/* 已有数据时重新加载（保存后/轮询）也要有明确遮罩，避免整页看起来“点不动”。 */}
      {loading && (
        <div className="app-loading-mask verify-precheck-loading-mask" role="status" aria-live="polite">
          <i className="app-spinner" aria-hidden="true" />
          <span>正在刷新预检数据…</span>
        </div>
      )}
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <h1>预检与最终发布</h1>
          <p>图片先以稳定素材 ID 在本地清单中增删排序；点击完成后，仅发布最终保留图片并生成店小秘表格。</p>
        </div>
        <div className="verify-command-stats">
          <span><i className="iconfont icon-database" aria-hidden="true" /><strong>{totalItemCount}</strong><em>项目</em></span>
          <span><i className="iconfont icon-check-circle" aria-hidden="true" /><strong>{exportableCount}</strong><em>可导出</em></span>
          <span><i className="iconfont icon-save" aria-hidden="true" /><strong>{dirtyCount}</strong><em>未保存</em></span>
          {mediaLoadingCount > 0 && (
            <span title="正在补齐当前页图片清单">
              <i className="app-spinner is-sm" aria-hidden="true" />
              <em>图片加载中</em>
            </span>
          )}
          {itemsLoading && (
            <span title="首屏已可用，剩余链接正在后台补齐">
              <i className="app-spinner is-sm" aria-hidden="true" />
              <em>加载全部链接</em>
            </span>
          )}
        </div>
        <div className="verify-command-actions">
          <button
            type="button"
            className="btn-mini primary"
            onClick={() => {
              void (async () => {
                const allLoadedItems = await ensureAllItemsLoaded();
                await hydrateAllMedia(allLoadedItems.filter((item) => !item.excluded));
                setBatchComposeOpen(true);
              })();
            }}
            disabled={mutationsLocked || batchComposeItems.length === 0}
          >
            批量拼装
          </button>
        </div>
      </header>

      {(message || error) && (
        <div className={`verify-message ${error ? 'error' : ''}`}>{error || message}</div>
      )}

      {initialChangeSetId && (
        <DimensionChangeSetReview changeSetId={initialChangeSetId} onChanged={() => void load(true)} />
      )}

      <section className="verify-section">
        <div className="verify-section-head">
          <h2>任务 #{preview.task_id} · {preview.task.title}</h2>
          <span className="verify-sub">
            {taskStatusLabel(preview.task.status)} · 共 {preview.task.total_count} 项 · 可导出 {exportableCount} · 成功 {preview.task.success_count} · 失败 {preview.task.failed_count}
          </span>
        </div>
        <div className="verify-actions">
          <button
            type="button"
            className="primary"
            onClick={() => void saveAll()}
            disabled={saving || loading || mutationsLocked || pendingUploads > 0 || allItems.length === 0}
          >
            {saving
              ? (
                mediaHydrate && mediaHydrate.total > 0
                  ? <><i className="app-spinner is-sm" aria-hidden="true" />保存中 · 图片 {mediaHydrate.done}/{mediaHydrate.total}</>
                  : <><i className="app-spinner is-sm" aria-hidden="true" />保存中…</>
              )
              : `保存预检修改${dirtyCount > 0 ? `（${dirtyCount} 条有修改）` : ''}`}
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => void startFinalize('dxm')}
            disabled={startingFinalize || loading || mutationsLocked || pendingUploads > 0 || finalizeNeedsResolution || exportableCount === 0}
          >
            {startingFinalize
              ? (
                mediaHydrate && mediaHydrate.total > 0
                  ? <><i className="app-spinner is-sm" aria-hidden="true" />补齐图片清单 {mediaHydrate.done}/{mediaHydrate.total}</>
                  : <><i className="app-spinner is-sm" aria-hidden="true" />正在建立完成任务…</>
              )
              : pendingUploads > 0
                ? <><i className="app-spinner is-sm" aria-hidden="true" />正在导入图片（{pendingUploads}）</>
                : '完成预检并导出'}
          </button>
          {failedItemCount > 0 && (
            <span className="verify-actions-hint">
              有 {failedItemCount} 个失败链接：默认不参与导出与计费（随后会释放）；如需清理，可勾选「只看失败链接」后删除所选
            </span>
          )}
          <button type="button" onClick={reloadForRetry} disabled={loading || mutationsLocked}>
            {loading ? <><i className="app-spinner is-sm" aria-hidden="true" />重新加载</> : '重新加载'}
          </button>
          <button
            type="button"
            onClick={() => {
              void (async () => {
                const allLoadedItems = await ensureAllItemsLoaded();
                await hydrateAllMedia(allLoadedItems.filter((item) => !item.excluded));
                setSkuManagerOpen(true);
              })();
            }}
            disabled={loading || mutationsLocked || allItems.length === 0}
          >
            管理 SKU 规格图
          </button>
        </div>
        {mediaHydrate && mediaHydrate.total > 0 && (
          <div className="precheck-media-progress" aria-live="polite">
            <div className="precheck-progress-copy">
              <strong>{saving ? '保存前补齐图片清单' : '导出前补齐图片清单'}</strong>
              <span>{mediaHydrate.done} / {mediaHydrate.total}</span>
            </div>
            <progress
              max={mediaHydrate.total}
              value={mediaHydrate.done}
              aria-label={`图片清单 ${mediaHydrate.done} / ${mediaHydrate.total}`}
            />
            <span className="precheck-progress-percent">
              {Math.round((mediaHydrate.done / mediaHydrate.total) * 100)}%
            </span>
          </div>
        )}
        {(dimensionSummary.estimated > 0 ||
          dimensionSummary.reused > 0 ||
          dimensionSummary.missing > 0 ||
          dimensionSummary.failed > 0) && (
          <div className="precheck-dim-summary">
            <span className="precheck-dim-summary-title">尺寸图</span>
            {dimensionSummary.estimated > 0 && (
              <span className="is-estimated">预估 {dimensionSummary.estimated}</span>
            )}
            {dimensionSummary.reused > 0 && (
              <span>原图加工 {dimensionSummary.reused}</span>
            )}
            {dimensionSummary.missing > 0 && (
              <span className="is-missing">缺失 {dimensionSummary.missing}</span>
            )}
            {dimensionSummary.failed > 0 && (
              <span className="is-failed">失败 {dimensionSummary.failed}</span>
            )}
            {(dimensionSummary.estimated > 0 ||
              dimensionSummary.missing + dimensionSummary.failed > 0) && (
              <span className="precheck-dim-summary-actions">
                {dimensionSummary.missing + dimensionSummary.failed > 0 && (
                  <button
                    type="button"
                    className="btn-mini"
                    onClick={() => setOnlyDimensionIssues((current) => !current)}
                  >
                    {onlyDimensionIssues
                      ? '取消只看待处理'
                      : `只看待处理（${dimensionSummary.missing + dimensionSummary.failed}）`}
                  </button>
                )}
                {dimensionSummary.estimated > 0 && (
                  <>
                    <button
                      type="button"
                      className="btn-mini"
                      onClick={() => setOnlyEstimated((current) => !current)}
                    >
                      {onlyEstimated ? '取消只看预估' : '只看预估'}
                    </button>
                    <button
                      type="button"
                      className="btn-mini primary"
                      disabled={confirmingDimensions || mutationsLocked}
                      onClick={() => void confirmAllEstimated()}
                      title="只把来源标记改为已确认，图上数字不变，不重烧"
                    >
                      {confirmingDimensions
                        ? '确认中…'
                        : `全部按预估值确认（${dimensionSummary.estimated}）`}
                    </button>
                  </>
                )}
              </span>
            )}
          </div>
        )}
        <div className="precheck-toolbar">
          <input
            type="search"
            className="precheck-search"
            placeholder="按序列号 / SKU / 商品编号搜索"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <label className="precheck-only-success">
            <input
              type="checkbox"
              checked={onlySuccess}
              onChange={(event) => {
                setOnlySuccess(event.target.checked);
                if (event.target.checked) setOnlyFailed(false);
              }}
            />
            只看成功链接
          </label>
          <label className="precheck-only-success">
            <input
              type="checkbox"
              checked={onlyFailed}
              onChange={(event) => {
                setOnlyFailed(event.target.checked);
                if (event.target.checked) setOnlySuccess(false);
              }}
            />
            只看失败链接
          </label>
          <label className="precheck-only-success">
            <input
              type="checkbox"
              checked={onlyEstimated}
              onChange={(event) => setOnlyEstimated(event.target.checked)}
            />
            只看预估尺寸链接
          </label>
          <label className="precheck-page-size">
            每页
            <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}>
              {PRECHECK_PAGE_SIZES.map((size) => (
                <option key={size} value={size}>{size} 条</option>
              ))}
            </select>
          </label>
          <span className="precheck-count">
            {filteredItems.length > 0
              ? `显示 ${(safePage - 1) * pageSize + 1}-${Math.min(safePage * pageSize, filteredItems.length)} / 共 ${filteredItems.length} 个商品`
              : '共 0 个商品'}
          </span>
          <label className="precheck-only-success">
            <input
              type="checkbox"
              checked={allInViewSelected}
              disabled={selectableDraftIds.length === 0 || mutationsLocked}
              onChange={() => {
                setSelectedDraftIds((current) => {
                  const next = new Set(current);
                  if (allInViewSelected) {
                    for (const draftId of selectableDraftIds) next.delete(draftId);
                  } else {
                    for (const draftId of selectableDraftIds) next.add(draftId);
                  }
                  return next;
                });
              }}
            />
            全选当前筛选（{selectableDraftIds.length}）
          </label>
          <button
            type="button"
            className="btn-mini danger precheck-bulk-delete-btn"
            disabled={selectedDraftIdsInView.length === 0 || mutationsLocked || excludingDraftIds.size > 0}
            onClick={() => void excludeItems(selectedDraftIdsInView)}
          >
            删除所选{selectedDraftIdsInView.length > 0 ? `（${selectedDraftIdsInView.length}）` : ''}
          </button>
          <span className="precheck-expand-actions">
            <button type="button" onClick={expandAllItems} disabled={allItems.length === 0}>全部展开</button>
            <button type="button" onClick={collapseAllItems} disabled={expandedDraftIds.size === 0}>全部折叠</button>
          </span>
        </div>
      </section>

      {finalizeRun && (
        <PrecheckFinalizeProgress
          run={finalizeRun}
          assets={allAssets}
          retrying={retrying}
          createdLabel={formatFinalizeTime(finalizeRun.created_at)}
          onRetry={() => void retryFinalize()}
          onReloadStale={() => void reloadAfterStale()}
          onExcludeFailed={() => void excludeFailedItems()}
        />
      )}

      {historyRuns.length > 0 && (
        <section className="precheck-finalize-history">
          <header>
            <h2>历史导出</h2>
            <span>每次导出的表格都一直保存在本机，这里是唯一下载入口（重新下载 = 不重新生成）；妙手模板也能按任意一次导出现场生成</span>
          </header>
          <ul>
            {historyRuns.map((run) => (
              <li key={run.id}>
                <div>
                  <strong>{formatFinalizeTime(run.created_at)}</strong>
                  <span>
                    {run.status === 'completed'
                      ? `${run.product_count} 个商品 / ${run.row_count} 行`
                      : FINALIZE_RUN_STATUS_LABELS[run.status]}
                    {run.file ? ` · ${run.file}` : ''}
                  </span>
                </div>
                <button
                  type="button"
                  disabled={downloading || !run.workbook_ready || !run.download}
                  onClick={() => void downloadRun(run, false)}
                >
                  {downloading
                    ? <><i className="app-spinner is-sm" aria-hidden="true" />重新下载</>
                    : run.workbook_ready
                      ? `重新下载（${EXPORT_FORMAT_LABELS[run.workbook_format] ?? run.workbook_format}）`
                      : '暂无表格'}
                </button>
                {/* 妙手模板按这一条历史导出现场生成：它是结果卡片的兜底入口——从「历史记录」进入
                    预检时卡片不存在，没有这两个按钮就再也拿不到妙手表格。已生成过的直接重下
                    （文件一直留在本机），不再重跑一遍生成。 */}
                {run.status === 'completed' && (['apparel', 'general'] as const).map((kind) => {
                  const kindLabel = kind === 'apparel' ? '服饰类' : '非服饰类';
                  const generated = (run.miaoshou_generated ?? []).includes(kind);
                  return (
                    <button
                      key={kind}
                      type="button"
                      disabled={msExporting !== null}
                      title={generated
                        ? '该文件已生成并保存在本机，直接重新下载（不会重新生成）'
                        : '按这次导出的快照现场生成妙手模板'}
                      onClick={() => runMiaoshouAction(run, kind)}
                    >
                      {msExporting === kind
                        ? <><i className="app-spinner is-sm" aria-hidden="true" />正在处理…</>
                        : `妙手·${kindLabel}（${generated ? '重新下载' : '生成'}）`}
                    </button>
                  );
                })}
              </li>
            ))}
          </ul>
        </section>
      )}

      {allItems.length === 0 && <p className="verify-empty">任务没有可预检的成功商品。</p>}

      {filteredItems.length === 0 && allItems.length > 0 && onlyFailed && (
        <p className="verify-empty">没有发布失败的链接，可切换「只看成功链接」或取消筛选查看全部商品。</p>
      )}

      {filteredItems.length === 0 && allItems.length > 0 && !onlyFailed && (
        <p className="verify-empty">没有匹配「{search}」的商品，请检查序列号 / SKU / 商品编号。</p>
      )}

      {pagedItems.map((item) => {
        const draftId = item.product_draft_id ?? item.item_id;
        const edit = editFor(item);
        const coreFields = effectiveCoreFields(item);
        const manifest = effectiveManifest(item);
        const assets = effectiveAssets(item);
        const hasOverrides = itemIsDirty(item);
        const isExpanded = expandedDraftIds.has(draftId);
        const sourceUrl = String(item.source_url ?? '').trim();
        const safeSourceUrl = /^https?:\/\//i.test(sourceUrl) ? sourceUrl : '';
        const listingAdvice = listingAdviceByDraftId[draftId];
        const listingAdviceLoading = listingAdviceLoadingIds.has(draftId);
        const listingAdviceError = listingAdviceErrors[draftId];
        // 风险等级 A~E 取首字母做配色；「待人工确认」这类非等级文案统一落到中性色，
        // 否则会生成 tone-待 这种没有样式定义的类名。
        const adviceToneKey = (listingAdvice?.level ?? '').trim().charAt(0).toLowerCase();
        const listingAdviceTone = /^[abcde]$/.test(adviceToneKey) ? adviceToneKey : 'c';
        return (
          <section key={item.item_id} className={`verify-section precheck-card${hasOverrides ? ' is-edited' : ''}`}>
            <div className="precheck-card-head">
              <label className="precheck-item-select">
                <input
                  type="checkbox"
                  aria-label={`选择 ${item.skc || `商品 #${draftId}`}`}
                  checked={item.product_draft_id != null && selectedDraftIds.has(item.product_draft_id)}
                  disabled={item.product_draft_id == null || mutationsLocked}
                  onChange={(event) => {
                    const targetDraftId = item.product_draft_id;
                    if (targetDraftId == null) return;
                    setSelectedDraftIds((current) => {
                      const next = new Set(current);
                      if (event.target.checked) next.add(targetDraftId);
                      else next.delete(targetDraftId);
                      return next;
                    });
                  }}
                />
              </label>
              <button
                type="button"
                className="precheck-card-toggle"
                onClick={() => toggleExpanded(draftId)}
                aria-expanded={isExpanded}
              >
                <span className="precheck-chevron">{isExpanded ? '▾' : '▸'}</span>
                <span className="precheck-card-toggle-title">{item.skc || `商品 #${draftId}`}</span>
                {!isExpanded && item.title && (
                  <span
                    className="precheck-card-toggle-summary"
                    title={item.title}
                  >
                    {compositionSlogan(
                      item.title,
                      item.core_fields.category_path || '',
                    )}
                  </span>
                )}
                <span className="verify-sub">
                  {item.exportable ? '可导出' : '不可导出'} · {hasOverrides ? '已修改' : '未修改'} · 版本 {item.preview_revision} · 状态 {item.status}
                  {item.export_blockers?.length ? (
                    <em className="precheck-no-main-hint"> · {item.export_blockers.join('；')}</em>
                  ) : (item.exportable && !manifest.main_asset_id) || item.image_ai_failed === true ? (
                    <em className="precheck-no-main-hint"> · 未选主图（导出回退来源图）</em>
                  ) : null}
                </span>
                {item.dimension_image && (
                  <span
                    className={`precheck-dim-badge is-${item.dimension_image.state}`}
                    title={
                      item.dimension_image.source === 'applied'
                        ? '第 4 格使用原链接尺寸图加工后的商品图'
                        : item.dimension_image.source === 'generated'
                          ? '第 4 格使用 AI 生成图'
                          : undefined
                    }
                  >
                    {DIMENSION_IMAGE_LABELS[item.dimension_image.state] || item.dimension_image.state}
                    {item.dimension_image.source === 'applied'
                      ? ' · 原图加工'
                      : item.dimension_image.source === 'generated'
                        ? ' · 生成图'
                        : ''}
                  </span>
                )}
              </button>
              <button
                type="button"
                className="btn-mini danger precheck-exclude-btn"
                disabled={mutationsLocked || item.product_draft_id == null || excludingDraftIds.has(draftId)}
                onClick={() => {
                  if (item.product_draft_id != null) void excludeItem(item.product_draft_id);
                }}
                title={item.product_draft_id == null ? '缺少草稿 ID，无法删除' : '从预检中删除此商品，可随时恢复'}
              >
                {excludingDraftIds.has(draftId)
                  ? <><i className="app-spinner is-sm" aria-hidden="true" />删除中…</>
                  : '删除'}
              </button>
            </div>

            {isExpanded && (
            <div className="precheck-grid">
              <div className="precheck-fields">
                <label className="precheck-label">
                  <span>标题（导出 *产品标题 / *英文标题）</span>
                  <textarea
                    rows={5}
                    value={edit.title ?? item.title}
                    disabled={mutationsLocked}
                    onChange={(event) => {
                      setEdit(draftId, { title: event.target.value });
                      clearListingAdvice(draftId);
                    }}
                    placeholder="AI 生成标题"
                  />
                </label>
                <div className="precheck-label precheck-description-field">
                  <div className="precheck-field-heading">
                    <span>产品描述（导出产品描述列，详情图自动追加）</span>
                    <button
                      type="button"
                      className="precheck-advice-button"
                      disabled={listingAdviceLoading || item.product_draft_id == null}
                      onClick={() => void requestListingAdvice(item)}
                      title="结合 996 类目资质表并使用一次文本 AI 分析"
                    >
                      {listingAdviceLoading
                        ? <i className="app-spinner is-sm" aria-hidden="true" />
                        : <span aria-hidden="true">✦</span>}
                      {listingAdviceLoading ? '分析中…' : listingAdvice ? '重新建议' : '上架建议'}
                    </button>
                  </div>
                  <textarea
                    rows={5}
                    value={edit.description ?? item.description}
                    disabled={mutationsLocked}
                    onChange={(event) => {
                      setEdit(draftId, { description: event.target.value });
                      clearListingAdvice(draftId);
                    }}
                    placeholder="AI 生成五点描述"
                  />
                  {listingAdviceError && (
                    <p className="listing-advice-error" role="alert">建议生成失败：{listingAdviceError}</p>
                  )}
                  {listingAdvice && (
                    <aside className={`listing-advice-card tone-${listingAdviceTone}`} aria-label="上架建议">
                      <div className="listing-advice-summary">
                        <span className="listing-advice-level">{listingAdvice.level}</span>
                        <strong>{listingAdvice.action}</strong>
                        <span className="listing-advice-source">
                          {listingAdvice.source === 'ai+rules' ? 'AI + 表格规则' : '表格规则'}
                        </span>
                      </div>
                      <p><b>建议类目：</b>{listingAdvice.recommended_category}</p>
                      <p><b>判断：</b>{listingAdvice.reason}</p>
                      <p><b>注意：</b>{listingAdvice.warning}</p>
                      {listingAdvice.required_documents.length > 0 && (
                        <p><b>资料：</b>{listingAdvice.required_documents.join('；')}</p>
                      )}
                      {listingAdvice.notice && <small>{listingAdvice.notice}</small>}
                      <small>仅供预检参考，最终以平台审核结果为准。</small>
                    </aside>
                  )}
                </div>
                {!!item.variant_translation_review_values?.length && (
                  <div className="precheck-variant-review" role="alert">
                    <div>
                      <strong>规格翻译待确认</strong>
                      <span>
                        AI 和常用词库未能翻译以下规格，系统已保留原值，请在导出前人工修改。
                      </span>
                    </div>
                    <div className="precheck-variant-review-values">
                      {item.variant_translation_review_values.slice(0, 12).map((value) => (
                        <span key={value} title={value}>{value}</span>
                      ))}
                      {item.variant_translation_review_values.length > 12 && (
                        <em>另有 {item.variant_translation_review_values.length - 12} 项</em>
                      )}
                    </div>
                  </div>
                )}
                <div className="precheck-core-grid">
                  <label>SKU货号
                    <input disabled={mutationsLocked} value={coreFields.sku ?? ''} onChange={(event) => setField(draftId, 'sku', event.target.value)} />
                  </label>
                  <label><span className="precheck-dim-label">物流包裹长(cm) {provenanceBadge(item.dimension_provenance, item.dimension_confidence, 'length_cm')}</span>
                    <input disabled={mutationsLocked} value={coreFields.length_cm ?? ''} onChange={(event) => setField(draftId, 'length_cm', event.target.value)} />
                  </label>
                  <label><span className="precheck-dim-label">物流包裹宽(cm) {provenanceBadge(item.dimension_provenance, item.dimension_confidence, 'width_cm')}</span>
                    <input disabled={mutationsLocked} value={coreFields.width_cm ?? ''} onChange={(event) => setField(draftId, 'width_cm', event.target.value)} />
                  </label>
                  <label><span className="precheck-dim-label">物流包裹高(cm) {provenanceBadge(item.dimension_provenance, item.dimension_confidence, 'height_cm')}</span>
                    <input disabled={mutationsLocked} value={coreFields.height_cm ?? ''} onChange={(event) => setField(draftId, 'height_cm', event.target.value)} />
                  </label>
                  <label><span className="precheck-dim-label">重量(g) {provenanceBadge(item.dimension_provenance, item.dimension_confidence, 'weight_g')}</span>
                    <input disabled={mutationsLocked} value={coreFields.weight_g ?? ''} onChange={(event) => setField(draftId, 'weight_g', event.target.value)} />
                  </label>
                </div>
              </div>

              {effectiveShippingPackageRecords(item).length > 0 && (
                <section className="precheck-shipping-package" aria-label="SKU 包装件重尺">
                  <div className="precheck-shipping-package-head">
                    <div>
                      <h3>SKU 包装件重尺</h3>
                      <p>1688 商品件重尺按规格匹配；未匹配行仅展示，不参与导出。</p>
                    </div>
                    <span>{effectiveShippingPackageRecords(item).length} 个规格</span>
                  </div>
                  <div className="precheck-shipping-package-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>规格</th>
                          <th>长(cm)</th>
                          <th>宽(cm)</th>
                          <th>高(cm)</th>
                          <th>体积(cm³)</th>
                          <th>重量(g)</th>
                          <th>匹配状态/来源</th>
                        </tr>
                      </thead>
                      <tbody>
                        {effectiveShippingPackageRecords(item).map((record) => {
                          const editable = isEditableShippingPackageRecord(record);
                          const recordDisabled = mutationsLocked || !editable;
                          const sourceLabel = record.source_label ?? record.source ?? '1688采集';
                          return (
                            <tr key={record.variant_key} className={editable ? '' : 'is-unmatched'}>
                              <td title={record.specification}>{record.specification || '—'}</td>
                              {(['length_cm', 'width_cm', 'height_cm', 'volume_cm3'] as const).map((field) => (
                                <td key={field}>
                                  <input
                                    type="number"
                                    min="0"
                                    step="any"
                                    disabled={recordDisabled}
                                    value={record[field] ?? ''}
                                    onChange={(event) => setShippingPackageField(draftId, record.variant_key, field, event.target.value)}
                                  />
                                </td>
                              ))}
                              <td>
                                <input
                                  type="number"
                                  min="0"
                                  step="any"
                                  disabled={recordDisabled}
                                  value={record.weight_g ?? ''}
                                  onChange={(event) => setShippingPackageField(draftId, record.variant_key, 'weight_g', event.target.value)}
                                />
                              </td>
                              <td>
                                <span className={`precheck-package-status ${editable ? 'is-matched' : 'is-unmatched'}`}>
                                  {editable ? '已匹配' : '未匹配'}
                                </span>
                                <small>{sourceLabel}</small>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}

              <div className="precheck-images">
                {item.dimension_image && (
                  <div className="precheck-dim-image-editor">
                    <div className="precheck-dim-image-editor-head">
                      <strong>第 4 格尺寸图（英寸）</strong>
                      <span className={`precheck-dim-badge is-${item.dimension_image.state}`}>
                        {DIMENSION_IMAGE_LABELS[item.dimension_image.state] || item.dimension_image.state}
                        {item.dimension_image.source === 'applied'
                          ? ' · 原图加工'
                          : item.dimension_image.source === 'generated'
                            ? ' · 生成图'
                            : ''}
                      </span>
                    </div>
                    <div className="precheck-dim-image-inputs">
                      {(['length', 'width', 'height'] as const).map((axis) => (
                        <label key={axis}>
                          <span>{axis === 'length' ? '长' : axis === 'width' ? '宽' : '高'}(cm)</span>
                          <input
                            type="number"
                            min="0"
                            step="0.1"
                            disabled={mutationsLocked || reburningDraftId === draftId}
                            value={
                              dimensionEdits[draftId]?.[axis] ??
                              formatCmValue(
                                axis === 'length'
                                  ? item.dimension_image?.length_cm
                                  : axis === 'width'
                                    ? item.dimension_image?.width_cm
                                    : item.dimension_image?.height_cm,
                              )
                            }
                            onChange={(event) => {
                              const value = event.target.value;
                              setDimensionEdits((previous) => {
                                const current = previous[draftId] ?? {
                                  length: '',
                                  width: '',
                                  height: '',
                                };
                                return {
                                  ...previous,
                                  [draftId]: { ...current, [axis]: value },
                                };
                              });
                            }}
                          />
                        </label>
                      ))}
                      <button
                        type="button"
                        className="btn-mini primary"
                        disabled={mutationsLocked || reburningDraftId === draftId}
                        onClick={() => void reburnDimensionImage(item)}
                      >
                        {reburningDraftId === draftId ? '重烧中…' : '重烧尺寸图'}
                      </button>
                    </div>
                  </div>
                )}
                <div className="precheck-dimension-entry">
                  <div><strong>商品本体尺寸图</strong><span>画布交回后登记为本地尺寸素材，不提前发布 COS。</span></div>
                  <button
                    type="button"
                    className="btn-mini primary"
                    disabled={mutationsLocked}
                    onClick={() => onOpenDimensionItem(taskId, item.item_id)}
                  >添加尺寸图</button>
                </div>
                <div className="precheck-source-address">
                  <strong>处理前商品地址</strong>
                  {safeSourceUrl ? (
                    <a href={safeSourceUrl} target="_blank" rel="noreferrer" title={sourceUrl}>{sourceUrl}</a>
                  ) : (
                    <span>暂无商品地址</span>
                  )}
                </div>
                <PrecheckImageManager
                  assets={assets}
                  manifest={manifest}
                  disabled={mutationsLocked || item.product_draft_id == null}
                  onAddFiles={(target, files) => {
                    if (item.product_draft_id != null) void uploadAssets(item.product_draft_id, target, files);
                  }}
                  onManifestChange={(nextManifest) => setManifest(draftId, nextManifest)}
                  onPreview={setActiveImage}
                  retryingMediaAssetIds={retryingMediaAssetIds}
                  onRetryMediaAsset={(assetId) => void retryMediaSource(assetId)}
                  onUndoAvailable={(undo) => setUndoSnackbar({
                    draftId,
                    undo,
                    expiresAt: Date.now() + 5000,
                  })}
                  regeneratingDetail={regeneratingDetailDraftIds.has(draftId)}
                  onRegenerateDetail={() => void regenerateDetailForItem(item)}
                  onCompose={
                    item.product_draft_id != null
                      ? () => setCompositionDraftId(draftId)
                      : undefined
                  }
                />
              </div>
            </div>
            )}
          </section>
        );
      })}

      {totalPages > 1 && (
        <div className="precheck-pagination">
          <button type="button" disabled={safePage <= 1} onClick={() => setPage(safePage - 1)}>上一页</button>
          <span>第 {safePage} / {totalPages} 页</span>
          <button type="button" disabled={safePage >= totalPages} onClick={() => setPage(safePage + 1)}>下一页</button>
        </div>
      )}

      {excludedItems.length > 0 && (
        <section className="verify-section precheck-excluded-section">
          <div className="precheck-excluded-head">
            <h2>已排除的商品（{excludedItems.length}）</h2>
            <span className="verify-sub">已从预检列表删除，不再参与最终导出；如需重新纳入，点击恢复。</span>
            <button type="button" className="btn-mini" onClick={() => void restoreAllExcluded()} disabled={mutationsLocked}>全部恢复</button>
          </div>
          <ul className="precheck-excluded-list">
            {excludedItems.map((item) => {
              const draftId = item.product_draft_id;
              return (
                <li key={item.item_id}>
                  <span className="precheck-excluded-name">{item.skc || `商品 #${draftId ?? item.item_id}`}</span>
                  {item.title && <small className="precheck-excluded-title">{item.title}</small>}
                  <span className="verify-sub">{item.exportable ? '可导出' : '不可导出'} · {item.status}</span>
                  <button
                    type="button"
                    className="btn-mini"
                    disabled={mutationsLocked || draftId == null}
                    onClick={() => {
                      if (draftId != null) void restoreItem(draftId);
                    }}
                  >
                    恢复
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {batchComposeOpen && createPortal(
        <BatchCompositionDialog
          open={batchComposeOpen}
          items={batchComposeItems}
          onClose={() => setBatchComposeOpen(false)}
          onUpload={async (draftId, file) => {
            await uploadAssets(draftId, 'detail', [file], { throwOnError: true });
          }}
        />,
        document.body,
      )}

      {compositionItem && createPortal(
        <Suspense fallback={null}>
          <LocalCompositionDrawer
            open={compositionDraftId != null}
            title={compositionSlogan(
              compositionItem.title,
              compositionCategory,
            )}
            subtitle={compositionSubtitle(compositionCategory)}
            category={compositionCategory}
            productImageUrl={compositionProductImage}
            specRows={compositionSpecRows}
            onClose={() => setCompositionDraftId(null)}
            onSave={async (file) => {
              const draftId = compositionItem.product_draft_id;
              if (draftId == null) throw new Error('缺少草稿 ID，无法保存拼装图');
              await uploadAssets(draftId, 'detail', [file]);
            }}
          />
        </Suspense>,
        document.body,
      )}

      {/* portal 到 body：tab 面板 fill-mode 动画的层叠上下文会锁住 fixed toast 的 z-index，被顶栏盖住 */}
      {undoSnackbar && createPortal(
        <div className="precheck-undo-snackbar" role="status" aria-live="polite">
          <span>图片已从当前输出清单移除</span>
          <button type="button" onClick={restoreUndo}>撤销</button>
          <small>5 秒内有效</small>
        </div>, document.body)}

      {activeImage && createPortal(
        <div className={`precheck-lightbox${imageZoomed ? ' is-zoomed' : ''}`} role="dialog" aria-modal="true" aria-label="图片预览" onClick={() => {
          if (imageZoomed) setImageZoomed(false);
          else setActiveImage(null);
        }}>
          <div className="precheck-lightbox-stage">
            <img
              src={activeImage}
              alt="预览大图"
              onClick={(event) => {
                event.stopPropagation();
                setImageZoomed((zoomed) => !zoomed);
              }}
            />
            <div className="precheck-lightbox-hint">{imageZoomed ? '滚动查看图片细节 · 点击图片恢复适应屏幕' : '点击图片放大查看细节'}</div>
          </div>
          <button type="button" className="precheck-lightbox-close" aria-label="关闭" onClick={() => setActiveImage(null)}>×</button>
        </div>,
        document.body,
      )}

      {/* portal 到 body：同上，避免 tab 面板动画的层叠上下文锁住侧栏 z-index */}
      {skuManagerOpen && createPortal(
        <PrecheckSkuManager
          items={allItems}
          modeOf={(draftId) => {
            const item = itemOfDraft(draftId);
            return item ? effectiveVariantImageMode(item) : 'source';
          }}
          modeSavedOf={(draftId) => {
            const item = itemOfDraft(draftId);
            if (!item) return true;
            // 本地编辑值与已保存值一致 ⇒ 已生效；否则提示「待保存」。
            return effectiveVariantImageMode(item) === (item.overrides.variant_image_mode ?? 'auto');
          }}
          onApplyMode={applyVariantImageMode}
          excludedOf={(draftId) => {
            const item = itemOfDraft(draftId);
            return item ? effectiveExcludedVariants(item) : [];
          }}
          overridesOf={(draftId) => {
            const item = itemOfDraft(draftId);
            return item ? effectiveVariantImageOverrides(item) : {};
          }}
          overrideUrlOf={overrideUrlOfDraft}
          textReviewOf={textReviewOfDraft}
          optionsOf={variantImageOptionsOf}
          onExclude={(entries) => applyVariantExclusion(entries, true)}
          onRestore={(entries) => applyVariantExclusion(entries, false)}
          onSetVariantImage={applyVariantImageOverrides}
          onUploadCrop={uploadVariantCrop}
          onPrepareCropSrc={prepareVariantCropSrc}
          onPreview={setActiveImage}
          onClose={() => setSkuManagerOpen(false)}
        />,
        document.body,
      )}
    </div>
  );
}

export default ProductProcessingPrecheckPage;
