import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ppDownload, ppRequest, type ApiContext } from '../api/client';
import {
  finalizeProductPreview,
  getPreviewFinalizeRun,
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
} from '../types';
import '../styles/ProductProcessingVerifyPage.css';

const API_BASE = '/api/product-processing';
const MAX_UPLOAD_FILES = 20;
const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
const ALLOWED_UPLOAD_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);

type Props = {
  taskId: number;
  initialChangeSetId?: string;
  onOpenDimensionItem: (taskId: number, taskItemId: number) => void;
};

type ItemEdits = {
  title?: string;
  description?: string;
  imageManifest?: PreviewImageManifest;
  addedAssets?: PreviewImageAsset[];
  core_fields?: PreviewCoreFields;
};

type UndoSnackbar = {
  draftId: number;
  undo: RemovedAssetUndo;
  expiresAt: number;
};

function api(): ApiContext {
  return { baseUrl: '', token: '', workspaceId: 'default' };
}

function taskStatusLabel(status: string): string {
  return ({
    queued: '等待处理',
    running: '处理中',
    paused: '已暂停',
    completed: '已完成',
    partial_failure: '部分完成',
    failed: '任务失败',
  })[status] || status;
}

function cloneManifest(manifest: PreviewImageManifest): PreviewImageManifest {
  return {
    main_asset_id: manifest.main_asset_id,
    carousel_asset_ids: [...manifest.carousel_asset_ids],
    detail_asset_ids: [...manifest.detail_asset_ids],
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

export function ProductProcessingPrecheckPage({ taskId, initialChangeSetId, onOpenDimensionItem }: Props) {
  const ctx = useMemo(() => api(), []);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [edits, setEdits] = useState<Record<number, ItemEdits>>({});
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

  const load = useCallback(async (preserveLocalEdits = false) => {
    setLoading(true);
    try {
      const data = await ppRequest<PreviewResponse>(ctx, `${API_BASE}/tasks/${taskId}/preview`);
      setPreview(data);
      if (!preserveLocalEdits) setEdits({});
    } catch (err) {
      fail(err);
    } finally {
      setLoading(false);
    }
  }, [ctx, fail, taskId]);

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

  if (!preview) {
    return (
      <div className="verify-page">
        <header className="verify-commandbar">
          <div className="verify-command-title">
            <span className="verify-eyebrow">PRODUCT PROCESSING · 预检</span>
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
    const item = preview.items.find((candidate) => (candidate.product_draft_id ?? candidate.item_id) === draftId);
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
      const item = preview.items.find((candidate) => candidate.product_draft_id === draftId);
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

  const saveAll = async () => {
    if (pendingUploads > 0) {
      fail('图片仍在导入，请等待完成后保存');
      return;
    }
    setSaving(true);
    setError('');
    setMessage('');
    try {
      const items = preview.items
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
      const exportableItems = preview.items.filter((item) => item.exportable);
      if (exportableItems.length === 0) throw new Error('任务没有可完成并导出的商品');
      const missingDraft = exportableItems.find((item) => item.product_draft_id == null);
      if (missingDraft) throw new Error(`可导出商品 #${missingDraft.item_id} 缺少草稿 ID，已阻止不完整提交`);
      const missingMain = exportableItems.find((item) => !effectiveManifest(item).main_asset_id);
      if (missingMain) throw new Error(`${missingMain.skc || `商品 #${missingMain.item_id}`} 尚未选择主图`);

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
    const item = preview.items.find((candidate) => (candidate.product_draft_id ?? candidate.item_id) === draftId);
    if (!item) return;
    setEdits((previous) => {
      const current = previous[draftId] ?? {};
      const manifest = restoreRemovedAsset(current.imageManifest ?? item.image_manifest, undo);
      return { ...previous, [draftId]: { ...current, imageManifest: manifest } };
    });
    setUndoSnackbar(null);
    notify('已撤销图片删除');
  };

  const finalizing = finalizeRun?.status === 'queued' || finalizeRun?.status === 'publishing';
  const finalizeNeedsResolution = finalizeRun?.status === 'publish_failed' || finalizeRun?.status === 'stale';
  const mutationsLocked = Boolean(finalizing || startingFinalize || retrying);
  const dirtyCount = preview.items.filter(itemIsDirty).length;
  const exportableCount = preview.items.filter((item) => item.exportable).length;
  const allAssets = mergeAssets(...preview.items.map(effectiveAssets));

  return (
    <div className="verify-page">
      <header className="verify-commandbar">
        <div className="verify-command-title">
          <span className="verify-eyebrow">PRODUCT PROCESSING · 预检环节</span>
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
            disabled={saving || loading || mutationsLocked || pendingUploads > 0 || preview.items.length === 0}
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

      {preview.items.length === 0 && <p className="verify-empty">任务没有可预检的成功商品。</p>}

      {preview.items.map((item) => {
        const draftId = item.product_draft_id ?? item.item_id;
        const edit = editFor(item);
        const coreFields = effectiveCoreFields(item);
        const manifest = effectiveManifest(item);
        const assets = effectiveAssets(item);
        const hasOverrides = itemIsDirty(item);
        return (
          <section key={item.item_id} className={`verify-section precheck-card${hasOverrides ? ' is-edited' : ''}`}>
            <div className="verify-section-head">
              <h2>{item.skc || `商品 #${draftId}`}</h2>
              <span className="verify-sub">
                {item.exportable ? '可导出' : '不可导出'} · {hasOverrides ? '已修改' : '未修改'} · 版本 {item.preview_revision} · 状态 {item.status}
              </span>
            </div>

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
                  <label>物流包裹长(cm)
                    <input disabled={mutationsLocked} value={coreFields.length_cm ?? ''} onChange={(event) => setField(draftId, 'length_cm', event.target.value)} />
                  </label>
                  <label>物流包裹宽(cm)
                    <input disabled={mutationsLocked} value={coreFields.width_cm ?? ''} onChange={(event) => setField(draftId, 'width_cm', event.target.value)} />
                  </label>
                  <label>物流包裹高(cm)
                    <input disabled={mutationsLocked} value={coreFields.height_cm ?? ''} onChange={(event) => setField(draftId, 'height_cm', event.target.value)} />
                  </label>
                  <label>重量(g)
                    <input disabled={mutationsLocked} value={coreFields.weight_g ?? ''} onChange={(event) => setField(draftId, 'weight_g', event.target.value)} />
                  </label>
                </div>
              </div>

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
                <PrecheckImageManager
                  assets={assets}
                  manifest={manifest}
                  disabled={mutationsLocked || item.product_draft_id == null}
                  onAddFiles={(target, files) => {
                    if (item.product_draft_id != null) void uploadAssets(item.product_draft_id, target, files);
                  }}
                  onManifestChange={(nextManifest) => setManifest(draftId, nextManifest)}
                  onPreview={setActiveImage}
                  onUndoAvailable={(undo) => setUndoSnackbar({
                    draftId,
                    undo,
                    expiresAt: Date.now() + 5000,
                  })}
                />
              </div>
            </div>
          </section>
        );
      })}

      {undoSnackbar && (
        <div className="precheck-undo-snackbar" role="status" aria-live="polite">
          <span>图片已从当前输出清单移除</span>
          <button type="button" onClick={restoreUndo}>撤销</button>
          <small>5 秒内有效</small>
        </div>
      )}

      {activeImage && (
        <div className="precheck-lightbox" role="dialog" aria-modal="true" aria-label="图片预览" onClick={() => setActiveImage(null)}>
          <img src={activeImage} alt="预览大图" onClick={(event) => event.stopPropagation()} />
          <button type="button" className="precheck-lightbox-close" aria-label="关闭" onClick={() => setActiveImage(null)}>×</button>
        </div>
      )}
    </div>
  );
}

export default ProductProcessingPrecheckPage;
