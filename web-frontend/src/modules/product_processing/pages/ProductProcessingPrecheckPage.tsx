import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { ppDownload, ppRequest, type ApiContext } from '../api/client';
import { productProcessingApiContext } from '../api/context';
import {
  excludePreviewItem,
  finalizeProductPreview,
  getPreviewFinalizeRun,
  restorePreviewItem,
  retryMediaAsset,
  retryPreviewFinalizeRun,
  saveProductPreview,
  uploadPreviewAssets,
  type PreviewSavePayload,
} from '../api/productProcessingApi';
import { DimensionChangeSetReview } from '../components/DimensionChangeSetReview';
import { PrecheckFinalizeProgress } from '../components/PrecheckFinalizeProgress';
import { PrecheckImageManager } from '../components/PrecheckImageManager';
import {
  addAssets,
  restoreRemovedAsset,
  type PrecheckImageTarget,
  type RemovedAssetUndo,
} from '../data/precheckImageModel';
import {
  createPrecheckFinalizeRefresh,
  type PrecheckFinalizeRefresh,
} from '../data/precheckFinalizeRefresh';
import type {
  PreviewCoreFields,
  PreviewFinalizeRun,
  PreviewImageAsset,
  PreviewImageManifest,
  PreviewItem,
  PreviewResponse,
  ShippingPackageRecord,
  ShippingPackageRecordOverride,
} from '../types';
import '../styles/ProductProcessingVerifyPage.css';

const API_BASE = '/api/product-processing';
const MAX_UPLOAD_FILES = 20;
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const ALLOWED_UPLOAD_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
const PRECHECK_PAGE_SIZES = [10, 30, 50, 100] as const;
const PRECHECK_DEFAULT_PAGE_SIZE = 30;

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
};

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

function stableStringify(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(',')}]`;
  const record = value as Record<string, unknown>;
  const entries = Object.keys(record)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${stableStringify(record[key])}`);
  return `{${entries.join(',')}}`;
}

async function hashStableValue(value: unknown): Promise<string> {
  const input = stableStringify(value);
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(input));
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
  }
  let hash = 2166136261;
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16).padStart(8, '0');
}

type StoredFinalizeRequest = {
  fingerprint: string;
  idempotencyKey: string;
  items: PreviewSavePayload[];
};

async function resolveFinalizeRequest(
  workspaceId: string,
  taskId: number,
  items: PreviewSavePayload[],
  storageKey: string,
): Promise<{ idempotencyKey: string; items: PreviewSavePayload[] }> {
  const fingerprint = await hashStableValue({
    workspaceId,
    taskId,
    desiredState: items.map(({ product_draft_id, expected_result_version, overrides }) => ({
      product_draft_id,
      expected_result_version,
      overrides,
    })),
  });
  const stored = readSession(storageKey);
  if (stored) {
    try {
      const parsed = JSON.parse(stored) as Partial<StoredFinalizeRequest>;
      if (
        parsed.fingerprint === fingerprint
        && typeof parsed.idempotencyKey === 'string'
        && parsed.idempotencyKey.length > 0
        && Array.isArray(parsed.items)
      ) {
        return { idempotencyKey: parsed.idempotencyKey, items: parsed.items as PreviewSavePayload[] };
      }
    } catch {
      // Replace malformed or legacy session data below.
    }
  }
  const idempotencyKey = `pp-preview-finalize-${taskId}-${fingerprint}`;
  const record: StoredFinalizeRequest = { fingerprint, idempotencyKey, items };
  writeSession(storageKey, JSON.stringify(record));
  return { idempotencyKey, items };
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

export function ProductProcessingPrecheckPage({ taskId, initialChangeSetId, onOpenDimensionItem, isActive = true }: Props) {
  const ctx = useMemo(() => api(), []);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [edits, setEdits] = useState<Record<number, ItemEdits>>({});
  const [retryingMediaAssetIds, setRetryingMediaAssetIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(false);
  const [pendingUploads, setPendingUploads] = useState(0);
  const [saving, setSaving] = useState(false);
  const [startingFinalize, setStartingFinalize] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [finalizeRun, setFinalizeRun] = useState<PreviewFinalizeRun | null>(null);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [activeImage, setActiveImage] = useState<string | null>(null);
  const [undoSnackbar, setUndoSnackbar] = useState<UndoSnackbar | null>(null);
  const refreshRef = useRef<PrecheckFinalizeRefresh | null>(null);
  const [pageSize, setPageSize] = useState<number>(PRECHECK_DEFAULT_PAGE_SIZE);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [onlySuccess, setOnlySuccess] = useState(false);
  const [imageZoomed, setImageZoomed] = useState(false);
  const [expandedDraftIds, setExpandedDraftIds] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!isActive) setActiveImage(null);
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

  const load = useCallback(async (preserveLocalEdits = false, quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const data = await ppRequest<PreviewResponse>(ctx, `${API_BASE}/tasks/${taskId}/preview`);
      setPreview(data);
      if (!preserveLocalEdits) setEdits({});
    } catch (err) {
      if (!quiet) fail(err);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [ctx, fail, taskId]);

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
  useEffect(() => {
    if (hasPendingSourceSync && sourceSyncPollRef.current === null) {
      sourceSyncPollRef.current = window.setInterval(() => {
        void load(true, true);
      }, 3000);
    }
    return () => {
      if (sourceSyncPollRef.current !== null) {
        window.clearInterval(sourceSyncPollRef.current);
        sourceSyncPollRef.current = null;
      }
    };
  }, [hasPendingSourceSync, load]);

  const downloadRun = useCallback(async (run: PreviewFinalizeRun, automatic: boolean) => {
    if (!run.workbook_ready || !run.download || !run.file) return;
    if (automatic) {
      if (readSession(downloadedStorageKey) === run.id) return;
      writeSession(downloadedStorageKey, run.id);
    }
    setDownloading(true);
    try {
      await ppDownload(ctx, run.download, run.file);
      notify(`最终版表格已生成（${run.product_count} 个商品 / ${run.row_count} 行）`);
    } catch (err) {
      fail(err);
    } finally {
      setDownloading(false);
    }
  }, [ctx, downloadedStorageKey, fail, notify]);

  const acceptFinalizeRun = useCallback((run: PreviewFinalizeRun) => {
    setFinalizeRun(run);
    if (run.status === 'completed') {
      removeSession(runStorageKey);
      void downloadRun(run, true);
    }
  }, [downloadRun, idempotencyStorageKey, runStorageKey]);

  useEffect(() => {
    setPreview(null);
    setEdits({});
    setRetryingMediaAssetIds(new Set());
    setFinalizeRun(null);
    setUndoSnackbar(null);
    setActiveImage(null);
    setMessage('');
    setError('');
  }, [taskId]);

  useEffect(() => {
    void load();
  }, [load]);

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
    if (storedRunId) refresh.watch(storedRunId);

    return () => {
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
  const excludedItems = (preview?.items ?? []).filter((item) => item.excluded);
  const filteredItems = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    const successScoped = onlySuccess ? allItems.filter((item) => item.status === 'completed') : allItems;
    if (!keyword) return successScoped;
    return successScoped.filter((item) => {
      const candidates = [
        item.skc,
        item.core_fields?.sku,
        String(item.item_id),
        item.product_draft_id != null ? String(item.product_draft_id) : '',
      ];
      return candidates.some((value) => value && value.toLowerCase().includes(keyword));
    });
  }, [allItems, search, onlySuccess]);

  useEffect(() => {
    setPage(1);
  }, [search, pageSize]);

  const totalPages = Math.max(1, Math.ceil(filteredItems.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pagedItems = filteredItems.slice((safePage - 1) * pageSize, safePage * pageSize);

  if (!preview) {
    return (
      <div className="verify-page">
        <header className="verify-commandbar">
          <div className="verify-command-title">
            <h1>预检</h1>
          </div>
        </header>
        {(message || error) && <div className={`verify-message ${error ? 'error' : ''}`}>{error || message}</div>}
        <p className="verify-empty">{loading ? '加载预检数据…' : '任务尚未完成，无法预检'}</p>
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

  const uploadAssets = async (draftId: number, target: PrecheckImageTarget, files: File[]) => {
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

  const saveAll = async () => {
    if (pendingUploads > 0) {
      fail('图片仍在导入，请等待完成后保存');
      return;
    }
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const items = allItems
        .filter((item) => item.product_draft_id != null)
        .map(collectDesiredState);
      const data = await saveProductPreview(ctx, taskId, items);
      notify(`已保存 ${data.saved_count ?? items.length} 条商品的预检修改`);
      await load();
    } catch (err) {
      fail(err);
    } finally {
      setSaving(false);
    }
  };

  const startFinalize = async () => {
    if (pendingUploads > 0) {
      fail('图片仍在导入，请等待完成后再完成预审');
      return;
    }
    setStartingFinalize(true);
    setError('');
    setMessage('');
    try {
      const exportableItems = allItems.filter((item) => item.exportable && (!onlySuccess || item.status === 'completed'));
      if (exportableItems.length === 0) throw new Error(onlySuccess ? '没有可导出的成功链接' : '任务没有可完成并导出的商品');
      const missingDraft = exportableItems.find((item) => item.product_draft_id == null);
      if (missingDraft) throw new Error(`可导出商品 #${missingDraft.item_id} 缺少草稿 ID，已阻止不完整提交`);
      const missingMainItems = exportableItems.filter((item) => !effectiveManifest(item).main_asset_id);
      if (missingMainItems.length > 0) {
        setMessage(`${missingMainItems.length} 个商品未选择主图，导出时将自动回退使用来源主图`);
      }

      const desiredItems = exportableItems.map(collectDesiredState);
      const request = await resolveFinalizeRequest(
        ctx.workspaceId,
        taskId,
        desiredItems,
        idempotencyStorageKey,
      );
      const run = await finalizeProductPreview(
        ctx,
        taskId,
        request.items,
        request.idempotencyKey,
      );
      writeSession(runStorageKey, run.id);
      acceptFinalizeRun(run);
      if (run.status === 'queued' || run.status === 'publishing') refreshRef.current?.watch(run.id);
    } catch (err) {
      fail(err);
    } finally {
      setStartingFinalize(false);
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
    notify('正在读取最新版本；当前未保存编辑会继续保留，请重新核对后再完成预审。');
    await load(true);
    removeSession(runStorageKey);
    removeSession(idempotencyStorageKey);
    setFinalizeRun(null);
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

  const replacePreview = (data: PreviewResponse) => {
    setPreview(data);
    setEdits({});
    // 预检清单已变化，旧 finalize run 的快照（含已删除/新恢复的链接）已失效；
    // 必须清除 run 状态，否则「仅重试失败图片」会复用旧快照继续被删除项阻挡。
    setFinalizeRun(null);
    removeSession(runStorageKey);
    removeSession(idempotencyStorageKey);
  };

  const excludeItem = async (draftId: number) => {
    const item = allItems.find((candidate) => candidate.product_draft_id === draftId);
    const label = item?.skc || `商品 #${draftId}`;
    if (!window.confirm(`确定从预检中删除「${label}」吗？删除后该商品不再参与最终导出，可在页面底部「已排除」列表中恢复。`)) return;
    setError('');
    setMessage('');
    try {
      const data = await excludePreviewItem(ctx, taskId, draftId);
      replacePreview(data);
      notify(`已从预检删除「${label}」`);
    } catch (err) {
      fail(err);
    }
  };

  const restoreItem = async (draftId: number) => {
    setError('');
    setMessage('');
    try {
      const data = await restorePreviewItem(ctx, taskId, draftId);
      replacePreview(data);
      notify(`已恢复商品 #${draftId} 到预检列表`);
    } catch (err) {
      fail(err);
    }
  };

  const restoreAllExcluded = async () => {
    if (excludedItems.length === 0) return;
    if (!window.confirm(`确定恢复全部 ${excludedItems.length} 个已删除商品吗？`)) return;
    for (const item of excludedItems) {
      const draftId = item.product_draft_id;
      if (draftId == null) continue;
      await restoreItem(draftId);
    }
  };

  const finalizing = finalizeRun?.status === 'queued' || finalizeRun?.status === 'publishing';
  const finalizeNeedsResolution = finalizeRun?.status === 'publish_failed' || finalizeRun?.status === 'stale';
  const mutationsLocked = Boolean(finalizing || startingFinalize || retrying);
  const dirtyCount = allItems.filter(itemIsDirty).length;
  const exportableCount = allItems.filter((item) => item.exportable).length;
  const allAssets = mergeAssets(...allItems.map(effectiveAssets));

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
    <div className="verify-page">
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <h1>预检与最终发布</h1>
          <p>图片先以稳定素材 ID 在本地清单中增删排序；点击完成后，仅发布最终保留图片并生成店小秘表格。</p>
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
            {saving ? '保存中…' : `保存预检修改${dirtyCount > 0 ? `（${dirtyCount} 条有修改）` : ''}`}
          </button>
          <button
            type="button"
            onClick={() => void startFinalize()}
            disabled={startingFinalize || loading || mutationsLocked || pendingUploads > 0 || finalizeNeedsResolution || exportableCount === 0}
          >
            {startingFinalize ? '正在建立完成任务…' : pendingUploads > 0 ? `正在导入图片（${pendingUploads}）` : '完成预审并导出'}
          </button>
          <button type="button" onClick={() => void load()} disabled={loading || mutationsLocked}>重新加载</button>
        </div>
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
              onChange={(event) => setOnlySuccess(event.target.checked)}
            />
            只看成功链接
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
          downloading={downloading}
          onRetry={() => void retryFinalize()}
          onDownload={() => void downloadRun(finalizeRun, false)}
          onReloadStale={() => void reloadAfterStale()}
        />
      )}

      {allItems.length === 0 && <p className="verify-empty">任务没有可预检的成功商品。</p>}

      {filteredItems.length === 0 && allItems.length > 0 && (
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
        return (
          <section key={item.item_id} className={`verify-section precheck-card${hasOverrides ? ' is-edited' : ''}`}>
            <div className="precheck-card-head">
              <button
                type="button"
                className="precheck-card-toggle"
                onClick={() => toggleExpanded(draftId)}
                aria-expanded={isExpanded}
              >
                <span className="precheck-chevron">{isExpanded ? '▾' : '▸'}</span>
                <span className="precheck-card-toggle-title">{item.skc || `商品 #${draftId}`}</span>
                {!isExpanded && item.title && <span className="precheck-card-toggle-summary">{item.title}</span>}
                <span className="verify-sub">
                  {item.exportable ? '可导出' : '不可导出'} · {hasOverrides ? '已修改' : '未修改'} · 版本 {item.preview_revision} · 状态 {item.status}
                  {item.exportable && !manifest.main_asset_id && <em className="precheck-no-main-hint"> · 未选主图（导出回退来源图）</em>}
                </span>
              </button>
              <button
                type="button"
                className="btn-mini danger precheck-exclude-btn"
                disabled={mutationsLocked || item.product_draft_id == null}
                onClick={() => {
                  if (item.product_draft_id != null) void excludeItem(item.product_draft_id);
                }}
                title={item.product_draft_id == null ? '缺少草稿 ID，无法删除' : '从预检中删除此商品，可随时恢复'}
              >
                删除
              </button>
            </div>

            {isExpanded && (
            <div className="precheck-grid">
              <div className="precheck-fields">
                <label className="precheck-label">
                  <span>标题（导出 *产品标题 / *英文标题）</span>
                  <textarea
                    rows={3}
                    value={edit.title ?? item.title}
                    disabled={mutationsLocked}
                    onChange={(event) => setEdit(draftId, { title: event.target.value })}
                    placeholder="AI 生成标题"
                  />
                </label>
                <label className="precheck-label">
                  <span>产品描述（导出产品描述列，详情图自动追加）</span>
                  <textarea
                    rows={5}
                    value={edit.description ?? item.description}
                    disabled={mutationsLocked}
                    onChange={(event) => setEdit(draftId, { description: event.target.value })}
                    placeholder="AI 生成五点描述"
                  />
                </label>
                <div className="precheck-core-grid">
                  <label>SKU货号
                    <input disabled={mutationsLocked} value={coreFields.sku ?? ''} onChange={(event) => setField(draftId, 'sku', event.target.value)} />
                  </label>
                  <label>申报价格
                    <input disabled={mutationsLocked} value={coreFields.declared_price ?? ''} onChange={(event) => setField(draftId, 'declared_price', event.target.value)} />
                  </label>
                  <label>建议售价
                    <input disabled={mutationsLocked} value={coreFields.suggested_price ?? ''} onChange={(event) => setField(draftId, 'suggested_price', event.target.value)} />
                  </label>
                  <label>库存
                    <input disabled={mutationsLocked} value={coreFields.stock ?? ''} onChange={(event) => setField(draftId, 'stock', event.target.value)} />
                  </label>
                  <label>类目路径
                    <input disabled={mutationsLocked} value={coreFields.category_path ?? ''} onChange={(event) => setField(draftId, 'category_path', event.target.value)} />
                  </label>
                  <label>类目ID
                    <input disabled={mutationsLocked} value={coreFields.category_id ?? ''} onChange={(event) => setField(draftId, 'category_id', event.target.value)} />
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

      {undoSnackbar && (
        <div className="precheck-undo-snackbar" role="status" aria-live="polite">
          <span>图片已从当前输出清单移除</span>
          <button type="button" onClick={restoreUndo}>撤销</button>
          <small>5 秒内有效</small>
        </div>
      )}

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
    </div>
  );
}

export default ProductProcessingPrecheckPage;
